// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// find_console_variables - prefix-search the IConsoleManager registry
// and return matching CVar names + types + read-only flags. Pairs with
// get_console_variable / set_console_variable from PR #39.
//
// Part of the language-shim experiment (PR #46): a "C++ canonical" handler
// where C++ is the obvious choice -- iterating IConsoleManager's internal
// registry is most natural with the native API. The paired Python-shim
// handlers in the same PR (get/set_camera_transform) cover cases where
// pure-Python via execute_unreal_python composition is plausibly cleaner.
// See docs/LANGUAGE-CHOICE-RETROSPECTIVE.md for the full comparison.
//
// UE 5.7 surface used:
//   - IConsoleManager::ForEachConsoleObjectThatStartsWith
//                                                IConsoleManager.h:1228
//   - FConsoleObjectVisitor (TwoParams delegate) IConsoleManager.h:827
//   - IConsoleManager::FindConsoleVariable       IConsoleManager.h:1170
//     (used to filter visitor hits down to variables only -- the visitor
//     delivers IConsoleObject*, which can be a variable OR a command)
//   - IConsoleVariable::IsVariable* / GetFlags / ECVF_ReadOnly
//                                                lines 478-481, 410, 71
//
// Error format: "find_console_variables: <error_code>: <human-readable detail>"
// Stable error codes: invalid_value_shape.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/IConsoleManager.h"

namespace
{
    static constexpr int32 kDefaultLimit = 100;
    static constexpr int32 kHardMaxLimit = 1000;

    static FString CVarTypeToString(IConsoleVariable* CVar)
    {
        if (CVar->IsVariableInt())    return TEXT("int");
        if (CVar->IsVariableFloat())  return TEXT("float");
        if (CVar->IsVariableBool())   return TEXT("bool");
        if (CVar->IsVariableString()) return TEXT("string");
        return TEXT("unknown");
    }
}

class FHandler_FindConsoleVariables : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("find_console_variables"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- read optional params -------------------------------------------

        FString Prefix;  // empty = match all
        int32 Limit = kDefaultLimit;

        if (Params.IsValid())
        {
            // Optional 'prefix' string. Empty / omitted => return everything.
            Params->TryGetStringField(TEXT("prefix"), Prefix);

            const TSharedPtr<FJsonValue> LimitVal = Params->TryGetField(TEXT("limit"));
            if (LimitVal.IsValid())
            {
                if (LimitVal->Type != EJson::Number)
                {
                    OutError = TEXT("find_console_variables: invalid_value_shape: 'limit' must be a positive integer");
                    return nullptr;
                }
                const double Raw = LimitVal->AsNumber();
                if (!FMath::IsFinite(Raw) || FMath::TruncToDouble(Raw) != Raw || Raw <= 0)
                {
                    OutError = FString::Printf(
                        TEXT("find_console_variables: invalid_value_shape: 'limit' must be a finite positive integer (got %g)"), Raw);
                    return nullptr;
                }
                Limit = static_cast<int32>(FMath::Min(Raw, static_cast<double>(kHardMaxLimit)));
            }
        }

        // --- enumerate via IConsoleManager visitor ------------------------
        //
        // The visitor delivers EVERY console object (variables AND commands)
        // matching the prefix. We filter to variables only by re-querying
        // FindConsoleVariable for each name -- the alternative would be
        // calling IConsoleObject::AsCommand() to detect non-variables, but
        // the symmetric AsVariable() API isn't reliably exposed across UE
        // 5.x versions. The double-lookup cost is negligible for prefix
        // searches returning <1000 results.

        struct FRow
        {
            FString Name;
            FString Type;
            bool bReadOnly = false;
        };
        TArray<FRow> Rows;

        IConsoleManager::Get().ForEachConsoleObjectThatStartsWith(
            FConsoleObjectVisitor::CreateLambda(
                [&Rows, Limit](const TCHAR* Name, IConsoleObject* /*Obj*/)
                {
                    if (Rows.Num() >= Limit) { return; }
                    if (!Name) { return; }

                    IConsoleVariable* CV = IConsoleManager::Get().FindConsoleVariable(
                        Name, /*bTrackFrequentCalls=*/ false);
                    if (!CV) { return; }  // skip console *commands*

                    FRow Row;
                    Row.Name = Name;
                    Row.Type = CVarTypeToString(CV);
                    Row.bReadOnly = (CV->GetFlags() & ECVF_ReadOnly) != 0;
                    Rows.Add(MoveTemp(Row));
                }),
            *Prefix);

        // --- build response ------------------------------------------------

        TArray<TSharedPtr<FJsonValue>> JsonRows;
        JsonRows.Reserve(Rows.Num());
        for (const FRow& R : Rows)
        {
            TSharedPtr<FJsonObject> RowObj = MakeShared<FJsonObject>();
            RowObj->SetStringField(TEXT("name"), R.Name);
            RowObj->SetStringField(TEXT("type"), R.Type);
            RowObj->SetBoolField(TEXT("read_only"), R.bReadOnly);
            JsonRows.Add(MakeShared<FJsonValueObject>(RowObj));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("prefix"), Prefix);
        Out->SetNumberField(TEXT("limit"), static_cast<double>(Limit));
        Out->SetNumberField(TEXT("returned"), static_cast<double>(Rows.Num()));
        Out->SetArrayField(TEXT("variables"), JsonRows);
        if (Rows.Num() >= Limit)
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Result count reached the cap; additional matches may exist. "
                     "Use a more specific prefix or raise 'limit' (hard max 1000)."));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_FindConsoleVariables()
{
    return MakeShared<FHandler_FindConsoleVariables>();
}
