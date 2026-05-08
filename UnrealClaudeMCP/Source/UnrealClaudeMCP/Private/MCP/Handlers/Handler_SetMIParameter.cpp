// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_mi_parameter - override a scalar/vector/texture parameter on a
// UMaterialInstanceConstant. Single handler with a `type` discriminator
// instead of three per-type handlers — the JSON value shape varies by type.
//
// Type → value shape:
//   "scalar"  → number
//   "vector"  → object { r, g, b, a } (alpha defaults to 1.0)
//   "texture" → string (asset path of a UTexture)
//
// Error format: "set_mi_parameter: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found,
// not_a_material_instance, invalid_parameter_type, invalid_value_shape,
// texture_not_found, parameter_not_applied.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "Materials/MaterialInstanceConstant.h"
#include "Engine/Texture.h"
#include "MaterialEditingLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_SetMIParameter : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_mi_parameter"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("set_mi_parameter: missing_required_field: 'path', 'parameter', 'type', and 'value' are required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("set_mi_parameter: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        FString ParameterName;
        if (!Params->TryGetStringField(TEXT("parameter"), ParameterName) || ParameterName.IsEmpty())
        {
            OutError = TEXT("set_mi_parameter: missing_required_field: 'parameter' is required and must not be empty");
            return nullptr;
        }

        FString TypeStr;
        if (!Params->TryGetStringField(TEXT("type"), TypeStr) || TypeStr.IsEmpty())
        {
            OutError = TEXT("set_mi_parameter: missing_required_field: 'type' is required (one of 'scalar', 'vector', 'texture')");
            return nullptr;
        }

        TSharedPtr<FJsonValue> ValueField = Params->TryGetField(TEXT("value"));
        if (!ValueField.IsValid())
        {
            OutError = TEXT("set_mi_parameter: missing_required_field: 'value' is required");
            return nullptr;
        }

        // --- load and cast asset --------------------------------------------

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("set_mi_parameter: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }
        UMaterialInstanceConstant* MIC = Cast<UMaterialInstanceConstant>(LoadedAsset);
        if (!MIC)
        {
            OutError = FString::Printf(
                TEXT("set_mi_parameter: not_a_material_instance: '%s' is a %s, not a UMaterialInstanceConstant"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- dispatch on type ----------------------------------------------

        const FName ParamFName(*ParameterName);
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), ObjectPath);
        Out->SetStringField(TEXT("parameter"), ParameterName);
        Out->SetStringField(TEXT("type"), TypeStr);

        // Codex review on PR #16 (P1) flagged that
        // SetMaterialInstance*ParameterValue's bool return is unreliable —
        // some UE versions return false even on successful writes, which
        // would falsely trip a parameter_not_applied error and make the tool
        // unusable on those engines.
        //
        // Better fix than codex's "post-verify by reading back": pre-verify
        // the parameter is declared on the MIC's parent chain via
        // Get<Type>ParameterNames BEFORE calling the setter, then call the
        // setter and ignore its bool. This preserves the "param not declared"
        // detection (which was the original intent) without depending on the
        // unreliable bool. The pre-verify call is cheap (single registry
        // walk) and Get<Type>ParameterNames is verified at
        // MaterialEditingLibrary.h:357/361/365.

        if (TypeStr == TEXT("scalar"))
        {
            // Scalar value must be a number.
            if (ValueField->Type != EJson::Number)
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: invalid_value_shape: type='scalar' requires a number 'value', got json type %d"),
                    (int32)ValueField->Type);
                return nullptr;
            }

            TArray<FName> AvailableNames;
            UMaterialEditingLibrary::GetScalarParameterNames(MIC, AvailableNames);
            if (!AvailableNames.Contains(ParamFName))
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: parameter_not_applied: scalar parameter '%s' is not declared on the parent material of '%s'"),
                    *ParameterName, *InputPath);
                return nullptr;
            }

            const float ScalarValue = static_cast<float>(ValueField->AsNumber());
            // Verified at MaterialEditingLibrary.h:301. Bool return ignored
            // intentionally — see header comment above this block.
            UMaterialEditingLibrary::SetMaterialInstanceScalarParameterValue(
                MIC, ParamFName, ScalarValue);
            Out->SetNumberField(TEXT("applied_value"), ScalarValue);
        }
        else if (TypeStr == TEXT("vector"))
        {
            // Vector value must be an object with r/g/b (and optional a, default 1.0).
            if (ValueField->Type != EJson::Object)
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: invalid_value_shape: type='vector' requires an object 'value' with r/g/b/a fields, got json type %d"),
                    (int32)ValueField->Type);
                return nullptr;
            }
            const TSharedPtr<FJsonObject>& ValueObj = ValueField->AsObject();
            double R = 0.0, G = 0.0, B = 0.0, A = 1.0;
            if (!ValueObj->TryGetNumberField(TEXT("r"), R) ||
                !ValueObj->TryGetNumberField(TEXT("g"), G) ||
                !ValueObj->TryGetNumberField(TEXT("b"), B))
            {
                OutError = TEXT("set_mi_parameter: invalid_value_shape: type='vector' requires r, g, b numbers (a optional, default 1.0)");
                return nullptr;
            }
            ValueObj->TryGetNumberField(TEXT("a"), A);  // optional

            TArray<FName> AvailableNames;
            UMaterialEditingLibrary::GetVectorParameterNames(MIC, AvailableNames);
            if (!AvailableNames.Contains(ParamFName))
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: parameter_not_applied: vector parameter '%s' is not declared on the parent material of '%s'"),
                    *ParameterName, *InputPath);
                return nullptr;
            }

            const FLinearColor Color(
                static_cast<float>(R), static_cast<float>(G),
                static_cast<float>(B), static_cast<float>(A));
            // Verified at MaterialEditingLibrary.h:337. Bool return ignored.
            UMaterialEditingLibrary::SetMaterialInstanceVectorParameterValue(
                MIC, ParamFName, Color);
            TSharedRef<FJsonObject> AppliedJson = MakeShared<FJsonObject>();
            AppliedJson->SetNumberField(TEXT("r"), R);
            AppliedJson->SetNumberField(TEXT("g"), G);
            AppliedJson->SetNumberField(TEXT("b"), B);
            AppliedJson->SetNumberField(TEXT("a"), A);
            Out->SetObjectField(TEXT("applied_value"), AppliedJson);
        }
        else if (TypeStr == TEXT("texture"))
        {
            // Texture value must be a string asset path.
            if (ValueField->Type != EJson::String)
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: invalid_value_shape: type='texture' requires a string 'value' (asset path), got json type %d"),
                    (int32)ValueField->Type);
                return nullptr;
            }
            const FString TexturePathInput = ValueField->AsString();
            const FString TextureObjectPath = UCMCPAssetPath::ToObjectPath(TexturePathInput);
            UObject* LoadedTexture = UEditorAssetLibrary::LoadAsset(TextureObjectPath);
            UTexture* Texture = Cast<UTexture>(LoadedTexture);
            if (!Texture)
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: texture_not_found: '%s' did not resolve to a UTexture"),
                    *TexturePathInput);
                return nullptr;
            }

            TArray<FName> AvailableNames;
            UMaterialEditingLibrary::GetTextureParameterNames(MIC, AvailableNames);
            if (!AvailableNames.Contains(ParamFName))
            {
                OutError = FString::Printf(
                    TEXT("set_mi_parameter: parameter_not_applied: texture parameter '%s' is not declared on the parent material of '%s'"),
                    *ParameterName, *InputPath);
                return nullptr;
            }

            // Verified at MaterialEditingLibrary.h:310. Bool return ignored.
            UMaterialEditingLibrary::SetMaterialInstanceTextureParameterValue(
                MIC, ParamFName, Texture);
            Out->SetStringField(TEXT("applied_value"), Texture->GetPathName());
        }
        else
        {
            OutError = FString::Printf(
                TEXT("set_mi_parameter: invalid_parameter_type: 'type' must be 'scalar', 'vector', or 'texture', got '%s'"),
                *TypeStr);
            return nullptr;
        }

        // --- save and return -----------------------------------------------
        //
        // Surface SaveAsset failures explicitly. Same Codex P2 pattern from
        // v0.8.0 PR #15 — the proactive fix on the v0.9.0 branch (commit
        // 461ed17) never reached main because PR #16 was merged before that
        // push, so this is the canonical fix for main.
        if (!UEditorAssetLibrary::SaveAsset(ObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("set_mi_parameter: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). Parameter was applied in memory but not persisted to disk."),
                *ObjectPath);
            return nullptr;
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetMIParameter()
{
    return MakeShared<FHandler_SetMIParameter>();
}
