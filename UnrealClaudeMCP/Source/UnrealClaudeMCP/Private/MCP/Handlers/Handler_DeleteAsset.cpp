// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// delete_asset - delete an asset from the project.
//
// Safety: UE's UEditorAssetLibrary::DeleteAsset is documented as a
// *force-delete* — it does NOT check whether the asset is referenced by
// other packages. Header comment in EditorAssetLibrary.h says verbatim:
// "It doesn't check if the asset has references in other Levels or by
// Actors." Deleting a referenced texture, mesh, or Blueprint can cascade
// into broken references across the project.
//
// We therefore run IAssetRegistry::GetReferencers ourselves before calling
// DeleteAsset and refuse to proceed if any referencers exist. Callers can
// override the safety check by passing force=true.
//
// Error format: "delete_asset: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// has_referencers, delete_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Modules/ModuleManager.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_DeleteAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("delete_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("delete_asset: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("delete_asset: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        bool bForce = false;
        Params->TryGetBoolField(TEXT("force"), bForce);

        // UE's DeleteAsset wants the object-path form ("/Game/.../Foo.Foo").
        // GetReferencers wants the package-name FName ("/Game/.../Foo").
        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);
        const FName PackageName(*PackagePath);

        // Source must exist before we attempt anything.
        if (!UEditorAssetLibrary::DoesAssetExist(ObjectPath))
        {
            OutError = FString::Printf(
                TEXT("delete_asset: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        // Run the safety check unless explicitly bypassed.
        if (!bForce)
        {
            FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
            IAssetRegistry& Registry = Module.Get();

            TArray<FName> OutReferencers;
            Registry.GetReferencers(PackageName, OutReferencers);

            if (OutReferencers.Num() > 0)
            {
                // Build a comma-joined preview of up to 5 referencer names so
                // the caller can see *what* references the asset without
                // dumping a potentially huge list into the error message.
                const int32 PreviewCount = FMath::Min(OutReferencers.Num(), 5);
                FString Preview;
                for (int32 i = 0; i < PreviewCount; ++i)
                {
                    if (i > 0) { Preview += TEXT(", "); }
                    Preview += OutReferencers[i].ToString();
                }
                if (OutReferencers.Num() > PreviewCount)
                {
                    Preview += FString::Printf(TEXT(", and %d more"), OutReferencers.Num() - PreviewCount);
                }

                OutError = FString::Printf(
                    TEXT("delete_asset: has_referencers: '%s' is referenced by %d package(s): %s. "
                         "Set force=true to delete anyway."),
                    *InputPath, OutReferencers.Num(), *Preview);
                return nullptr;
            }
        }

        // Safe (or forced) — proceed with the delete.
        if (!UEditorAssetLibrary::DeleteAsset(ObjectPath))
        {
            OutError = FString::Printf(
                TEXT("delete_asset: delete_failed: UE DeleteAsset returned false for '%s' "
                     "(possible causes: file lock, SCC checkout failure, asset open in editor)"),
                *ObjectPath);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("deleted_path"), ObjectPath);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_DeleteAsset()
{
    return MakeShared<FHandler_DeleteAsset>();
}
