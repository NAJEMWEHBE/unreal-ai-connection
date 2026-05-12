// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// list_levels - enumerate all UWorld assets (levels) in the project's
// /Game tree. Closes the gap where load_level_by_path required the caller
// to already know the package path — there was no inventory tool.
//
// Pure asset-registry query (IAssetRegistry::GetAssetsByClass). Fast,
// game-thread safe, no UE delegate / threading concerns.
//
// Error format: "list_levels: <error_code>: <human-readable detail>".
// Stable error codes: invalid_path_filter (when 'path_under' is supplied
// but does not start with /Game/ or /Engine/). Handler never returns
// errors for the empty-result case — it returns levels:[] with count=0.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/ARFilter.h"
#include "AssetRegistry/AssetData.h"
#include "Modules/ModuleManager.h"
#include "Engine/World.h"

class FHandler_ListLevels : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("list_levels"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // Optional path filter. Defaults to /Game/ so engine-content levels
        // don't pollute project-level inventories.
        FString PathUnder = TEXT("/Game/");
        if (Params.IsValid())
        {
            Params->TryGetStringField(TEXT("path_under"), PathUnder);
        }
        if (!PathUnder.StartsWith(TEXT("/Game/")) && !PathUnder.StartsWith(TEXT("/Engine/")))
        {
            OutError = FString::Printf(
                TEXT("list_levels: invalid_path_filter: 'path_under' must start with /Game/ or /Engine/, got '%s'"),
                *PathUnder);
            return nullptr;
        }

        // Optional name-substring filter (case-insensitive).
        FString NameContains;
        if (Params.IsValid())
        {
            Params->TryGetStringField(TEXT("name_contains"), NameContains);
        }

        FAssetRegistryModule& Mod = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
        IAssetRegistry& Reg = Mod.Get();

        // UWorld is the canonical level asset class. The asset registry
        // also tracks ULevelStreaming / UWorldComposition but those are
        // metadata, not loadable levels — we restrict to UWorld so the
        // list matches load_level_by_path's input domain.
        FARFilter Filter;
        Filter.bRecursivePaths = true;
        Filter.PackagePaths.Add(FName(*PathUnder));
        Filter.ClassPaths.Add(UWorld::StaticClass()->GetClassPathName());

        TArray<FAssetData> Assets;
        Reg.GetAssets(Filter, Assets);

        TArray<TSharedPtr<FJsonValue>> LevelsArr;
        LevelsArr.Reserve(Assets.Num());

        for (const FAssetData& Asset : Assets)
        {
            const FString AssetName = Asset.AssetName.ToString();
            if (!NameContains.IsEmpty() && !AssetName.Contains(NameContains))
            {
                continue;
            }
            const TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
            Entry->SetStringField(TEXT("name"), AssetName);
            Entry->SetStringField(TEXT("package_path"), Asset.PackageName.ToString());
            Entry->SetStringField(TEXT("object_path"), Asset.GetObjectPathString());
            LevelsArr.Add(MakeShared<FJsonValueObject>(Entry));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("count"), LevelsArr.Num());
        Out->SetArrayField(TEXT("levels"), LevelsArr);
        Out->SetStringField(TEXT("path_under"), PathUnder);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ListLevels()
{
    return MakeShared<FHandler_ListLevels>();
}
