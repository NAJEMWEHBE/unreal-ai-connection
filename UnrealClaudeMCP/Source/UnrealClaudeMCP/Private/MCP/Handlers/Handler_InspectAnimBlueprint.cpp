// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_anim_blueprint - read the structural properties of a UAnimBlueprint
// asset: parent class, template/skeleton relationship, compile status,
// parent Anim Blueprint, baked state machines, Anim Blueprint functions, and
// sync groups. Pairs with inspect_asset (which reads registry-level metadata)
// and the existing Inspect* handlers for asset-introspection workflows.
//
// Part of the language-shim experiment (PR #46): "C++ canonical" handler.
// Anim Blueprint compiled data lives on UAnimBlueprintGeneratedClass, not on
// the UAnimBlueprint asset, so C++ is the safest place to guard the generated
// class before reading baked state machines, function metadata, or sync groups.
//
// UE 5.7 surface used:
//   - UAnimBlueprint::TargetSkeleton                         AnimBlueprint.h:91
//   - UAnimBlueprint::bIsTemplate                            AnimBlueprint.h:100
//   - UAnimBlueprint::GetAnimBlueprintGeneratedClass         AnimBlueprint.h:124
//   - UAnimBlueprint::GetParentAnimBlueprint                 AnimBlueprint.h:162
//   - UAnimBlueprintGeneratedClass::BakedStateMachines       AnimBlueprintGeneratedClass.h:376
//   - UAnimBlueprintGeneratedClass::GetSyncGroupNames        AnimBlueprintGeneratedClass.h:460
//   - UAnimBlueprintGeneratedClass::GetAnimBlueprintFunctions AnimBlueprintGeneratedClass.h:464
//   - FBakedAnimationStateMachine::MachineName               AnimStateMachineTypes.h:369
//   - FAnimBlueprintFunction::Name / bImplemented            AnimClassInterface.h:64/109
//   - UBlueprint::ParentClass / Status                       Blueprint.h:412/504
//
// Error format: "inspect_anim_blueprint: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_an_anim_blueprint.

#include "Animation/AnimBlueprint.h"
#include "Animation/AnimBlueprintGeneratedClass.h"
#include "Animation/AnimStateMachineTypes.h"
#include "Animation/AnimClassInterface.h"
#include "Animation/Skeleton.h"
#include "Engine/Blueprint.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
    static FString BlueprintStatusToString(EBlueprintStatus Status)
    {
        switch (Status)
        {
        case BS_UpToDate:
            return TEXT("UpToDate");
        case BS_UpToDateWithWarnings:
            return TEXT("UpToDateWithWarnings");
        case BS_Dirty:
            return TEXT("Dirty");
        case BS_Error:
            // Compile-failed blueprint. PR #52 Gemini medium review: prior
            // version had no case for BS_Error and silently fell through to
            // "Unknown" -- masking real compile errors as "this is unknown,
            // try recompiling" instead of "this failed to compile, look at
            // the editor's compile log."
            return TEXT("Error");
        case BS_Unknown:
        default:
            return TEXT("Unknown");
        }
    }
}

class FHandler_InspectAnimBlueprint : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_anim_blueprint"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_anim_blueprint: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_anim_blueprint: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_anim_blueprint: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }
        UAnimBlueprint* AnimBP = Cast<UAnimBlueprint>(Loaded);
        if (!AnimBP)
        {
            OutError = FString::Printf(
                TEXT("inspect_anim_blueprint: not_an_anim_blueprint: '%s' is a %s, not a UAnimBlueprint"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        const FString ParentClassName = AnimBP->ParentClass ? AnimBP->ParentClass->GetName() : TEXT("");
        const bool bIsTemplate = AnimBP->bIsTemplate;
        const FString StatusString = BlueprintStatusToString(static_cast<EBlueprintStatus>(AnimBP->Status));
        USkeleton* TargetSkeleton = bIsTemplate ? nullptr : AnimBP->TargetSkeleton.Get();
        UAnimBlueprint* ParentAnimBP = UAnimBlueprint::GetParentAnimBlueprint(AnimBP);

        // --- compiled data ----------------------------------------------

        bool bIsCompiled = false;
        TArray<TSharedPtr<FJsonValue>> StateMachineArray;
        TArray<TSharedPtr<FJsonValue>> AnimFunctionArray;
        TArray<TSharedPtr<FJsonValue>> SyncGroupArray;
        if (UAnimBlueprintGeneratedClass* GC = AnimBP->GetAnimBlueprintGeneratedClass())
        {
            bIsCompiled = true;

            const TArray<FBakedAnimationStateMachine>& StateMachines = GC->BakedStateMachines;
            StateMachineArray.Reserve(StateMachines.Num());
            for (const FBakedAnimationStateMachine& Machine : StateMachines)
            {
                TSharedPtr<FJsonObject> MachineObj = MakeShared<FJsonObject>();
                MachineObj->SetStringField(TEXT("name"), Machine.MachineName.ToString());
                StateMachineArray.Add(MakeShared<FJsonValueObject>(MachineObj));
            }

            const TArray<FAnimBlueprintFunction>& AnimFunctions = GC->GetAnimBlueprintFunctions();
            AnimFunctionArray.Reserve(AnimFunctions.Num());
            for (const FAnimBlueprintFunction& Function : AnimFunctions)
            {
                TSharedPtr<FJsonObject> FunctionObj = MakeShared<FJsonObject>();
                FunctionObj->SetStringField(TEXT("name"), Function.Name.ToString());
                FunctionObj->SetBoolField(TEXT("implemented"), Function.bImplemented);
                AnimFunctionArray.Add(MakeShared<FJsonValueObject>(FunctionObj));
            }

            const TArray<FName>& SyncGroupNames = GC->GetSyncGroupNames();
            SyncGroupArray.Reserve(SyncGroupNames.Num());
            for (const FName& SyncGroupName : SyncGroupNames)
            {
                SyncGroupArray.Add(MakeShared<FJsonValueString>(SyncGroupName.ToString()));
            }
        }

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), AnimBP->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);
        Out->SetStringField(TEXT("parent_class"), ParentClassName);
        Out->SetBoolField(TEXT("is_template"), bIsTemplate);

        if (TargetSkeleton)
        {
            Out->SetStringField(TEXT("target_skeleton"), TargetSkeleton->GetPathName());
        }

        Out->SetStringField(TEXT("blueprint_status"), StatusString);
        Out->SetBoolField(TEXT("is_compiled"), bIsCompiled);

        if (ParentAnimBP)
        {
            Out->SetStringField(TEXT("parent_anim_blueprint"), ParentAnimBP->GetPathName());
        }

        Out->SetNumberField(TEXT("state_machine_count"), static_cast<double>(StateMachineArray.Num()));
        Out->SetArrayField(TEXT("state_machines"), StateMachineArray);
        Out->SetNumberField(TEXT("anim_function_count"), static_cast<double>(AnimFunctionArray.Num()));
        Out->SetArrayField(TEXT("anim_functions"), AnimFunctionArray);
        Out->SetNumberField(TEXT("sync_group_count"), static_cast<double>(SyncGroupArray.Num()));
        Out->SetArrayField(TEXT("sync_groups"), SyncGroupArray);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectAnimBlueprint()
{
    return MakeShared<FHandler_InspectAnimBlueprint>();
}
