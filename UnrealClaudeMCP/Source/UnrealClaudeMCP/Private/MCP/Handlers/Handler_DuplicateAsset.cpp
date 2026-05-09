// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// duplicate_asset - duplicate an asset to a destination object path.
//
// Error format: "duplicate_asset: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// dest_exists, duplicate_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_DuplicateAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("duplicate_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("duplicate_asset: missing_required_field: 'path' and 'dest_path' are required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("duplicate_asset: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        FString DestPath;
        if (!Params->TryGetStringField(TEXT("dest_path"), DestPath) || DestPath.IsEmpty())
        {
            OutError = TEXT("duplicate_asset: missing_required_field: 'dest_path' is required and must not be empty");
            return nullptr;
        }

        const FString SourceObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString DestObjectPath = UCMCPAssetPath::ToObjectPath(DestPath);

        if (!UEditorAssetLibrary::DoesAssetExist(SourceObjectPath))
        {
            OutError = FString::Printf(
                TEXT("duplicate_asset: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        if (UEditorAssetLibrary::DoesAssetExist(DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("duplicate_asset: dest_exists: an asset already exists at '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        UObject* Duplicated = UEditorAssetLibrary::DuplicateAsset(SourceObjectPath, DestObjectPath);
        if (Duplicated == nullptr)
        {
            OutError = FString::Printf(
                TEXT("duplicate_asset: duplicate_failed: UE DuplicateAsset returned nullptr duplicating '%s' to '%s' (likely SCC checkout failure or destination folder unwritable)"),
                *SourceObjectPath, *DestObjectPath);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("src_path"), SourceObjectPath);
        Out->SetStringField(TEXT("dest_path"), DestObjectPath);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_DuplicateAsset()
{
    return MakeShared<FHandler_DuplicateAsset>();
}

