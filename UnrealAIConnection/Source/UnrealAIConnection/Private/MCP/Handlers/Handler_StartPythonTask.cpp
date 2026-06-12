// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// start_python_task / start_python_file_task - async python execution via
// the task registry (dive PR). Long python ops (FBX imports, builds) used to
// run synchronously inside the MCP dispatch tick and blow the bridge's 30s
// socket timeout (-32099, 3x in one production session). These handlers
// return a task_id immediately and defer the actual exec to a LATER
// game-thread pump via AsyncTask(ENamedThreads::GameThread, ...):
//
//   handler (game thread, inside MCPServer::TickClients)
//     -> CreateTask + AsyncTask enqueue          (no python runs yet)
//     -> returns task_id; TickClients writes the response to the socket
//   next game-thread task pump
//     -> python executes (editor busy while it runs - that part is
//        unavoidable, UE python is game-thread-only and blocking)
//     -> result lands in the task registry; caller polls poll_task
//
// Cancellation is cooperative and only effective BEFORE exec starts
// (cancel_task sets the flag; the deferred lambda checks it once). Python
// cannot be interrupted mid-exec - UE exposes no such API.
//
// Output capture caveat (same as run_python_file): ExecuteFile mode's
// CommandResult is often "None"; scripts should emit results via
// unreal.log(...) markers, which are captured into the task result's
// log_output field (capped at 64KB).
//
// Error format: "<tool_name>: <error_code>: <detail>".
// Stable error codes: missing_required_field, file_not_found,
// python_unavailable, file_write_failed.

#include "MCP/MCPHandler.h"
#include "MCP/TaskRegistry.h"

#if WITH_EDITOR

#include "IPythonScriptPlugin.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/ScopeExit.h"
#include "HAL/FileManager.h"
#include "Async/Async.h"

namespace
{
    // Cap on the joined LogOutput string stored in the task result. Long
    // import logs are useful up to a point; past that they just bloat the
    // poll_task response.
    static constexpr int32 kMaxLogOutputChars = 64 * 1024;

    // Runs ON THE GAME THREAD via AsyncTask. Executes the script and writes
    // the outcome into the task registry.
    void RunPythonDeferred(
        const FString& TaskId,
        const FString& ScriptPath,
        const bool bIsTempFile,
        TSharedPtr<TAtomic<bool>> CancelFlag)
    {
        // Re-check python availability inside the deferred lambda - the
        // editor may be tearing down between enqueue and pump.
        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            FUCMCPTaskRegistry::Get().MarkFailed(TaskId,
                TEXT("python_unavailable: Python script plugin no longer available"));
            return;
        }

        // Cancel-before-start support.
        if (CancelFlag.IsValid() && CancelFlag->Load())
        {
            FUCMCPTaskRegistry::Get().MarkCancelled(TaskId);
            return;
        }

        FUCMCPTaskRegistry::Get().MarkRunning(TaskId);

        FPythonCommandEx Cmd;
        Cmd.Command = ScriptPath;
        Cmd.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
        Cmd.Flags = EPythonCommandFlags::None;

        const bool bOk = Py->ExecPythonCommandEx(Cmd);

        FString LogOutputStr;
        for (const FPythonLogOutputEntry& Entry : Cmd.LogOutput)
        {
            LogOutputStr += Entry.Output;
            LogOutputStr += TEXT("\n");
        }
        bool bTruncated = false;
        if (LogOutputStr.Len() > kMaxLogOutputChars)
        {
            LogOutputStr.LeftInline(kMaxLogOutputChars);
            bTruncated = true;
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        // Python-level errors are data, not task failures: ok:false +
        // log_output tell the caller what went wrong. MarkFailed is reserved
        // for infrastructure faults (plugin gone, temp write failed).
        Result->SetBoolField(TEXT("ok"), bOk);
        Result->SetStringField(TEXT("output"), Cmd.CommandResult);
        Result->SetStringField(TEXT("log_output"), LogOutputStr);
        if (bTruncated)
        {
            Result->SetStringField(TEXT("note"), TEXT("log_output truncated to 64KB"));
        }
        Result->SetStringField(bIsTempFile ? TEXT("temp_script") : TEXT("path"), ScriptPath);

        FUCMCPTaskRegistry::Get().MarkCompleted(TaskId, Result);
    }

