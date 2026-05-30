// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_blueprint_node - add one K2 graph node (CallFunction-by-name, VariableGet,
// VariableSet, or Branch/IfThenElse) into a UBlueprint's event or function graph,
// at an optional X/Y position, and return the new node's GUID + name + listable
// pins so connect_blueprint_pins / set_blueprint_node_pin_default can address it.
//
// UE 5.7 surface (verified against engine source):
//   - FGraphNodeCreator<NodeType> (EdGraph/EdGraph.h:273-307, module Engine) — the
//     canonical node-creation idiom. ctor FGraphNodeCreator(UEdGraph&);
//     NodeType* CreateNode(bool bSelectNewNode=true, ...); void Finalize(). ORDER
//     IS LOAD-BEARING: CreateNode(false) -> configure the node (SetFromFunction /
//     VariableReference.SetSelfMember) + set NodePosX/NodePosY -> Finalize().
//     Finalize() internally runs AllocateDefaultPins() (only if no pins yet),
//     CreateNewGuid(), PostPlacedNewNode(). Verified patterns: EdGraphSchema_K2.cpp
//     (CallFunction) and WidgetBlueprintCompiler.cpp:283-286 (VariableGet). The
//     creator wraps CreateNode+AddNode, so we do NOT call UEdGraph::AddNode.
//   - UK2Node_CallFunction::SetFromFunction(const UFunction*) (K2Node_CallFunction.h:217,
//     module BlueprintGraph). Resolve the UFunction first via FindFunctionByName.
//   - UK2Node_Variable::VariableReference (public FMemberReference, K2Node_Variable.h:58);
//     GetValuePin() (K2Node_Variable.h:183, inherited by VariableGet/VariableSet).
//     FMemberReference::SetSelfMember(FName) (Engine/MemberReference.h:187) — the
//     engine does exactly VarNode->VariableReference.SetSelfMember(Name) + Finalize.
//   - UK2Node_IfThenElse (K2Node_IfThenElse.h:23): GetElsePin()/GetConditionPin()
//     public; NO public GetThenPin() — the exec-out is found via PN_Then.
//   - UClass::FindFunctionByName(FName) (CoreUObject UObject/Class.h:4156).
//   - UEdGraphNode::NodePosX/NodePosY/NodeGuid; UEdGraphNode::Pins (the harvested
//     pin list). UEdGraphSchema_K2::PC_* pin-category consts (EdGraphSchema_K2.h)
//     are reported as each pin's 'category'.
//   - UBlueprint::UbergraphPages (Blueprint.h:539, the event graph 'EventGraph'
//     lives here) + FunctionGraphs (Blueprint.h:543, user functions). Resolve the
//     target graph by GetFName() across UbergraphPages first, then FunctionGraphs.
//   - FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(UBlueprint*)
//     (Kismet2/BlueprintEditorUtils.h:304, module UnrealEd) — queues a recompile so
//     the new node is compiled in.
//
// IsMutating() is false: this edits an asset (BP graph content), not a level actor
// on the editor undo stack the dispatcher's FScopedTransaction wraps. Same posture
// as add_blueprint_variable/function and the material graph handlers.
//
// Error format: "add_blueprint_node: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// blueprint_not_found, graph_not_found, unsupported_node_type, function_not_found,
// variable_not_found, node_create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraphSchema_K2.h"
#include "K2Node_CallFunction.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "K2Node_Variable.h"
#include "K2Node_IfThenElse.h"
#include "Engine/MemberReference.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Engine/Blueprint.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "UObject/Class.h"
#include "UObject/UObjectGlobals.h"

namespace
{
    // Resolve a graph by name: search UbergraphPages first (the event graph
    // 'EventGraph' lives here, NOT in FunctionGraphs), then FunctionGraphs.
    UEdGraph* FindGraphByName(const UBlueprint* BP, const FName GraphName)
    {
        for (UEdGraph* Graph : BP->UbergraphPages)
        {
            if (Graph && Graph->GetFName() == GraphName) { return Graph; }
        }
        for (UEdGraph* Graph : BP->FunctionGraphs)
        {
            if (Graph && Graph->GetFName() == GraphName) { return Graph; }
        }
        return nullptr;
    }

    // Append the node's pins (name / direction / category) to the result so the
    // caller can wire/set defaults without guessing pin names.
    void HarvestPins(const UEdGraphNode* Node, TArray<TSharedPtr<FJsonValue>>& OutPins)
    {
        for (const UEdGraphPin* Pin : Node->Pins)
        {
            if (!Pin) { continue; }
            TSharedPtr<FJsonObject> PinObj = MakeShared<FJsonObject>();
            PinObj->SetStringField(TEXT("name"), Pin->PinName.ToString());
            PinObj->SetStringField(TEXT("direction"),
                Pin->Direction == EGPD_Output ? TEXT("output") : TEXT("input"));
            PinObj->SetStringField(TEXT("category"), Pin->PinType.PinCategory.ToString());
            OutPins.Add(MakeShared<FJsonValueObject>(PinObj));
        }
    }
}

