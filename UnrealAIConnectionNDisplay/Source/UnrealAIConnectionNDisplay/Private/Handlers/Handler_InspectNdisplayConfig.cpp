// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_ndisplay_config - read-only inspector for an nDisplay cluster configuration
// blueprint asset (UDisplayClusterBlueprint). Reports the primary node id and, per
// cluster node, its host IP, window rectangle and viewports (region, camera/view point,
// GPU index, projection policy type + parameters, enabled flag).
//
// Discovery tool: pair with find_assets to locate nDisplay config blueprints, then this
// to learn the cluster topology before driving it.
//
// Error format: "inspect_ndisplay_config: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found, wrong_asset_type,
// no_config_data.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "Blueprints/DisplayClusterBlueprint.h"
#include "DisplayClusterConfigurationTypes.h"
#include "DisplayClusterConfigurationTypes_Base.h"
#include "DisplayClusterConfigurationTypes_Viewport.h"

namespace
{
    // Serialize an nDisplay rectangle (X/Y/W/H, all int32) to a JSON object.
    TSharedPtr<FJsonObject> RectToJson(const FDisplayClusterConfigurationRectangle& Rect)
    {
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetNumberField(TEXT("x"), Rect.X);
        Obj->SetNumberField(TEXT("y"), Rect.Y);
        Obj->SetNumberField(TEXT("w"), Rect.W);
        Obj->SetNumberField(TEXT("h"), Rect.H);
        return Obj;
    }
}

class FHandler_InspectNdisplayConfig : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_ndisplay_config"); }

    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_ndisplay_config: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_ndisplay_config: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // --- load and cast asset --------------------------------------------
        //
        // The nDisplay config asset is a UBlueprint subclass (UDisplayClusterBlueprint),
        // so load it as a UObject and cast - the same loaded asset can then satisfy the
        // "wrong type" diagnostic below.

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_ndisplay_config: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        UDisplayClusterBlueprint* Blueprint = Cast<UDisplayClusterBlueprint>(LoadedAsset);
        if (!Blueprint)
        {
            OutError = FString::Printf(
                TEXT("inspect_ndisplay_config: wrong_asset_type: '%s' is a %s, not a UDisplayClusterBlueprint"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // GetOrLoadConfig() returns the UDisplayClusterConfigurationData CDO, loading it
        // from the generated class if needed. Null on a malformed / not-yet-compiled asset.
        UDisplayClusterConfigurationData* ConfigData = Blueprint->GetOrLoadConfig();
        if (!ConfigData)
        {
            OutError = FString::Printf(
                TEXT("inspect_ndisplay_config: no_config_data: '%s' has no nDisplay configuration data (asset may be uncompiled or corrupt)"),
                *InputPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Blueprint->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);
        Out->SetStringField(TEXT("class"), Blueprint->GetClass()->GetName());

        // Cluster may be null/empty on a transient or freshly created config; guard it.
        UDisplayClusterConfigurationCluster* Cluster = ConfigData->Cluster;
        if (Cluster)
        {
            Out->SetStringField(TEXT("primary_node_id"), Cluster->PrimaryNode.Id);
        }
        else
        {
            Out->SetStringField(TEXT("primary_node_id"), FString());
        }

        // Walk the cluster nodes. Sort node ids for stable output. Each node maps to
        // a UDisplayClusterConfigurationClusterNode in Cluster->Nodes.
        TArray<TSharedPtr<FJsonValue>> NodesJson;
        if (Cluster)
        {
            TArray<FString> NodeIds;
            Cluster->Nodes.GenerateKeyArray(NodeIds);
            NodeIds.Sort([](const FString& A, const FString& B) { return A.Compare(B) < 0; });

            for (const FString& NodeId : NodeIds)
            {
                const TObjectPtr<UDisplayClusterConfigurationClusterNode>* NodePtr = Cluster->Nodes.Find(NodeId);
                if (!NodePtr || !*NodePtr)
                {
                    continue;
                }
                const UDisplayClusterConfigurationClusterNode* Node = *NodePtr;

                TSharedPtr<FJsonObject> NodeObj = MakeShared<FJsonObject>();
                NodeObj->SetStringField(TEXT("id"), NodeId);
                NodeObj->SetStringField(TEXT("host"), Node->Host);
                NodeObj->SetObjectField(TEXT("window"), RectToJson(Node->WindowRect));

                // Viewports within this node. Sort viewport ids for stable output.
                TArray<TSharedPtr<FJsonValue>> ViewportsJson;
                TArray<FString> ViewportIds;
                Node->Viewports.GenerateKeyArray(ViewportIds);
                ViewportIds.Sort([](const FString& A, const FString& B) { return A.Compare(B) < 0; });

                for (const FString& ViewportId : ViewportIds)
                {
                    const TObjectPtr<UDisplayClusterConfigurationViewport>* ViewportPtr = Node->Viewports.Find(ViewportId);
                    if (!ViewportPtr || !*ViewportPtr)
                    {
                        continue;
                    }
                    const UDisplayClusterConfigurationViewport* Viewport = *ViewportPtr;

                    TSharedPtr<FJsonObject> ViewportObj = MakeShared<FJsonObject>();
                    ViewportObj->SetStringField(TEXT("id"), ViewportId);
                    ViewportObj->SetBoolField(TEXT("enabled"), Viewport->bAllowRendering);
                    ViewportObj->SetStringField(TEXT("camera"), Viewport->Camera);
                    ViewportObj->SetNumberField(TEXT("gpu_index"), static_cast<int32>(Viewport->GPUIndex));
                    ViewportObj->SetObjectField(TEXT("region"), RectToJson(Viewport->Region));

                    // Projection policy: a polymorphic entity with a Type string and a
                    // free-form string parameter map.
                    ViewportObj->SetStringField(TEXT("projection_type"), Viewport->ProjectionPolicy.Type);
                    TSharedPtr<FJsonObject> ProjParams = MakeShared<FJsonObject>();
                    for (const TPair<FString, FString>& Pair : Viewport->ProjectionPolicy.Parameters)
                    {
                        ProjParams->SetStringField(Pair.Key, Pair.Value);
                    }
                    ViewportObj->SetObjectField(TEXT("projection_parameters"), ProjParams);

                    ViewportsJson.Add(MakeShared<FJsonValueObject>(ViewportObj));
                }
                NodeObj->SetArrayField(TEXT("viewports"), ViewportsJson);

                NodesJson.Add(MakeShared<FJsonValueObject>(NodeObj));
            }
        }
        Out->SetArrayField(TEXT("nodes"), NodesJson);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectNdisplayConfig()
{
    return MakeShared<FHandler_InspectNdisplayConfig>();
}
