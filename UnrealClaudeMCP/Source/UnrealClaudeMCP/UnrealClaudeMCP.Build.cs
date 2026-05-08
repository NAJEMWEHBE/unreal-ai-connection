// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

using UnrealBuildTool;

public class UnrealClaudeMCP : ModuleRules
{
    public UnrealClaudeMCP(ReadOnlyTargetRules Target) : base(Target)
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
            "EngineSettings",
            "UMG",
            "UMGEditor"
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "InputCore",
            "Projects",
            "PropertyEditor",
            "LevelEditor"
        });
    }
}
