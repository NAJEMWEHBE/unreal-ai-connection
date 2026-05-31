// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

using UnrealBuildTool;

public class UnrealAIConnectionOCIO : ModuleRules
{
    public UnrealAIConnectionOCIO(ReadOnlyTargetRules Target) : base(Target)
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
            // The OCIO dependency this companion exists to isolate out of the core:
            //   OpenColorIO        - UOpenColorIOConfiguration + the FOpenColorIOColorSpace /
            //                        FOpenColorIODisplayView structs (inspect_ocio_config).
            //   OpenColorIOWrapper - FOpenColorIOWrapperConfig, used to enumerate every
            //                        color space / display / view in the .ocio file. It also
            //                        defines WITH_OCIO (PublicDefinition), which the handler
            //                        guards the wrapper calls behind for non-OCIO targets.
            "OpenColorIO",
            "OpenColorIOWrapper",
        });
    }
}
