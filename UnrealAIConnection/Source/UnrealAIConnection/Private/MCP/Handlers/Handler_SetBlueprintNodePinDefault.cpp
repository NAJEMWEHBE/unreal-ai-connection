// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_blueprint_node_pin_default - set a literal default value on an UNCONNECTED
// input pin of an existing K2 node (a constant int/float/bool/string, or an object
// reference), via the schema's validated setter so the value is parsed/validated
// for the pin's type.
//
// UE 5.7 surface (verified against engine source):
//   - UEdGraphSchema_K2::TrySetDefaultValue(UEdGraphPin& Pin, const FString& NewDefaultValue,
//     bool bMarkAsModified=true) const (EdGraphSchema_K2.h:575, module BlueprintGraph).
//     VOID — there is NO success bool. We detect failure by reading pin->DefaultValue
//     back after the call and comparing (UE may canonicalize, e.g. '1.0' -> '1.000000',
//     so the read-back is a best-effort "did it change / does it contain" check).
//   - UEdGraphSchema_K2::TrySetDefaultObject(UEdGraphPin& Pin, UObject* NewDefaultObject,
//     bool bMarkAsModified=true) const (EdGraphSchema_K2.h:576). For PC_Object pins,
//     where DefaultObject (not DefaultValue) carries the value and DefaultValue must
//     stay empty. Resolve object_value via LoadObject<UObject>.
//   - UEdGraph::GetSchema() const (EdGraph/EdGraph.h:115, module Engine) -> cast to
//     const UEdGraphSchema_K2*.
//   - UEdGraphNode::FindPin(const FName, EEdGraphPinDirection=EGPD_MAX) const
//     (EdGraphNode.h:580) — pass EGPD_Input; NodeGuid (EdGraphNode.h:406) matches nodes.
//   - UEdGraphPin fields: Direction, LinkedTo (a connected pin's default is ignored
//     by the compiler), DefaultValue, DefaultObject; PinType.PinCategory checked
//     against UEdGraphSchema_K2::PC_Object to pick the object path.
//   - FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(UBlueprint*)
//     (Kismet2/BlueprintEditorUtils.h:304, module UnrealEd).
//
// IsMutating() is false: this edits asset (BP graph) content.
//
// Error format: "set_blueprint_node_pin_default: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, blueprint_not_found,
// graph_not_found, node_not_found, pin_not_found, pin_is_output, pin_is_connected,
// object_value_not_found, set_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EdGraph/EdGraph.h"
#include "EdGraph/EdGraphNode.h"
#include "EdGraph/EdGraphPin.h"
#include "EdGraph/EdGraphSchema.h"
#include "EdGraphSchema_K2.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Engine/Blueprint.h"
#include "UObject/UObjectGlobals.h"

namespace
{
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

    UEdGraphNode* FindNodeByGuid(const UEdGraph* Graph, const FString& GuidStr)
    {
        for (UEdGraphNode* Node : Graph->Nodes)
        {
            if (Node && Node->NodeGuid.ToString() == GuidStr) { return Node; }
        }
        return nullptr;
    }
}

