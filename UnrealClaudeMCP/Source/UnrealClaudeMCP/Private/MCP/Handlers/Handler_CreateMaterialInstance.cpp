// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_material_instance - create a UMaterialInstanceConstant asset and
// set its parent to an existing UMaterial or UMaterialInstance.
//
// Mirrors the create_sequence (v0.8.0) factory pattern: IAssetTools::CreateAsset
// with the destination class auto-resolves the factory (UE matches
// UMaterialInstanceConstantFactoryNew by class).
//
// Error format: "create_material_instance: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, invalid_path, invalid_asset_name,
// parent_not_found, parent_not_a_material, dest_exists, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Materials/MaterialInterface.h"
#include "MaterialEditingLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_CreateMaterialInstance : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_material_instance"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("create_material_instance: missing_required_field: 'parent_path', 'path' and 'name' are required");
            return nullptr;
        }

        FString ParentPath;
        if (!Params->TryGetStringField(TEXT("parent_path"), ParentPath) || ParentPath.IsEmpty())
        {
            OutError = TEXT("create_material_instance: missing_required_field: 'parent_path' is required and must not be empty");
            return nullptr;
        }

        FString DestPath;
        if (!Params->TryGetStringField(TEXT("path"), DestPath) || DestPath.IsEmpty())
        {
            OutError = TEXT("create_material_instance: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }
        if (!DestPath.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: invalid_path: 'path' must start with /Game/, got '%s'"),
                *DestPath);
            return nullptr;
        }
        if (DestPath.EndsWith(TEXT("/")))
        {
            DestPath = DestPath.LeftChop(1);
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("create_material_instance: missing_required_field: 'name' is required and must not be empty");
            return nullptr;
        }
        if (!UCMCPAssetPath::IsValidLeafName(Name))
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: invalid_asset_name: 'name' must be a non-empty string with no '/' or '.', got '%s'"),
                *Name);
            return nullptr;
        }

        // --- load parent material ------------------------------------------

        const FString ParentObjectPath = UCMCPAssetPath::ToObjectPath(ParentPath);
        UObject* LoadedParent = UEditorAssetLibrary::LoadAsset(ParentObjectPath);
        if (!LoadedParent)
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: parent_not_found: '%s' is not in the asset registry"),
                *ParentPath);
            return nullptr;
        }
        UMaterialInterface* Parent = Cast<UMaterialInterface>(LoadedParent);
        if (!Parent)
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: parent_not_a_material: '%s' is a %s, not a UMaterialInterface"),
                *ParentPath, *LoadedParent->GetClass()->GetName());
            return nullptr;
        }

        // --- check destination ----------------------------------------------

        const FString DestObjectPath = DestPath + TEXT("/") + Name + TEXT(".") + Name;
        if (UEditorAssetLibrary::DoesAssetExist(DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: dest_exists: an asset already exists at '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        // --- create the asset -----------------------------------------------
        //
        // IAssetTools::CreateAsset auto-resolves UMaterialInstanceConstantFactoryNew
        // when given UMaterialInstanceConstant::StaticClass() and a null factory
        // pointer. Same pattern as v0.8.0 create_sequence handler.
        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        IAssetTools& AssetTools = AssetToolsModule.Get();

        UObject* NewAsset = AssetTools.CreateAsset(
            Name, DestPath, UMaterialInstanceConstant::StaticClass(), nullptr);
        if (!NewAsset)
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: create_failed: UAssetTools::CreateAsset returned null for '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        UMaterialInstanceConstant* MIC = Cast<UMaterialInstanceConstant>(NewAsset);
        if (!MIC)
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: create_failed: created asset is %s, not UMaterialInstanceConstant"),
                *NewAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- wire parent + save --------------------------------------------
        //
        // SetMaterialInstanceParent verified at MaterialEditingLibrary.h:288.
        UMaterialEditingLibrary::SetMaterialInstanceParent(MIC, Parent);

        // Surface SaveAsset failures explicitly. Same Codex P2 pattern from
        // v0.8.0 PR #15 — the proactive fix on the v0.9.0 branch (commit
        // 461ed17) never reached main because PR #16 was merged before that
        // push, so this is the canonical fix for main.
        if (!UEditorAssetLibrary::SaveAsset(DestObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("create_material_instance: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file)"),
                *DestObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("asset_path"), DestObjectPath);
        Out->SetStringField(TEXT("package_path"), DestPath + TEXT("/") + Name);
        Out->SetStringField(TEXT("parent_path"), Parent->GetPathName());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateMaterialInstance()
{
    return MakeShared<FHandler_CreateMaterialInstance>();
}
