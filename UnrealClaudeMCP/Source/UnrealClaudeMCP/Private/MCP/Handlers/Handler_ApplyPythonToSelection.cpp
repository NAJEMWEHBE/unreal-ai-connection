// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// apply_python_to_selection - run user-supplied Python with the editor's
// current selection pre-bound as locals. Convenience wrapper around
// execute_unreal_python that injects boilerplate to fetch:
//
//   selection        -> currently-selected level actors (TArray<AActor*>)
//   selected_assets  -> currently-selected content-browser assets (TArray<UObject*>)
//
// The user's code follows the boilerplate, so it can use either name
// directly without re-implementing the lookup. Cuts the most common
// "operate on what I have selected" pattern down to one line of intent.
//
// Boilerplate uses unreal.get_editor_subsystem(EditorActorSubsystem) -- the
// canonical UE 5.x access path -- with a try/except fallback to the older
// EditorLevelLibrary for projects on older Python plugin builds. Same
// fallback for selected_assets.
//
// Output capture caveat (same as execute_unreal_python / run_python_file):
// FPythonCommandEx::ExecuteFile mode does not return stdout / eval-result
// through CommandResult. To round-trip a result back, the script should
// emit it via unreal.log("__MARKER__<json>__END__") and the caller
// retrieves through get_log_lines (category_filter: "LogPython").
//
// Error format: "apply_python_to_selection: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, python_unavailable, write_failed.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "IPythonScriptPlugin.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/ScopeExit.h"
#include "HAL/FileManager.h"

namespace
{
    // The boilerplate prepended to the user's code. Every binding is wrapped
    // in try/except so a single missing API doesn't kill the script before
    // the user's code runs -- empty list is a safe default for both bindings.
    static const TCHAR* kSelectionBoilerplate = TEXT(
        "import unreal\n"
        "try:\n"
        "    _ucmcp_actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)\n"
        "    selection = list(_ucmcp_actor_subsystem.get_selected_level_actors())\n"
        "except Exception:\n"
        "    try:\n"
        "        selection = list(unreal.EditorLevelLibrary.get_selected_level_actors())\n"
        "    except Exception:\n"
        "        selection = []\n"
        "try:\n"
        "    selected_assets = list(unreal.EditorUtilityLibrary.get_selected_assets())\n"
        "except Exception:\n"
        "    selected_assets = []\n"
        "\n"
        "# --- user code begins ---\n"
    );
}

class FHandler_ApplyPythonToSelection : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("apply_python_to_selection"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        FString UserCode;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("code"), UserCode))
        {
            OutError = TEXT("apply_python_to_selection: missing_required_field: 'code' is required");
            return nullptr;
        }

        // --- get python plugin ---------------------------------------------

        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("apply_python_to_selection: python_unavailable: PythonScriptPlugin not enabled or available");
            return nullptr;
        }

        // --- compose wrapped script ----------------------------------------
        //
        // Concatenate the boilerplate + user code into a single string and
        // write to a unique temp file under Intermediate/UnrealClaudeMCPPython/
        // (matches the execute_unreal_python pattern). ExecuteFile mode then
        // tries the path resolution path; the file always wins over the
        // literal-source heuristic when a real path exists on disk.

        const FString WrappedCode = FString(kSelectionBoilerplate) + UserCode;

        const FString TempDir = FPaths::Combine(
            FPaths::ProjectIntermediateDir(),
            TEXT("UnrealClaudeMCPPython")
        );
        IFileManager::Get().MakeDirectory(*TempDir, /*Tree=*/ true);

        const FString TempPath = FPaths::Combine(
            TempDir,
            *FString::Printf(TEXT("apply_sel_%s.py"), *FGuid::NewGuid().ToString(EGuidFormats::Short))
        );

        if (!FFileHelper::SaveStringToFile(WrappedCode, *TempPath, FFileHelper::EEncodingOptions::ForceUTF8))
        {
            OutError = FString::Printf(
                TEXT("apply_python_to_selection: write_failed: could not write wrapped script to '%s'"),
                *TempPath);
            return nullptr;
        }

        // Guarantee deletion even on exception/early-return.
        ON_SCOPE_EXIT
        {
            IFileManager::Get().Delete(*TempPath, /*RequireExists=*/ false);
        };

        // --- exec ----------------------------------------------------------

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

TSharedRef<IUCMCPHandler> Make_Handler_ApplyPythonToSelection()
{
    return MakeShared<FHandler_ApplyPythonToSelection>();
}