class FHandler_AddBlueprintNode : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_blueprint_node"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("add_blueprint_node: missing_params: request had no params object");
            return nullptr;
        }

        FString BlueprintPath;
        if (!Params->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
        {
            OutError = TEXT("add_blueprint_node: missing_required_field: 'blueprint' is required");
            return nullptr;
        }

        FString GraphName;
        if (!Params->TryGetStringField(TEXT("graph"), GraphName) || GraphName.IsEmpty())
        {
            OutError = TEXT("add_blueprint_node: missing_required_field: 'graph' is required");
            return nullptr;
        }

        FString NodeType;
        if (!Params->TryGetStringField(TEXT("node_type"), NodeType) || NodeType.IsEmpty())
        {
            OutError = TEXT("add_blueprint_node: missing_required_field: 'node_type' is required");
            return nullptr;
        }

        // Optional node graph coordinates (default 0,0). JSON numbers arrive as
        // double; NodePosX/Y are int32 — cast like Handler_AddMaterialExpression.
        int32 NodePosX = 0;
        int32 NodePosY = 0;
        {
            double X = 0, Y = 0;
            if (Params->TryGetNumberField(TEXT("node_pos_x"), X)) { NodePosX = static_cast<int32>(X); }
            if (Params->TryGetNumberField(TEXT("node_pos_y"), Y)) { NodePosY = static_cast<int32>(Y); }
        }

        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!BP)
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_node: blueprint_not_found: no UBlueprint at '%s'"),
                *BlueprintPath);
            return nullptr;
        }

        UEdGraph* Graph = FindGraphByName(BP, FName(*GraphName));
        if (!Graph)
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_node: graph_not_found: no graph named '%s' in '%s' (searched UbergraphPages then FunctionGraphs; the event graph is usually 'EventGraph')"),
                *GraphName, *BlueprintPath);
            return nullptr;
        }

        // Validate node_type and pre-resolve any references BEFORE constructing
        // the FGraphNodeCreator. The creator's destructor checkf(bPlaced) fires if
        // it is constructed but never Finalize()'d, so we keep all fallible
        // validation ahead of construction and guarantee Finalize() on every path
        // where the creator exists.
        const FString NodeTypeLower = NodeType.ToLower();

        // For call_function: resolve the UFunction up front.
        UFunction* ResolvedFunction = nullptr;
        if (NodeTypeLower == TEXT("call_function"))
        {
            FString FunctionName;
            if (!Params->TryGetStringField(TEXT("function_name"), FunctionName) || FunctionName.IsEmpty())
            {
                OutError = TEXT("add_blueprint_node: missing_required_field: 'function_name' is required when node_type is 'call_function'");
                return nullptr;
            }

            // function_class is optional: default to the BP's own generated class
            // (self-context call). SkeletonGeneratedClass holds freshly-added
            // members pre/post-compile; fall back to GeneratedClass.
            UClass* OwnerClass = nullptr;
            FString FunctionClassPath;
            if (Params->TryGetStringField(TEXT("function_class"), FunctionClassPath) && !FunctionClassPath.IsEmpty())
            {
                OwnerClass = UCMCPAssetPath::ResolveClassByPath(FunctionClassPath);
                if (!OwnerClass)
                {
                    OutError = FString::Printf(
                        TEXT("add_blueprint_node: invalid_field: function_class_not_found: no class resolved from '%s' (pass a /Script path or a Blueprint asset path)"),
                        *FunctionClassPath);
                    return nullptr;
                }
            }
            else
            {
                OwnerClass = BP->SkeletonGeneratedClass ? BP->SkeletonGeneratedClass : BP->GeneratedClass;
                if (!OwnerClass)
                {
                    OutError = FString::Printf(
                        TEXT("add_blueprint_node: invalid_field: no_self_class: '%s' has no generated class to resolve a self-context function; pass 'function_class' explicitly (compile the Blueprint first if it was just created)"),
                        *BlueprintPath);
                    return nullptr;
                }
            }

            ResolvedFunction = OwnerClass->FindFunctionByName(FName(*FunctionName));
            if (!ResolvedFunction)
            {
                OutError = FString::Printf(
                    TEXT("add_blueprint_node: function_not_found: '%s' is not a function on class '%s'"),
                    *FunctionName, *OwnerClass->GetName());
                return nullptr;
            }
        }

        // For variable_get/variable_set: capture var_name up front.
        FString VarName;
        if (NodeTypeLower == TEXT("variable_get") || NodeTypeLower == TEXT("variable_set"))
        {
            if (!Params->TryGetStringField(TEXT("var_name"), VarName) || VarName.IsEmpty())
            {
                OutError = FString::Printf(
                    TEXT("add_blueprint_node: missing_required_field: 'var_name' is required when node_type is '%s'"),
                    *NodeTypeLower);
                return nullptr;
            }

            // Verify the variable exists (BP-local or inherited) before authoring a
            // node — otherwise we'd create an unresolved VariableReference that only
            // surfaces as a compile error later.
            const bool bVarExists =
                FBlueprintEditorUtils::FindNewVariableIndex(BP, FName(*VarName)) != INDEX_NONE
                || (BP->SkeletonGeneratedClass && BP->SkeletonGeneratedClass->FindPropertyByName(FName(*VarName)) != nullptr)
                || (BP->GeneratedClass && BP->GeneratedClass->FindPropertyByName(FName(*VarName)) != nullptr);
            if (!bVarExists)
            {
                OutError = FString::Printf(
                    TEXT("add_blueprint_node: var_not_found: no variable named '%s' on '%s' (declare it first, or compile the Blueprint if it was just added)"),
                    *VarName, *BlueprintPath);
                return nullptr;
            }
        }

        // Reject unknown node types before any creator is constructed.
        if (NodeTypeLower != TEXT("call_function")
            && NodeTypeLower != TEXT("variable_get")
            && NodeTypeLower != TEXT("variable_set")
            && NodeTypeLower != TEXT("branch"))
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_node: unsupported_node_type: '%s' is not supported (use one of: call_function, variable_get, variable_set, branch)"),
                *NodeType);
            return nullptr;
        }

        // ----------------------------------------------------------------
        // All validation passed. Create the node. ORDER IS LOAD-BEARING:
        // CreateNode(false) -> configure + set position -> Finalize().
        // ----------------------------------------------------------------
        UEdGraphNode* NewNode = nullptr;

        if (NodeTypeLower == TEXT("call_function"))
        {
            FGraphNodeCreator<UK2Node_CallFunction> Creator(*Graph);
            UK2Node_CallFunction* CallNode = Creator.CreateNode(/*bSelectNewNode=*/false);
            CallNode->SetFromFunction(ResolvedFunction);
            CallNode->NodePosX = NodePosX;
            CallNode->NodePosY = NodePosY;
            Creator.Finalize();
            NewNode = CallNode;
        }
        else if (NodeTypeLower == TEXT("variable_get"))
        {
            FGraphNodeCreator<UK2Node_VariableGet> Creator(*Graph);
            UK2Node_VariableGet* VarNode = Creator.CreateNode(/*bSelectNewNode=*/false);
            VarNode->VariableReference.SetSelfMember(FName(*VarName));
            VarNode->NodePosX = NodePosX;
            VarNode->NodePosY = NodePosY;
            Creator.Finalize();
            NewNode = VarNode;
        }
        else if (NodeTypeLower == TEXT("variable_set"))
        {
            FGraphNodeCreator<UK2Node_VariableSet> Creator(*Graph);
            UK2Node_VariableSet* VarNode = Creator.CreateNode(/*bSelectNewNode=*/false);
            VarNode->VariableReference.SetSelfMember(FName(*VarName));
            VarNode->NodePosX = NodePosX;
            VarNode->NodePosY = NodePosY;
            Creator.Finalize();
            NewNode = VarNode;
        }
        else // branch
        {
            FGraphNodeCreator<UK2Node_IfThenElse> Creator(*Graph);
            UK2Node_IfThenElse* BranchNode = Creator.CreateNode(/*bSelectNewNode=*/false);
            BranchNode->NodePosX = NodePosX;
            BranchNode->NodePosY = NodePosY;
            Creator.Finalize();
            NewNode = BranchNode;
        }

        if (!NewNode)
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_node: node_create_failed: FGraphNodeCreator returned null for node_type '%s' in graph '%s'"),
                *NodeType, *GraphName);
            return nullptr;
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);

        TArray<TSharedPtr<FJsonValue>> Pins;
        HarvestPins(NewNode, Pins);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("blueprint"), BlueprintPath);
        Out->SetStringField(TEXT("graph"), GraphName);
        Out->SetStringField(TEXT("node_type"), NodeTypeLower);
        Out->SetStringField(TEXT("node_guid"), NewNode->NodeGuid.ToString());
        Out->SetStringField(TEXT("node_name"), NewNode->GetName());
        Out->SetArrayField(TEXT("pins"), Pins);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddBlueprintNode()
{
    return MakeShared<FHandler_AddBlueprintNode>();
}
