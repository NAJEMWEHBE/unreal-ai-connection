// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

// inspect_sound_attenuation - inspect a USoundAttenuation asset's stable editor-visible surface.
//
// UE 5.7 surface citations:
// Engine/Attenuation.h:16   enum class EAttenuationDistanceModel : uint8
// Engine/Attenuation.h:27   namespace EAttenuationShape
// Engine/Attenuation.h:39   enum class ENaturalSoundFalloffMode : uint8
// Engine/Attenuation.h:56   struct FBaseAttenuationSettings
// Engine/Attenuation.h:64   EAttenuationDistanceModel DistanceAlgorithm
// Engine/Attenuation.h:68   TEnumAsByte<enum EAttenuationShape::Type> AttenuationShape
// Engine/Attenuation.h:74   ENaturalSoundFalloffMode FalloffMode
// Engine/Attenuation.h:78   float dBAttenuationAtMax
// Engine/Attenuation.h:87   FVector AttenuationShapeExtents
// Engine/Attenuation.h:91   float ConeOffset
// Engine/Attenuation.h:95   float FalloffDistance
// Engine/Attenuation.h:99   float ConeSphereRadius
// Engine/Attenuation.h:103  float ConeSphereFalloffDistance
// Engine/EngineTypes.h:1087 enum ECollisionChannel : int
// Sound/SoundAttenuation.h:31   enum ESoundSpatializationAlgorithm : int
// Sound/SoundAttenuation.h:41   enum class EAirAbsorptionMethod : uint8
// Sound/SoundAttenuation.h:52   enum class EReverbSendMethod : uint8
// Sound/SoundAttenuation.h:65   enum class EPriorityAttenuationMethod : uint8
// Sound/SoundAttenuation.h:114  enum class ENonSpatializedRadiusSpeakerMapMode : uint8
// Sound/SoundAttenuation.h:138  struct FSoundAttenuationSettings : public FBaseAttenuationSettings
// Sound/SoundAttenuation.h:144  uint8 bAttenuate : 1
// Sound/SoundAttenuation.h:148  uint8 bSpatialize : 1
// Sound/SoundAttenuation.h:152  uint8 bAttenuateWithLPF : 1
// Sound/SoundAttenuation.h:156  uint8 bEnableListenerFocus : 1
// Sound/SoundAttenuation.h:160  uint8 bEnableFocusInterpolation : 1
// Sound/SoundAttenuation.h:164  uint8 bEnableOcclusion : 1
// Sound/SoundAttenuation.h:168  uint8 bUseComplexCollisionForOcclusion : 1
// Sound/SoundAttenuation.h:172  uint8 bEnableReverbSend : 1
// Sound/SoundAttenuation.h:176  uint8 bEnablePriorityAttenuation : 1
// Sound/SoundAttenuation.h:180  uint8 bApplyNormalizationToStereoSounds : 1
// Sound/SoundAttenuation.h:184  uint8 bEnableLogFrequencyScaling : 1
// Sound/SoundAttenuation.h:188  uint8 bEnableSubmixSends : 1
// Sound/SoundAttenuation.h:192  uint8 bEnableSourceDataOverride : 1
// Sound/SoundAttenuation.h:196  uint8 bEnableSendToAudioLink : 1
// Sound/SoundAttenuation.h:200  TEnumAsByte<enum ESoundSpatializationAlgorithm> SpatializationAlgorithm
// Sound/SoundAttenuation.h:208  float BinauralRadius
// Sound/SoundAttenuation.h:220  EAirAbsorptionMethod AbsorptionMethod
// Sound/SoundAttenuation.h:224  TEnumAsByte<enum ECollisionChannel> OcclusionTraceChannel
// Sound/SoundAttenuation.h:228  EReverbSendMethod ReverbSendMethod
// Sound/SoundAttenuation.h:232  EPriorityAttenuationMethod PriorityAttenuationMethod
// Sound/SoundAttenuation.h:244  float NonSpatializedRadiusStart
// Sound/SoundAttenuation.h:248  float NonSpatializedRadiusEnd
// Sound/SoundAttenuation.h:252  ENonSpatializedRadiusSpeakerMapMode NonSpatializedRadiusMode
// Sound/SoundAttenuation.h:256  float StereoSpread
// Sound/SoundAttenuation.h:271  float LPFRadiusMin
// Sound/SoundAttenuation.h:275  float LPFRadiusMax
// Sound/SoundAttenuation.h:279  float LPFFrequencyAtMin
// Sound/SoundAttenuation.h:283  float LPFFrequencyAtMax
// Sound/SoundAttenuation.h:287  float HPFFrequencyAtMin
// Sound/SoundAttenuation.h:291  float HPFFrequencyAtMax
// Sound/SoundAttenuation.h:295  float FocusAzimuth
// Sound/SoundAttenuation.h:299  float NonFocusAzimuth
// Sound/SoundAttenuation.h:303  float FocusDistanceScale
// Sound/SoundAttenuation.h:307  float NonFocusDistanceScale
// Sound/SoundAttenuation.h:311  float FocusPriorityScale
// Sound/SoundAttenuation.h:315  float NonFocusPriorityScale
// Sound/SoundAttenuation.h:319  float FocusVolumeAttenuation
// Sound/SoundAttenuation.h:323  float NonFocusVolumeAttenuation
// Sound/SoundAttenuation.h:327  float FocusAttackInterpSpeed
// Sound/SoundAttenuation.h:331  float FocusReleaseInterpSpeed
// Sound/SoundAttenuation.h:335  float OcclusionLowPassFilterFrequency
// Sound/SoundAttenuation.h:339  float OcclusionVolumeAttenuation
// Sound/SoundAttenuation.h:343  float OcclusionInterpolationTime
// Sound/SoundAttenuation.h:355  float ReverbWetLevelMin
// Sound/SoundAttenuation.h:359  float ReverbWetLevelMax
// Sound/SoundAttenuation.h:363  float ReverbDistanceMin
// Sound/SoundAttenuation.h:367  float ReverbDistanceMax
// Sound/SoundAttenuation.h:371  float ManualReverbSendLevel
// Sound/SoundAttenuation.h:375  float PriorityAttenuationMin
// Sound/SoundAttenuation.h:379  float PriorityAttenuationMax
// Sound/SoundAttenuation.h:383  float PriorityAttenuationDistanceMin
// Sound/SoundAttenuation.h:387  float PriorityAttenuationDistanceMax
// Sound/SoundAttenuation.h:391  float ManualPriorityAttenuation
// Sound/SoundAttenuation.h:443  class USoundAttenuation : public UObject
// Sound/SoundAttenuation.h:448  FSoundAttenuationSettings Attenuation
//
// Error format: "inspect_sound_attenuation: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_sound_attenuation.

