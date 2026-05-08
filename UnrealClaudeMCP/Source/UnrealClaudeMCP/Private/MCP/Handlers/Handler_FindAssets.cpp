// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// find_assets - query the asset registry by class + path + name substring.
//
// Error format: "find_assets: <error_code>: <human-readable detail>".
// Stable error codes (parseable by clients): missing_params,
// missing_required_field, invalid_class_path, invalid_path_filter.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/ARFilter.h"
#include "Modules/ModuleManager.h"

class FHandler_FindAssets : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("find_assets"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("find_assets: missing_params: request had no params object");
            return nullptr;
        }

        FString ClassPath;
        if (!Params->TryGetStringField(TEXT("class_path"), ClassPath) || ClassPath.IsEmpty())
        {
            OutError = TEXT("find_assets: missing_required_field: 'class_path' is required and must be non-empty");
            return nullptr;
        }

        FString PathUnder = TEXT("/Game/");
        Params->TryGetStringField(TEXT("path_under"), PathUnder);
        if (!PathUnder.StartsWith(TEXT("/Game/")) && !PathUnder.StartsWith(TEXT("/Engine/")))
        {
            OutError = FString::Printf(
                TEXT("find_assets: invalid_path_filter: 'path_under' must start with /Game/ or /Engine/, got '%s'"),
                *PathUnder);
            return nullptr;
        }

        FString NameContains;
        Params->TryGetStringField(TEXT("name_contains"), NameContains);

        int32 Limit = 100;
        Params->TryGetNumberField(TEXT("limit"), Limit);
        Limit = FMath::Clamp(Limit, 1, 500);

        // Build the asset registry filter
        FAssetRegistryModule& Module = FModuleManager::LoadModuleChecked<FAssetRegistryModule>("AssetRegistry");
        IAssetRegistry& Registry = Module.Get();

        FARFilter Filter;
        Filter.ClassPaths.Add(FTopLevelAssetPath(ClassPath));
        Filter.PackagePaths.Add(FName(*PathUnder));
        Filter.bRecursivePaths = true;

        TArray<FAssetData> Found;
        Registry.GetAssets(Filter, Found);

        if (Found.Num() == 0 && Filter.ClassPaths.Num() == 1)
        {
            // Validate that the class path actually resolved (otherwise it's an
            // invalid class, not "no matches").
            UClass* Resolved = LoadClass<UObject>(nullptr, *ClassPath);
            if (!Resolved)
            {
                OutError = FString::Printf(
                    TEXT("find_assets: invalid_class_path: '%s' did not resolve to a UClass"),
                    *ClassPath);
                return nullptr;
            }
        }

        // Apply the name_contains filter in-memory (case-insensitive)
        TArray<FAssetData> NameFiltered;
        if (!NameContains.IsEmpty())
        {
            for (const FAssetData& Data : Found)
            {
                if (Data.AssetName.ToString().Contains(NameContains, ESearchCase::IgnoreCase))
                {
                    NameFiltered.Add(Data);
                }
            }
        }
        else
        {
            NameFiltered = Found;
        }

        // Sort by asset name for stable output
        NameFiltered.Sort([](const FAssetData& A, const FAssetData& B) {
            return A.AssetName.LexicalLess(B.AssetName);
        });

        const int32 Matched = NameFiltered.Num();
        const int32 Returned = FMath::Min(Matched, Limit);

        TArray<TSharedPtr<FJsonValue>> JsonAssets;
        JsonAssets.Reserve(Returned);
        for (int32 i = 0; i < Returned; ++i)
        {
            const FAssetData& Data = NameFiltered[i];
            TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
            A->SetStringField(TEXT("name"), Data.AssetName.ToString());
            A->SetStringField(TEXT("package_path"), Data.PackageName.ToString());
            A->SetStringField(TEXT("class"), Data.AssetClassPath.GetAssetName().ToString());
            JsonAssets.Add(MakeShared<FJsonValueObject>(A));
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetNumberField(TEXT("matched"), Matched);
        Result->SetNumberField(TEXT("returned"), Returned);
        Result->SetArrayField(TEXT("assets"), JsonAssets);
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_FindAssets()
{
    return MakeShared<FHandler_FindAssets>();
}
