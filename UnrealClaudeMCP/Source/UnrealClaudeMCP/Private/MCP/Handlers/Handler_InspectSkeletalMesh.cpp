// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_skeletal_mesh - read the structural properties of a USkeletalMesh
// asset: LOD count, per-LOD vertex / triangle / section counts, imported
// bounds, skeleton, reference-skeleton bone counts, material slots, morph
// targets, clothing presence, and physics asset. Pairs with inspect_asset
// (which reads registry-level metadata) and inspect_static_mesh for
// asset-introspection workflows.
//
// UE 5.7 surface used:
//   - USkeletalMesh::GetResourceForRendering       SkeletalMesh.h:725
//   - USkeletalMesh::GetSkeleton                   SkeletalMesh.h:750
//   - USkeletalMesh::GetImportedBounds             SkeletalMesh.h:839
//   - USkeletalMesh::GetMaterials                  SkeletalMesh.h:923
//   - USkeletalMesh::GetPhysicsAsset               SkeletalMesh.h:1514
//   - USkeletalMesh::GetMorphTargets               SkeletalMesh.h:1981
//   - USkeletalMesh::GetRefSkeleton                SkeletalMesh.h:2041
//   - USkeletalMesh::GetMeshClothingAssets         SkeletalMesh.h:2297
//   - USkeletalMesh::HasActiveClothingAssets       SkeletalMesh.h:2326
//   - USkeletalMesh::GetLODNum                     SkeletalMesh.h:3017
//   - FSkeletalMeshRenderData::LODRenderData       SkeletalMeshRenderData.h:20
//   - FSkeletalMeshLODRenderData::RenderSections   SkeletalMeshLODRenderData.h:132
//   - FSkeletalMeshLODRenderData::GetNumVertices   SkeletalMeshLODRenderData.h:263
//   - FSkeletalMeshLODRenderData::GetTotalFaces    SkeletalMeshLODRenderData.h:298
//   - FReferenceSkeleton::GetNum / GetRawBoneNum   ReferenceSkeleton.h
//
// Error format: "inspect_skeletal_mesh: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_skeletal_mesh.

#include "Engine/SkeletalMesh.h"
#include "Engine/SkinnedAsset.h"
#include "Engine/SkinnedAssetCommon.h"
#include "Animation/Skeleton.h"
#include "Animation/MorphTarget.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
#include "ReferenceSkeleton.h"
#include "EditorAssetLibrary.h"
#include "Materials/MaterialInterface.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

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

    static TSharedPtr<FJsonObject> BoxToJson(const FBox& Box)
    {
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetObjectField(TEXT("min"), VectorToJson(Box.Min));
        Obj->SetObjectField(TEXT("max"), VectorToJson(Box.Max));
        Obj->SetObjectField(TEXT("size"), VectorToJson(Box.GetSize()));
        Obj->SetObjectField(TEXT("center"), VectorToJson(Box.GetCenter()));
        return Obj;
    }
}

