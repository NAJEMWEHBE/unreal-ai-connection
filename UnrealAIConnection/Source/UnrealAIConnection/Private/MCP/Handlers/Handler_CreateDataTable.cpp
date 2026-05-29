// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_data_table - create a new UDataTable asset bound to a row UScriptStruct.
//
// UE 5.7 surface (verified against engine source):
//   - UDataTableFactory (Editor/UnrealEd/Classes/Factories/DataTableFactory.h:13)
//     with a public `TObjectPtr<const UScriptStruct> Struct` member (line 18) —
//     the row struct the new table's rows conform to.
//   - IAssetTools::CreateAsset(AssetName, PackagePath, AssetClass, Factory)
//     (Developer/AssetTools/Public/IAssetTools.h:348). Passing the factory
//     explicitly (rather than a null pointer) so the row struct is carried
//     through to the created table.
//   - LoadObject<UScriptStruct>(nullptr, *Path) resolves the row struct from a
//     full path, e.g. "/Script/Engine.Vector" or a /Game user struct.
//
// IsMutating() is false: asset creation, not an actor edit on the undo stack.
//
// Error format: "create_data_table: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// editor_unavailable, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"
#include "UObject/UObjectGlobals.h"
#include "Factories/DataTableFactory.h"
#include "Engine/DataTable.h"

class FHandler_CreateDataTable : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_data_table"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("create_data_table: missing_params: request had no params object");
            return nullptr;
        }

        FString PackagePath;
        if (!Params->TryGetStringField(TEXT("path"), PackagePath) || PackagePath.IsEmpty())
        {
            OutError = TEXT("create_data_table: missing_required_field: 'path' is required");
            return nullptr;
        }
        if (!PackagePath.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(
                TEXT("create_data_table: invalid_field: 'path' must start with /Game, got '%s'"),
                *PackagePath);
            return nullptr;
        }
        if (PackagePath.Contains(TEXT("..")) || PackagePath.Contains(TEXT("\\")))
        {
            OutError = FString::Printf(
                TEXT("create_data_table: invalid_field: 'path' must not contain '..' or backslash, got '%s'"),
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
            OutError = TEXT("create_data_table: missing_required_field: 'name' is required");
            return nullptr;
        }

        FString RowStructPath;
        if (!Params->TryGetStringField(TEXT("row_struct"), RowStructPath) || RowStructPath.IsEmpty())
        {
            OutError = TEXT("create_data_table: missing_required_field: 'row_struct' is required");
            return nullptr;
        }

        UScriptStruct* RowStruct = LoadObject<UScriptStruct>(nullptr, *RowStructPath);
        if (!RowStruct)
        {
            OutError = FString::Printf(
                TEXT("create_data_table: invalid_field: row_struct_not_found: no UScriptStruct at '%s'"),
                *RowStructPath);
            return nullptr;
        }
        // A UDataTable requires its row struct to derive from FTableRowBase;
        // anything else produces an unusable table.
        if (!RowStruct->IsChildOf(FTableRowBase::StaticStruct()))
        {
            OutError = FString::Printf(
                TEXT("create_data_table: invalid_field: row_struct '%s' must derive from FTableRowBase"),
                *RowStructPath);
            return nullptr;
        }

        FAssetToolsModule* AssetToolsModule =
            FModuleManager::GetModulePtr<FAssetToolsModule>(TEXT("AssetTools"));
        if (!AssetToolsModule)
        {
            OutError = TEXT("create_data_table: editor_unavailable: AssetTools module not loaded");
            return nullptr;
        }
        IAssetTools& AssetTools = AssetToolsModule->Get();

        UDataTableFactory* Factory = NewObject<UDataTableFactory>();
        Factory->Struct = RowStruct;

        UObject* NewAsset = AssetTools.CreateAsset(
            Name, PackagePath, UDataTable::StaticClass(), Factory);
        if (!NewAsset)
        {
            OutError = FString::Printf(
                TEXT("create_data_table: create_failed: IAssetTools::CreateAsset returned null for '%s/%s' (path may already contain an asset of that name)"),
                *PackagePath, *Name);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), NewAsset->GetPathName());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateDataTable()
{
    return MakeShared<FHandler_CreateDataTable>();
}
