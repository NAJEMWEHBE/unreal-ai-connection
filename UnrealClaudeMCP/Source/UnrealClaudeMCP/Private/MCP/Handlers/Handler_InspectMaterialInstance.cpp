// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_material_instance - read a UMaterialInstanceConstant's parent and
// currently-overridden parameter values. Complements inspect_material (which
// lists declared params on the parent material).
//
// Only OVERRIDDEN parameters appear in the output maps. Parameters inherited
// unchanged from the parent are not listed. Use inspect_material on the
// parent to see the full set of declared parameter names.
//
// Error format: "inspect_material_instance: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// not_a_material_instance.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Materials/MaterialInstance.h"
#include "Materials/MaterialInterface.h"
#include "Materials/MaterialParameters.h"
#include "Engine/Texture.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_InspectMaterialInstance : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_material_instance"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_material_instance: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_material_instance: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // --- load and cast asset --------------------------------------------

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_material_instance: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }
        UMaterialInstanceConstant* MIC = Cast<UMaterialInstanceConstant>(LoadedAsset);
        if (!MIC)
        {
            OutError = FString::Printf(
                TEXT("inspect_material_instance: not_a_material_instance: '%s' is a %s, not a UMaterialInstanceConstant"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- build the result JSON ------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), MIC->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);

        // Parent: TObjectPtr<UMaterialInterface> at MaterialInstance.h:647.
        // Stringify via GetPathName when present; empty string when parented
        // to nothing (rare but possible for transient or partially-init MIs).
        if (MIC->Parent.Get())
        {
            Out->SetStringField(TEXT("parent_path"), MIC->Parent->GetPathName());
        }
        else
        {
            Out->SetStringField(TEXT("parent_path"), TEXT(""));
        }

        // Scalar overrides. ScalarParameterValues at MaterialInstance.h:750;
        // FScalarParameterValue at MaterialInstance.h:63 with ParameterInfo
        // (line 76) and ParameterValue (line 79). FMaterialParameterInfo
        // exposes the parameter name as a public FName field `Name` at
        // MaterialParameters.h:32 (no accessor method).
        TSharedRef<FJsonObject> ScalarJson = MakeShared<FJsonObject>();
        for (const FScalarParameterValue& SV : MIC->ScalarParameterValues)
        {
            ScalarJson->SetNumberField(SV.ParameterInfo.Name.ToString(),
                static_cast<double>(SV.ParameterValue));
        }
        Out->SetObjectField(TEXT("scalar_overrides"), ScalarJson);

        // Vector overrides. FLinearColor → {r, g, b, a}.
        TSharedRef<FJsonObject> VectorJson = MakeShared<FJsonObject>();
        for (const FVectorParameterValue& VV : MIC->VectorParameterValues)
        {
            TSharedRef<FJsonObject> ColorJson = MakeShared<FJsonObject>();
            ColorJson->SetNumberField(TEXT("r"), VV.ParameterValue.R);
            ColorJson->SetNumberField(TEXT("g"), VV.ParameterValue.G);
            ColorJson->SetNumberField(TEXT("b"), VV.ParameterValue.B);
            ColorJson->SetNumberField(TEXT("a"), VV.ParameterValue.A);
            VectorJson->SetObjectField(VV.ParameterInfo.Name.ToString(), ColorJson);
        }
        Out->SetObjectField(TEXT("vector_overrides"), VectorJson);

        // Texture overrides. ParameterValue is TObjectPtr<UTexture> at
        // MaterialInstance.h:240; emit asset path or empty string if null.
        TSharedRef<FJsonObject> TextureJson = MakeShared<FJsonObject>();
        for (const FTextureParameterValue& TV : MIC->TextureParameterValues)
        {
            const FString AssetPath = TV.ParameterValue
                ? TV.ParameterValue->GetPathName()
                : FString();
            TextureJson->SetStringField(TV.ParameterInfo.Name.ToString(), AssetPath);
        }
        Out->SetObjectField(TEXT("texture_overrides"), TextureJson);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectMaterialInstance()
{
    return MakeShared<FHandler_InspectMaterialInstance>();
}
