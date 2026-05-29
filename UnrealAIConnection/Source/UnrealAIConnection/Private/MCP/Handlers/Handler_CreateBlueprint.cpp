// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_blueprint - create a new UBlueprint asset under /Game/ from a parent class.
//
// UE 5.7 surface (verified against engine source):
//   - UBlueprintFactory (Editor/UnrealEd/Classes/Factories/BlueprintFactory.h)
//     with a public `TSubclassOf<UObject> ParentClass` member — the class the
//     new Blueprint derives from.
//   - IAssetTools::CreateAsset(AssetName, PackagePath, AssetClass, Factory)
//     (Developer/AssetTools/Public/IAssetTools.h). AssetClass is
//     UBlueprint::StaticClass(); the factory carries the parent class.
//   - LoadClass<UObject>(nullptr, *Path) resolves the parent class from a full
//     class path, e.g. "/Script/Engine.Actor" or a /Game BP generated class
//     (path ends with _C). Abstract classes are rejected.
//
// IsMutating() is false: asset creation, not an actor edit on the undo stack.
//
// Error format: "create_blueprint: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// parent_class_not_found, editor_unavailable, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/Class.h"
#include "Factories/BlueprintFactory.h"
#include "Engine/Blueprint.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_CreateBlueprint : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_blueprint"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("create_blueprint: missing_params: request had no params object");
            return nullptr;
        }

        FString PackagePath;
        if (!Params->TryGetStringField(TEXT("path"), PackagePath) || PackagePath.IsEmpty())
        {
            OutError = TEXT("create_blueprint: missing_required_field: 'path' is required");
            return nullptr;
        }
        if (!PackagePath.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(
                TEXT("create_blueprint: invalid_field: 'path' must start with /Game/, got '%s'"),
                *PackagePath);
            return nullptr;
        }
        if (PackagePath.Contains(TEXT("..")) || PackagePath.Contains(TEXT("\\")))
        {
            OutError = FString::Printf(
                TEXT("create_blueprint: invalid_field: 'path' must not contain '..' or backslash, got '%s'"),
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
            OutError = TEXT("create_blueprint: missing_required_field: 'name' is required");
            return nullptr;
        }

        // parent_class is optional; default to AActor.
        FString ParentPath;
        if (!Params->TryGetStringField(TEXT("parent_class"), ParentPath) || ParentPath.IsEmpty())
        {
            ParentPath = TEXT("/Script/Engine.Actor");
        }

        // Resolve native class paths (/Script/...) and Blueprint parents (plain
        // /Game asset path -> its GeneratedClass) uniformly.
        UClass* Parent = UCMCPAssetPath::ResolveClassByPath(ParentPath);
        if (!Parent)
        {
            OutError = FString::Printf(
                TEXT("create_blueprint: parent_class_not_found: no class resolved from '%s' (pass a native path like /Script/Engine.Actor, or a Blueprint asset path)"),
                *ParentPath);
            return nullptr;
        }
        if (Parent->HasAnyClassFlags(CLASS_Abstract))
        {
            OutError = FString::Printf(
                TEXT("create_blueprint: invalid_field: parent_class_is_abstract: '%s' is abstract and cannot be a Blueprint parent"),
                *ParentPath);
            return nullptr;
        }

        FAssetToolsModule* AssetToolsModule =
            FModuleManager::GetModulePtr<FAssetToolsModule>(TEXT("AssetTools"));
        if (!AssetToolsModule)
        {
            OutError = TEXT("create_blueprint: editor_unavailable: AssetTools module not loaded");
            return nullptr;
        }
        IAssetTools& AssetTools = AssetToolsModule->Get();

        UBlueprintFactory* Factory = NewObject<UBlueprintFactory>();
        Factory->ParentClass = Parent;

        UObject* NewBP = AssetTools.CreateAsset(
            Name, PackagePath, UBlueprint::StaticClass(), Factory);
        if (!NewBP)
        {
            OutError = FString::Printf(
                TEXT("create_blueprint: create_failed: IAssetTools::CreateAsset returned null for '%s/%s' (path may already contain an asset of that name)"),
                *PackagePath, *Name);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), NewBP->GetPathName());
        Out->SetStringField(TEXT("parent_class"), Parent->GetPathName());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateBlueprint()
{
    return MakeShared<FHandler_CreateBlueprint>();
}