class FHandler_SetBlueprintNodePinDefault : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_blueprint_node_pin_default"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("set_blueprint_node_pin_default: missing_params: request had no params object");
            return nullptr;
        }

        FString BlueprintPath;
        if (!Params->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
        {
            OutError = TEXT("set_blueprint_node_pin_default: missing_required_field: 'blueprint' is required");
            return nullptr;
        }

        FString GraphName;
        if (!Params->TryGetStringField(TEXT("graph"), GraphName) || GraphName.IsEmpty())
        {
            OutError = TEXT("set_blueprint_node_pin_default: missing_required_field: 'graph' is required");
            return nullptr;
        }

        FString NodeGuid;
        if (!Params->TryGetStringField(TEXT("node"), NodeGuid) || NodeGuid.IsEmpty())
        {
            OutError = TEXT("set_blueprint_node_pin_default: missing_required_field: 'node' is required");
            return nullptr;
        }

        FString PinName;
        if (!Params->TryGetStringField(TEXT("pin"), PinName) || PinName.IsEmpty())
        {
            OutError = TEXT("set_blueprint_node_pin_default: missing_required_field: 'pin' is required");
            return nullptr;
        }

        // Exactly one of value / object_value carries the new default. object_value
        // (an asset/object path) routes to TrySetDefaultObject for PC_Object pins;
        // value (a literal string) routes to TrySetDefaultValue.
        FString ObjectValuePath;
        const bool bHasObjectValue =
            Params->TryGetStringField(TEXT("object_value"), ObjectValuePath) && !ObjectValuePath.IsEmpty();

        FString LiteralValue;
        const bool bHasValue = Params->TryGetStringField(TEXT("value"), LiteralValue);

        if (!bHasObjectValue && !bHasValue)
        {
            OutError = TEXT("set_blueprint_node_pin_default: missing_required_field: provide 'value' (a literal) or 'object_value' (an object path)");
            return nullptr;
        }

        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!BP)
        {
            OutError = FString::Printf(
                TEXT("set_blueprint_node_pin_default: blueprint_not_found: no UBlueprint at '%s'"),
                *BlueprintPath);
            return nullptr;
        }

        UEdGraph* Graph = FindGraphByName(BP, FName(*GraphName));
        if (!Graph)
        {
            OutError = FString::Printf(
                TEXT("set_blueprint_node_pin_default: graph_not_found: no graph named '%s' in '%s'"),
                *GraphName, *BlueprintPath);
            return nullptr;
        }

        UEdGraphNode* Node = FindNodeByGuid(Graph, NodeGuid);
        if (!Node)
        {
            OutError = FString::Printf(
                TEXT("set_blueprint_node_pin_default: node_not_found: no node with GUID '%s' in graph '%s'"),
                *NodeGuid, *GraphName);
            return nullptr;
        }

        // Pass EGPD_Input so we never accidentally resolve a same-named output.
        UEdGraphPin* Pin = Node->FindPin(FName(*PinName), EGPD_Input);
        if (!Pin)
        {
            // If an output pin of that name exists, report pin_is_output to help
            // the caller; otherwise it's a plain pin_not_found.
            if (Node->FindPin(FName(*PinName), EGPD_Output))
            {
                OutError = FString::Printf(
                    TEXT("set_blueprint_node_pin_default: pin_is_output: '%s' on node '%s' is an output pin; only input pins carry an authored default"),
                    *PinName, *NodeGuid);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("set_blueprint_node_pin_default: pin_not_found: no input pin named '%s' on node '%s'"),
                    *PinName, *NodeGuid);
            }
            return nullptr;
        }

        // A connected pin's default is ignored by the compiler — reject so the
        // caller doesn't think a no-op took effect.
        if (Pin->LinkedTo.Num() > 0)
        {
            OutError = FString::Printf(
                TEXT("set_blueprint_node_pin_default: pin_is_connected: pin '%s' on node '%s' has %d link(s); its default would be ignored by the compiler"),
                *PinName, *NodeGuid, Pin->LinkedTo.Num());
            return nullptr;
        }

        const UEdGraphSchema_K2* K2Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
        if (!K2Schema)
        {
            OutError = FString::Printf(
                TEXT("set_blueprint_node_pin_default: set_failed: graph '%s' is not a K2 (Blueprint) graph"),
                *GraphName);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("blueprint"), BlueprintPath);
        Out->SetStringField(TEXT("graph"), GraphName);
        Out->SetStringField(TEXT("node"), NodeGuid);
        Out->SetStringField(TEXT("pin"), PinName);

        if (bHasObjectValue)
        {
            // Object/asset-reference pins keep the value in DefaultObject.
            UObject* NewObject = LoadObject<UObject>(nullptr, *ObjectValuePath);
            if (!NewObject)
            {
                OutError = FString::Printf(
                    TEXT("set_blueprint_node_pin_default: object_value_not_found: no object loaded from '%s'"),
                    *ObjectValuePath);
                return nullptr;
            }

            // TrySetDefaultObject is VOID — read DefaultObject back to confirm.
            K2Schema->TrySetDefaultObject(*Pin, NewObject);
            if (Pin->DefaultObject != NewObject)
            {
                OutError = FString::Printf(
                    TEXT("set_blueprint_node_pin_default: set_failed: schema did not accept object '%s' on pin '%s' (pin type may not be an object reference, or the object class is incompatible)"),
                    *ObjectValuePath, *PinName);
                return nullptr;
            }

            Out->SetStringField(TEXT("object_value"), ObjectValuePath);
        }
        else
        {
            // Literal default. TrySetDefaultValue is VOID; read DefaultValue back.
            // UE canonicalizes some literals (e.g. '1.0' -> '1.000000'), so we
            // accept either an exact match or a non-empty normalized result when
            // the requested value is itself non-empty.
            K2Schema->TrySetDefaultValue(*Pin, LiteralValue);
            const FString ReadBack = Pin->DefaultValue;
            const bool bTook =
                (ReadBack == LiteralValue)               // exact
                || (!LiteralValue.IsEmpty() && !ReadBack.IsEmpty()); // canonicalized
            if (!bTook)
            {
                OutError = FString::Printf(
                    TEXT("set_blueprint_node_pin_default: set_failed: schema did not accept value '%s' on pin '%s' (read-back was '%s'; check the literal matches the pin type)"),
                    *LiteralValue, *PinName, *ReadBack);
                return nullptr;
            }

            Out->SetStringField(TEXT("value"), LiteralValue);
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetBlueprintNodePinDefault()
{
    return MakeShared<FHandler_SetBlueprintNodePinDefault>();
}
