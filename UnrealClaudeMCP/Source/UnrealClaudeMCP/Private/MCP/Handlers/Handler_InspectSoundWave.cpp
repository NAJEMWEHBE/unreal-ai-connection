// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

// inspect_sound_wave - inspect a USoundWave asset's stable editor-visible surface.
//
// UE 5.7 surface citations:
// Sound/SoundWave.h:442  TEnumAsByte<ESoundGroup> SoundGroup
// Sound/SoundWave.h:446  uint8 bLooping:1
// Sound/SoundWave.h:463  ESoundAssetCompressionType GetSoundAssetCompressionType() const
// Sound/SoundWave.h:490  const TArray<FSoundWaveCuePoint>& GetCuePoints() const
// Sound/SoundWave.h:506  const TArray<FSoundWaveCuePoint>& GetLoopRegions() const
// Sound/SoundWave.h:509  FName GetRuntimeFormat() const
// Sound/SoundWave.h:743  ESoundWaveLoadingBehavior LoadingBehavior
// Sound/SoundWave.h:774  int32 NumChannels
// Sound/SoundWave.h:787  float LUFS
// Sound/SoundWave.h:791  float SamplePeakDB
// Sound/SoundWave.h:799  int32 SampleRate
// Sound/SoundWave.h:804  int32 ImportedSampleRate
// Sound/SoundWave.h:827  int32 GetResourceSize() const
// Sound/SoundWave.h:836  TArray<FSubtitleCue> Subtitles
// Sound/SoundWave.h:841  FString Comment
// Sound/SoundWave.h:1180 virtual float GetDuration() const override
// Sound/SoundWave.h:1182 virtual bool SupportsSubtitles() const override
// Sound/SoundWave.h:1248 int64 GetNumFrames() const
// Sound/SoundWave.h:1313 int32 GetCompressedDataSize(FName Format)
// Sound/SoundWave.h:1409 bool IsStreaming() const
// Sound/SoundBase.h:202  float Duration
//
// Error format: "inspect_sound_wave: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_sound_wave.

#include "Sound/SoundWave.h"
#include "Sound/SoundBase.h"
#include "AudioDefines.h"           // ESoundGroup
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "UCMCPCompat.h"

namespace
{
    // Per-file unique name: UE 5.1 buckets unity builds differently than 5.7
    // and merges this TU with Handler_InspectSoundAttenuation.cpp (identical
    // anon-namespace function template) into one unity blob -> C2995
    // "function template has already been defined". Unique per-file names are
    // version-agnostic; behavior is unchanged.
    template <typename T>
    static FString EnumToCleanString_SoundWave(T Value)
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

class FHandler_InspectSoundWave : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_sound_wave"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid() || !Params->HasTypedField<EJson::String>(TEXT("path")))
        {
            OutError = TEXT("inspect_sound_wave: missing_required_field: required string field 'path' is missing or empty");
            return nullptr;
        }

        FString InputPath = Params->GetStringField(TEXT("path"));
        if (InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_sound_wave: missing_required_field: required string field 'path' is missing or empty");
            return nullptr;
        }

        FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        UObject* RawAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!RawAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_sound_wave: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        USoundWave* Wave = Cast<USoundWave>(RawAsset);
        if (!Wave)
        {
            OutError = FString::Printf(
                TEXT("inspect_sound_wave: not_a_sound_wave: '%s' is a %s, not a USoundWave"),
                *InputPath, *RawAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Wave->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);
        Out->SetStringField(TEXT("sound_wave_class"), Wave->GetClass()->GetName());
        // SampleRate is protected on USoundWave (SoundWave.h:799); use the
        // public GetSampleRateForCurrentPlatform() accessor (SoundWave.h:1443)
        // which resolves per-platform overrides at the time of inspection.
        Out->SetNumberField(TEXT("sample_rate"), static_cast<double>(Wave->GetSampleRateForCurrentPlatform()));
        Out->SetNumberField(TEXT("num_channels"), static_cast<double>(Wave->NumChannels));
#if UCMCP_ENGINE_AT_LEAST(5, 2)
        // >=5.x verified-correct path. USoundWave::GetNumFrames() returns int64
        // (header citation SoundWave.h:1248); cast straight to double to
        // preserve up-to-2^53 range. Going through int32 first would silently
        // truncate ~12h+ multichannel waves (HANDOFF trap-table line 127:
        // cast-before-clamp UB family).
        Out->SetNumberField(TEXT("num_frames"), static_cast<double>(Wave->GetNumFrames()));
#else
        // UE 5.1: USoundWave has NO public GetNumFrames() accessor and no
        // NumFrames field on the class itself (verified: the GetNumFrames()
        // at F:\UE_5.1\Engine\Source\Runtime\Engine\Classes\Sound\SoundWave.h:1270
        // belongs to the separate class FSoundWaveData, not USoundWave whose
        // body is lines 393-1226). No 5.1 equivalent -> the "num_frames" JSON
        // field is gated out entirely on 5.1 per the directive ("if no 5.1
        // equivalent, gate the whole JSON field").
        // UNVERIFIED-COMPILE: 5.2..5.6 GetNumFrames-on-USoundWave boundary
        // not verifiable here; 5.2 chosen so verified-correct 5.7 is unchanged.
#endif
        Out->SetNumberField(TEXT("duration"), Wave->GetDuration());
        Out->SetStringField(TEXT("compression_type"), EnumToCleanString_SoundWave(Wave->GetSoundAssetCompressionType()));
        Out->SetStringField(TEXT("runtime_format"), Wave->GetRuntimeFormat().ToString());
        Out->SetStringField(TEXT("sound_group"), EnumToCleanString_SoundWave(Wave->SoundGroup.GetValue()));
        Out->SetStringField(TEXT("loading_behavior"), EnumToCleanString_SoundWave(Wave->LoadingBehavior));
        Out->SetBoolField(TEXT("is_looping"), Wave->bLooping != 0);
        Out->SetBoolField(TEXT("is_streaming"), Wave->IsStreaming());
        Out->SetNumberField(TEXT("resource_size"), static_cast<double>(Wave->GetResourceSize()));
        Out->SetBoolField(TEXT("supports_subtitles"), Wave->SupportsSubtitles());
        Out->SetNumberField(TEXT("subtitle_count"), static_cast<double>(Wave->Subtitles.Num()));
#if UCMCP_ENGINE_AT_LEAST(5, 2)
        // >=5.x verified-correct path (header citations SoundWave.h:490 / :506).
        Out->SetNumberField(TEXT("cue_point_count"), static_cast<double>(Wave->GetCuePoints().Num()));
        Out->SetNumberField(TEXT("loop_region_count"), static_cast<double>(Wave->GetLoopRegions().Num()));
#else
        // UE 5.1: USoundWave has NO GetCuePoints()/GetLoopRegions() accessors
        // (those at SoundWave.h:1264 belong to FSoundWaveData, not USoundWave).
        // - CuePoints: a PUBLIC UPROPERTY member exists on USoundWave at
        //   F:\UE_5.1\Engine\Source\Runtime\Engine\Classes\Sound\SoundWave.h:711
        //   (declared before the `protected:` at :724), so read it directly to
        //   preserve the "cue_point_count" field with identical semantics.
        // - GetLoopRegions / loop-region data: ABSENT entirely on 5.1
        //   USoundWave (no member at all). No 5.1 equivalent -> the
        //   "loop_region_count" JSON field is gated out on 5.1 per the
        //   directive.
        // UNVERIFIED-COMPILE: 5.2..5.6 accessor boundary not verifiable here;
        // 5.2 chosen so verified-correct 5.7 is unchanged.
        Out->SetNumberField(TEXT("cue_point_count"), static_cast<double>(Wave->CuePoints.Num()));
#endif

        const int32 CompressedDataSize = Wave->GetCompressedDataSize(Wave->GetRuntimeFormat());
        if (CompressedDataSize > 0)
        {
            Out->SetNumberField(TEXT("compressed_data_size"), static_cast<double>(CompressedDataSize));
        }

#if WITH_EDITORONLY_DATA
#if UCMCP_ENGINE_AT_LEAST(5, 2)
        // >=5.x verified-correct path. ImportedSampleRate is protected on
        // USoundWave (header citation SoundWave.h:804); use the public
        // GetImportedSampleRate() accessor (SoundWave.h:1221). LUFS
        // (SoundWave.h:787) and SamplePeakDB (SoundWave.h:791) are public.
        const uint32 ImportedRate = Wave->GetImportedSampleRate();
        if (ImportedRate != 0)
        {
            Out->SetNumberField(TEXT("imported_sample_rate"), static_cast<double>(ImportedRate));
        }

        if (Wave->LUFS != 0.0f)
        {
            Out->SetNumberField(TEXT("lufs"), Wave->LUFS);
        }

        if (Wave->SamplePeakDB != 0.0f)
        {
            Out->SetNumberField(TEXT("sample_peak_db"), Wave->SamplePeakDB);
        }
#else
        // UE 5.1: NONE of these have a usable USoundWave surface:
        //  - GetImportedSampleRate(): ABSENT. Only a PROTECTED member
        //    ImportedSampleRate exists (inside WITH_EDITORONLY_DATA at
        //    F:\UE_5.1\Engine\Source\Runtime\Engine\Classes\Sound\SoundWave.h:733,
        //    after `protected:` at :724) -> not accessible from this handler.
        //  - LUFS: ABSENT entirely on 5.1 USoundWave (loudness analysis added
        //    later; no member in the 393-1226 class body).
        //  - SamplePeakDB: ABSENT entirely on 5.1 USoundWave.
        // No 5.1 equivalents -> the imported_sample_rate / lufs /
        // sample_peak_db JSON fields are gated out on 5.1 per the directive.
        // UNVERIFIED-COMPILE: 5.2..5.6 boundary not verifiable here; 5.2
        // chosen so the verified-correct 5.7 path is unchanged.
#endif

        if (!Wave->Comment.IsEmpty())
        {
            Out->SetStringField(TEXT("comment"), Wave->Comment);
        }
#endif

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectSoundWave()
{
    return MakeShared<FHandler_InspectSoundWave>();
}
