// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_console_variable - read a single UE Console Variable by name and
// return all four representations (string / int / float / bool) along with
// its detected type and read-only flag. Pairs with set_console_variable
// (Tier 1 ergonomics, completes the v0.10.x roadmap from docs/HANDOFF.md).
//
// Distinct from execute_console_command: that runs a console *command*
// (which may or may not mutate a CVar). This handler reads CVar state
// directly via IConsoleManager and never executes the underlying console
// engine, so it works on any CVar regardless of whether typing it would
// produce parseable Exec output.
//
// UE 5.7 surface used (cited against
// F:/UE_5.7/Engine/Source/Runtime/Core/Public/HAL/IConsoleManager.h):
//   - IConsoleManager::Get()                                  line 1270
//   - IConsoleManager::FindConsoleVariable(name, bTrack=true) line 1170
//     Returns nullptr for unknown names AND for console *commands* —
//     the latter live in IConsoleObject's broader namespace and require
//     FindConsoleObject + IsConsoleCommand to disambiguate. We point
//     the user toward execute_console_command in the not-found message.
//   - IConsoleVariable::IsVariableInt / Float / Bool / String    lines 478-481
//   - IConsoleVariable::GetInt / GetFloat / GetBool / GetString  lines 628-637
//     All four are coercing — safe to call regardless of underlying type.
//   - IConsoleVariable::GetFlags()                               line 410
//   - ECVF_ReadOnly = 0x4                                        line 71
//   - ECVF_SetByMask = 0xff000000                                line 140
//   - GetConsoleVariableSetByName(flags) -> human-readable str   line 201
//   - IConsoleObject::GetHelp()                                  line 402
//
// Error format: "get_console_variable: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, cvar_not_found.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "HAL/IConsoleManager.h"

namespace
{
    static FString CVarTypeToString(IConsoleVariable* CVar)
    {
        if (CVar->IsVariableInt())    return TEXT("int");
        if (CVar->IsVariableFloat())  return TEXT("float");
        if (CVar->IsVariableBool())   return TEXT("bool");
        if (CVar->IsVariableString()) return TEXT("string");
        return TEXT("unknown");
    }
}

class FHandler_GetConsoleVariable : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_console_variable"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("get_console_variable: missing_required_field: 'name' is required");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("get_console_variable: missing_required_field: 'name' is required and must not be empty");
            return nullptr;
        }

        // --- look up the CVar ----------------------------------------------
        //
        // bTrackFrequentCalls=false avoids polluting UE's console-history
        // telemetry with our automation reads.

        IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(*Name, /*bTrackFrequentCalls=*/ false);
        if (!CVar)
        {
            OutError = FString::Printf(
                TEXT("get_console_variable: cvar_not_found: '%s' is not a registered Console Variable. "
                     "If this is a console *command* (e.g. 'r.RestartRenderer'), use execute_console_command instead."),
                *Name);
            return nullptr;
        }

        // --- read state ----------------------------------------------------

        const EConsoleVariableFlags Flags = CVar->GetFlags();
        const bool bReadOnly = (Flags & ECVF_ReadOnly) != 0;
        const TCHAR* SetByName = GetConsoleVariableSetByName(Flags);
        const TCHAR* HelpText = CVar->GetHelp();

        // --- build response ------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Name);
        Out->SetStringField(TEXT("type"), CVarTypeToString(CVar));
        Out->SetBoolField(TEXT("read_only"), bReadOnly);
        Out->SetStringField(TEXT("set_by"), SetByName ? SetByName : TEXT(""));
        Out->SetStringField(TEXT("value_string"), CVar->GetString());
        Out->SetNumberField(TEXT("value_int"), static_cast<double>(CVar->GetInt()));
        Out->SetNumberField(TEXT("value_float"), static_cast<double>(CVar->GetFloat()));
        Out->SetBoolField(TEXT("value_bool"), CVar->GetBool());
        Out->SetStringField(TEXT("help"), HelpText ? HelpText : TEXT(""));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetConsoleVariable()
{
    return MakeShared<FHandler_GetConsoleVariable>();
}
