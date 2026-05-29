// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// move_asset - move an asset to a different folder; leaf name unchanged.
// UE creates a redirector at the source path so existing references continue
// to resolve until the user runs Fix Up Redirectors.
//
// Error format: "move_asset: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// invalid_dest_folder, dest_exists, rename_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_MoveAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("move_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("move_asset: missing_required_field: 'path' and 'dest_folder' are required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("move_asset: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        FString DestFolder;
        if (!Params->TryGetStringField(TEXT("dest_folder"), DestFolder) || DestFolder.IsEmpty())
        {
            OutError = TEXT("move_asset: missing_required_field: 'dest_folder' is required and must not be empty");
            return nullptr;
        }

        // Validate dest folder root.
        if (!DestFolder.StartsWith(TEXT("/Game/")) && !DestFolder.StartsWith(TEXT("/Engine/")))
        {
            OutError = FString::Printf(
                TEXT("move_asset: invalid_dest_folder: '%s' must start with /Game/ or /Engine/"),
                *DestFolder);
            return nullptr;
        }

        // Trim trailing slash so we always concatenate `Folder + "/" + LeafName`.
        if (DestFolder.EndsWith(TEXT("/")))
        {
            DestFolder = DestFolder.LeftChop(1);
        }

        const FString SourceObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        // Source must exist.
        if (!UEditorAssetLibrary::DoesAssetExist(SourceObjectPath))
        {
            OutError = FString::Printf(
                TEXT("move_asset: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        // Compute destination object path: dest_folder + "/" + leaf + "." + leaf.
        const FString LeafName = UCMCPAssetPath::ExtractLeafName(SourceObjectPath);
        const FString DestObjectPath = DestFolder + TEXT("/") + LeafName + TEXT(".") + LeafName;

        // Refuse if an asset with that leaf name already lives in dest folder.
        if (UEditorAssetLibrary::DoesAssetExist(DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("move_asset: dest_exists: an asset already exists at '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        // Perform the move. UE's RenameAsset is documented as "Equivalent to a
        // Move operation"; same call serves both move (folder change) and
        // rename (leaf change). Returns false on SCC checkout failure or other
        // file-level issues.
        if (!UEditorAssetLibrary::RenameAsset(SourceObjectPath, DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("move_asset: rename_failed: UE RenameAsset returned false moving '%s' to '%s' (likely SCC checkout failure)"),
                *SourceObjectPath, *DestObjectPath);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("old_path"), SourceObjectPath);
        Out->SetStringField(TEXT("new_path"), DestObjectPath);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_MoveAsset()
{
    return MakeShared<FHandler_MoveAsset>();
}
