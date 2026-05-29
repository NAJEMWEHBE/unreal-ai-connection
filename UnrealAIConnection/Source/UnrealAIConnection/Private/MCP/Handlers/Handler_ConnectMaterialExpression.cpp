// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// connect_material_expression - wire a material expression's output to either a
// material property input (BaseColor, Metallic, ...) or another expression's
// input (Lane 4: material graph authoring).
//
// UE 5.7 surface (verified against engine source):
//   - UMaterialEditingLibrary::ConnectMaterialProperty(UMaterialExpression* From,
//     FString FromOutputName, EMaterialProperty Property) -> bool
//     (Editor/MaterialEditor/Public/MaterialEditingLibrary.h:152).
//   - UMaterialEditingLibrary::ConnectMaterialExpressions(UMaterialExpression* From,
//     FString FromOutputName, UMaterialExpression* To, FString ToInputName) -> bool
//     (line 162).
//   - UMaterialEditingLibrary::RecompileMaterial(UMaterial*) (line 168).
//   - UMaterial::GetExpressions() -> TConstArrayView<TObjectPtr<UMaterialExpression>>
//     (Runtime/Engine/Public/Materials/Material.h:1412, WITH_EDITORONLY_DATA — fine
//     in this editor-only module). Used to look up nodes by GetName().
//   - EMaterialProperty (MP_BaseColor, MP_Metallic, ...) lives in
//     Runtime/Engine/Public/SceneTypes.h:147 (ships in the Engine module).
//
// IsMutating() is false: edits asset content, not level/actor undo state.
//
// Error format: "connect_material_expression: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, material_not_found,
// from_expression_not_found, to_expression_not_found, invalid_field
// (bad_to_format / unknown_property), connect_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "MaterialEditingLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"
#include "SceneTypes.h"
#include "UObject/UObjectGlobals.h"

namespace
{
    // Find an expression in the material's graph by its UObject name.
    UMaterialExpression* FindExpressionByName(UMaterial* Mat, const FString& Name)
    {
        for (UMaterialExpression* Expr : Mat->GetExpressions())
        {
            if (Expr && Expr->GetName() == Name)
            {
                return Expr;
            }
        }
        return nullptr;
    }

    // Map a user-facing material-output name to its EMaterialProperty value.
    // Returns true and writes OutProp on a hit; false on an unknown name.
    bool MapMaterialProperty(const FString& Name, EMaterialProperty& OutProp)
    {
        if (Name.Equals(TEXT("BaseColor"), ESearchCase::IgnoreCase))         { OutProp = MP_BaseColor; return true; }
        if (Name.Equals(TEXT("Metallic"), ESearchCase::IgnoreCase))          { OutProp = MP_Metallic; return true; }
        if (Name.Equals(TEXT("Specular"), ESearchCase::IgnoreCase))          { OutProp = MP_Specular; return true; }
        if (Name.Equals(TEXT("Roughness"), ESearchCase::IgnoreCase))         { OutProp = MP_Roughness; return true; }
        if (Name.Equals(TEXT("EmissiveColor"), ESearchCase::IgnoreCase))     { OutProp = MP_EmissiveColor; return true; }
        if (Name.Equals(TEXT("Opacity"), ESearchCase::IgnoreCase))           { OutProp = MP_Opacity; return true; }
        if (Name.Equals(TEXT("OpacityMask"), ESearchCase::IgnoreCase))       { OutProp = MP_OpacityMask; return true; }
        if (Name.Equals(TEXT("Normal"), ESearchCase::IgnoreCase))            { OutProp = MP_Normal; return true; }
        if (Name.Equals(TEXT("AmbientOcclusion"), ESearchCase::IgnoreCase))  { OutProp = MP_AmbientOcclusion; return true; }
        if (Name.Equals(TEXT("WorldPositionOffset"), ESearchCase::IgnoreCase)) { OutProp = MP_WorldPositionOffset; return true; }
        return false;
    }
}