#include "Sound/SoundAttenuation.h"
#include "Engine/EngineTypes.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
    template <typename T>
    static FString EnumToCleanString(T Value)
    {
        const FString Raw = UEnum::GetValueAsString(Value);
        int32 Idx = INDEX_NONE;
        if (Raw.FindLastChar(TEXT(':'), Idx))
        {
            return Raw.Mid(Idx + 1);
        }
        return Raw;
    }
}

class FHandler_InspectSoundAttenuation : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_sound_attenuation"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid() || !Params->HasTypedField<EJson::String>(TEXT("path")))
        {
            OutError = TEXT("inspect_sound_attenuation: missing_required_field: required string field 'path' is missing or empty");
            return nullptr;
        }

        FString InputPath = Params->GetStringField(TEXT("path"));
        if (InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_sound_attenuation: missing_required_field: required string field 'path' is missing or empty");
            return nullptr;
        }

        FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        UObject* RawAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!RawAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_sound_attenuation: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        USoundAttenuation* SoundAttenuation = Cast<USoundAttenuation>(RawAsset);
        if (!SoundAttenuation)
        {
            OutError = FString::Printf(
                TEXT("inspect_sound_attenuation: not_a_sound_attenuation: '%s' is not a USoundAttenuation"),
                *InputPath);
            return nullptr;
        }

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), SoundAttenuation->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);

        TSharedPtr<FJsonObject> DistanceObject = MakeShared<FJsonObject>();
        DistanceObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bAttenuate != 0);
        DistanceObject->SetStringField(TEXT("distance_algorithm"), EnumToCleanString(SoundAttenuation->Attenuation.DistanceAlgorithm));
        DistanceObject->SetStringField(TEXT("attenuation_shape"), EnumToCleanString(SoundAttenuation->Attenuation.AttenuationShape.GetValue()));
        DistanceObject->SetStringField(TEXT("falloff_mode"), EnumToCleanString(SoundAttenuation->Attenuation.FalloffMode));
        DistanceObject->SetNumberField(TEXT("db_attenuation_at_max"), SoundAttenuation->Attenuation.dBAttenuationAtMax);
        TSharedPtr<FJsonObject> AttenuationShapeExtentsObject = MakeShared<FJsonObject>();
        AttenuationShapeExtentsObject->SetNumberField(TEXT("x"), SoundAttenuation->Attenuation.AttenuationShapeExtents.X);
        AttenuationShapeExtentsObject->SetNumberField(TEXT("y"), SoundAttenuation->Attenuation.AttenuationShapeExtents.Y);
        AttenuationShapeExtentsObject->SetNumberField(TEXT("z"), SoundAttenuation->Attenuation.AttenuationShapeExtents.Z);
        DistanceObject->SetObjectField(TEXT("attenuation_shape_extents"), AttenuationShapeExtentsObject);
        DistanceObject->SetNumberField(TEXT("cone_offset"), SoundAttenuation->Attenuation.ConeOffset);
        DistanceObject->SetNumberField(TEXT("falloff_distance"), SoundAttenuation->Attenuation.FalloffDistance);
        DistanceObject->SetNumberField(TEXT("cone_sphere_radius"), SoundAttenuation->Attenuation.ConeSphereRadius);
        DistanceObject->SetNumberField(TEXT("cone_sphere_falloff_distance"), SoundAttenuation->Attenuation.ConeSphereFalloffDistance);
        Out->SetObjectField(TEXT("distance"), DistanceObject);

        TSharedPtr<FJsonObject> SpatializationObject = MakeShared<FJsonObject>();
        SpatializationObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bSpatialize != 0);
        if (SoundAttenuation->Attenuation.bSpatialize != 0)
        {
            SpatializationObject->SetStringField(TEXT("spatialization_algorithm"), EnumToCleanString(SoundAttenuation->Attenuation.SpatializationAlgorithm.GetValue()));
            SpatializationObject->SetNumberField(TEXT("binaural_radius"), SoundAttenuation->Attenuation.BinauralRadius);
            SpatializationObject->SetNumberField(TEXT("non_spatialized_radius_start"), SoundAttenuation->Attenuation.NonSpatializedRadiusStart);
            SpatializationObject->SetNumberField(TEXT("non_spatialized_radius_end"), SoundAttenuation->Attenuation.NonSpatializedRadiusEnd);
            SpatializationObject->SetStringField(TEXT("non_spatialized_radius_mode"), EnumToCleanString(SoundAttenuation->Attenuation.NonSpatializedRadiusMode));
            SpatializationObject->SetNumberField(TEXT("stereo_spread"), SoundAttenuation->Attenuation.StereoSpread);
        }
        Out->SetObjectField(TEXT("spatialization"), SpatializationObject);

        TSharedPtr<FJsonObject> AirAbsorptionObject = MakeShared<FJsonObject>();
        AirAbsorptionObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bAttenuateWithLPF != 0);
        if (SoundAttenuation->Attenuation.bAttenuateWithLPF != 0)
        {
            AirAbsorptionObject->SetStringField(TEXT("absorption_method"), EnumToCleanString(SoundAttenuation->Attenuation.AbsorptionMethod));
            AirAbsorptionObject->SetNumberField(TEXT("lpf_radius_min"), SoundAttenuation->Attenuation.LPFRadiusMin);
            AirAbsorptionObject->SetNumberField(TEXT("lpf_radius_max"), SoundAttenuation->Attenuation.LPFRadiusMax);
            AirAbsorptionObject->SetNumberField(TEXT("lpf_frequency_at_min"), SoundAttenuation->Attenuation.LPFFrequencyAtMin);
            AirAbsorptionObject->SetNumberField(TEXT("lpf_frequency_at_max"), SoundAttenuation->Attenuation.LPFFrequencyAtMax);
            AirAbsorptionObject->SetNumberField(TEXT("hpf_frequency_at_min"), SoundAttenuation->Attenuation.HPFFrequencyAtMin);
            AirAbsorptionObject->SetNumberField(TEXT("hpf_frequency_at_max"), SoundAttenuation->Attenuation.HPFFrequencyAtMax);
        }
        Out->SetObjectField(TEXT("air_absorption"), AirAbsorptionObject);

        TSharedPtr<FJsonObject> ListenerFocusObject = MakeShared<FJsonObject>();
        ListenerFocusObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bEnableListenerFocus != 0);
        if (SoundAttenuation->Attenuation.bEnableListenerFocus != 0)
        {
            ListenerFocusObject->SetNumberField(TEXT("focus_azimuth"), SoundAttenuation->Attenuation.FocusAzimuth);
            ListenerFocusObject->SetNumberField(TEXT("non_focus_azimuth"), SoundAttenuation->Attenuation.NonFocusAzimuth);
            ListenerFocusObject->SetNumberField(TEXT("focus_distance_scale"), SoundAttenuation->Attenuation.FocusDistanceScale);
            ListenerFocusObject->SetNumberField(TEXT("non_focus_distance_scale"), SoundAttenuation->Attenuation.NonFocusDistanceScale);
            ListenerFocusObject->SetNumberField(TEXT("focus_priority_scale"), SoundAttenuation->Attenuation.FocusPriorityScale);
            ListenerFocusObject->SetNumberField(TEXT("non_focus_priority_scale"), SoundAttenuation->Attenuation.NonFocusPriorityScale);
            ListenerFocusObject->SetNumberField(TEXT("focus_volume_attenuation"), SoundAttenuation->Attenuation.FocusVolumeAttenuation);
            ListenerFocusObject->SetNumberField(TEXT("non_focus_volume_attenuation"), SoundAttenuation->Attenuation.NonFocusVolumeAttenuation);
            if (SoundAttenuation->Attenuation.bEnableFocusInterpolation != 0)
            {
                ListenerFocusObject->SetNumberField(TEXT("focus_attack_interp_speed"), SoundAttenuation->Attenuation.FocusAttackInterpSpeed);
                ListenerFocusObject->SetNumberField(TEXT("focus_release_interp_speed"), SoundAttenuation->Attenuation.FocusReleaseInterpSpeed);
            }
        }
        Out->SetObjectField(TEXT("listener_focus"), ListenerFocusObject);

        TSharedPtr<FJsonObject> OcclusionObject = MakeShared<FJsonObject>();
        OcclusionObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bEnableOcclusion != 0);
        if (SoundAttenuation->Attenuation.bEnableOcclusion != 0)
        {
            OcclusionObject->SetStringField(TEXT("occlusion_trace_channel"), EnumToCleanString(SoundAttenuation->Attenuation.OcclusionTraceChannel.GetValue()));
            OcclusionObject->SetBoolField(TEXT("use_complex_collision_for_occlusion"), SoundAttenuation->Attenuation.bUseComplexCollisionForOcclusion != 0);
            OcclusionObject->SetNumberField(TEXT("occlusion_low_pass_filter_frequency"), SoundAttenuation->Attenuation.OcclusionLowPassFilterFrequency);
            OcclusionObject->SetNumberField(TEXT("occlusion_volume_attenuation"), SoundAttenuation->Attenuation.OcclusionVolumeAttenuation);
            OcclusionObject->SetNumberField(TEXT("occlusion_interpolation_time"), SoundAttenuation->Attenuation.OcclusionInterpolationTime);
        }
        Out->SetObjectField(TEXT("occlusion"), OcclusionObject);

        TSharedPtr<FJsonObject> ReverbSendObject = MakeShared<FJsonObject>();
        ReverbSendObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bEnableReverbSend != 0);
        if (SoundAttenuation->Attenuation.bEnableReverbSend != 0)
        {
            ReverbSendObject->SetStringField(TEXT("reverb_send_method"), EnumToCleanString(SoundAttenuation->Attenuation.ReverbSendMethod));
            ReverbSendObject->SetNumberField(TEXT("reverb_wet_level_min"), SoundAttenuation->Attenuation.ReverbWetLevelMin);
            ReverbSendObject->SetNumberField(TEXT("reverb_wet_level_max"), SoundAttenuation->Attenuation.ReverbWetLevelMax);
            ReverbSendObject->SetNumberField(TEXT("reverb_distance_min"), SoundAttenuation->Attenuation.ReverbDistanceMin);
            ReverbSendObject->SetNumberField(TEXT("reverb_distance_max"), SoundAttenuation->Attenuation.ReverbDistanceMax);
            ReverbSendObject->SetNumberField(TEXT("manual_reverb_send_level"), SoundAttenuation->Attenuation.ManualReverbSendLevel);
        }
        Out->SetObjectField(TEXT("reverb_send"), ReverbSendObject);

        TSharedPtr<FJsonObject> PriorityAttenuationObject = MakeShared<FJsonObject>();
        PriorityAttenuationObject->SetBoolField(TEXT("enabled"), SoundAttenuation->Attenuation.bEnablePriorityAttenuation != 0);
        if (SoundAttenuation->Attenuation.bEnablePriorityAttenuation != 0)
        {
            PriorityAttenuationObject->SetStringField(TEXT("priority_attenuation_method"), EnumToCleanString(SoundAttenuation->Attenuation.PriorityAttenuationMethod));
            PriorityAttenuationObject->SetNumberField(TEXT("priority_attenuation_min"), SoundAttenuation->Attenuation.PriorityAttenuationMin);
            PriorityAttenuationObject->SetNumberField(TEXT("priority_attenuation_max"), SoundAttenuation->Attenuation.PriorityAttenuationMax);
            PriorityAttenuationObject->SetNumberField(TEXT("priority_attenuation_distance_min"), SoundAttenuation->Attenuation.PriorityAttenuationDistanceMin);
            PriorityAttenuationObject->SetNumberField(TEXT("priority_attenuation_distance_max"), SoundAttenuation->Attenuation.PriorityAttenuationDistanceMax);
            PriorityAttenuationObject->SetNumberField(TEXT("manual_priority_attenuation"), SoundAttenuation->Attenuation.ManualPriorityAttenuation);
        }
        Out->SetObjectField(TEXT("priority_attenuation"), PriorityAttenuationObject);

        TSharedPtr<FJsonObject> FeatureFlagsObject = MakeShared<FJsonObject>();
        FeatureFlagsObject->SetBoolField(TEXT("apply_normalization_to_stereo_sounds"), SoundAttenuation->Attenuation.bApplyNormalizationToStereoSounds != 0);
        FeatureFlagsObject->SetBoolField(TEXT("enable_log_frequency_scaling"), SoundAttenuation->Attenuation.bEnableLogFrequencyScaling != 0);
        FeatureFlagsObject->SetBoolField(TEXT("enable_submix_sends"), SoundAttenuation->Attenuation.bEnableSubmixSends != 0);
        FeatureFlagsObject->SetBoolField(TEXT("enable_source_data_override"), SoundAttenuation->Attenuation.bEnableSourceDataOverride != 0);
        FeatureFlagsObject->SetBoolField(TEXT("enable_send_to_audio_link"), SoundAttenuation->Attenuation.bEnableSendToAudioLink != 0);
        Out->SetObjectField(TEXT("feature_flags"), FeatureFlagsObject);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectSoundAttenuation()
{
    return MakeShared<FHandler_InspectSoundAttenuation>();
}
