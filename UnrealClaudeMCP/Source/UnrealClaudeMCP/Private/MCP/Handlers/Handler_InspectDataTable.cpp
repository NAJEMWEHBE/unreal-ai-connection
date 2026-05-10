// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_data_table - read structural properties of a UDataTable asset:
// the RowStruct identity, every row name (sorted), and the per-property
// name+type of the row struct so callers can introspect "what shape are
// my rows?" without loading + walking row payloads themselves.
//
// UE 5.7 surface used (header:line citations for reviewer traceability):
//   DataTable.h:79   -- class UDataTable : UObject
//   DataTable.h:94   -- TObjectPtr<UScriptStruct> RowStruct
//   DataTable.h:98   -- TMap<FName, uint8*> RowMap
//   DataTable.h:110  -- const TMap<FName, uint8*>& GetRowMap() const
//   DataTable.h:113  -- const UScriptStruct* GetRowStruct() const
//   DataTable.h:120  -- uint8 bStripFromClientBuilds : 1
//   DataTable.h:124  -- uint8 bIgnoreExtraFields : 1
//   DataTable.h:128  -- uint8 bIgnoreMissingFields : 1
//   DataTable.h:136  -- FString ImportKeyField
//
// Property iteration:
//   TFieldIterator<FProperty>(RowStruct, EFieldIterationFlags::None)
//   -- the explicit "no super fields" form, surfacing only the user-
//   declared row columns rather than every reachable FProperty on the
//   UScriptStruct hierarchy. Matches the row author's mental model
//   ("the columns of my table") rather than the C++ inheritance model.
//
// Error format: "inspect_data_table: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_data_table

#include "Engine/DataTable.h"
#include "UObject/UnrealType.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

class FHandler_InspectDataTable : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_data_table"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_data_table: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_data_table: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_data_table: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        UDataTable* DataTable = Cast<UDataTable>(Loaded);
        if (!DataTable)
        {
            OutError = FString::Printf(
                TEXT("inspect_data_table: not_a_data_table: '%s' is a %s, not a UDataTable"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- row struct identity (null-guarded) ---

        const UScriptStruct* RowStruct = DataTable->GetRowStruct();

        // --- sorted row names (TMap iteration order is unspecified; sort for
        //     stable cross-call output -- same convention as
        //     inspect_widget_blueprint::inherited_slots_with_content) ---

        const TMap<FName, uint8*>& RowMap = DataTable->GetRowMap();
        TArray<FString> SortedRowNames;
        SortedRowNames.Reserve(RowMap.Num());
        for (const TPair<FName, uint8*>& Row : RowMap)
        {
            SortedRowNames.Add(Row.Key.ToString());
        }
        SortedRowNames.Sort();

        TArray<TSharedPtr<FJsonValue>> RowsArray;
        RowsArray.Reserve(SortedRowNames.Num());
        for (const FString& RowName : SortedRowNames)
        {
            RowsArray.Add(MakeShared<FJsonValueString>(RowName));
        }

        // --- per-property name + type (only when RowStruct is non-null) ---

        TArray<TSharedPtr<FJsonValue>> RowPropertiesArray;
        if (RowStruct)
        {
            // EFieldIterationFlags::None -> skip inherited base fields; emit
            // only the user-declared row struct fields. Default IncludeSuper
            // would surface UObject/UScriptStruct base properties that aren't
            // the row's authored "columns".
            for (TFieldIterator<FProperty> It(RowStruct, EFieldIterationFlags::None); It; ++It)
            {
                const FProperty* Prop = *It;
                if (!Prop) { continue; }

                TSharedPtr<FJsonObject> PropObj = MakeShared<FJsonObject>();
                PropObj->SetStringField(TEXT("name"), Prop->GetName());
                PropObj->SetStringField(TEXT("type"), Prop->GetCPPType());
                RowPropertiesArray.Add(MakeShared<FJsonValueObject>(PropObj));
            }
        }

        // --- response ---

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), DataTable->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);

        if (RowStruct)
        {
            Out->SetStringField(TEXT("row_struct"), RowStruct->GetPathName());
            Out->SetStringField(TEXT("row_struct_name"), RowStruct->GetName());
        }

        Out->SetNumberField(TEXT("row_count"), static_cast<double>(RowMap.Num()));
        Out->SetArrayField(TEXT("rows"), RowsArray);
        Out->SetNumberField(TEXT("row_property_count"), static_cast<double>(RowPropertiesArray.Num()));
        Out->SetArrayField(TEXT("row_properties"), RowPropertiesArray);

        Out->SetBoolField(TEXT("strip_from_client_builds"), DataTable->bStripFromClientBuilds);
        Out->SetBoolField(TEXT("ignore_extra_fields"), DataTable->bIgnoreExtraFields);
        Out->SetBoolField(TEXT("ignore_missing_fields"), DataTable->bIgnoreMissingFields);

        if (!DataTable->ImportKeyField.IsEmpty())
        {
            Out->SetStringField(TEXT("import_key_field"), DataTable->ImportKeyField);
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectDataTable()
{
    return MakeShared<FHandler_InspectDataTable>();
}
