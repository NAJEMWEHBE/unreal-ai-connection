// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// exec_python_persistent - Tier 2 PR #45.
//
// Identical to execute_unreal_python except FileExecutionScope = Public,
// which tells UE's Python plugin to share the console's globals/locals
// dict across calls. Variables, imports, and function/class definitions
// from one call are visible in the next -- letting Claude build up state
// across turns without re-loading or re-importing every time.
//
// Why a separate handler instead of an opt-in flag on execute_unreal_python:
//   - Persistent state is a sticky semantic surprise. A handler that
//     might-or-might-not share globals based on a flag is harder to
//     reason about than two clearly-named variants.
//   - Named handlers make MCP-client behavior obvious: a tool called
//     'exec_python_persistent' clearly signals "expect state to carry over",
//     while one called 'execute_unreal_python' clearly signals "fresh slate".
//
// Same FPythonCommandEx + temp-file pattern as execute_unreal_python (the
// ExecuteFile-mode-resolves-as-path heuristic still applies). Same output-
// capture caveat: ExecuteFile mode does not return stdout via CommandResult
// -- if you need to round-trip results, use unreal.log("__MARKER__<json>__END__")
// + get_log_lines{category_filter:"LogPython"} (same pattern documented for
// run_python_file and apply_python_to_selection).
//
// Pairs with reset_python_state which clears the persistent globals.
//
// Error format: "exec_python_persistent: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, python_unavailable, write_failed.

#include "MCP/MCPHandler.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/ScopeExit.h"
#include "HAL/FileManager.h"

class FHandler_ExecPythonPersistent : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("exec_python_persistent"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString Code;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("code"), Code))
        {
            OutError = TEXT("exec_python_persistent: missing_required_field: 'code' is required");
            return nullptr;
        }

        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("exec_python_persistent: python_unavailable: PythonScriptPlugin is not available "
                            "(enable it in the project's .uproject and restart the editor)");
            return nullptr;
        }

        const FString TempDir = FPaths::Combine(
            FPaths::ProjectIntermediateDir(),
            TEXT("UnrealClaudeMCPPython")
        );
        IFileManager::Get().MakeDirectory(*TempDir, /*Tree=*/true);

        const FString TempPath = FPaths::Combine(
            TempDir,
            *FString::Printf(TEXT("exec_persistent_%s.py"), *FGuid::NewGuid().ToString(EGuidFormats::Short))
        );

        if (!FFileHelper::SaveStringToFile(Code, *TempPath, FFileHelper::EEncodingOptions::ForceUTF8))
        {
            OutError = FString::Printf(
                TEXT("exec_python_persistent: write_failed: could not write temp script to '%s'"), *TempPath);
            return nullptr;
        }

        ON_SCOPE_EXIT
        {
            IFileManager::Get().Delete(*TempPath, /*RequireExists=*/false);
        };

        FPythonCommandEx Cmd;
        Cmd.Command = TempPath;
        Cmd.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
        // The ONE difference vs execute_unreal_python: Public scope shares
        // the globals/locals dict with UE's Python console, so state from
        // prior exec_python_persistent calls is visible. (Default Private
        // scope creates a fresh dict per call -- which is what
        // execute_unreal_python deliberately uses for its "fresh slate"
        // semantics.)
        Cmd.FileExecutionScope = EPythonFileExecutionScope::Public;
        Cmd.Flags = EPythonCommandFlags::None;

        const bool bOk = Py->ExecPythonCommandEx(Cmd);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), bOk);
        Out->SetStringField(TEXT("output"), Cmd.CommandResult);
        Out->SetStringField(TEXT("temp_script"), TempPath);
        Out->SetStringField(TEXT("scope"), TEXT("public"));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ExecPythonPersistent()
{
    return MakeShared<FHandler_ExecPythonPersistent>();
}
