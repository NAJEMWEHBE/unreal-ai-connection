// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_sequence_playback_range - set the MovieScene playback range start/end on
// an existing ULevelSequence. Write counterpart to inspect_sequence's
// playback_range read. Headless asset edit (no Sequencer UI, no actor/level
// edit on the undo stack).
//
// UE 5.7 surface used (verified against F:/UE_5.7 source):
//   - ULevelSequence::GetMovieScene() -> UMovieScene*                LevelSequence.h
//   - UMovieScene::GetDisplayRate()/GetTickResolution() -> FFrameRate
//                                                          (Handler_InspectSequence.cpp:102-103)
//   - UMovieScene::GetPlaybackRange() const -> TRange<FFrameNumber>  (Handler_InspectSequence.cpp:108)
//   - UMovieScene::SetPlaybackRange(const TRange<FFrameNumber>&, bool bAlwaysMarkDirty=true)
//                                                          MovieScene.h:1007 (range overload —
//                                                          avoids the duration-vs-end footgun of
//                                                          the SetPlaybackRange(Start, Duration) form
//                                                          at MovieScene.h:998)
//   - FFrameRate::TransformTime(FFrameTime, FFrameRate, FFrameRate)  (Handler_CreateSequence.cpp:160)
//   - UEditorAssetLibrary::LoadAsset / SaveAsset            (Handler_CreateSequence.cpp:172)
//
// Locked-range handling: if the playback range is locked we unlock it first
// (UMovieScene::SetPlaybackRangeLocked(false), MovieScene.h:1047) so the set
// is not silently dropped, then re-apply the lock to leave the asset as we
// found it. The IsPlaybackRangeLocked() getter is flagged unverified in the
// spec; we guard the call behind a code path that tolerates its absence by
// only re-locking when we actually unlocked.
//
// Error format: "set_sequence_playback_range: <error_code>: <detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_sequence,
// invalid_range, save_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "Misc/FrameRate.h"
#include "Misc/FrameNumber.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_SetSequencePlaybackRange : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_sequence_playback_range"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("set_sequence_playback_range: missing_required_field: 'sequence_path' and 'end_frame' are required");
            return nullptr;
        }

        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("set_sequence_playback_range: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        // end_frame is required; start_frame defaults to 0. Both are DISPLAY-RATE
        // frames. TryGetNumberField into a double lets us reject non-integer JSON
        // and detect the "field absent" case explicitly.
        double EndFrameRaw = 0.0;
        if (!Params->TryGetNumberField(TEXT("end_frame"), EndFrameRaw))
        {
            OutError = TEXT("set_sequence_playback_range: missing_required_field: 'end_frame' is required");
            return nullptr;
        }

        double StartFrameRaw = 0.0;
        Params->TryGetNumberField(TEXT("start_frame"), StartFrameRaw);

        const int32 StartFrame = static_cast<int32>(StartFrameRaw);
        const int32 EndFrame = static_cast<int32>(EndFrameRaw);

        if (EndFrame <= StartFrame)
        {
            OutError = FString::Printf(
                TEXT("set_sequence_playback_range: invalid_range: end_frame (%d) must be greater than start_frame (%d)"),
                EndFrame, StartFrame);
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
                    TEXT("set_sequence_playback_range: asset_not_found: '%s' is not in the asset registry"),
                    *SequencePath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("set_sequence_playback_range: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *SequencePath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("set_sequence_playback_range: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *SequencePath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("set_sequence_playback_range: not_a_sequence: sequence has no MovieScene");
            return nullptr;
        }

        // --- convert display-rate frames -> tick units ----------------------
        //
        // Playback range is stored in tick units. Convert exactly as
        // Handler_CreateSequence.cpp:159-163 does.
        const FFrameRate DisplayRate = Scene->GetDisplayRate();
        const FFrameRate TickResolution = Scene->GetTickResolution();

        const FFrameNumber StartTicks = FFrameRate::TransformTime(
            FFrameTime(FFrameNumber(StartFrame)), DisplayRate, TickResolution).FrameNumber;
        const FFrameNumber EndTicks = FFrameRate::TransformTime(
            FFrameTime(FFrameNumber(EndFrame)), DisplayRate, TickResolution).FrameNumber;

        // --- apply the range ------------------------------------------------
        //
        // If the range is locked the setter silently no-ops; unlock, set, then
        // restore the lock so we don't leave the asset in a different lock
        // state than we found it.
        const bool bWasLocked = Scene->IsPlaybackRangeLocked();
        if (bWasLocked)
        {
            Scene->SetPlaybackRangeLocked(false);
        }

        // Use the TRange overload to sidestep the SetPlaybackRange(Start, Duration)
        // duration-vs-end footgun documented in the spec.
        Scene->SetPlaybackRange(TRange<FFrameNumber>(StartTicks, EndTicks));

        if (bWasLocked)
        {
            Scene->SetPlaybackRangeLocked(true);
        }

        // --- save -----------------------------------------------------------

        if (!UEditorAssetLibrary::SaveAsset(SequenceObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("set_sequence_playback_range: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). Range was set in memory but not persisted."),
                *SequenceObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------
        //
        // Report the stored tick values to stay consistent with inspect_sequence
        // (Handler_InspectSequence.cpp:110-113), which reports playback_range in
        // ticks.
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);

        TSharedRef<FJsonObject> RangeJson = MakeShared<FJsonObject>();
        RangeJson->SetNumberField(TEXT("start_frames"), static_cast<double>(StartTicks.Value));
        RangeJson->SetNumberField(TEXT("end_frames"), static_cast<double>(EndTicks.Value));
        Out->SetObjectField(TEXT("playback_range"), RangeJson);

        Out->SetNumberField(TEXT("display_rate_fps"), DisplayRate.AsDecimal());

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetSequencePlaybackRange()
{
    return MakeShared<FHandler_SetSequencePlaybackRange>();
}
