// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_visibility_track - add a UMovieSceneVisibilityTrack to an EXISTING object
// binding (possessable/spawnable) on a Level Sequence and key the actor's
// visibility on/off at given frames. Headless asset edit.
//
// is_mutating = false: asset authoring, not a level/undo-stack edit.
//
// >>> INVERTED-BOOL CORRECTNESS TRAP (single biggest gotcha) <<<
// The underlying reflected property is the actor's HIDDEN flag, so the bool
// channel stores !visible: to make the actor VISIBLE we key the channel to
// FALSE, to HIDE it we key TRUE. We invert the caller's `visible` bool before
// AddKeys, exactly as the engine track editor does
// (VisibilityPropertyTrackEditor.cpp:37-39: KeyedValue = !GetPropertyValue<bool>()).
// We deliberately do NOT rely on the section's bIsExternallyInverted display
// flag to auto-invert; we store inverted values ourselves.
//
// UE 5.7 surface used (verified against F:/UE_5.7 source):
//   - UMovieSceneVisibilityTrack : UMovieScenePropertyTrack     MovieSceneVisibilityTrack.h:15
//   - UMovieScene::AddTrack<UMovieSceneVisibilityTrack>(const FGuid&) (binding-attached)
//                                                               MovieScene.h:513/536
//   - UMovieScenePropertyTrack::SetPropertyNameAndPath(FName, const FString&)
//                                                               MovieScenePropertyTrack.h:65
//   - UMovieSceneVisibilityTrack::CreateNewSection() -> UMovieSceneVisibilitySection
//                                                               MovieSceneVisibilityTrack.cpp:77
//   - UMovieSceneVisibilitySection : UMovieSceneBoolSection     MovieSceneVisibilitySection.h:17
//   - UMovieSceneBoolSection::GetChannel() -> FMovieSceneBoolChannel&   MovieSceneBoolSection.h:36
//   - FMovieSceneBoolChannel::GetData().AddKeys(TArray<FFrameNumber>, TArray<bool>)
//                                                               MovieSceneBoolChannel.h:104
//   - UMovieSceneTrack::AddSection(UMovieSceneSection&)
//   - UMovieSceneSection::SetRange(const TRange<FFrameNumber>&)  MovieSceneSection.h:318
//   - FFrameRate::TransformTime                                 (Handler_CreateSequence.cpp:160)
//
// UNVERIFIED (flagged in spec — host build must scrutinize):
//   * The literal reflected property name ('bHidden' vs 'bHiddenInGame'). UE
//     convention is the AActor 'bHidden' property; SetPropertyNameAndPath is
//     called with FName("bHidden"). VERIFY on the live editor by creating a
//     visibility track via the Sequencer UI and reading GetPropertyName().
//   * Whether SetPropertyNameAndPath alone (without the editor track-editor
//     metadata wiring) is enough for headless evaluation. Confirm a keyed
//     visibility track actually toggles the actor in PIE/render.
//
// Error format: "add_visibility_track: <error_code>: <detail>".
// Stable error codes: missing_required_field, invalid_guid, asset_not_found,
// not_a_sequence, binding_not_found, no_keys, track_add_failed,
// section_add_failed, save_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "MovieScenePossessable.h"
#include "MovieSceneSpawnable.h"
#include "Tracks/MovieSceneVisibilityTrack.h"
#include "Sections/MovieSceneVisibilitySection.h"
#include "Sections/MovieSceneBoolSection.h"
#include "Channels/MovieSceneBoolChannel.h"
#include "Misc/FrameRate.h"
#include "Misc/FrameNumber.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_AddVisibilityTrack : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_visibility_track"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("add_visibility_track: missing_required_field: 'sequence_path', 'binding_guid' and 'keys' are required");
            return nullptr;
        }

        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("add_visibility_track: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        FString GuidStr;
        if (!Params->TryGetStringField(TEXT("binding_guid"), GuidStr) || GuidStr.IsEmpty())
        {
            OutError = TEXT("add_visibility_track: missing_required_field: 'binding_guid' is required and must not be empty");
            return nullptr;
        }

        FGuid BindingGuid;
        if (!FGuid::Parse(GuidStr, BindingGuid) || !BindingGuid.IsValid())
        {
            OutError = FString::Printf(
                TEXT("add_visibility_track: invalid_guid: '%s' is not a parseable GUID"),
                *GuidStr);
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
                    TEXT("add_visibility_track: asset_not_found: '%s' is not in the asset registry"),
                    *SequencePath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("add_visibility_track: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *SequencePath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("add_visibility_track: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *SequencePath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("add_visibility_track: not_a_sequence: sequence has no MovieScene");
            return nullptr;
        }

        // --- validate the binding exists ------------------------------------
        //
        // Scan possessables + spawnables (Handler_InspectSequence.cpp:129-161).
        bool bBindingFound = false;
        const int32 PossessableCount = Scene->GetPossessableCount();
        for (int32 i = 0; i < PossessableCount && !bBindingFound; ++i)
        {
            if (Scene->GetPossessable(i).GetGuid() == BindingGuid)
            {
                bBindingFound = true;
            }
        }
        if (!bBindingFound)
        {
            const int32 SpawnableCount = Scene->GetSpawnableCount();
            for (int32 i = 0; i < SpawnableCount && !bBindingFound; ++i)
            {
                if (Scene->GetSpawnable(i).GetGuid() == BindingGuid)
                {
                    bBindingFound = true;
                }
            }
        }
        if (!bBindingFound)
        {
            OutError = FString::Printf(
                TEXT("add_visibility_track: binding_not_found: no possessable or spawnable binding with GUID '%s' exists in '%s'"),
                *BindingGuid.ToString(EGuidFormats::DigitsWithHyphens), *SequencePath);
            return nullptr;
        }

        // --- gather keys (display-rate frames + visible bool) ---------------
        //
        // Two input shapes:
        //   keys: [ { frame: int, visible: bool }, ... ]      (primary)
        //   visible_at_start: bool (+ optional start_frame)   (convenience single key)
        const FFrameRate DisplayRate = Scene->GetDisplayRate();
        const FFrameRate TickResolution = Scene->GetTickResolution();

        TArray<FFrameNumber> KeyTimes;
        TArray<bool> KeyValues;  // stored INVERTED (channel value = !visible)

        const TArray<TSharedPtr<FJsonValue>>* KeysArray = nullptr;
        if (Params->TryGetArrayField(TEXT("keys"), KeysArray) && KeysArray)
        {
            for (const TSharedPtr<FJsonValue>& Entry : *KeysArray)
            {
                const TSharedPtr<FJsonObject>* KeyObj = nullptr;
                if (!Entry.IsValid() || !Entry->TryGetObject(KeyObj) || !KeyObj || !(*KeyObj).IsValid())
                {
                    continue;
                }
                double FrameRaw = 0.0;
                (*KeyObj)->TryGetNumberField(TEXT("frame"), FrameRaw);
                bool bVisible = true;
                (*KeyObj)->TryGetBoolField(TEXT("visible"), bVisible);

                const FFrameNumber Ticks = FFrameRate::TransformTime(
                    FFrameTime(FFrameNumber(static_cast<int32>(FrameRaw))),
                    DisplayRate, TickResolution).FrameNumber;
                KeyTimes.Add(Ticks);
                // INVERSION: channel stores the hidden flag, so value = !visible.
                KeyValues.Add(!bVisible);
            }
        }
        else
        {
            // Convenience single-key form.
            bool bVisibleAtStart = false;
            if (Params->TryGetBoolField(TEXT("visible_at_start"), bVisibleAtStart))
            {
                double StartFrameRaw = 0.0;
                Params->TryGetNumberField(TEXT("start_frame"), StartFrameRaw);
                const FFrameNumber Ticks = FFrameRate::TransformTime(
                    FFrameTime(FFrameNumber(static_cast<int32>(StartFrameRaw))),
                    DisplayRate, TickResolution).FrameNumber;
                KeyTimes.Add(Ticks);
                KeyValues.Add(!bVisibleAtStart);
            }
        }

        if (KeyTimes.Num() == 0)
        {
            OutError = TEXT("add_visibility_track: no_keys: at least one key is required (provide 'keys' [{frame, visible}, ...] or 'visible_at_start')");
            return nullptr;
        }

        // --- add the binding-attached visibility track ----------------------

        UMovieSceneVisibilityTrack* VisTrack =
            Scene->AddTrack<UMovieSceneVisibilityTrack>(BindingGuid);
        if (!VisTrack)
        {
            OutError = TEXT("add_visibility_track: track_add_failed: AddTrack<UMovieSceneVisibilityTrack>(Guid) returned null");
            return nullptr;
        }

        // Bind the property track to the actor's hidden property. Without this
        // the track has no property and won't evaluate. UE convention: AActor
        // 'bHidden'. (Flagged unverified — host build to confirm the literal.)
        VisTrack->SetPropertyNameAndPath(FName(TEXT("bHidden")), TEXT("bHidden"));

        // --- create + add a section, span the keyed range -------------------
        //
        // AddTrack does NOT create a section. CreateNewSection() returns a
        // UMovieSceneVisibilitySection (VisibilityTrack.cpp:77); add it to the
        // track, then SetRange to span the keyed frames.
        UMovieSceneSection* Section = VisTrack->CreateNewSection();
        if (!Section)
        {
            OutError = TEXT("add_visibility_track: section_add_failed: CreateNewSection() returned null");
            return nullptr;
        }
        VisTrack->AddSection(*Section);

        // Compute the [min,max] of the key times for the section range. Use an
        // inclusive upper bound so the last key sits inside the section.
        FFrameNumber MinTick = KeyTimes[0];
        FFrameNumber MaxTick = KeyTimes[0];
        for (const FFrameNumber& T : KeyTimes)
        {
            MinTick = FMath::Min(MinTick, T);
            MaxTick = FMath::Max(MaxTick, T);
        }
        Section->SetRange(TRange<FFrameNumber>::Inclusive(MinTick, MaxTick));

        // --- write the (inverted) keys --------------------------------------

        UMovieSceneBoolSection* BoolSection = Cast<UMovieSceneBoolSection>(Section);
        if (!BoolSection)
        {
            OutError = TEXT("add_visibility_track: section_add_failed: visibility section is not a UMovieSceneBoolSection");
            return nullptr;
        }
        FMovieSceneBoolChannel& Channel = BoolSection->GetChannel();
        // TMovieSceneChannelData<bool> exposes AddKey(FFrameNumber, bool) (singular);
        // there is no array AddKeys overload. AddKey assumes ascending time order,
        // so insert keys sorted by time.
        TMovieSceneChannelData<bool> ChannelData = Channel.GetData();
        TArray<int32> KeyOrder;
        KeyOrder.Reserve(KeyTimes.Num());
        for (int32 KeyIdx = 0; KeyIdx < KeyTimes.Num(); ++KeyIdx)
        {
            KeyOrder.Add(KeyIdx);
        }
        KeyOrder.Sort([&KeyTimes](int32 A, int32 B) { return KeyTimes[A] < KeyTimes[B]; });
        for (int32 OrderedIdx : KeyOrder)
        {
            ChannelData.AddKey(KeyTimes[OrderedIdx], KeyValues[OrderedIdx]);
        }

        // --- save -----------------------------------------------------------

        if (!UEditorAssetLibrary::SaveAsset(SequenceObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("add_visibility_track: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). The visibility track was added in memory but not persisted."),
                *SequenceObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        const int32 SectionIndex = VisTrack->GetAllSections().IndexOfByKey(Section);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);
        Out->SetStringField(TEXT("binding_guid"),
            BindingGuid.ToString(EGuidFormats::DigitsWithHyphens));
        Out->SetStringField(TEXT("track_class"), TEXT("MovieSceneVisibilityTrack"));
        Out->SetNumberField(TEXT("section_index"), static_cast<double>(SectionIndex));
        Out->SetNumberField(TEXT("key_count"), static_cast<double>(KeyTimes.Num()));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddVisibilityTrack()
{
    return MakeShared<FHandler_AddVisibilityTrack>();
}