    TSharedPtr<FJsonObject> MakePendingResponse(const FString& TaskId, const TCHAR* Type)
    {
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("task_id"), TaskId);
        Out->SetStringField(TEXT("type"), Type);
        Out->SetStringField(TEXT("status"), UCMCPTaskStatus::Pending);
        Out->SetStringField(TEXT("note"),
            TEXT("Python runs on the game thread on a later tick; the editor will be busy while it runs. "
                 "Poll via poll_task. cancel_task only works before execution starts."));
        return Out;
    }
} // namespace

// ---------------------------------------------------------------------------

class FHandler_StartPythonTask : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("start_python_task"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        check(IsInGameThread());

        FString Code;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("code"), Code) || Code.IsEmpty())
        {
            OutError = TEXT("start_python_task: missing_required_field: 'code' must be a non-empty string");
            return nullptr;
        }

        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("start_python_task: python_unavailable: Python script plugin not available (is PythonScriptPlugin enabled?)");
            return nullptr;
        }

        // Temp-file pattern mirrors Handler_ExecutePython.cpp: ExecuteFile
        // mode resolves Command as a path first; a real file bypasses the
        // path-vs-literal heuristic for multi-line scripts.
        const FString TempDir = FPaths::Combine(
            FPaths::ProjectIntermediateDir(),
            TEXT("UnrealAIConnectionPython"));
        IFileManager::Get().MakeDirectory(*TempDir, /*Tree=*/true);

        const FString TempPath = FPaths::Combine(
            TempDir,
            *FString::Printf(TEXT("task_%s.py"), *FGuid::NewGuid().ToString(EGuidFormats::Short)));

        if (!FFileHelper::SaveStringToFile(Code, *TempPath, FFileHelper::EEncodingOptions::ForceUTF8))
        {
            OutError = FString::Printf(
                TEXT("start_python_task: file_write_failed: failed to write Python script to %s"), *TempPath);
            return nullptr;
        }

        TSharedPtr<TAtomic<bool>> CancelFlag;
        const FString TaskId = FUCMCPTaskRegistry::Get().CreateTask(TEXT("python"), CancelFlag);

        // Captures by VALUE - lifetime extends to the deferred pump.
        AsyncTask(ENamedThreads::GameThread,
            [TaskId, TempPath, CancelFlag]()
            {
                ON_SCOPE_EXIT
                {
                    IFileManager::Get().Delete(*TempPath, /*RequireExists=*/false);
                };
                RunPythonDeferred(TaskId, TempPath, /*bIsTempFile=*/true, CancelFlag);
            });

        return MakePendingResponse(TaskId, TEXT("python"));
    }
};

// ---------------------------------------------------------------------------

class FHandler_StartPythonFileTask : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("start_python_file_task"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        check(IsInGameThread());

        FString Path;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
        {
            OutError = TEXT("start_python_file_task: missing_required_field: 'path' must be a non-empty string");
            return nullptr;
        }

        const FString FullPath = FPaths::ConvertRelativePathToFull(Path);
        if (!FPaths::FileExists(FullPath))
        {
            OutError = FString::Printf(
                TEXT("start_python_file_task: file_not_found: file does not exist: %s"), *FullPath);
            return nullptr;
        }

        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("start_python_file_task: python_unavailable: Python script plugin not available (is PythonScriptPlugin enabled?)");
            return nullptr;
        }

        TSharedPtr<TAtomic<bool>> CancelFlag;
        const FString TaskId = FUCMCPTaskRegistry::Get().CreateTask(TEXT("python_file"), CancelFlag);

        AsyncTask(ENamedThreads::GameThread,
            [TaskId, FullPath, CancelFlag]()
            {
                RunPythonDeferred(TaskId, FullPath, /*bIsTempFile=*/false, CancelFlag);
            });

        return MakePendingResponse(TaskId, TEXT("python_file"));
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_StartPythonTask()
{
    return MakeShared<FHandler_StartPythonTask>();
}

TSharedRef<IUCMCPHandler> Make_Handler_StartPythonFileTask()
{
    return MakeShared<FHandler_StartPythonFileTask>();
}

#endif // WITH_EDITOR
