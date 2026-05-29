// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_blueprint_variable - add a member variable to an existing UBlueprint.
//
// UE 5.7 surface (verified against engine source):
//   - FBlueprintEditorUtils::AddMemberVariable(UBlueprint*, FName VarName,
//     const FEdGraphPinType&, const FString& DefaultValue = FString())
//     (Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h). Returns false
//     if a variable of that name already exists.
//   - FEdGraphPinType describes the variable type. Pin categories live as
//     static FName consts on UEdGraphSchema_K2 (EdGraphSchema_K2.h):
//     PC_Boolean, PC_Int, PC_Real (with PC_Float subcategory), PC_String,
//     PC_Name, PC_Struct (SubCategoryObject = the UScriptStruct), PC_Object
//     (SubCategoryObject = the UClass).
//   - FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(UBlueprint*)
//     flags the BP dirty + queues a recompile so the new variable is picked up.
//
// IsMutating() is false: this edits an asset, not a level actor on the undo
// stack the dispatcher's FScopedTransaction wraps.
//
// Error format: "add_blueprint_variable: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// blueprint_not_found, unsupported_type, add_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "EdGraphSchema_K2.h"
#include "EdGraph/EdGraphPin.h"
#include "Engine/Blueprint.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "UObject/Class.h"
#include "UObject/UObjectGlobals.h"

class FHandler_AddBlueprintVariable : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_blueprint_variable"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("add_blueprint_variable: missing_params: request had no params object");
            return nullptr;
        }

        FString BlueprintPath;
        if (!Params->TryGetStringField(TEXT("blueprint"), BlueprintPath) || BlueprintPath.IsEmpty())
        {
            OutError = TEXT("add_blueprint_variable: missing_required_field: 'blueprint' is required");
            return nullptr;
        }

        FString VarName;
        if (!Params->TryGetStringField(TEXT("var_name"), VarName) || VarName.IsEmpty())
        {
            OutError = TEXT("add_blueprint_variable: missing_required_field: 'var_name' is required");
            return nullptr;
        }

        FString TypeStr;
        if (!Params->TryGetStringField(TEXT("type"), TypeStr) || TypeStr.IsEmpty())
        {
            OutError = TEXT("add_blueprint_variable: missing_required_field: 'type' is required");
            return nullptr;
        }

        UBlueprint* BP = LoadObject<UBlueprint>(nullptr, *BlueprintPath);
        if (!BP)
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_variable: blueprint_not_found: no UBlueprint at '%s'"),
                *BlueprintPath);
            return nullptr;
        }

        // Build the pin type from the requested high-level type string.
        FEdGraphPinType PinType;
        if (TypeStr == TEXT("bool"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
        }
        else if (TypeStr == TEXT("int"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Int;
        }
        else if (TypeStr == TEXT("float"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Real;
            PinType.PinSubCategory = UEdGraphSchema_K2::PC_Float;
        }
        else if (TypeStr == TEXT("string"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_String;
        }
        else if (TypeStr == TEXT("name"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Name;
        }
        else if (TypeStr == TEXT("vector"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            PinType.PinSubCategoryObject = TBaseStructure<FVector>::Get();
        }
        else if (TypeStr == TEXT("rotator"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            PinType.PinSubCategoryObject = TBaseStructure<FRotator>::Get();
        }
        else if (TypeStr == TEXT("transform"))
        {
            PinType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            PinType.PinSubCategoryObject = TBaseStructure<FTransform>::Get();
        }
        else if (TypeStr == TEXT("object"))
        {
            FString ObjectClassPath;
            if (!Params->TryGetStringField(TEXT("object_class"), ObjectClassPath) || ObjectClassPath.IsEmpty())
            {
                OutError = TEXT("add_blueprint_variable: missing_required_field: 'object_class' is required when type is 'object'");
                return nullptr;
            }
            UClass* OC = UCMCPAssetPath::ResolveClassByPath(ObjectClassPath);
            if (!OC)
            {
                OutError = FString::Printf(
                    TEXT("add_blueprint_variable: invalid_field: object_class_not_found: no class resolved from '%s' (pass a native path or a Blueprint asset path)"),
                    *ObjectClassPath);
                return nullptr;
            }
            PinType.PinCategory = UEdGraphSchema_K2::PC_Object;
            PinType.PinSubCategoryObject = OC;
        }
        else
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_variable: unsupported_type: '%s' is not supported (use one of: bool, int, float, string, name, vector, rotator, transform, object)"),
                *TypeStr);
            return nullptr;
        }

        // default_value is optional; AddMemberVariable takes an empty string as
        // "no explicit default".
        FString DefaultValue;
        Params->TryGetStringField(TEXT("default_value"), DefaultValue);

        const bool bOk = FBlueprintEditorUtils::AddMemberVariable(
            BP, FName(*VarName), PinType, DefaultValue);
        if (!bOk)
        {
            OutError = FString::Printf(
                TEXT("add_blueprint_variable: add_failed: AddMemberVariable returned false for '%s' on '%s' (a variable of that name likely already exists)"),
                *VarName, *BlueprintPath);
            return nullptr;
        }

        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("blueprint"), BlueprintPath);
        Out->SetStringField(TEXT("var_name"), VarName);
        Out->SetStringField(TEXT("type"), TypeStr);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddBlueprintVariable()
{
    return MakeShared<FHandler_AddBlueprintVariable>();
}
