// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// connect_blueprint_pins - wire two pins (exec or data) between two existing K2
// nodes in the same graph of a UBlueprint, using the SCHEMA-VALIDATED path so
// type-incompatible / illegal links are rejected with a reason rather than
// silently creating a broken graph.
//
// UE 5.7 surface (verified against engine source):
//   - UEdGraphSchema_K2::TryCreateConnection(UEdGraphPin* A, UEdGraphPin* B) const
//     (EdGraphSchema_K2.h:568, module BlueprintGraph) — validates + makes the link;
//     returns bool. THIS is the correct entry point, NOT UEdGraphPin::MakeLinkTo
//     (raw; bypasses validation and will happily wire an int into an exec pin).
//   - UEdGraphSchema_K2::CanCreateConnection(const UEdGraphPin* A, const UEdGraphPin* B)
//     const -> const FPinConnectionResponse (EdGraphSchema_K2.h:567) — called first
//     to obtain a human-readable rejection message for the error string.
//     FPinConnectionResponse lives in the base header EdGraph/EdGraphSchema.h.
//   - UEdGraph::GetSchema() const -> const UEdGraphSchema* (EdGraph/EdGraph.h:115,
//     module Engine); Cast<UEdGraphSchema_K2> to reach the K2 entry points.
//   - UEdGraphNode::FindPin(const FName, EEdGraphPinDirection=EGPD_MAX) const
//     (EdGraphNode.h:580) — pass EGPD_Output for from_pin, EGPD_Input for to_pin
//     to disambiguate same-named pins.
//   - UEdGraphNode::NodeGuid (FGuid, EdGraphNode.h:406) — nodes are matched by GUID
//     string (stable across reconstruction), NOT by name. add_blueprint_node
//     returns the GUID for exactly this purpose.
//   - UBlueprint::UbergraphPages / FunctionGraphs — graph resolution mirrors
//     add_blueprint_node.
//   - FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(UBlueprint*)
//     (Kismet2/BlueprintEditorUtils.h:304, module UnrealEd) — called after a
//     successful connection.
//
// IsMutating() is false: this edits asset (BP graph) content, same rationale as
// add_blueprint_node.
//
// Error format: "connect_blueprint_pins: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, blueprint_not_found,
// graph_not_found, from_node_not_found, to_node_not_found, from_pin_not_found,
// to_pin_not_found, connect_failed.

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

    // Resolve a node by NodeGuid string (stable; names are not).
    UEdGraphNode* FindNodeByGuid(const UEdGraph* Graph, const FString& GuidStr)
    {
        for (UEdGraphNode* Node : Graph->Nodes)
        {
            if (Node && Node->NodeGuid.ToString() == GuidStr) { return Node; }
        }
        return nullptr;
    }
}

class FHandler_ConnectBlueprintPins : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("connect_blueprint_pins"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("connect_blueprint_pins: missing_params: request had no params object");
            return nullptr;
        }

        FString BlueprintPath;
        if (!Params->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
        {
            OutError = TEXT("connect_blueprint_pins: missing_required_field: 'blueprint' is required");
            return nullptr;
        }

        FString GraphName;
        if (!Params->TryGetStringField(TEXT("graph"), GraphName) || GraphName.IsEmpty())
        {
            OutError = TEXT("connect_blueprint_pins: missing_required_field: 'graph' is required");
            return nullptr;
        }

        FString FromNodeGuid;
        if (!Params->TryGetStringField(TEXT("from_node"), FromNodeGuid) || FromNodeGuid.IsEmpty())
        {
            OutError = TEXT("connect_blueprint_pins: missing_required_field: 'from_node' is required");
            return nullptr;
        }

        FString FromPinName;
        if (!Params->TryGetStringField(TEXT("from_pin"), FromPinName) || FromPinName.IsEmpty())
        {
            OutError = TEXT("connect_blueprint_pins: missing_required_field: 'from_pin' is required");
            return nullptr;
        }

        FString ToNodeGuid;
        if (!Params->TryGetStringField(TEXT("to_node"), ToNodeGuid) || ToNodeGuid.IsEmpty())
        {
            OutError = TEXT("connect_blueprint_pins: missing_required_field: 'to_node' is required");
            return nullptr;
        }

        FString ToPinName;
        if (!Params->TryGetStringField(TEXT("to_pin"), ToPinName) || ToPinName.IsEmpty())
        {
            OutError = TEXT("connect_blueprint_pins: missing_required_field: 'to_pin' is required");
            return nullptr;
        }

        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!BP)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: blueprint_not_found: no UBlueprint at '%s'"),
                *BlueprintPath);
            return nullptr;
        }

        UEdGraph* Graph = FindGraphByName(BP, FName(*GraphName));
        if (!Graph)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: graph_not_found: no graph named '%s' in '%s'"),
                *GraphName, *BlueprintPath);
            return nullptr;
        }

        UEdGraphNode* FromNode = FindNodeByGuid(Graph, FromNodeGuid);
        if (!FromNode)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: from_node_not_found: no node with GUID '%s' in graph '%s'"),
                *FromNodeGuid, *GraphName);
            return nullptr;
        }

        UEdGraphNode* ToNode = FindNodeByGuid(Graph, ToNodeGuid);
        if (!ToNode)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: to_node_not_found: no node with GUID '%s' in graph '%s'"),
                *ToNodeGuid, *GraphName);
            return nullptr;
        }

        // Disambiguate same-named pins by direction: from_pin is an output,
        // to_pin is an input. EGPD_MAX (FindPin's default) would match either.
        UEdGraphPin* FromPin = FromNode->FindPin(FName(*FromPinName), EGPD_Output);
        if (!FromPin)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: from_pin_not_found: no output pin named '%s' on from_node '%s'"),
                *FromPinName, *FromNodeGuid);
            return nullptr;
        }

        UEdGraphPin* ToPin = ToNode->FindPin(FName(*ToPinName), EGPD_Input);
        if (!ToPin)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: to_pin_not_found: no input pin named '%s' on to_node '%s'"),
                *ToPinName, *ToNodeGuid);
            return nullptr;
        }

        // GetSchema() returns the base type; cast to K2 to reach TryCreateConnection.
        const UEdGraphSchema_K2* K2Schema = Cast<UEdGraphSchema_K2>(Graph->GetSchema());
        if (!K2Schema)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: connect_failed: graph '%s' is not a K2 (Blueprint) graph"),
                *GraphName);
            return nullptr;
        }

        // Validated wire. Capture the schema's human-readable verdict for the
        // error string before attempting the link.
        const FPinConnectionResponse Response = K2Schema->CanCreateConnection(FromPin, ToPin);
        const bool bConnected = K2Schema->TryCreateConnection(FromPin, ToPin);
        if (!bConnected)
        {
            OutError = FString::Printf(
                TEXT("connect_blueprint_pins: connect_failed: schema rejected the link from '%s' to '%s': %s"),
                *FromPinName, *ToPinName, *Response.Message.ToString());
            return nullptr;
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("blueprint"), BlueprintPath);
        Out->SetStringField(TEXT("graph"), GraphName);
        Out->SetStringField(TEXT("from_node"), FromNodeGuid);
        Out->SetStringField(TEXT("from_pin"), FromPinName);
        Out->SetStringField(TEXT("to_node"), ToNodeGuid);
        Out->SetStringField(TEXT("to_pin"), ToPinName);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ConnectBlueprintPins()
{
    return MakeShared<FHandler_ConnectBlueprintPins>();
}
