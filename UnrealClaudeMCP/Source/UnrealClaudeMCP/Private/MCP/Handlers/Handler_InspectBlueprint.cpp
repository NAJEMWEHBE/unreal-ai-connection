// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_blueprint - return parent class, declared variables, function/event
// graph names of a Blueprint asset.
//
// Error format: free-form OutError strings (legacy surface — predates the canonical
// "<tool_name>: <error_code>: <detail>" convention used by later handlers). Migration
// is deferred; bridge consumers treat OutError as human-readable text rather than
// parsing for a code prefix.

#include "MCP/MCPHandler.h"

#include "Engine/Blueprint.h"
#include "EdGraph/EdGraph.h"

class FHandler_InspectBlueprint : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_blueprint"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString Path;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("path"), Path))
        {
            OutError = TEXT("Missing required string param: 'path'");
            return nullptr;
        }

        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *Path);
        if (!BP)
        {
            OutError = FString::Printf(TEXT("Blueprint not found at path: %s"), *Path);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("path"), Path);
        Out->SetStringField(TEXT("parent_class"), BP->ParentClass ? BP->ParentClass->GetName() : TEXT(""));
        Out->SetStringField(TEXT("blueprint_class"), BP->GetClass()->GetName());

        TArray<TSharedPtr<FJsonValue>> Vars;
        for (const FBPVariableDescription& V : BP->NewVariables)
        {
            const TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
            J->SetStringField(TEXT("name"), V.VarName.ToString());
            J->SetStringField(TEXT("type_category"), V.VarType.PinCategory.ToString());
            J->SetStringField(TEXT("type_subcategory"), V.VarType.PinSubCategory.ToString());
            J->SetStringField(TEXT("default"), V.DefaultValue);
            Vars.Add(MakeShared<FJsonValueObject>(J));
        }
        Out->SetArrayField(TEXT("variables"), Vars);

        TArray<TSharedPtr<FJsonValue>> FuncGraphs;
        for (UEdGraph* G : BP->FunctionGraphs)
        {
            if (G) FuncGraphs.Add(MakeShared<FJsonValueString>(G->GetName()));
        }
        Out->SetArrayField(TEXT("function_graphs"), FuncGraphs);

        TArray<TSharedPtr<FJsonValue>> EventGraphs;
        for (UEdGraph* G : BP->UbergraphPages)
        {
            if (G) EventGraphs.Add(MakeShared<FJsonValueString>(G->GetName()));
        }
        Out->SetArrayField(TEXT("event_graphs"), EventGraphs);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectBlueprint()
{
    return MakeShared<FHandler_InspectBlueprint>();
}
