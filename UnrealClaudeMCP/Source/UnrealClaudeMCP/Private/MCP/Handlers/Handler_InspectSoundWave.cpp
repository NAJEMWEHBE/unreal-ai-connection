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
        Out->SetNumberField(TEXT("sample_rate"), static_cast<double>(Wave->SampleRate));
        Out->SetNumberField(TEXT("num_channels"), static_cast<double>(Wave->NumChannels));
        // GetNumFrames() returns int64 (SoundWave.h:1248); cast straight to
        // double to preserve up-to-2^53 range. Going through int32 first
        // would silently truncate ~12h+ multichannel waves (HANDOFF trap-
        // table line 127: cast-before-clamp UB family).
        Out->SetNumberField(TEXT("num_frames"), static_cast<double>(Wave->GetNumFrames()));
        Out->SetNumberField(TEXT("duration"), Wave->GetDuration());
        Out->SetStringField(TEXT("compression_type"), EnumToCleanString(Wave->GetSoundAssetCompressionType()));
        Out->SetStringField(TEXT("runtime_format"), Wave->GetRuntimeFormat().ToString());
        Out->SetStringField(TEXT("sound_group"), EnumToCleanString(Wave->SoundGroup.GetValue()));
        Out->SetStringField(TEXT("loading_behavior"), EnumToCleanString(Wave->LoadingBehavior));
        Out->SetBoolField(TEXT("is_looping"), Wave->bLooping != 0);
        Out->SetBoolField(TEXT("is_streaming"), Wave->IsStreaming());
        Out->SetNumberField(TEXT("resource_size"), static_cast<double>(Wave->GetResourceSize()));
        Out->SetBoolField(TEXT("supports_subtitles"), Wave->SupportsSubtitles());
        Out->SetNumberField(TEXT("subtitle_count"), static_cast<double>(Wave->Subtitles.Num()));
        Out->SetNumberField(TEXT("cue_point_count"), static_cast<double>(Wave->GetCuePoints().Num()));
        Out->SetNumberField(TEXT("loop_region_count"), static_cast<double>(Wave->GetLoopRegions().Num()));

        const int32 CompressedDataSize = Wave->GetCompressedDataSize(Wave->GetRuntimeFormat());
        if (CompressedDataSize > 0)
        {
            Out->SetNumberField(TEXT("compressed_data_size"), static_cast<double>(CompressedDataSize));
        }

#if WITH_EDITORONLY_DATA
        if (Wave->ImportedSampleRate != 0)
        {
            Out->SetNumberField(TEXT("imported_sample_rate"), static_cast<double>(Wave->ImportedSampleRate));
        }

        if (Wave->LUFS != 0.0f)
        {
            Out->SetNumberField(TEXT("lufs"), Wave->LUFS);
        }

        if (Wave->SamplePeakDB != 0.0f)
        {
            Out->SetNumberField(TEXT("sample_peak_db"), Wave->SamplePeakDB);
        }

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
