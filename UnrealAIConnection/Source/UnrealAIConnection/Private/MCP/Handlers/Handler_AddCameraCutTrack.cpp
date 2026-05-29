// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_camera_cut_track - add (or reuse) the single UMovieSceneCameraCutTrack on
// a Level Sequence and append a camera-cut section that points the viewer at an
// existing camera binding (by binding GUID) over a [start_frame, end_frame]
// range. Headless asset edit; no Sequencer UI.
//
// is_mutating = false: editing a ULevelSequence asset is not an actor/level
// edit on the undo stack (matches create_sequence / bind_actor_to_sequence).
//
// UE 5.7 surface used (verified against F:/UE_5.7 source):
//   - UMovieScene::GetCameraCutTrack() const -> UMovieSceneTrack*    MovieScene.h:754
//   - UMovieScene::AddCameraCutTrack(TSubclassOf<UMovieSceneTrack>) -> UMovieSceneTrack*
//                                                                    MovieScene.h:751
//   - UMovieSceneCameraCutTrack::AddNewCameraCut(const FMovieSceneObjectBindingID&, FFrameNumber)
//       -> UMovieSceneCameraCutSection*                             MovieSceneCameraCutTrack.h:44
//   - UMovieSceneCameraCutTrack::SetIsAutoManagingSections(bool)     MovieSceneCameraCutTrack.h:69
//   - UMovieSceneCameraCutSection::SetCameraBindingID(const FMovieSceneObjectBindingID&)
//                                                                    MovieSceneCameraCutSection.h:49
//   - FMovieSceneObjectBindingID <- UE::MovieScene::FRelativeObjectBindingID(const FGuid&)
//                                                                    MovieSceneObjectBindingID.h:42
//                                    (CameraCutSection.h:37 does exactly this)
//   - UMovieSceneSection::SetRange(const TRange<FFrameNumber>&)      MovieSceneSection.h:318
//   - UMovieScene::GetPlaybackRange / GetDisplayRate / GetTickResolution
//   - FFrameRate::TransformTime                                     (Handler_CreateSequence.cpp:160)
//
// GOTCHAS handled (per verified spec):
//   * AddNewCameraCut IGNORES the caller's end: it auto-sizes end to
//     DiscreteExclusiveUpper(playback range) (CameraCutTrack.cpp:250). We
//     capture the returned section and call SetRange ourselves to honor
//     end_frame.
//   * The track auto-arranges sections on add (bAutoArrangeSections default
//     true). We call SetIsAutoManagingSections(false) BEFORE SetRange so the
//     explicit range is not clobbered.
//   * There is only ONE camera cut track per MovieScene — GetCameraCutTrack()
//     first; AddCameraCutTrack(StaticClass()) only if null.
//   * camera_binding_guid MUST already exist as a possessable/spawnable —
//     validated by scanning GetPossessableCount/GetSpawnableCount
//     (Handler_InspectSequence.cpp:129-161) before AddNewCameraCut.
//
// Error format: "add_camera_cut_track: <error_code>: <detail>".
// Stable error codes: missing_required_field, invalid_guid, asset_not_found,
// not_a_sequence, binding_not_found, invalid_range, track_add_failed,
// section_add_failed, save_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "MovieScenePossessable.h"
#include "MovieSceneSpawnable.h"
#include "MovieSceneObjectBindingID.h"
#include "Tracks/MovieSceneCameraCutTrack.h"
#include "Sections/MovieSceneCameraCutSection.h"
#include "Misc/FrameRate.h"
#include "Misc/FrameNumber.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_AddCameraCutTrack : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_camera_cut_track"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("add_camera_cut_track: missing_required_field: 'sequence_path' and 'camera_binding_guid' are required");
            return nullptr;
        }

        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("add_camera_cut_track: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        FString GuidStr;
        if (!Params->TryGetStringField(TEXT("camera_binding_guid"), GuidStr) || GuidStr.IsEmpty())
        {
            OutError = TEXT("add_camera_cut_track: missing_required_field: 'camera_binding_guid' is required and must not be empty");
            return nullptr;
        }

        FGuid CameraGuid;
        if (!FGuid::Parse(GuidStr, CameraGuid) || !CameraGuid.IsValid())
        {
            OutError = FString::Printf(
                TEXT("add_camera_cut_track: invalid_guid: '%s' is not a parseable GUID"),
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
                    TEXT("add_camera_cut_track: asset_not_found: '%s' is not in the asset registry"),
                    *SequencePath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("add_camera_cut_track: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *SequencePath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("add_camera_cut_track: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *SequencePath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("add_camera_cut_track: not_a_sequence: sequence has no MovieScene");
            return nullptr;
        }

        // --- validate the camera binding exists in the sequence -------------
        //
        // Scan possessables + spawnables for a matching GUID (pattern from
        // Handler_InspectSequence.cpp:129-161). An invalid binding silently
        // produces a black cut at runtime, so reject up front.
        bool bBindingFound = false;
        const int32 PossessableCount = Scene->GetPossessableCount();
        for (int32 i = 0; i < PossessableCount && !bBindingFound; ++i)
        {
            if (Scene->GetPossessable(i).GetGuid() == CameraGuid)
            {
                bBindingFound = true;
            }
        }
        if (!bBindingFound)
        {
            const int32 SpawnableCount = Scene->GetSpawnableCount();
            for (int32 i = 0; i < SpawnableCount && !bBindingFound; ++i)
            {
                if (Scene->GetSpawnable(i).GetGuid() == CameraGuid)
                {
                    bBindingFound = true;
                }
            }
        }
        if (!bBindingFound)
        {
            OutError = FString::Printf(
                TEXT("add_camera_cut_track: binding_not_found: no possessable or spawnable binding with GUID '%s' exists in '%s'"),
                *CameraGuid.ToString(EGuidFormats::DigitsWithHyphens), *SequencePath);
            return nullptr;
        }

        // --- resolve the frame range (display-rate -> ticks) ----------------

        const FFrameRate DisplayRate = Scene->GetDisplayRate();
        const FFrameRate TickResolution = Scene->GetTickResolution();

        // start_frame defaults to 0.
        double StartFrameRaw = 0.0;
        Params->TryGetNumberField(TEXT("start_frame"), StartFrameRaw);
        const int32 StartFrame = static_cast<int32>(StartFrameRaw);

        // end_frame defaults to the playback-range end expressed in display
        // frames. Read the stored tick upper bound and convert back to display
        // frames so the default lines up with the user-facing frame space.
        int32 EndFrame = 0;
        bool bHasEnd = false;
        double EndFrameRaw = 0.0;
        if (Params->TryGetNumberField(TEXT("end_frame"), EndFrameRaw))
        {
            EndFrame = static_cast<int32>(EndFrameRaw);
            bHasEnd = true;
        }

        if (!bHasEnd)
        {
            const TRange<FFrameNumber> PlaybackRange = Scene->GetPlaybackRange();
            const FFrameNumber EndTicksDefault = PlaybackRange.GetUpperBoundValue();
            const FFrameNumber EndDisplayDefault = FFrameRate::TransformTime(
                FFrameTime(EndTicksDefault), TickResolution, DisplayRate).FrameNumber;
            EndFrame = EndDisplayDefault.Value;
        }

        if (EndFrame <= StartFrame)
        {
            OutError = FString::Printf(
                TEXT("add_camera_cut_track: invalid_range: end_frame (%d) must be greater than start_frame (%d)"),
                EndFrame, StartFrame);
            return nullptr;
        }

        const FFrameNumber StartTicks = FFrameRate::TransformTime(
            FFrameTime(FFrameNumber(StartFrame)), DisplayRate, TickResolution).FrameNumber;
        const FFrameNumber EndTicks = FFrameRate::TransformTime(
            FFrameTime(FFrameNumber(EndFrame)), DisplayRate, TickResolution).FrameNumber;

        // --- get-or-create the single camera cut track ----------------------

        UMovieSceneCameraCutTrack* CutTrack =
            Cast<UMovieSceneCameraCutTrack>(Scene->GetCameraCutTrack());
        if (!CutTrack)
        {
            CutTrack = Cast<UMovieSceneCameraCutTrack>(
                Scene->AddCameraCutTrack(UMovieSceneCameraCutTrack::StaticClass()));
        }
        if (!CutTrack)
        {
            OutError = TEXT("add_camera_cut_track: track_add_failed: could not get or create a UMovieSceneCameraCutTrack");
            return nullptr;
        }

        // Stop the track auto-resizing sections so our explicit SetRange below
        // is not clobbered by AutoArrangeSectionsIfNeeded. Capture the prior
        // value first (getter MovieSceneCameraCutTrack.h:66) and restore it
        // after SetRange — otherwise we permanently flip the asset's
        // section-layout behavior, which is a surprising side effect on a track
        // we may merely be reusing.
        const bool bWasAutoManaging = CutTrack->IsAutoManagingSections();
        CutTrack->SetIsAutoManagingSections(false);

        // --- add the section + force the exact range ------------------------
        //
        // Build the binding ID from the validated GUID exactly as
        // MovieSceneCameraCutSection.h:37 does (FRelativeObjectBindingID(InGuid)
        // implicitly converts to FMovieSceneObjectBindingID).
        // Named intermediate avoids the most-vexing-parse: writing
        // FMovieSceneObjectBindingID BindingID(FRelativeObjectBindingID(CameraGuid))
        // makes the compiler read the inner "(CameraGuid)" as a parameter name and
        // treat BindingID as a function declaration. A named local is unambiguous.
        const UE::MovieScene::FRelativeObjectBindingID RelBindingID(CameraGuid);
        const FMovieSceneObjectBindingID BindingID(RelBindingID);

        UMovieSceneCameraCutSection* Section = CutTrack->AddNewCameraCut(BindingID, StartTicks);
        if (!Section)
        {
            OutError = TEXT("add_camera_cut_track: section_add_failed: AddNewCameraCut returned null");
            return nullptr;
        }

        // AddNewCameraCut ignores the caller end; force the exact [start,end]
        // range now that auto-management is off.
        Section->SetRange(TRange<FFrameNumber>(StartTicks, EndTicks));
        // Ensure the binding is set (AddNewCameraCut sets it, but be explicit so
        // a future refactor of the engine helper can't silently drop it).
        Section->SetCameraBindingID(BindingID);

        // Restore the track's prior auto-managing state now that the explicit
        // range is committed, so we leave the asset's section-layout behavior as
        // we found it (see capture above).
        CutTrack->SetIsAutoManagingSections(bWasAutoManaging);

        // --- save -----------------------------------------------------------

        if (!UEditorAssetLibrary::SaveAsset(SequenceObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("add_camera_cut_track: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). The cut was added in memory but not persisted."),
                *SequenceObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------
        //
        // section_index is the new section's position in the track's section
        // list. Report tick values to match inspect_sequence.
        const int32 SectionIndex = CutTrack->GetAllSections().IndexOfByKey(Section);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);
        Out->SetStringField(TEXT("track_class"), TEXT("MovieSceneCameraCutTrack"));
        Out->SetNumberField(TEXT("section_index"), static_cast<double>(SectionIndex));
        Out->SetStringField(TEXT("camera_binding_guid"),
            CameraGuid.ToString(EGuidFormats::DigitsWithHyphens));
        Out->SetNumberField(TEXT("start_frame"), static_cast<double>(StartTicks.Value));
        Out->SetNumberField(TEXT("end_frame"), static_cast<double>(EndTicks.Value));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddCameraCutTrack()
{
    return MakeShared<FHandler_AddCameraCutTrack>();
}
