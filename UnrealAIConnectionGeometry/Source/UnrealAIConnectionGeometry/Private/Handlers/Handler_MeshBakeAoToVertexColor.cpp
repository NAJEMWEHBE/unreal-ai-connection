// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// mesh_bake_ao_to_vertex_color - bake self-occlusion ambient occlusion into the
// vertex colors of a StaticMesh asset, in place, and (optionally) save it.
//
// Pipeline (all Geometry Scripting; engine file:line cited at each call):
//   1. Copy the mesh OUT of the StaticMesh asset into a transient UDynamicMesh.
//   2. Build an Ambient-Occlusion bake-type descriptor.
//   3. BakeVertex with the SAME mesh as both target and source (self-occlusion),
//      writing the AO result into the RGBA vertex-color channel.
//   4. Optionally blur the resulting vertex colors.
//   5. Copy the mesh BACK into the asset (no nested transaction) and save.
//
// This is a MUTATING handler (IsMutating() == true): the dispatcher already wraps
// Handle() in an FScopedTransaction (MCPDispatcher.cpp:95-100), so the CopyMeshToStaticMesh
// options set bEmitTransaction = false to avoid a nested transaction (a known trap).
//
// Editor-only: CopyMeshFromStaticMesh reads the asset's editor-only MeshDescription /
// SourceModel data, so the whole pipeline is guarded by WITH_EDITOR and returns the
// editor_only error code on a non-editor target.
//
// Error format: "mesh_bake_ao_to_vertex_color: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_static_mesh,
// copy_from_failed, copy_to_failed, editor_only.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "Engine/StaticMesh.h"
#include "UDynamicMesh.h"
#include "UObject/StrongObjectPtr.h"

#include "GeometryScript/GeometryScriptTypes.h"
#include "GeometryScript/MeshAssetFunctions.h"
#include "GeometryScript/MeshBakeFunctions.h"
#include "GeometryScript/MeshVertexColorFunctions.h"
#include "GeometryScript/GeometryScriptSelectionTypes.h"

