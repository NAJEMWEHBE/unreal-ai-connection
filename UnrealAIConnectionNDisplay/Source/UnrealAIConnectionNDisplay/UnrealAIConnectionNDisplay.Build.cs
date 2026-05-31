// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

using UnrealBuildTool;

public class UnrealAIConnectionNDisplay : ModuleRules
{
    public UnrealAIConnectionNDisplay(ReadOnlyTargetRules Target) : base(Target)
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
            // UEditorAssetLibrary::LoadAsset (asset I/O in the inspect handler).
            "EditorScriptingUtilities",
            // Core plugin module: provides IUCMCPHandler + the exported
            // FUCMCPHandlerRegistry singleton (MCP/MCPHandler.h is Public there),
            // so this companion can implement handlers and register them into the
            // same registry the running TCP dispatcher reads per-call.
            "UnrealAIConnection",
            // The nDisplay dependency this companion exists to isolate out of the core:
            //   DisplayCluster              - UDisplayClusterBlueprint (the asset class) +
            //                                 GetOrLoadConfig() (inspect_ndisplay_config).
            //   DisplayClusterConfiguration - UDisplayClusterConfigurationData / Cluster /
            //                                 ClusterNode / Viewport config UObjects + the
            //                                 FDisplayClusterConfigurationRectangle / Projection
            //                                 structs the handler reads.
            "DisplayCluster",
            "DisplayClusterConfiguration",
        });
    }
}
