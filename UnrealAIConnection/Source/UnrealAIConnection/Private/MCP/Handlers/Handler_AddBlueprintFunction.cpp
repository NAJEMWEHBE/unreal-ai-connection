// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_blueprint_function - add a new (empty) function graph to a UBlueprint.
//
// UE 5.7 surface (verified against engine source):
//   - FBlueprintEditorUtils::CreateNewGraph(UObject* ParentScope, FName GraphName,
//     TSubclassOf<UEdGraph> GraphClass, TSubclassOf<UEdGraphSchema> SchemaClass)
//     (Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h). The K2 schema
//     (UEdGraphSchema_K2) makes it a Blueprint function graph.
//   - FBlueprintEditorUtils::AddFunctionGraph<UClass>(UBlueprint*, UEdGraph*,
//     bool bIsUserCreated, const UClass* SignatureFromObject). Templated on the
//     signature-source type; passing a null UClass* signature means "no
//     override signature" (a fresh user function with an auto-created entry node).
//   - FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(UBlueprint*)
//     flags the BP dirty + queues a recompile so the new graph is picked up.
//
// IsMutating() is false: this edits an asset, not a level actor on the undo
// stack the dispatcher's FScopedTransaction wraps.
//
// Error format: "add_blueprint_function: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field,
// blueprint_not_found, function_exists.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "EdGraphSchema_K2.h"
#include "EdGraph/EdGraph.h"
#include "Engine/Blueprint.h"
#include "UObject/UObjectGlobals.h"

class FHandler_AddBlueprintFunction : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_blueprint_function"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("add_blueprint_function: missing_params: request had no params object");
            return nullptr;
        }

        FString BlueprintPath;
        if (!Params->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
        {
            OutError = TEXT("add_blueprint_function: missing_required_field: 'blueprint' is required");
            return nullptr;
        }

        FString FunctionName;
        if (!Params->TryGetStringField(TEXT("function_name"), FunctionName) || FunctionName.IsEmpty())
        {
            OutError = TEXT("add_blueprint_function: missing_required_field: 'function_name' is required");
            return nullptr;
        }

        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!BP)
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_function: blueprint_not_found: no UBlueprint at '%s'"),
                *BlueprintPath);
            return nullptr;
        }

        // Guard against a name collision with an existing function graph.
        const FName FunctionFName(*FunctionName);
        for (const UEdGraph* Graph : BP->FunctionGraphs)
        {
            if (Graph && Graph->GetFName() == FunctionFName)
            {
                OutError = FString::Printf(
                    TEXT("add_blueprint_function: function_exists: '%s' already has a function graph named '%s'"),
                    *BlueprintPath, *FunctionName);
                return nullptr;
            }
        }

        UEdGraph* NewGraph = FBlueprintEditorUtils::CreateNewGraph(
            BP, FunctionFName, UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());

        FBlueprintEditorUtils::AddFunctionGraph<UClass>(
            BP, NewGraph, /*bIsUserCreated=*/true, /*SignatureFromObject=*/(UClass*)nullptr);

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("blueprint"), BlueprintPath);
        Out->SetStringField(TEXT("function_name"), FunctionName);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddBlueprintFunction()
{
    return MakeShared<FHandler_AddBlueprintFunction>();
}