class FHandler_MeshBakeAoToVertexColor : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("mesh_bake_ao_to_vertex_color"); }

    // Edits + saves the StaticMesh asset. Returning true makes the dispatcher open a
    // single FScopedTransaction around Handle() (one Ctrl+Z step). We call
    // UStaticMesh::Modify() below so the change is recorded in that transaction.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------
        if (!Params.IsValid())
        {
            OutError = TEXT("mesh_bake_ao_to_vertex_color: missing_required_field: 'static_mesh' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("static_mesh"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("mesh_bake_ao_to_vertex_color: missing_required_field: 'static_mesh' is required and must not be empty");
            return nullptr;
        }

#if !WITH_EDITOR
        // CopyMeshFromStaticMesh reads editor-only MeshDescription/SourceModel data,
        // which does not exist in a cooked/non-editor build.
        OutError = TEXT("mesh_bake_ao_to_vertex_color: editor_only: this tool requires an editor build (it reads editor-only StaticMesh source data)");
        return nullptr;
#else
        // --- optional params with defaults ----------------------------------
        // occlusion_rays clamped to [1, 256] to bound CPU; the bake cost scales with rays.
        int32 OcclusionRays = 16;
        Params->TryGetNumberField(TEXT("occlusion_rays"), OcclusionRays);
        OcclusionRays = FMath::Clamp(OcclusionRays, 1, 256);

        double MaxDistance = 0.0;
        Params->TryGetNumberField(TEXT("max_distance"), MaxDistance);

        double SpreadAngle = 180.0;
        Params->TryGetNumberField(TEXT("spread_angle"), SpreadAngle);

        double BiasAngle = 15.0;
        Params->TryGetNumberField(TEXT("bias_angle"), BiasAngle);

        int32 LODIndex = 0;
        Params->TryGetNumberField(TEXT("lod_index"), LODIndex);
        LODIndex = FMath::Max(LODIndex, 0);

        int32 BlurIterations = 0;
        Params->TryGetNumberField(TEXT("blur_iterations"), BlurIterations);
        BlurIterations = FMath::Max(BlurIterations, 0);

        double BlurStrength = 0.5;
        Params->TryGetNumberField(TEXT("blur_strength"), BlurStrength);

        bool bSplitAtUVSeams = false;
        Params->TryGetBoolField(TEXT("split_at_uv_seams"), bSplitAtUVSeams);

        bool bSplitAtNormalSeams = false;
        Params->TryGetBoolField(TEXT("split_at_normal_seams"), bSplitAtNormalSeams);

        bool bSave = true;
        Params->TryGetBoolField(TEXT("save"), bSave);

        // --- load and cast asset --------------------------------------------
        // Load via UEditorAssetLibrary (resolves the registry + loads from disk if the
        // package is not yet in memory) then cast - same load+cast+disambiguation pattern
        // as Handler_InspectOcioConfig.cpp. A typed LoadObject<T> can return null on a
        // not-yet-loaded asset and would then misreport a right-type asset as wrong type.
        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("mesh_bake_ao_to_vertex_color: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        UStaticMesh* Mesh = Cast<UStaticMesh>(LoadedAsset);
        if (!Mesh)
        {
            OutError = FString::Printf(
                TEXT("mesh_bake_ao_to_vertex_color: not_a_static_mesh: '%s' is a %s, not a UStaticMesh"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- mint a transient UDynamicMesh to operate on --------------------
        // UDynamicMesh.h:118. NewObject into the transient package, and hold it in a
        // TStrongObjectPtr for the handler's duration so GC cannot collect it mid-bake.
        TStrongObjectPtr<UDynamicMesh> DynMesh(NewObject<UDynamicMesh>(GetTransientPackage()));

        // --- 1. copy mesh OUT of the static mesh asset ----------------------
        // UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshFromStaticMesh, the
        // convenience overload that forwards to ...V2 with bUseSectionMaterials=true
        // (MeshAssetFunctions.h:264). RequestedLOD uses the SourceModel of the requested
        // index so the write-back LODIndex (below) targets the matching source model.
        FGeometryScriptCopyMeshFromAssetOptions FromOptions;          // MeshAssetFunctions.h:56 (defaults are fine)
        FGeometryScriptMeshReadLOD ReadLOD;                           // GeometryScriptTypes.h:86
        ReadLOD.LODType  = EGeometryScriptLODType::SourceModel;       // GeometryScriptTypes.h:47 - editor source mesh
        ReadLOD.LODIndex = LODIndex;

        EGeometryScriptOutcomePins CopyFromOutcome = EGeometryScriptOutcomePins::Failure; // GeometryScriptTypes.h:22
        UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshFromStaticMesh(
            Mesh,
            DynMesh.Get(),
            FromOptions,
            ReadLOD,
            CopyFromOutcome,
            /*Debug=*/nullptr);                                       // MeshAssetFunctions.h:264

        if (CopyFromOutcome != EGeometryScriptOutcomePins::Success)
        {
            OutError = FString::Printf(
                TEXT("mesh_bake_ao_to_vertex_color: copy_from_failed: CopyMeshFromStaticMesh failed for '%s' at SourceModel LOD %d"),
                *InputPath, LODIndex);
            return nullptr;
        }

        // --- 2. build the Ambient-Occlusion bake-type descriptor ------------
        // UGeometryScriptLibrary_MeshBakeFunctions::MakeBakeTypeAmbientOcclusion
        // (MeshBakeFunctions.h:583). Returns an FGeometryScriptBakeTypeOptions.
        const FGeometryScriptBakeTypeOptions AoBakeType =
            UGeometryScriptLibrary_MeshBakeFunctions::MakeBakeTypeAmbientOcclusion(
                OcclusionRays,
                static_cast<float>(MaxDistance),
                static_cast<float>(SpreadAngle),
                static_cast<float>(BiasAngle));

        // Wire the AO bake type into the RGBA output channel of the vertex-bake output
        // descriptor. FGeometryScriptBakeOutputType (MeshBakeFunctions.h:337): OutputMode
        // defaults to RGBA (line 343) and the RGBA member is the FGeometryScriptBakeTypeOptions
        // used for that mode (line 346). So writing AoBakeType to .RGBA bakes AO to all channels.
        FGeometryScriptBakeOutputType BakeOutput;                    // MeshBakeFunctions.h:337
        BakeOutput.OutputMode = EGeometryScriptBakeOutputMode::RGBA; // MeshBakeFunctions.h:343 (explicit for clarity)
        BakeOutput.RGBA       = AoBakeType;                          // MeshBakeFunctions.h:346

        // Vertex-bake options. TopologyMode defaults to CreateNew (MeshBakeFunctions.h:315),
        // which builds fresh vertex-color topology so meshes with no existing vertex colors work.
        FGeometryScriptBakeVertexOptions VertexOptions;              // MeshBakeFunctions.h:309
        VertexOptions.bSplitAtNormalSeams = bSplitAtNormalSeams;     // MeshBakeFunctions.h:319
        VertexOptions.bSplitAtUVSeams     = bSplitAtUVSeams;         // MeshBakeFunctions.h:323

        // --- 3. bake AO to vertex colors (self-occlusion) ------------------
        // UGeometryScriptLibrary_MeshBakeFunctions::BakeVertex (MeshBakeFunctions.h:636).
        // For self-occlusion AO, pass the SAME UDynamicMesh as both TargetMesh and
        // SourceMesh, with identity transforms. Default-constructed target/source mesh
        // options are fine (no separate source normal map, target UV channel 0).
        UGeometryScriptLibrary_MeshBakeFunctions::BakeVertex(
            DynMesh.Get(),                                           // TargetMesh
            FTransform::Identity,                                    // TargetTransform
            FGeometryScriptBakeTargetMeshOptions(),                  // MeshBakeFunctions.h:362
            DynMesh.Get(),                                           // SourceMesh (same mesh -> self-occlusion)
            FTransform::Identity,                                    // SourceTransform
            FGeometryScriptBakeSourceMeshOptions(),                  // MeshBakeFunctions.h:371
            BakeOutput,
            VertexOptions,
            /*Debug=*/nullptr);                                      // MeshBakeFunctions.h:636

        // --- 4. optional blur of the baked vertex colors --------------------
        bool bBlurred = false;
        if (BlurIterations > 0)
        {
            // UGeometryScriptLibrary_MeshVertexColorFunctions::BlurMeshVertexColors
            // (MeshVertexColorFunctions.h:211). A default-constructed FGeometryScriptMeshSelection
            // means "all vertices" (GeometryScriptSelectionTypes.h). Uniform blur mode + default
            // per-channel options (all RGBA channels blurred).
            UGeometryScriptLibrary_MeshVertexColorFunctions::BlurMeshVertexColors(
                DynMesh.Get(),
                FGeometryScriptMeshSelection(),                      // all vertices
                BlurIterations,
                BlurStrength,
                EGeometryScriptBlurColorMode::Uniform,               // MeshVertexColorFunctions.h:20
                FGeometryScriptBlurMeshVertexColorsOptions(),        // MeshVertexColorFunctions.h:28
                /*Debug=*/nullptr);                                  // MeshVertexColorFunctions.h:211
            bBlurred = true;
        }

        // --- capture mesh stats before writing back -------------------------
        // GetTriangleCount() is a UFUNCTION on UDynamicMesh (UDynamicMesh.h:160). There is
        // no equivalent vertex-count UFUNCTION, so read VertexCount() off the underlying
        // FDynamicMesh3 via GetMeshRef() (UDynamicMesh.h:167; FDynamicMesh3::VertexCount()
        // at DynamicMesh3.h:378).
        const int32 NumTriangles = DynMesh->GetTriangleCount();
        const int32 NumVertices  = DynMesh->GetMeshRef().VertexCount();

        // --- 5. copy mesh BACK into the asset -------------------------------
        // Record the asset on the dispatcher's transaction before mutating it.
        Mesh->Modify();

        FGeometryScriptCopyMeshToAssetOptions ToOptions;             // MeshAssetFunctions.h:100
        // CRITICAL: the MCP dispatcher already wraps this handler in an FScopedTransaction
        // (MCPDispatcher.cpp:95-100). Setting bEmitTransaction=false here avoids a nested
        // transaction (a known trap). MeshAssetFunctions.h:162 (defaults true).
        ToOptions.bEmitTransaction     = false;
        // Preserve the asset's existing Nanite settings rather than overwriting them.
        // MeshAssetFunctions.h:152 (already defaults false; explicit for intent).
        ToOptions.bApplyNaniteSettings = false;

        FGeometryScriptMeshWriteLOD WriteLOD;                        // GeometryScriptTypes.h:99
        WriteLOD.bWriteHiResSource = false;                          // GeometryScriptTypes.h:104
        WriteLOD.LODIndex          = LODIndex;                       // match the read LOD

        EGeometryScriptOutcomePins CopyToOutcome = EGeometryScriptOutcomePins::Failure;
        // Call the non-deprecated overload explicitly with bUseSectionMaterials=true
        // (MeshAssetFunctions.h:282), matching the CopyMeshFromStaticMesh default above.
        // (The no-bUseSectionMaterials overload at MeshAssetFunctions.h:293 is UE_DEPRECATED(5.5).)
        UGeometryScriptLibrary_StaticMeshFunctions::CopyMeshToStaticMesh(
            DynMesh.Get(),
            Mesh,
            ToOptions,
            WriteLOD,
            CopyToOutcome,
            /*bUseSectionMaterials=*/true,
            /*Debug=*/nullptr);                                      // MeshAssetFunctions.h:282

        if (CopyToOutcome != EGeometryScriptOutcomePins::Success)
        {
            OutError = FString::Printf(
                TEXT("mesh_bake_ao_to_vertex_color: copy_to_failed: CopyMeshToStaticMesh failed for '%s' at LOD %d"),
                *InputPath, LODIndex);
            return nullptr;
        }

        // --- persist --------------------------------------------------------
        bool bSaved = false;
        if (bSave)
        {
            // SaveLoadedAsset(UObject*, bOnlyIfIsDirty=true) - EditorAssetLibrary.h:262.
            // Force a save (CopyMeshToStaticMesh has marked the package dirty).
            bSaved = UEditorAssetLibrary::SaveLoadedAsset(Mesh, /*bOnlyIfIsDirty=*/false);
        }

        // --- build response -------------------------------------------------
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("static_mesh"), InputPath);
        Out->SetNumberField(TEXT("lod_index"), LODIndex);
        Out->SetNumberField(TEXT("vertices"), NumVertices);
        Out->SetNumberField(TEXT("triangles"), NumTriangles);
        Out->SetNumberField(TEXT("occlusion_rays"), OcclusionRays);
        Out->SetBoolField(TEXT("blurred"), bBlurred);
        Out->SetBoolField(TEXT("saved"), bSaved);

        return Out;
#endif // WITH_EDITOR
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_MeshBakeAoToVertexColor()
{
    return MakeShared<FHandler_MeshBakeAoToVertexColor>();
}
