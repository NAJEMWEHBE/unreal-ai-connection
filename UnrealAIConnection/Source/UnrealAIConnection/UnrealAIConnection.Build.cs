// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

using UnrealBuildTool;

public class UnrealAIConnection : ModuleRules
{
    public UnrealAIConnection(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "UnrealEd",
            "Slate",
            "SlateCore",
            "EditorScriptingUtilities",
            "EditorSubsystem",
            "AssetRegistry",
            "AssetTools",
            // MCP server transport
            "Sockets",
            "Networking",
            "Json",
            "JsonUtilities",
            // Handler dependencies
            "PythonScriptPlugin",
            "GraphEditor",
            "Kismet",
            // Blueprint authoring (Lane 3) — UEdGraphSchema_K2::PC_* pin
            // categories from EdGraphSchema_K2.h live in BlueprintGraph.
            "BlueprintGraph",
            "EngineSettings",
            "UMG",
            "UMGEditor",
            "Niagara",
            "NiagaraCore",
            // Sequencer (v0.8.0)
            "LevelSequence",
            "MovieScene",
            "MovieSceneTracks",
            // DMX patch authoring (create_dmx_patch handler, v0.9.2)
            "DMXRuntime",
            // DMX continuous output streamer (dmx_stream_* handlers, v0.9.3) —
            // FDMXPortManager / FDMXOutputPort live in the DMXProtocol module.
            "DMXProtocol"
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "InputCore",
            "Projects",
            "PropertyEditor",
            "LevelEditor",
            // Project Settings reflection (Handler_InspectProjectSetting) — private,
            // used only inside the implementation; no need to leak transitively.
            "DeveloperSettings",
            // Sequencer editor library (v0.8.0)
            "LevelSequenceEditor",
            // Material instance authoring (v0.9.0)
            "Landscape",
            "MaterialEditor",
            // render_camera_to_png (Path A+B sync render)
            "RenderCore",
            "RHI",
            "ImageWrapper"
        });
    }
}
