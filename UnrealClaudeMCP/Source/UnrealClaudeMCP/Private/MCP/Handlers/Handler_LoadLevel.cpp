// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// load_level_by_path - load a UE level (UWorld asset) by its package path.
//
// Error format: free-form OutError strings (legacy surface — predates the canonical
// "<tool_name>: <error_code>: <detail>" convention used by later handlers). Migration
// is deferred; bridge consumers treat OutError as human-readable text rather than
// parsing for a code prefix.

#include "MCP/MCPHandler.h"

#include "Editor.h"
#include "EditorAssetLibrary.h"
#include "LevelEditorSubsystem.h"

class FHandler_LoadLevel : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("load_level_by_path"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString Path;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("path"), Path))
        {
            OutError = TEXT("Missing required string param: 'path' (e.g. /Game/Maps/MyMap)");
            return nullptr;
        }

        // Pre-check on disk - the editor subsystem returns true even for missing assets in some cases.
        if (!UEditorAssetLibrary::DoesAssetExist(Path))
        {
            OutError = FString::Printf(TEXT("Level asset does not exist: %s"), *Path);
            return nullptr;
        }

        // Strip the .ext if user passed the full object path
        FString MapName = Path;
        int32 DotIdx;
        if (MapName.FindChar(TEXT('.'), DotIdx))
        {
            MapName = MapName.Left(DotIdx);
        }

        bool bResult = false;
        if (GEditor)
        {
            if (ULevelEditorSubsystem* LES = GEditor->GetEditorSubsystem<ULevelEditorSubsystem>())
            {
                bResult = LES->LoadLevel(MapName);
            }
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("path"), Path);
        Out->SetBoolField(TEXT("loaded"), bResult);
        if (!bResult)
        {
            OutError = FString::Printf(TEXT("Failed to load level: %s"), *Path);
            return nullptr;
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_LoadLevel()
{
    return MakeShared<FHandler_LoadLevel>();
}
