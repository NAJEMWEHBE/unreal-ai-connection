// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// execute_unreal_python - universal escape hatch.
//
// Always writes the source to a unique temp .py file under
// Intermediate/UnrealClaudeMCPPython/ and passes UE the file path.
// ExecPythonCommandEx in ExecuteFile mode tries to resolve the Command
// as a file path FIRST and only falls back to literal source on some
// inputs. Multi-line scripts with comments / quotes / paths embedded
// confuse this heuristic. Writing to a real file bypasses the ambiguity.

#include "MCP/MCPHandler.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/ScopeExit.h"
#include "HAL/FileManager.h"

class FHandler_ExecutePython : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("execute_unreal_python"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString Code;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("code"), Code))
        {
            OutError = TEXT("Missing required string param: 'code'");
            return nullptr;
        }

        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("Python script plugin not available (is PythonScriptPlugin enabled?)");
            return nullptr;
        }

        const FString TempDir = FPaths::Combine(
            FPaths::ProjectIntermediateDir(),
            TEXT("UnrealClaudeMCPPython")
        );
        IFileManager::Get().MakeDirectory(*TempDir, /*Tree=*/true);

        const FString TempPath = FPaths::Combine(
            TempDir,
            *FString::Printf(TEXT("exec_%s.py"), *FGuid::NewGuid().ToString(EGuidFormats::Short))
        );

        if (!FFileHelper::SaveStringToFile(Code, *TempPath, FFileHelper::EEncodingOptions::ForceUTF8))
        {
            OutError = FString::Printf(TEXT("Failed to write Python script to %s"), *TempPath);
            return nullptr;
        }

        // Guarantee deletion even if ExecPythonCommandEx throws or we early-return below.
        ON_SCOPE_EXIT
        {
            IFileManager::Get().Delete(*TempPath, /*RequireExists=*/false);
        };

        FPythonCommandEx Cmd;
        Cmd.Command = TempPath;
        Cmd.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
        Cmd.Flags = EPythonCommandFlags::None;

        const bool bOk = Py->ExecPythonCommandEx(Cmd);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), bOk);
        Out->SetStringField(TEXT("output"), Cmd.CommandResult);
        Out->SetStringField(TEXT("temp_script"), TempPath);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ExecutePython()
{
    return MakeShared<FHandler_ExecutePython>();
}
