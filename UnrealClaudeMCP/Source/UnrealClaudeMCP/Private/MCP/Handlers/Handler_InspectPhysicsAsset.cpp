// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_physics_asset - read structural properties of a UPhysicsAsset:
// preview skeletal mesh, body setups (one per simulated bone), constraint
// setups (joint between two bodies), bounds-bodies subset, and the named
// physical-animation / constraint profiles. Cross-links to USkeletalMesh
// via the preview_skeletal_mesh asset path -- callers can stitch a full
// "rigged + simulated character" view by following that path into
// inspect_skeletal_mesh + inspect_anim_blueprint.
//
// UE 5.7 surface used (header:line citations for reviewer traceability):
//   PhysicsAsset.h:171   class UPhysicsAsset
//   PhysicsAsset.h:184   TSoftObjectPtr<USkeletalMesh> PreviewSkeletalMesh
//   PhysicsAsset.h:187   TArray<FName> PhysicalAnimationProfiles
//   PhysicsAsset.h:190   TArray<FName> ConstraintProfiles
//   PhysicsAsset.h:206   TArray<int32> BoundsBodies
//   PhysicsAsset.h:213   TArray<TObjectPtr<USkeletalBodySetup>> SkeletalBodySetups
//   PhysicsAsset.h:220   TArray<TObjectPtr<UPhysicsConstraintTemplate>> ConstraintSetup
//   SkeletalBodySetup.h:25  class USkeletalBodySetup : UBodySetup
//   BodySetup.h:152      uint8 bConsiderForBounds : 1
//   PhysicsConstraintTemplate.h:32 class UPhysicsConstraintTemplate : UObject
//   PhysicsConstraintTemplate.h:37 FConstraintInstance DefaultInstance
//   ConstraintInstance.h:260   FName JointName
//   ConstraintInstance.h:269   FName ConstraintBone1   (child)
//   ConstraintInstance.h:276   FName ConstraintBone2   (parent)
//
// USkeletalBodySetup carries the bone name via its UBodySetup base's
// BoneName UPROPERTY. Bodies array is emitted in the order stored on the
// asset (matching the Physics Asset Editor's display order, which is the
// order the engine simulates them).
//
// Null-skip discipline: TArray<TObjectPtr<USkeletalBodySetup>> and
// TArray<TObjectPtr<UPhysicsConstraintTemplate>> can carry null entries
// after deletes (PR #55->#57 lesson). body_count and constraint_count
// reflect VALID-only counts.
//
// Error format: "inspect_physics_asset: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_physics_asset

#include "PhysicsEngine/PhysicsAsset.h"
#include "PhysicsEngine/SkeletalBodySetup.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/PhysicsConstraintTemplate.h"
#include "PhysicsEngine/ConstraintInstance.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

