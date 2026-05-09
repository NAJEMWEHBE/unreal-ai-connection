// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// reset_python_state - clear all user-defined names from UE Python's
// public (shared-with-console) globals dict. Pairs with
// exec_python_persistent: lets Claude wipe accumulated state and start
// fresh without restarting the editor.
//
// Implementation: runs a small Python snippet via Public scope that
// iterates globals() and deletes every name not starting with '_'.
// The leading-underscore convention covers both Python's dunder names
// (__name__, __builtins__, etc.) and conventional private names. Imports
// the user explicitly added (e.g. `import unreal`) ARE cleared -- the
// caller can re-import in the next exec_python_persistent call.
//
// Error format: "reset_python_state: <error_code>: <human-readable detail>"
// Stable error codes: python_unavailable, reset_failed.

#include "MCP/MCPHandler.h"
#include "IPythonScriptPlugin.h"

class FHandler_ResetPythonState : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("reset_python_state"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& OutError) override
    {
        IPythonScriptPlugin* Py = IPythonScriptPlugin::Get();
        if (!Py || !Py->IsPythonAvailable())
        {
            OutError = TEXT("reset_python_state: python_unavailable: PythonScriptPlugin is not available "
                            "(enable it in the project's .uproject and restart the editor)");
            return nullptr;
        }

        // List comprehension wraps list(globals()) so we're not mutating
        // the dict while iterating. The leading-underscore filter spares
        // dunder names (__name__, __builtins__, __doc__, etc.) and any
        // private names the user happened to define with a _ prefix --
        // safer to over-preserve than to break Python introspection.
        // The trailing `_n` cleanup deletes the loop variable itself
        // (otherwise it'd persist as a stale residue of the reset).
        const FString ResetCode = TEXT(
            "for _n in [k for k in list(globals()) if not k.startswith('_')]:\n"
            "    del globals()[_n]\n"
            "try:\n"
            "    del _n\n"
            "except NameError:\n"
            "    pass\n");

        FPythonCommandEx Cmd;
        Cmd.Command = ResetCode;
        Cmd.ExecutionMode = EPythonCommandExecutionMode::ExecuteFile;
        // Public scope is the entire point -- we MUST be acting on the
        // same globals dict that exec_python_persistent uses.
        Cmd.FileExecutionScope = EPythonFileExecutionScope::Public;
        Cmd.Flags = EPythonCommandFlags::None;

        const bool bOk = Py->ExecPythonCommandEx(Cmd);
        if (!bOk)
        {
            OutError = FString::Printf(
                TEXT("reset_python_state: reset_failed: ExecPythonCommandEx returned false. CommandResult: %s"),
                *Cmd.CommandResult);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("scope"), TEXT("public"));
        Out->SetStringField(TEXT("note"),
            TEXT("All user-defined names cleared from UE Python's public globals dict. "
                 "Names starting with '_' (dunders + conventional private) are preserved. "
                 "Subsequent exec_python_persistent calls start with a fresh user state but "
                 "must re-import any modules they need (e.g. 'import unreal')."));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ResetPythonState()
{
    return MakeShared<FHandler_ResetPythonState>();
}
