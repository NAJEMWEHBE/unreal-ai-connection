// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_static_mesh - read the structural properties of a UStaticMesh
// asset: LOD count, per-LOD vertex / triangle counts, bounding box,
// material slots. Pairs with inspect_asset (which reads registry-level
// metadata) and inspect_material (which reads UMaterial/UMaterialInstance
// parameters) for asset-introspection workflows.
//
// Part of the language-shim experiment (PR #46): "C++ canonical" handler.
// Reading UStaticMesh struct fields via the native UE API is dramatically
// cleaner than equivalent Python (which would require multi-call FFI to
// unreal.StaticMesh + per-LOD lookups + manual bounds-vector unpacking).
// See docs/LANGUAGE-CHOICE-RETROSPECTIVE.md for the comparison.
//
// UE 5.7 surface used:
//   - UStaticMesh::GetNumLODs                StaticMesh.h:2155
//   - UStaticMesh::GetNumVertices(LODIndex)  StaticMesh.h:2128
//   - UStaticMesh::GetNumTriangles(LODIndex) StaticMesh.h:2134
//   - UStaticMesh::GetBoundingBox            StaticMesh.h:2182
//   - UStaticMesh::GetStaticMaterials        (read-only TArray<FStaticMaterial>)
//
// Error format: "inspect_static_mesh: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_static_mesh.

#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "Engine/StaticMesh.h"

namespace
{
    static TSharedPtr<FJsonObject> VectorToJson(const FVector& V)
    {
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetNumberField(TEXT("x"), V.X);
        Obj->SetNumberField(TEXT("y"), V.Y);
        Obj->SetNumberField(TEXT("z"), V.Z);
        return Obj;
    }
}

class FHandler_InspectStaticMesh : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_static_mesh"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_static_mesh: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_static_mesh: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_static_mesh: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }
        UStaticMesh* Mesh = Cast<UStaticMesh>(Loaded);
        if (!Mesh)
        {
            OutError = FString::Printf(
                TEXT("inspect_static_mesh: not_a_static_mesh: '%s' is a %s, not a UStaticMesh"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- gather per-LOD geometry stats -------------------------------

        const int32 NumLODs = Mesh->GetNumLODs();
        TArray<TSharedPtr<FJsonValue>> LodArray;
        LodArray.Reserve(NumLODs);
        // int64 accumulators -- per-LOD counts are int32, but the total
        // across many LODs on a high-poly mesh could theoretically exceed
        // INT32_MAX. Cheap defensive change. (Gemini medium on PR #46.)
        int64 TotalVertices = 0;
        int64 TotalTriangles = 0;
        for (int32 i = 0; i < NumLODs; ++i)
        {
            const int32 V = Mesh->GetNumVertices(i);
            const int32 T = Mesh->GetNumTriangles(i);
            TotalVertices  += V;
            TotalTriangles += T;

            TSharedPtr<FJsonObject> LodObj = MakeShared<FJsonObject>();
            LodObj->SetNumberField(TEXT("index"), static_cast<double>(i));
            LodObj->SetNumberField(TEXT("vertices"), static_cast<double>(V));
            LodObj->SetNumberField(TEXT("triangles"), static_cast<double>(T));
            LodArray.Add(MakeShared<FJsonValueObject>(LodObj));
        }

        // --- bounding box ------------------------------------------------

        const FBox Bounds = Mesh->GetBoundingBox();
        TSharedPtr<FJsonObject> BoundsObj = MakeShared<FJsonObject>();
        BoundsObj->SetObjectField(TEXT("min"), VectorToJson(Bounds.Min));
        BoundsObj->SetObjectField(TEXT("max"), VectorToJson(Bounds.Max));
        BoundsObj->SetObjectField(TEXT("size"), VectorToJson(Bounds.GetSize()));
        BoundsObj->SetObjectField(TEXT("center"), VectorToJson(Bounds.GetCenter()));

        // --- material slots ----------------------------------------------

        const TArray<FStaticMaterial>& Materials = Mesh->GetStaticMaterials();
        TArray<TSharedPtr<FJsonValue>> MatArray;
        MatArray.Reserve(Materials.Num());
        for (int32 i = 0; i < Materials.Num(); ++i)
        {
            const FStaticMaterial& M = Materials[i];
            TSharedPtr<FJsonObject> MatObj = MakeShared<FJsonObject>();
            MatObj->SetNumberField(TEXT("index"), static_cast<double>(i));
            MatObj->SetStringField(TEXT("slot_name"), M.MaterialSlotName.ToString());
            MatObj->SetStringField(TEXT("material_path"),
                M.MaterialInterface ? M.MaterialInterface->GetPathName() : TEXT(""));
            MatArray.Add(MakeShared<FJsonValueObject>(MatObj));
        }

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Mesh->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);
        Out->SetNumberField(TEXT("num_lods"), static_cast<double>(NumLODs));
        Out->SetNumberField(TEXT("total_vertices"), static_cast<double>(TotalVertices));
        Out->SetNumberField(TEXT("total_triangles"), static_cast<double>(TotalTriangles));
        Out->SetArrayField(TEXT("lods"), LodArray);
        Out->SetObjectField(TEXT("bounds"), BoundsObj);
        Out->SetArrayField(TEXT("material_slots"), MatArray);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectStaticMesh()
{
    return MakeShared<FHandler_InspectStaticMesh>();
}