class FHandler_ConnectMaterialExpression : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("connect_material_expression"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("connect_material_expression: missing_params: request had no params object");
            return nullptr;
        }

        FString MaterialPath;
        if (!Params->TryGetStringField(TEXT("material"), MaterialPath) || MaterialPath.IsEmpty())
        {
            OutError = TEXT("connect_material_expression: missing_required_field: 'material' is required");
            return nullptr;
        }

        FString FromExprName;
        if (!Params->TryGetStringField(TEXT("from_expression"), FromExprName) || FromExprName.IsEmpty())
        {
            OutError = TEXT("connect_material_expression: missing_required_field: 'from_expression' is required");
            return nullptr;
        }

        FString ToSpec;
        if (!Params->TryGetStringField(TEXT("to"), ToSpec) || ToSpec.IsEmpty())
        {
            OutError = TEXT("connect_material_expression: missing_required_field: 'to' is required");
            return nullptr;
        }

        // Optional: which output of the source expression to wire from.
        // Empty string -> the expression's default/first output.
        FString FromOutput;
        Params->TryGetStringField(TEXT("from_output"), FromOutput);

        UMaterial* Mat = LoadObject<UMaterial>(nullptr, *MaterialPath);
        if (!Mat)
        {
            OutError = FString::Printf(
                TEXT("connect_material_expression: material_not_found: no UMaterial loaded from '%s'"),
                *MaterialPath);
            return nullptr;
        }

        UMaterialExpression* FromExpr = FindExpressionByName(Mat, FromExprName);
        if (!FromExpr)
        {
            OutError = FString::Printf(
                TEXT("connect_material_expression: from_expression_not_found: no expression named '%s' in material '%s'"),
                *FromExprName, *MaterialPath);
            return nullptr;
        }

        bool bConnected = false;

        if (ToSpec.StartsWith(TEXT("property:")))
        {
            const FString PropName = ToSpec.RightChop(9); // len("property:")
            EMaterialProperty Prop = MP_MAX;
            if (!MapMaterialProperty(PropName, Prop))
            {
                OutError = FString::Printf(
                    TEXT("connect_material_expression: invalid_field: unknown_property: '%s' is not a recognized material property (BaseColor, Metallic, Specular, Roughness, EmissiveColor, Opacity, OpacityMask, Normal, AmbientOcclusion, WorldPositionOffset)"),
                    *PropName);
                return nullptr;
            }
            bConnected = UMaterialEditingLibrary::ConnectMaterialProperty(FromExpr, FromOutput, Prop);
        }
        else if (ToSpec.StartsWith(TEXT("node:")))
        {
            // node:<ExprName>:<InputName>
            const FString Rest = ToSpec.RightChop(5); // len("node:")
            FString ToExprName, ToInputName;
            if (!Rest.Split(TEXT(":"), &ToExprName, &ToInputName) || ToExprName.IsEmpty() || ToInputName.IsEmpty())
            {
                OutError = FString::Printf(
                    TEXT("connect_material_expression: invalid_field: bad_to_format: 'to' node form must be 'node:<ExprName>:<InputName>' with a non-empty input name, got '%s'"),
                    *ToSpec);
                return nullptr;
            }
            UMaterialExpression* ToExpr = FindExpressionByName(Mat, ToExprName);
            if (!ToExpr)
            {
                OutError = FString::Printf(
                    TEXT("connect_material_expression: to_expression_not_found: no expression named '%s' in material '%s'"),
                    *ToExprName, *MaterialPath);
                return nullptr;
            }
            bConnected = UMaterialEditingLibrary::ConnectMaterialExpressions(FromExpr, FromOutput, ToExpr, ToInputName);
        }
        else
        {
            OutError = FString::Printf(
                TEXT("connect_material_expression: invalid_field: bad_to_format: 'to' must start with 'property:' or 'node:', got '%s'"),
                *ToSpec);
            return nullptr;
        }

        if (!bConnected)
        {
            OutError = FString::Printf(
                TEXT("connect_material_expression: connect_failed: the connect call returned false for from '%s' to '%s' (check the output/input names exist on those nodes)"),
                *FromExprName, *ToSpec);
            return nullptr;
        }

        UMaterialEditingLibrary::RecompileMaterial(Mat);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("material"), MaterialPath);
        Out->SetStringField(TEXT("from_expression"), FromExprName);
        Out->SetStringField(TEXT("to"), ToSpec);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ConnectMaterialExpression()
{
    return MakeShared<FHandler_ConnectMaterialExpression>();
}
