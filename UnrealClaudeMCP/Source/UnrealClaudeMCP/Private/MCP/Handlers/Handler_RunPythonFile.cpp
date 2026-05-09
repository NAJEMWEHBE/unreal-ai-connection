// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// run_python_file - execute a .py file from disk via UE's Python plugin.
// Complement to execute_unreal_python (which takes the source as a string
// in the JSON-RPC params). For non-trivial scripts, embedding the source
// in JSON requires double-escaping every quote and backslash; pointing at
// a file on disk eliminates that pain entirely.
//
// Path semantics: caller-supplied path can be absolute OR relative.
// Relative paths resolve via FPaths::ConvertRelativePathToFull -- which
// in editor sessions resolves against the engine binary CWD, typically
// the project root.
//
// Output capture caveat (same as execute_unreal_python): UE Python's
// ExecuteFile mode does NOT return stdout / eval-result through
// FPythonCommandEx::CommandResult; the field is "None" for file-mode
// runs. To round-trip a result back, the script should emit it via
// unreal.log("__MARKER__<json>__END__") and the caller retrieves it
// with get_log_lines (category_filter: "LogPython"). See
// scripts/seed_test_project.py for the canonical pattern.
//
// Error format: "run_python_file: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, file_not_found,
// python_unavailable.

#include "MCP/MCPHandler.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "Dom/JsonObject.h"

class FHandler_RunPythonFile : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("run_python_file"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("run_python_file: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("run_python_file: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // Resolve relative paths to absolute. If the input is already
        // absolute, ConvertRelativePathToFull returns it unchanged. Doing
        // this resolution ourselves (rather than letting UE Python do it)
        // gives a clear error path when the file is missing.
        const FString FullPath = FPaths::ConvertRelativePathToFull(InputPath);

        if (!IFileManager::Get().FileExists(*FullPath))
        {
            OutError = FString::Printf(
                TEXT("run_python_file: file_not_found: '%s' does not exist (resolved to '%s')"),
                *InputPath, *FullPath);
            return nullptr;
        }

        // --- get python plugin ---------------------------------------------

        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("run_python_file: python_unavailable: PythonScriptPlugin not enabled or available");
            return nullptr;
        }

        // --- exec ----------------------------------------------------------

        FPythonCommandEx Cmd;
        Cmd.Command = FullPath;
        Cmd.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
        Cmd.Flags = EPythonCommandFlags::None;

        const bool bOk = Py->ExecPythonCommandEx(Cmd);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), bOk);
        Out->SetStringField(TEXT("output"), Cmd.CommandResult);
        Out->SetStringField(TEXT("path"), FullPath);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_RunPythonFile()
{
    return MakeShared<FHandler_RunPythonFile>();
}
