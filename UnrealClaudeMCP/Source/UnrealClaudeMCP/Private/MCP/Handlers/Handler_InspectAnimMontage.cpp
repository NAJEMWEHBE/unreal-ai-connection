// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_anim_montage - read the structural properties of a UAnimMontage
// asset: target skeleton, play length, frame rate, blend envelope, named
// composite sections (with start/end times and next-section linkage),
// slot-animation tracks, and notify events. Completes the animation
// introspection trio with inspect_anim_blueprint and inspect_skeletal_mesh:
// callers can correlate a montage's skeleton to the same skeleton on a
// skeletal mesh or referenced by an anim blueprint via the `skeleton`
// asset path.
//
// Tier 3 PR #56. C++ canonical handler -- direct field access on
// UAnimMontage (which inherits UAnimCompositeBase : UAnimSequenceBase :
// UAnimationAsset). The Python equivalent would require multi-call FFI
// through unreal.AnimMontage with reflection limits on FCompositeSection /
// FAnimNotifyEvent.
//
// UE 5.7 surface used:
//   - UAnimMontage                                      AnimMontage.h:622
//   - UAnimMontage::CompositeSections                   AnimMontage.h:684
//   - UAnimMontage::GetSectionStartAndEndTime           AnimMontage.h:801
//     (use this rather than the WITH_EDITORONLY_DATA StartTime_DEPRECATED)
//   - UAnimMontage::SlotAnimTracks                      AnimMontage.h:688
//   - UAnimMontage::BlendIn / BlendOut                  AnimMontage.h:637 / :645
//   - UAnimMontage::BlendOutTriggerTime                 AnimMontage.h:657
//   - UAnimMontage::bEnableAutoBlendOut                 AnimMontage.h:706
//   - UAnimMontage::IsDynamicMontage                    AnimMontage.h:733
//   - UAnimMontage::GetSamplingFrameRate                AnimMontage.h:746
//   - UAnimSequenceBase::GetPlayLength                  AnimSequenceBase.h:86
//   - UAnimSequenceBase::Notifies                       AnimSequenceBase.h:43
//     (use this rather than the WITH_EDITORONLY_DATA AnimNotifyTracks)
//   - FAnimNotifyEvent (.NotifyName, .Notify, .NotifyStateClass, .Duration)
//   - FAnimLinkableElement::GetTime                     AnimLinkableElement.h:94
//   - UAnimationAsset::GetSkeleton                      AnimationAsset.h:1229
//   - UAnimCompositeBase::GetParentAsset                (inherited)
//
// Bounds shape: NOT applicable (montages have no bounding box).
//
// Error format: "inspect_anim_montage: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found,
// not_an_anim_montage.

#include "Animation/AnimMontage.h"
#include "Animation/AnimCompositeBase.h"
#include "Animation/AnimLinkableElement.h"
#include "Animation/AnimNotifies/AnimNotify.h"
#include "Animation/AnimNotifies/AnimNotifyState.h"
#include "Animation/AnimSequenceBase.h"
#include "Animation/AnimationAsset.h"
#include "Animation/AnimTypes.h"
#include "Animation/Skeleton.h"
#include "Misc/FrameRate.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

