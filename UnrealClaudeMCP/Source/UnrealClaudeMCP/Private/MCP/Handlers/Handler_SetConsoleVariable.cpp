// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_console_variable - mutate a UE Console Variable by name.
// Polymorphic input: 'value' may be a JSON string, number, or bool — the
// handler coerces to a string and forwards to IConsoleVariable::Set, which
// parses against the CVar's declared type. Pre-rejects ECVF_ReadOnly CVars
// (those silently no-op after early init), and post-verifies the change
// landed (per the post-verify discipline established for material instance
// parameter sets — see HANDOFF.md "UE 5.7 traps already mapped").
//
// Decision: use ECVF_SetByConsole as our SetBy priority. UE's CVar system
// silently drops Set calls whose priority is below the current setter; the
// console-tier is the highest, matching "user typed it in the editor
// console" semantics — the natural pairing for an MCP-driven mutation.
// (See IConsoleManager.h:142-175 for the priority ordering.)
//
// UE 5.7 surface used (cited against
// F:/UE_5.7/Engine/Source/Runtime/Core/Public/HAL/IConsoleManager.h):
//   - IConsoleManager::Get()                                  line 1270
//   - IConsoleManager::FindConsoleVariable(name, bTrack=true) line 1170
//   - IConsoleVariable::Set<T>(T, EConsoleVariableFlags=ECVF_SetByCode, FName=NONE)
//                                                             line 750 (templated convenience).
//     Routes through Set(TCHAR*, FSetContext&) at line 721 and ultimately
//     through the pure virtual Set(TCHAR*, FResolvedContext&) at line 615.
//   - IConsoleVariable::GetString/GetInt/GetFloat/GetBool     lines 628-637
//   - IConsoleVariable::IsVariable*                           lines 478-481
//   - IConsoleVariable::GetFlags() / ECVF_ReadOnly            line 410 / 71
//   - GetConsoleVariableSetByName(flags) -> human-readable    line 201
//   - ECVF_SetByConsole = 0x0F000000                          line 175
//
// Error format: "set_console_variable: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, cvar_not_found, read_only,
//                     invalid_value_type.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
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

    // Coerce a JSON value (string / number / bool) to the canonical string
    // form expected by IConsoleVariable::Set. UE's CVar parser is type-aware
    // on the receiving side, so "1.5" works for float CVars, "5" works for
    // int CVars, "1"/"0" works for bools. Returning the bool-as-"1"/"0"
    // matches UE's own convenience overload at IConsoleManager.h:733.
    static bool CoerceJsonValueToString(const TSharedPtr<FJsonValue>& Value, FString& OutString, FString& OutError)
    {
        if (!Value.IsValid())
        {
            OutError = TEXT("set_console_variable: missing_required_field: 'value' is required");
            return false;
        }

        switch (Value->Type)
        {
        case EJson::String:
            Value->TryGetString(OutString);
            return true;

        case EJson::Number:
            // %g emits clean integer form for integer-valued doubles
            // ("42" not "42.000000") and concise float form for fractions
            // ("1.5" not "1.500000"). UE's CVar string parser handles both.
            OutString = FString::Printf(TEXT("%g"), Value->AsNumber());
            return true;

        case EJson::Boolean:
            OutString = Value->AsBool() ? TEXT("1") : TEXT("0");
            return true;

        default:
            OutError = TEXT("set_console_variable: invalid_value_type: 'value' must be a string, number, or bool (got JSON object/array/null)");
            return false;
        }
    }
}

class FHandler_SetConsoleVariable : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_console_variable"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("set_console_variable: missing_required_field: 'name' and 'value' are required");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("set_console_variable: missing_required_field: 'name' is required and must not be empty");
            return nullptr;
        }

        const TSharedPtr<FJsonValue> ValueField = Params->TryGetField(TEXT("value"));
        FString ValueString;
        if (!CoerceJsonValueToString(ValueField, ValueString, OutError))
        {
            // OutError is already populated by the coercer.
            return nullptr;
        }

        // --- look up the CVar ----------------------------------------------

        IConsoleVariable* CVar = IConsoleManager::Get().FindConsoleVariable(*Name, /*bTrackFrequentCalls=*/ false);
        if (!CVar)
        {
            OutError = FString::Printf(
                TEXT("set_console_variable: cvar_not_found: '%s' is not a registered Console Variable. "
                     "If this is a console *command*, use execute_console_command instead."),
                *Name);
            return nullptr;
        }

        // --- reject read-only CVars early ----------------------------------
        //
        // ECVF_ReadOnly CVars (e.g. r.RHIThreadEnable, r.SkinCache.CompileShaders)
        // only accept Set during very early initialization. After editor
        // startup, IConsoleVariable::Set silently no-ops on these. Surface
        // a clear error rather than letting the call disappear.

        const EConsoleVariableFlags FlagsBefore = CVar->GetFlags();
        if ((FlagsBefore & ECVF_ReadOnly) != 0)
        {
            OutError = FString::Printf(
                TEXT("set_console_variable: read_only: '%s' has the ECVF_ReadOnly flag and only accepts changes during early initialization. "
                     "Set it via DefaultEngine.ini ([ConsoleVariables] section) or a Scalability/DeviceProfile ini for persistent config."),
                *Name);
            return nullptr;
        }

        // --- apply --------------------------------------------------------
        //
        // ECVF_SetByConsole is the highest SetBy priority — matches the
        // semantics of the user typing the change in the editor's console.
        // Lower priorities (ECVF_SetByCode default, ECVF_SetByDeviceProfile,
        // etc.) silently drop the call if a higher-priority setter already
        // owns the value, which would be invisible to MCP callers. Choosing
        // the top of the priority lattice avoids that failure mode.

        CVar->Set(*ValueString, ECVF_SetByConsole);

        // --- post-verify --------------------------------------------------
        //
        // Read the value back and compare. A mismatch is reported as a
        // non-error 'note' rather than a hard failure: the priority semantics
        // can legitimately reject the change against an even-higher-priority
        // override (rare with ECVF_SetByConsole but possible), and UE's
        // type parser may coerce silently (e.g. "true" -> 0 on a float CVar).
        // The caller gets the actual landed value either way.

        const FString PostString = CVar->GetString();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Name);
        Out->SetStringField(TEXT("type"), CVarTypeToString(CVar));
        Out->SetStringField(TEXT("requested_value"), ValueString);
        Out->SetStringField(TEXT("value_string"), PostString);
        Out->SetNumberField(TEXT("value_int"), static_cast<double>(CVar->GetInt()));
        Out->SetNumberField(TEXT("value_float"), static_cast<double>(CVar->GetFloat()));
        Out->SetBoolField(TEXT("value_bool"), CVar->GetBool());

        const TCHAR* SetByName = GetConsoleVariableSetByName(CVar->GetFlags());
        Out->SetStringField(TEXT("set_by"), SetByName ? SetByName : TEXT(""));

        if (PostString != ValueString)
        {
            Out->SetStringField(TEXT("note"),
                FString::Printf(
                    TEXT("Set was issued but the post-set value ('%s') differs from the requested value ('%s'). "
                         "The CVar may have a higher-priority setter (see set_by) or its parser coerced/rejected the input."),
                    *PostString, *ValueString));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetConsoleVariable()
{
    return MakeShared<FHandler_SetConsoleVariable>();
}
