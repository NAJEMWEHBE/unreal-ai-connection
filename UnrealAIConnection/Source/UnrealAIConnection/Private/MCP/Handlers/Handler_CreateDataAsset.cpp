// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_data_asset - create a new UDataAsset (or subclass) asset.
//
// UE 5.7 surface (verified against engine source):
//   - UDataAssetFactory (Editor/UnrealEd/Classes/Factories/DataAssetFactory.h:14)
//     with a public `TSubclassOf<UDataAsset> DataAssetClass` member (line 19) —
//     the concrete class to instantiate.
//   - IAssetTools::CreateAsset(AssetName, PackagePath, AssetClass, Factory)
//     (Developer/AssetTools/Public/IAssetTools.h:348). The destination class is
//     the resolved UDataAsset subclass; the factory carries the same class.
//   - LoadClass<UDataAsset>(nullptr, *Path) resolves a UDataAsset subclass from
//     a full path, e.g. "/Script/Engine.PrimaryDataAsset" or a /Game BP-based
//     DataAsset class. Abstract classes are rejected (cannot be instantiated).
//
// IsMutating() is false: asset creation, not an actor edit on the undo stack.
//
// Error format: "create_data_asset: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// editor_unavailable, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"
#include "UObject/UObjectGlobals.h"
#include "Factories/DataAssetFactory.h"
#include "Engine/DataAsset.h"

class FHandler_CreateDataAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_data_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("create_data_asset: missing_params: request had no params object");
            return nullptr;
        }

        FString PackagePath;
        if (!Params->TryGetStringField(TEXT("path"), PackagePath) || PackagePath.IsEmpty())
        {
            OutError = TEXT("create_data_asset: missing_required_field: 'path' is required");
            return nullptr;
        }
        if (!PackagePath.StartsWith(TEXT("/Game")))
        {
            OutError = FString::Printf(
                TEXT("create_data_asset: invalid_field: 'path' must start with /Game, got '%s'"),
                *PackagePath);
            return nullptr;
        }
        if (PackagePath.Contains(TEXT("..")) || PackagePath.Contains(TEXT("\\")))
        {
            OutError = FString::Printf(
                TEXT("create_data_asset: invalid_field: 'path' must not contain '..' or backslash, got '%s'"),
                *PackagePath);
            return nullptr;
        }
        // Trim a trailing slash so AssetTools sees a clean folder path.
        if (PackagePath.EndsWith(TEXT("/")))
        {
            PackagePath = PackagePath.LeftChop(1);
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("create_data_asset: missing_required_field: 'name' is required");
            return nullptr;
        }

        FString ClassPath;
        if (!Params->TryGetStringField(TEXT("class_path"), ClassPath) || ClassPath.IsEmpty())
        {
            OutError = TEXT("create_data_asset: missing_required_field: 'class_path' is required");
            return nullptr;
        }

        UClass* DataAssetClass = LoadClass<UDataAsset>(nullptr, *ClassPath);
        if (!DataAssetClass)
        {
            OutError = FString::Printf(
                TEXT("create_data_asset: invalid_field: class_not_found: no UDataAsset subclass at '%s'"),
                *ClassPath);
            return nullptr;
        }
        if (DataAssetClass->HasAnyClassFlags(CLASS_Abstract))
        {
            OutError = FString::Printf(
                TEXT("create_data_asset: invalid_field: class_is_abstract: '%s' is abstract and cannot be instantiated"),
                *ClassPath);
            return nullptr;
        }

        FAssetToolsModule* AssetToolsModule =
            FModuleManager::GetModulePtr<FAssetToolsModule>(TEXT("AssetTools"));
        if (!AssetToolsModule)
        {
            OutError = TEXT("create_data_asset: editor_unavailable: AssetTools module not loaded");
            return nullptr;
        }
        IAssetTools& AssetTools = AssetToolsModule->Get();

        UDataAssetFactory* Factory = NewObject<UDataAssetFactory>();
        Factory->DataAssetClass = DataAssetClass;

        UObject* NewAsset = AssetTools.CreateAsset(
            Name, PackagePath, DataAssetClass, Factory);
        if (!NewAsset)
        {
            OutError = FString::Printf(
                TEXT("create_data_asset: create_failed: IAssetTools::CreateAsset returned null for '%s/%s' (path may already contain an asset of that name)"),
                *PackagePath, *Name);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), NewAsset->GetPathName());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateDataAsset()
{
    return MakeShared<FHandler_CreateDataAsset>();
}
