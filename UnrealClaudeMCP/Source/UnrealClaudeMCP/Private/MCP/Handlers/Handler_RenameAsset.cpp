// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// rename_asset - change an asset's leaf name; folder unchanged. UE creates a
// redirector at the old name so existing references continue to resolve.
//
// Error format: "rename_asset: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// invalid_asset_name, dest_exists, rename_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_RenameAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("rename_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("rename_asset: missing_required_field: 'path' and 'new_name' are required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("rename_asset: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        FString NewName;
        if (!Params->TryGetStringField(TEXT("new_name"), NewName) || NewName.IsEmpty())
        {
            OutError = TEXT("rename_asset: missing_required_field: 'new_name' is required and must not be empty");
            return nullptr;
        }

        // Validate the new leaf name has no path separators.
        if (!UCMCPAssetPath::IsValidLeafName(NewName))
        {
            OutError = FString::Printf(
                TEXT("rename_asset: invalid_asset_name: 'new_name' must be a non-empty string with no '/' or '.', got '%s'"),
                *NewName);
            return nullptr;
        }

        const FString SourceObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        if (!UEditorAssetLibrary::DoesAssetExist(SourceObjectPath))
        {
            OutError = FString::Printf(
                TEXT("rename_asset: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        // Compute destination object path: source_folder + "/" + new_name + "." + new_name.
        const FString SourceFolder = UCMCPAssetPath::ExtractFolder(SourceObjectPath);
        const FString DestObjectPath = SourceFolder + TEXT("/") + NewName + TEXT(".") + NewName;

        if (UEditorAssetLibrary::DoesAssetExist(DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("rename_asset: dest_exists: an asset already exists at '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        if (!UEditorAssetLibrary::RenameAsset(SourceObjectPath, DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("rename_asset: rename_failed: UE RenameAsset returned false renaming '%s' to '%s' (likely SCC checkout failure)"),
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

TSharedRef<IUCMCPHandler> Make_Handler_RenameAsset()
{
    return MakeShared<FHandler_RenameAsset>();
}