class FHandler_InspectPhysicsAsset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_physics_asset"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_physics_asset: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_physics_asset: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_physics_asset: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        UPhysicsAsset* PhysicsAsset = Cast<UPhysicsAsset>(Loaded);
        if (!PhysicsAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_physics_asset: not_a_physics_asset: '%s' is a %s, not a UPhysicsAsset"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- bodies (null-skip; report bone name + bConsiderForBounds) ---

        // Build a lookup of "is this body index in BoundsBodies?" for the
        // is_in_bounds flag emitted per body. Reserve up-front -- known size
        // (PR #72 Gemini perf cleanup).
        TSet<int32> BoundsBodyIndexSet;
        BoundsBodyIndexSet.Reserve(PhysicsAsset->BoundsBodies.Num());
        for (int32 BodyIndex : PhysicsAsset->BoundsBodies)
        {
            BoundsBodyIndexSet.Add(BodyIndex);
        }

        int32 ValidBodyCount = 0;
        TArray<TSharedPtr<FJsonValue>> BodiesArray;
        BodiesArray.Reserve(PhysicsAsset->SkeletalBodySetups.Num());
        for (int32 i = 0; i < PhysicsAsset->SkeletalBodySetups.Num(); ++i)
        {
            USkeletalBodySetup* Body = PhysicsAsset->SkeletalBodySetups[i].Get();
            if (!Body) { continue; }
            ++ValidBodyCount;

            TSharedPtr<FJsonObject> BodyObj = MakeShared<FJsonObject>();
            BodyObj->SetStringField(TEXT("name"), Body->BoneName.ToString());
            BodyObj->SetBoolField(TEXT("consider_for_bounds"), Body->bConsiderForBounds != 0);
            BodyObj->SetBoolField(TEXT("is_in_bounds_subset"), BoundsBodyIndexSet.Contains(i));
            BodyObj->SetNumberField(TEXT("body_index"), static_cast<double>(i));
            BodiesArray.Add(MakeShared<FJsonValueObject>(BodyObj));
        }

        // --- constraints (null-skip; emit joint + child/parent bone names) ---

        int32 ValidConstraintCount = 0;
        TArray<TSharedPtr<FJsonValue>> ConstraintsArray;
        ConstraintsArray.Reserve(PhysicsAsset->ConstraintSetup.Num());
        for (const TObjectPtr<UPhysicsConstraintTemplate>& Tpl : PhysicsAsset->ConstraintSetup)
        {
            UPhysicsConstraintTemplate* C = Tpl.Get();
            if (!C) { continue; }
            ++ValidConstraintCount;

            const FConstraintInstance& Inst = C->DefaultInstance;
            TSharedPtr<FJsonObject> CObj = MakeShared<FJsonObject>();
            CObj->SetStringField(TEXT("joint_name"),    Inst.JointName.ToString());
            CObj->SetStringField(TEXT("child_bone"),    Inst.ConstraintBone1.ToString());
            CObj->SetStringField(TEXT("parent_bone"),   Inst.ConstraintBone2.ToString());
            ConstraintsArray.Add(MakeShared<FJsonValueObject>(CObj));
        }

        // --- profile name lists (TArray<FName>) ---

        TArray<TSharedPtr<FJsonValue>> PhysAnimProfiles;
        PhysAnimProfiles.Reserve(PhysicsAsset->PhysicalAnimationProfiles.Num());
        for (const FName& N : PhysicsAsset->PhysicalAnimationProfiles)
        {
            PhysAnimProfiles.Add(MakeShared<FJsonValueString>(N.ToString()));
        }

        TArray<TSharedPtr<FJsonValue>> ConstraintProfileNames;
        ConstraintProfileNames.Reserve(PhysicsAsset->ConstraintProfiles.Num());
        for (const FName& N : PhysicsAsset->ConstraintProfiles)
        {
            ConstraintProfileNames.Add(MakeShared<FJsonValueString>(N.ToString()));
        }

        // --- response ---

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), PhysicsAsset->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);

        // Cross-link to USkeletalMesh (TSoftObjectPtr -> resolve path string
        // without loading the mesh -- keeps inspect_physics_asset cheap).
        if (!PhysicsAsset->PreviewSkeletalMesh.IsNull())
        {
            Out->SetStringField(TEXT("preview_skeletal_mesh"),
                PhysicsAsset->PreviewSkeletalMesh.ToSoftObjectPath().ToString());
        }

        Out->SetNumberField(TEXT("body_count"), static_cast<double>(ValidBodyCount));
        Out->SetArrayField(TEXT("bodies"), BodiesArray);
        Out->SetNumberField(TEXT("constraint_count"), static_cast<double>(ValidConstraintCount));
        Out->SetArrayField(TEXT("constraints"), ConstraintsArray);
        Out->SetNumberField(TEXT("bounds_body_count"),
            static_cast<double>(PhysicsAsset->BoundsBodies.Num()));
        Out->SetNumberField(TEXT("physical_animation_profile_count"),
            static_cast<double>(PhysAnimProfiles.Num()));
        Out->SetArrayField(TEXT("physical_animation_profiles"), PhysAnimProfiles);
        Out->SetNumberField(TEXT("constraint_profile_count"),
            static_cast<double>(ConstraintProfileNames.Num()));
        Out->SetArrayField(TEXT("constraint_profiles"), ConstraintProfileNames);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectPhysicsAsset()
{
    return MakeShared<FHandler_InspectPhysicsAsset>();
}
