// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_material_expression - create a UMaterialExpression node inside an existing
// UMaterial's graph (Lane 4: material graph authoring).
//
// UE 5.7 surface (verified against engine source):
//   - UMaterialEditingLibrary::CreateMaterialExpression(UMaterial*,
//     TSubclassOf<UMaterialExpression>, int32 NodePosX, int32 NodePosY)
//     -> UMaterialExpression* (Editor/MaterialEditor/Public/MaterialEditingLibrary.h:115).
//   - UMaterialEditingLibrary::RecompileMaterial(UMaterial*) (line 168) — must be
//     called after graph edits for changes to take effect.
//   - LoadObject<UMaterial>(nullptr, *Path) resolves a /Game material asset.
//   - LoadClass<UMaterialExpression>(nullptr, *Path) resolves an expression class
//     from a /Script path (e.g. /Script/Engine.MaterialExpressionConstant3Vector).
//     Abstract classes are rejected.
//
// IsMutating() is false: this edits asset content (the material's expression
// collection), not level/actor state on the editor undo stack. Same posture as
// create_material_instance and the other asset-authoring handlers.
//
// Error format: "add_material_expression: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, material_not_found,
// invalid_field (expression_class_not_found / expression_class_is_abstract),
// create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "MaterialEditingLibrary.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/Class.h"

class FHandler_AddMaterialExpression : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_material_expression"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("add_material_expression: missing_params: request had no params object");
            return nullptr;
        }

        FString MaterialPath;
        if (!Params->TryGetStringField(TEXT("material"), MaterialPath) || MaterialPath.IsEmpty())
        {
            OutError = TEXT("add_material_expression: missing_required_field: 'material' is required");
            return nullptr;
        }

        FString ExprClassPath;
        if (!Params->TryGetStringField(TEXT("expression_class"), ExprClassPath) || ExprClassPath.IsEmpty())
        {
            OutError = TEXT("add_material_expression: missing_required_field: 'expression_class' is required");
            return nullptr;
        }

        // Optional node graph coordinates (default 0,0).
        int32 NodePosX = 0;
        int32 NodePosY = 0;
        {
            double X = 0, Y = 0;
            if (Params->TryGetNumberField(TEXT("node_pos_x"), X)) { NodePosX = static_cast<int32>(X); }
            if (Params->TryGetNumberField(TEXT("node_pos_y"), Y)) { NodePosY = static_cast<int32>(Y); }
        }

        UMaterial* Mat = LoadObject<UMaterial>(nullptr, *MaterialPath);
        if (!Mat)
        {
            OutError = FString::Printf(
                TEXT("add_material_expression: material_not_found: no UMaterial loaded from '%s'"),
                *MaterialPath);
            return nullptr;
        }

        UClass* ExprClass = LoadClass<UMaterialExpression>(nullptr, *ExprClassPath);
        if (!ExprClass)
        {
            OutError = FString::Printf(
                TEXT("add_material_expression: invalid_field: expression_class_not_found: no UMaterialExpression subclass loaded from '%s' (pass a /Script path like /Script/Engine.MaterialExpressionConstant3Vector)"),
                *ExprClassPath);
            return nullptr;
        }
        if (ExprClass->HasAnyClassFlags(CLASS_Abstract))
        {
            OutError = FString::Printf(
                TEXT("add_material_expression: invalid_field: expression_class_is_abstract: '%s' is abstract and cannot be instantiated"),
                *ExprClassPath);
            return nullptr;
        }

        UMaterialExpression* Expr =
            UMaterialEditingLibrary::CreateMaterialExpression(Mat, ExprClass, NodePosX, NodePosY);
        if (!Expr)
        {
            OutError = FString::Printf(
                TEXT("add_material_expression: create_failed: CreateMaterialExpression returned null for class '%s' on material '%s'"),
                *ExprClassPath, *MaterialPath);
            return nullptr;
        }

        // Recompile so the new node is reflected in the material's shader.
        UMaterialEditingLibrary::RecompileMaterial(Mat);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("material"), MaterialPath);
        Out->SetStringField(TEXT("expression"), Expr->GetName());
        Out->SetStringField(TEXT("class"), ExprClass->GetName());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddMaterialExpression()
{
    return MakeShared<FHandler_AddMaterialExpression>();
}
