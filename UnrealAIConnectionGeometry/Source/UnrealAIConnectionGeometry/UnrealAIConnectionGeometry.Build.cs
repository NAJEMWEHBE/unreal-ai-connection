// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

using UnrealBuildTool;

public class UnrealAIConnectionGeometry : ModuleRules
{
    public UnrealAIConnectionGeometry(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "CoreUObject",
            "Engine",
            "Json",
            // UEditorAssetLibrary::LoadAsset / SaveLoadedAsset (asset I/O in the bake handler).
            "EditorScriptingUtilities",
            // Core plugin module: provides IUCMCPHandler + the exported
            // FUCMCPHandlerRegistry singleton (MCP/MCPHandler.h + MCP/Handlers/AssetPathUtil.h
            // are Public there), so this companion can implement handlers and register them
            // into the same registry the running TCP dispatcher reads per-call.
            "UnrealAIConnection",
            // The Geometry Scripting dependency this companion exists to isolate out of the core:
            //   GeometryScriptingCore - UGeometryScriptLibrary_StaticMeshFunctions (CopyMeshFrom/To
            //                           StaticMesh), UGeometryScriptLibrary_MeshBakeFunctions
            //                           (MakeBakeTypeAmbientOcclusion / BakeVertex) and
            //                           UGeometryScriptLibrary_MeshVertexColorFunctions
            //                           (BlurMeshVertexColors).
            //   GeometryFramework     - UDynamicMesh, the UObject container the bake operates on.
            "GeometryScriptingCore",
            "GeometryFramework",
        });
    }
}
