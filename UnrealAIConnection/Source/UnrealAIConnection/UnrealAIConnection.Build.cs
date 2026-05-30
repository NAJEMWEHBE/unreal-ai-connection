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
            // Cinematics authoring lane — ACineCameraActor / UCineCameraComponent
            // (add_cine_camera_to_sequence) live in the CinematicCamera module.
            "CinematicCamera",
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
            "ImageWrapper",
            // Movie Render Queue render lane (render_sequence_mrq) — all three
            // are used only inside Handler_RenderSequenceMrq.cpp, so Private:
            //   MovieRenderPipelineCore         — queue / job / config / output-setting / executor base + data types
            //   MovieRenderPipelineEditor       — UMoviePipelineQueueSubsystem + PIE / NewProcess executors
            //   MovieRenderPipelineRenderPasses — image-output containers (PNG/JPG/BMP/EXR) + deferred passes
            "MovieRenderPipelineCore",
            "MovieRenderPipelineEditor",
            "MovieRenderPipelineRenderPasses",
            // MoviePipelineEXROutput.h (pulled in by the _EXR output format) includes
            // Imath/OpenEXR headers. RenderPasses keeps Imath/UEOpenExr PRIVATE
            // (its Build.cs:20-22), so the include paths are NOT propagated to dependents —
            // add them here so the EXR public header resolves when compiled in this module.
            "Imath",
            "UEOpenExr",
            "UEOpenExrRTTI"
        });
    }
}
