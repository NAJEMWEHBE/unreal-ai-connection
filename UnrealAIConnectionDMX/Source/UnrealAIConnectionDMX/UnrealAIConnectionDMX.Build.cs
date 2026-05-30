// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

using UnrealBuildTool;

public class UnrealAIConnectionDMX : ModuleRules
{
    public UnrealAIConnectionDMX(ReadOnlyTargetRules Target) : base(Target)
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
            // UEditorAssetLibrary::LoadAsset / SaveLoadedAsset (create_dmx_patch asset I/O).
            "EditorScriptingUtilities",
            // Core plugin module: provides IUCMCPHandler + the exported
            // FUCMCPHandlerRegistry singleton (MCP/MCPHandler.h is Public there),
            // so this companion can implement handlers and register them into the
            // same registry the running TCP dispatcher reads per-call.
            "UnrealAIConnection",
            // The DMX dependency this companion exists to isolate out of the core:
            //   DMXRuntime  - UDMXLibrary / UDMXEntityFixturePatch / UDMXEntityFixtureType (create_dmx_patch)
            //   DMXProtocol - FDMXPortManager / FDMXOutputPort (dmx_stream_*)
            "DMXRuntime",
            "DMXProtocol",
        });
    }
}
