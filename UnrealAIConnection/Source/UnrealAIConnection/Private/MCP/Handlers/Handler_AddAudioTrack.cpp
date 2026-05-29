// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_audio_track - add a root-level (master) UMovieSceneAudioTrack to a Level
// Sequence and append a sound section playing a USoundBase (SoundWave /
// SoundCue / MetaSound source) starting at a given frame. Headless asset edit.
//
// is_mutating = false: asset authoring, not a level/undo-stack edit.
//
// UE 5.7 surface used (verified against F:/UE_5.7 source):
//   - UMovieScene::AddTrack<UMovieSceneAudioTrack>() (root/master, no binding)
//                                                          MovieScene.h:648/660
//   - UMovieSceneAudioTrack::AddNewSound(USoundBase*, FFrameNumber) -> UMovieSceneSection*
//                                                          MovieSceneAudioTrack.h:33 (forwards
//                                                          AddNewSoundOnRow(.., INDEX_NONE))
//   - UMovieSceneAudioTrack::AddNewSoundOnRow(USoundBase*, FFrameNumber, int32 RowIndex)
//                                                          MovieSceneAudioTrack.h:30
//   - UMovieSceneAudioSection::SetLooping(bool)            MovieSceneAudioSection.h:104
//   - UMovieSceneAudioSection::GetSoundVolumeChannel() -> FMovieSceneFloatChannel&
//                                                          MovieSceneAudioSection.h:61 (volume is a
//                                                          channel default, not a scalar setter)
//   - USoundBase (base for wave/cue/metasound)            SoundBase.h:108 (module Engine)
//   - UMovieSceneSection::SetRange(const TRange<FFrameNumber>&)   MovieSceneSection.h:318
//   - UEditorAssetLibrary::LoadAsset + Cast<USoundBase>   (Handler_BindActorToSequence.cpp:56)
//   - FFrameRate::TransformTime                           (Handler_CreateSequence.cpp:160)
//
// GOTCHAS handled (per verified spec):
//   * Audio track is a ROOT/MASTER track (no object binding) — use the no-GUID
//     AddTrack<T>() overload, NOT the AddTrack(TrackClass, FGuid) binding form.
//   * 'volume' is NOT a scalar setter — it is an FMovieSceneFloatChannel
//     (SoundVolume). We set the channel default value only.
//   * sound_path may be a USoundWave, USoundCue or MetaSound source — all derive
//     from USoundBase, so Cast<USoundBase> is the right (non-narrowing) check.
//   * AddNewSound/AddNewSoundOnRow auto-size the section from the sound's
//     duration; the caller normally does not set an end.
//
// Error format: "add_audio_track: <error_code>: <detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_sequence,
// sound_not_found, not_a_sound, track_add_failed, section_add_failed,
// save_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "Tracks/MovieSceneAudioTrack.h"
#include "Sections/MovieSceneAudioSection.h"
#include "Channels/MovieSceneFloatChannel.h"
#include "Sound/SoundBase.h"
#include "Misc/FrameRate.h"
#include "Misc/FrameNumber.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_AddAudioTrack : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_audio_track"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("add_audio_track: missing_required_field: 'sequence_path' and 'sound_path' are required");
            return nullptr;
        }

        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("add_audio_track: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        FString SoundPath;
        if (!Params->TryGetStringField(TEXT("sound_path"), SoundPath) || SoundPath.IsEmpty())
        {
            OutError = TEXT("add_audio_track: missing_required_field: 'sound_path' is required and must not be empty");
            return nullptr;
        }

        // --- resolve to ULevelSequence (mirrors inspect_sequence) -----------

        const FString SequenceObjectPath = UCMCPAssetPath::ToObjectPath(SequencePath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(SequenceObjectPath);
        if (!LoadedAsset)
        {
            if (!UEditorAssetLibrary::DoesAssetExist(SequenceObjectPath))
            {
                OutError = FString::Printf(
                    TEXT("add_audio_track: asset_not_found: '%s' is not in the asset registry"),
                    *SequencePath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("add_audio_track: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *SequencePath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("add_audio_track: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *SequencePath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("add_audio_track: not_a_sequence: sequence has no MovieScene");
            return nullptr;
        }

        // --- resolve the sound asset ----------------------------------------

        const FString SoundObjectPath = UCMCPAssetPath::ToObjectPath(SoundPath);
        UObject* LoadedSound = UEditorAssetLibrary::LoadAsset(SoundObjectPath);
        if (!LoadedSound)
        {
            OutError = FString::Printf(
                TEXT("add_audio_track: sound_not_found: '%s' is not in the asset registry"),
                *SoundPath);
            return nullptr;
        }
        USoundBase* Sound = Cast<USoundBase>(LoadedSound);
        if (!Sound)
        {
            OutError = FString::Printf(
                TEXT("add_audio_track: not_a_sound: '%s' is a %s, not a USoundBase (SoundWave / SoundCue / MetaSound source)"),
                *SoundPath, *LoadedSound->GetClass()->GetName());
            return nullptr;
        }

        // --- optional params ------------------------------------------------

        const FFrameRate DisplayRate = Scene->GetDisplayRate();
        const FFrameRate TickResolution = Scene->GetTickResolution();

        double StartFrameRaw = 0.0;
        Params->TryGetNumberField(TEXT("start_frame"), StartFrameRaw);
        const int32 StartFrame = static_cast<int32>(StartFrameRaw);
        const FFrameNumber StartTicks = FFrameRate::TransformTime(
            FFrameTime(FFrameNumber(StartFrame)), DisplayRate, TickResolution).FrameNumber;

        // row_index default -1 (= next free row, INDEX_NONE).
        double RowIndexRaw = -1.0;
        Params->TryGetNumberField(TEXT("row_index"), RowIndexRaw);
        const int32 RowIndex = static_cast<int32>(RowIndexRaw);

        // --- add the master audio track -------------------------------------
        //
        // Templated AddTrack<T>() is the no-GUID (root/master) overload.
        UMovieSceneAudioTrack* AudioTrack = Scene->AddTrack<UMovieSceneAudioTrack>();
        if (!AudioTrack)
        {
            OutError = TEXT("add_audio_track: track_add_failed: AddTrack<UMovieSceneAudioTrack>() returned null");
            return nullptr;
        }

        // --- add the sound section ------------------------------------------
        //
        // Use AddNewSoundOnRow when a row was supplied; otherwise AddNewSound
        // (which forwards INDEX_NONE).
        UMovieSceneSection* Section = (RowIndex >= 0)
            ? AudioTrack->AddNewSoundOnRow(Sound, StartTicks, RowIndex)
            : AudioTrack->AddNewSound(Sound, StartTicks);
        if (!Section)
        {
            // Roll back the empty track we just added so a section-add failure
            // doesn't strand a sound-less audio track on the MovieScene. It is a
            // root/master track, so remove it via UMovieScene::RemoveTrack
            // (MovieScene.h:585 — takes a UMovieSceneTrack&).
            Scene->RemoveTrack(*AudioTrack);
            OutError = TEXT("add_audio_track: section_add_failed: AddNewSound returned null");
            return nullptr;
        }

        UMovieSceneAudioSection* AudioSection = Cast<UMovieSceneAudioSection>(Section);
        if (AudioSection)
        {
            // Optional looping.
            bool bLooping = false;
            if (Params->TryGetBoolField(TEXT("looping"), bLooping))
            {
                AudioSection->SetLooping(bLooping);
            }

            // Optional volume — a float channel DEFAULT, not a scalar setter.
            // UMovieSceneAudioSection only exposes GetSoundVolumeChannel() as a
            // const accessor (MovieSceneAudioSection.h:61), but the backing
            // SoundVolume UPROPERTY (:248) is itself non-const, so const_cast on
            // the returned reference is well-defined here. We set only the
            // channel default (a constant volume); per the verified spec, full
            // volume keyframing is deferred to keep this handler low-complexity.
            double Volume = 1.0;
            if (Params->TryGetNumberField(TEXT("volume"), Volume))
            {
                FMovieSceneFloatChannel& VolumeChannel =
                    const_cast<FMovieSceneFloatChannel&>(AudioSection->GetSoundVolumeChannel());
                VolumeChannel.SetDefault(static_cast<float>(Volume));
            }
        }

        // --- save -----------------------------------------------------------

        if (!UEditorAssetLibrary::SaveAsset(SequenceObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("add_audio_track: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). The audio track was added in memory but not persisted."),
                *SequenceObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        const int32 SectionIndex = AudioTrack->GetAllSections().IndexOfByKey(Section);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);
        Out->SetStringField(TEXT("track_class"), TEXT("MovieSceneAudioTrack"));
        Out->SetStringField(TEXT("sound_path"), SoundObjectPath);
        Out->SetNumberField(TEXT("section_index"), static_cast<double>(SectionIndex));
        Out->SetNumberField(TEXT("start_frame"), static_cast<double>(StartTicks.Value));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddAudioTrack()
{
    return MakeShared<FHandler_AddAudioTrack>();
}