class FHandler_InspectAnimMontage : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_anim_montage"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_anim_montage: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_anim_montage: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_anim_montage: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }
        UAnimMontage* Montage = Cast<UAnimMontage>(Loaded);
        if (!Montage)
        {
            OutError = FString::Printf(
                TEXT("inspect_anim_montage: not_an_anim_montage: '%s' is a %s, not a UAnimMontage"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- composite sections ------------------------------------------
        // Use GetSectionStartAndEndTime (runtime-safe) rather than
        // FCompositeSection::StartTime_DEPRECATED (WITH_EDITORONLY_DATA).
        // Each section's NextSectionName encodes the inter-section jump
        // graph -- empty FName means "fall through to the next index in
        // CompositeSections" (or end if last).

        TArray<TSharedPtr<FJsonValue>> SectionArray;
        SectionArray.Reserve(Montage->CompositeSections.Num());
        for (int32 i = 0; i < Montage->CompositeSections.Num(); ++i)
        {
            const FCompositeSection& Section = Montage->CompositeSections[i];
            float StartTime = 0.0f;
            float EndTime = 0.0f;
            Montage->GetSectionStartAndEndTime(i, StartTime, EndTime);

            TSharedPtr<FJsonObject> SectionObj = MakeShared<FJsonObject>();
            SectionObj->SetStringField(TEXT("name"), Section.SectionName.ToString());
            SectionObj->SetStringField(TEXT("next"), Section.NextSectionName.IsNone()
                ? FString() : Section.NextSectionName.ToString());
            SectionObj->SetNumberField(TEXT("start_time"), StartTime);
            SectionObj->SetNumberField(TEXT("end_time"), EndTime);
            SectionArray.Add(MakeShared<FJsonValueObject>(SectionObj));
        }

        // --- slot animation tracks ---------------------------------------

        TArray<TSharedPtr<FJsonValue>> SlotArray;
        SlotArray.Reserve(Montage->SlotAnimTracks.Num());
        for (const FSlotAnimationTrack& Slot : Montage->SlotAnimTracks)
        {
            TSharedPtr<FJsonObject> SlotObj = MakeShared<FJsonObject>();
            SlotObj->SetStringField(TEXT("slot_name"), Slot.SlotName.ToString());
            SlotObj->SetNumberField(TEXT("segment_count"),
                static_cast<double>(Slot.AnimTrack.AnimSegments.Num()));
            SlotArray.Add(MakeShared<FJsonValueObject>(SlotObj));
        }

        // --- notifies ----------------------------------------------------
        // FAnimNotifyEvent::Notify (one-shot) and NotifyStateClass (durational)
        // can both be null for "named" notifies (just a name, no class).
        // Each pointer is independently null-checked. (Per explorer brief
        // gotcha #7: AnimTypes.h:301,304.)

        TArray<TSharedPtr<FJsonValue>> NotifyArray;
        NotifyArray.Reserve(Montage->Notifies.Num());
        for (const FAnimNotifyEvent& Notify : Montage->Notifies)
        {
            TSharedPtr<FJsonObject> NotifyObj = MakeShared<FJsonObject>();
            NotifyObj->SetStringField(TEXT("name"), Notify.NotifyName.ToString());
            NotifyObj->SetNumberField(TEXT("time"), Notify.GetTime());
            NotifyObj->SetNumberField(TEXT("duration"), Notify.Duration);
            if (Notify.Notify)
            {
                NotifyObj->SetStringField(TEXT("notify_class"),
                    Notify.Notify->GetClass()->GetName());
            }
            if (Notify.NotifyStateClass)
            {
                // NotifyStateClass IS the UClass (TObjectPtr<UClass>); call
                // GetName() directly. Prior code's GetClass()->GetName()
                // returned the meta-class name "Class" (and tripped C2027
                // because UAnimNotifyState was forward-declared only).
                NotifyObj->SetStringField(TEXT("notify_state_class"),
                    Notify.NotifyStateClass->GetName());
            }
            NotifyArray.Add(MakeShared<FJsonValueObject>(NotifyObj));
        }

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Montage->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);

        // Skeleton path -- the cross-link to inspect_skeletal_mesh and
        // inspect_anim_blueprint. Null-check defensively even though
        // well-formed montages always have a skeleton.
        if (const USkeleton* Skeleton = Montage->GetSkeleton())
        {
            Out->SetStringField(TEXT("skeleton"), Skeleton->GetPathName());
        }

        Out->SetNumberField(TEXT("play_length"), Montage->GetPlayLength());

        // Frame rate as numerator/denominator -- the rational form is
        // exact for typical NTSC rates (30000/1001) where a single float
        // would round-trip lossily.
        const FFrameRate FrameRate = Montage->GetSamplingFrameRate();
        TSharedPtr<FJsonObject> FrameRateObj = MakeShared<FJsonObject>();
        FrameRateObj->SetNumberField(TEXT("numerator"), static_cast<double>(FrameRate.Numerator));
        FrameRateObj->SetNumberField(TEXT("denominator"), static_cast<double>(FrameRate.Denominator));
        Out->SetObjectField(TEXT("frame_rate"), FrameRateObj);

        Out->SetBoolField(TEXT("is_dynamic"), Montage->IsDynamicMontage());

        // Blend envelope. Use accessor methods rather than the deprecated
        // BlendInTime_DEPRECATED / BlendOutTime_DEPRECATED scalar fields
        // (WITH_EDITORONLY_DATA per AnimMontage.h:640,649).
        Out->SetNumberField(TEXT("blend_in_time"), Montage->BlendIn.GetBlendTime());
        Out->SetNumberField(TEXT("blend_out_time"), Montage->BlendOut.GetBlendTime());
        Out->SetNumberField(TEXT("blend_out_trigger_time"), Montage->BlendOutTriggerTime);
        Out->SetBoolField(TEXT("auto_blend_out"), Montage->bEnableAutoBlendOut);

        // Parent asset (montage as child of another montage via asset
        // remapping). The ParentAsset field lives in UAnimationAsset under
        // WITH_EDITORONLY_DATA (AnimationAsset.h:1050-1055) -- there's no
        // GetParentAsset() accessor. HasParentAsset() (AnimationAsset.h:1173)
        // gives the boolean check publicly; field access is direct under
        // the same WITH_EDITORONLY_DATA guard.
#if WITH_EDITORONLY_DATA
        if (Montage->HasParentAsset())
        {
            if (UAnimationAsset* ParentAsset = Montage->ParentAsset.Get())
            {
                Out->SetStringField(TEXT("parent_asset"), ParentAsset->GetPathName());
            }
        }
#endif

        Out->SetNumberField(TEXT("section_count"), static_cast<double>(SectionArray.Num()));
        Out->SetArrayField(TEXT("sections"), SectionArray);
        Out->SetNumberField(TEXT("slot_count"), static_cast<double>(SlotArray.Num()));
        Out->SetArrayField(TEXT("slots"), SlotArray);
        Out->SetNumberField(TEXT("notify_count"), static_cast<double>(NotifyArray.Num()));
        Out->SetArrayField(TEXT("notifies"), NotifyArray);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectAnimMontage()
{
    return MakeShared<FHandler_InspectAnimMontage>();
}