class FHandler_InspectSkeletalMesh : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_skeletal_mesh"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_skeletal_mesh: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_skeletal_mesh: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        // Compute PackagePath separately so the `package_path` result field
        // emits the suffix-free package path (e.g. "/Game/X/Y") rather than
        // the object path (e.g. "/Game/X/Y.Y"). Mirrors inspect_static_mesh
        // which emits a true package path under that field name -- callers
        // depend on the field-name-to-shape contract being consistent across
        // sibling Inspect* handlers (cleanup PR #53 convention).
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_skeletal_mesh: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }
        USkeletalMesh* Mesh = Cast<USkeletalMesh>(Loaded);
        if (!Mesh)
        {
            OutError = FString::Printf(
                TEXT("inspect_skeletal_mesh: not_a_skeletal_mesh: '%s' is a %s, not a USkeletalMesh"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- gather per-LOD geometry stats -------------------------------

        TArray<TSharedPtr<FJsonValue>> LodArray;
        int32 NumLODs = 0;
        int64 TotalVertices = 0;
        int64 TotalTriangles = 0;

        // USkeletalMesh::GetResourceForRendering() can be null (SkeletalMesh.h:725);
        // report an empty render-data view rather than touching LODRenderData.
        FSkeletalMeshRenderData* RenderData = Mesh->GetResourceForRendering();
        if (RenderData)
        {
            NumLODs = FMath::Min(Mesh->GetLODNum(), RenderData->LODRenderData.Num()); // SkeletalMesh.h:3017
            LodArray.Reserve(NumLODs);

            for (int32 i = 0; i < NumLODs; ++i)
            {
                const FSkeletalMeshLODRenderData& LODData = RenderData->LODRenderData[i]; // SkeletalMeshRenderData.h:20
                const uint32 VertexCount = LODData.GetNumVertices(); // SkeletalMeshLODRenderData.h:263
                const int32 TriangleCount = LODData.GetTotalFaces(); // SkeletalMeshLODRenderData.h:298
                const int32 SectionCount = LODData.RenderSections.Num(); // SkeletalMeshLODRenderData.h:132
                TotalVertices += static_cast<int64>(VertexCount);
                TotalTriangles += static_cast<int64>(TriangleCount);

                TSharedPtr<FJsonObject> LodObj = MakeShared<FJsonObject>();
                LodObj->SetNumberField(TEXT("index"), static_cast<double>(i));
                LodObj->SetNumberField(TEXT("vertices"), static_cast<double>(VertexCount));
                LodObj->SetNumberField(TEXT("triangles"), static_cast<double>(TriangleCount));
                LodObj->SetNumberField(TEXT("section_count"), static_cast<double>(SectionCount));
                LodArray.Add(MakeShared<FJsonValueObject>(LodObj));
            }
        }

        // --- bounds ------------------------------------------------------

        const FBoxSphereBounds ImportedBounds = Mesh->GetImportedBounds(); // SkeletalMesh.h:839
        const FBox Bounds = ImportedBounds.GetBox();

        // --- skeleton and bones -----------------------------------------

        const FReferenceSkeleton& RefSkeleton = Mesh->GetRefSkeleton(); // SkeletalMesh.h:2041

        // --- material slots ----------------------------------------------

        const TArray<FSkeletalMaterial>& Materials = Mesh->GetMaterials(); // SkeletalMesh.h:923
        TArray<TSharedPtr<FJsonValue>> MatArray;
        MatArray.Reserve(Materials.Num());
        for (int32 i = 0; i < Materials.Num(); ++i)
        {
            const FSkeletalMaterial& M = Materials[i];
            TSharedPtr<FJsonObject> MatObj = MakeShared<FJsonObject>();
            MatObj->SetNumberField(TEXT("index"), static_cast<double>(i));
            MatObj->SetStringField(TEXT("slot_name"), M.MaterialSlotName.ToString());
            MatObj->SetStringField(TEXT("material_path"),
                M.MaterialInterface ? M.MaterialInterface->GetPathName() : TEXT(""));
            MatArray.Add(MakeShared<FJsonValueObject>(MatObj));
        }

        // --- morph targets -----------------------------------------------
        // GetMorphTargets() can contain null entries (e.g. when a morph
        // was deleted but the mesh wasn't saved, or during reimport).
        // Skip nulls entirely so morph_targets contains only valid names
        // and morph_target_count reflects only valid entries -- matches
        // what an LLM consumer expects from an "introspection of valid
        // morph targets" handler. PR #55 Gemini medium review.

        const TArray<TObjectPtr<UMorphTarget>>& MorphTargets = Mesh->GetMorphTargets(); // SkeletalMesh.h:1981
        TArray<TSharedPtr<FJsonValue>> MorphTargetArray;
        MorphTargetArray.Reserve(MorphTargets.Num());
        for (const TObjectPtr<UMorphTarget>& MorphTarget : MorphTargets)
        {
            if (MorphTarget)
            {
                MorphTargetArray.Add(MakeShared<FJsonValueString>(MorphTarget->GetName()));
            }
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
        Out->SetObjectField(TEXT("bounds"), BoxToJson(Bounds));
        Out->SetNumberField(TEXT("sphere_radius"), ImportedBounds.SphereRadius);

        if (const USkeleton* Skeleton = Mesh->GetSkeleton()) // SkeletalMesh.h:750
        {
            Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
        }

        Out->SetNumberField(TEXT("bone_count"), static_cast<double>(RefSkeleton.GetNum()));
        Out->SetNumberField(TEXT("raw_bone_count"), static_cast<double>(RefSkeleton.GetRawBoneNum()));
        Out->SetArrayField(TEXT("material_slots"), MatArray);
        // morph_target_count reflects valid (non-null) entries only -- matches
        // the morph_targets array's filtered length. (PR #55 Gemini medium.)
        Out->SetNumberField(TEXT("morph_target_count"), static_cast<double>(MorphTargetArray.Num()));
        Out->SetArrayField(TEXT("morph_targets"), MorphTargetArray);
        Out->SetBoolField(TEXT("has_clothing_assets"), Mesh->HasActiveClothingAssets()); // SkeletalMesh.h:2326
        Out->SetNumberField(TEXT("clothing_asset_count"),
            static_cast<double>(Mesh->GetMeshClothingAssets().Num())); // SkeletalMesh.h:2297

        if (UPhysicsAsset* PhysicsAsset = Mesh->GetPhysicsAsset()) // SkeletalMesh.h:1514
        {
            Out->SetStringField(TEXT("physics_asset"), PhysicsAsset->GetPathName());
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectSkeletalMesh()
{
    return MakeShared<FHandler_InspectSkeletalMesh>();
}

