// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_asset - read every fact the asset registry knows about a single
// asset: class, all registry tags, dependency packages, referencer packages,
// and on-disk file size.
//
// Error format: "inspect_asset: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/AssetData.h"
#include "Modules/ModuleManager.h"
#include "Misc/PackageName.h"
#include "HAL/FileManager.h"
#include "UObject/SoftObjectPath.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_InspectAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_asset: missing_required_field: 'path' is required but no params were provided");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_asset: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // --- normalize path forms -------------------------------------------

        // ObjectPath has a ".Name" suffix; PackagePath does not. The asset
        // registry's GetAssetByObjectPath wants the FSoftObjectPath form
        // (with suffix); GetReferencers/GetDependencies want a package FName.
        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);
        const FName PackageName(*PackagePath);

        // --- look up in registry --------------------------------------------

        FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
        IAssetRegistry& Registry = Module.Get();

        const FAssetData Data = Registry.GetAssetByObjectPath(FSoftObjectPath(ObjectPath));
        if (!Data.IsValid())
        {
            OutError = FString::Printf(
                TEXT("inspect_asset: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        // --- build the result JSON ------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Data.AssetName.ToString());
        Out->SetStringField(TEXT("package_path"), Data.PackageName.ToString());
        Out->SetStringField(TEXT("asset_path"), ObjectPath);
        Out->SetStringField(TEXT("class"), Data.AssetClassPath.GetAssetName().ToString());
        Out->SetStringField(TEXT("class_path"), Data.AssetClassPath.ToString());

        // tags: stringify every entry via FAssetTagValueRef::AsString().
        TSharedRef<FJsonObject> TagsJson = MakeShared<FJsonObject>();
        Data.TagsAndValues.ForEach([&TagsJson](const TPair<FName, FAssetTagValueRef>& TagPair) {
            TagsJson->SetStringField(TagPair.Key.ToString(), TagPair.Value.AsString());
        });
        Out->SetObjectField(TEXT("tags"), TagsJson);

        // dependencies: package paths this asset hard-references.
        TArray<FName> OutDeps;
        Registry.GetDependencies(PackageName, OutDeps);
        TArray<TSharedPtr<FJsonValue>> DepsArray;
        DepsArray.Reserve(OutDeps.Num());
        for (const FName& DepName : OutDeps)
        {
            // Drop trivially-self-referencing entries (rare but possible).
            if (DepName == PackageName) { continue; }
            DepsArray.Add(MakeShared<FJsonValueString>(DepName.ToString()));
        }
        Out->SetArrayField(TEXT("dependencies"), DepsArray);

        // referencers: package paths that hard-reference this asset.
        TArray<FName> OutRefs;
        Registry.GetReferencers(PackageName, OutRefs);
        TArray<TSharedPtr<FJsonValue>> RefsArray;
        RefsArray.Reserve(OutRefs.Num());
        for (const FName& RefName : OutRefs)
        {
            if (RefName == PackageName) { continue; }
            RefsArray.Add(MakeShared<FJsonValueString>(RefName.ToString()));
        }
        Out->SetArrayField(TEXT("referencers"), RefsArray);

        // package_size_bytes: on-disk size of the package file. UE stores assets
        // with two extensions: most asset types are .uasset, but UWorld packages
        // (levels) are .umap. DoesPackageExist resolves to the right one
        // automatically and fills OutFilename only when the package is on disk;
        // transient/in-memory packages return false and we surface JSON null.
        // (Caught by Codex review on v0.7.0 PR #12 — P2 finding for the .umap case.)
        FString DiskFilename;
        int64 SizeBytes = -1;
        if (FPackageName::DoesPackageExist(PackagePath, &DiskFilename))
        {
            SizeBytes = IFileManager::Get().FileSize(*DiskFilename);
        }
        if (SizeBytes >= 0)
        {
            Out->SetNumberField(TEXT("package_size_bytes"), static_cast<double>(SizeBytes));
        }
        else
        {
            // FJsonValueNull doesn't have a SetField helper; build it manually.
            Out->SetField(TEXT("package_size_bytes"), MakeShared<FJsonValueNull>());
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectAsset()
{
    return MakeShared<FHandler_InspectAsset>();
}
