// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_material - list parameter names declared by any UMaterialInterface
// (UMaterial base material or UMaterialInstance). Discovery tool: pair with
// find_assets to find materials, then this to learn what parameters are
// available, then set_mi_parameter to override on a child instance.
//
// Error format: "inspect_material: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_material.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "Materials/MaterialInterface.h"
#include "MaterialEditingLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

namespace
{
    // Convert a TArray<FName> to a sorted JSON string array. Sorting gives
    // stable output across calls — useful for snapshot diffs and LLM
    // pattern-matching.
    TArray<TSharedPtr<FJsonValue>> NamesToSortedJsonArray(TArray<FName> Names)
    {
        Names.Sort([](const FName& A, const FName& B) {
            return A.Compare(B) < 0;
        });
        TArray<TSharedPtr<FJsonValue>> Result;
        Result.Reserve(Names.Num());
        for (const FName& N : Names)
        {
            Result.Add(MakeShared<FJsonValueString>(N.ToString()));
        }
        return Result;
    }
}

class FHandler_InspectMaterial : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_material"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_material: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_material: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // --- load and cast asset --------------------------------------------

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_material: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }
        UMaterialInterface* Material = Cast<UMaterialInterface>(LoadedAsset);
        if (!Material)
        {
            OutError = FString::Printf(
                TEXT("inspect_material: not_a_material: '%s' is a %s, not a UMaterialInterface"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- gather parameter name lists ------------------------------------
        //
        // All four Get<Type>ParameterNames signatures verified at
        // MaterialEditingLibrary.h lines 357 / 361 / 365 / 369.

        TArray<FName> ScalarNames;
        UMaterialEditingLibrary::GetScalarParameterNames(Material, ScalarNames);

        TArray<FName> VectorNames;
        UMaterialEditingLibrary::GetVectorParameterNames(Material, VectorNames);

        TArray<FName> TextureNames;
        UMaterialEditingLibrary::GetTextureParameterNames(Material, TextureNames);

        TArray<FName> StaticSwitchNames;
        UMaterialEditingLibrary::GetStaticSwitchParameterNames(Material, StaticSwitchNames);

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Material->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);
        Out->SetStringField(TEXT("class"), Material->GetClass()->GetName());
        Out->SetArrayField(TEXT("scalar_parameters"), NamesToSortedJsonArray(MoveTemp(ScalarNames)));
        Out->SetArrayField(TEXT("vector_parameters"), NamesToSortedJsonArray(MoveTemp(VectorNames)));
        Out->SetArrayField(TEXT("texture_parameters"), NamesToSortedJsonArray(MoveTemp(TextureNames)));
        Out->SetArrayField(TEXT("static_switch_parameters"), NamesToSortedJsonArray(MoveTemp(StaticSwitchNames)));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectMaterial()
{
    return MakeShared<FHandler_InspectMaterial>();
}
