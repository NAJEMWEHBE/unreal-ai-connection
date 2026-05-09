// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_sequence - read structure of a Level Sequence asset: tracks,
// sections, bindings, frame rate, playback range.
//
// Error format: "inspect_sequence: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_sequence.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "MovieSceneTrack.h"
#include "MovieSceneSection.h"
#include "MovieSceneSpawnable.h"
#include "MovieScenePossessable.h"
#include "MovieSceneBinding.h"
#include "Misc/FrameRate.h"
#include "Misc/FrameNumber.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_InspectSequence : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_sequence"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_sequence: missing_required_field: 'path' is required but no params were provided");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_sequence: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // --- resolve to ULevelSequence --------------------------------------

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            // Distinguish "asset doesn't exist" from "asset failed to load".
            // DoesAssetExist is a lighter-weight check via the registry.
            if (!UEditorAssetLibrary::DoesAssetExist(ObjectPath))
            {
                OutError = FString::Printf(
                    TEXT("inspect_sequence: asset_not_found: '%s' is not in the asset registry"),
                    *InputPath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("inspect_sequence: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *InputPath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("inspect_sequence: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            // Defensive — a valid ULevelSequence should always have a MovieScene
            // after Initialize(), but be resilient to corrupted assets.
            OutError = FString::Printf(
                TEXT("inspect_sequence: not_a_sequence: '%s' has no MovieScene (asset may be corrupted)"),
                *InputPath);
            return nullptr;
        }

        // --- build the result JSON ------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Sequence->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);

        // Frame rates: tick_resolution is the internal tick rate (typically high
        // numerator like 24000); display_rate is the user-facing playback rate.
        const FFrameRate TickResolution = Scene->GetTickResolution();
        const FFrameRate DisplayRate = Scene->GetDisplayRate();
        Out->SetNumberField(TEXT("tick_resolution"), static_cast<double>(TickResolution.Numerator));
        Out->SetNumberField(TEXT("display_rate_fps"), DisplayRate.AsDecimal());

        // Playback range is in tick units (FFrameNumber wraps an int32).
        const TRange<FFrameNumber> PlaybackRange = Scene->GetPlaybackRange();
        TSharedRef<FJsonObject> RangeJson = MakeShared<FJsonObject>();
        RangeJson->SetNumberField(TEXT("start_frames"),
            static_cast<double>(PlaybackRange.GetLowerBoundValue().Value));
        RangeJson->SetNumberField(TEXT("end_frames"),
            static_cast<double>(PlaybackRange.GetUpperBoundValue().Value));
        Out->SetObjectField(TEXT("playback_range"), RangeJson);

        // --- bindings (possessables + spawnables) ---------------------------
        //
        // For typical workflows, a possessable's name equals the bound actor's
        // label (because Handler_BindActorToSequence calls
        // Scene->AddPossessable(Actor->GetActorLabel(), ...)). We surface that
        // as bound_actor_label without going through LocateBoundObjects, which
        // would require a runtime context.
        //
        // We also build a Guid -> name map up front so the per-binding-track
        // pass below can look up the binding name without re-scanning.
        TMap<FGuid, FString> BindingNamesByGuid;
        TArray<TSharedPtr<FJsonValue>> BindingsArray;

        const int32 PossessableCount = Scene->GetPossessableCount();
        for (int32 i = 0; i < PossessableCount; ++i)
        {
            FMovieScenePossessable& Pos = Scene->GetPossessable(i);
            const FGuid Guid = Pos.GetGuid();
            const FString Name = Pos.GetName();
            BindingNamesByGuid.Add(Guid, Name);

            TSharedRef<FJsonObject> BindingJson = MakeShared<FJsonObject>();
            BindingJson->SetStringField(TEXT("guid"), Guid.ToString(EGuidFormats::DigitsWithHyphens));
            BindingJson->SetStringField(TEXT("name"), Name);
            BindingJson->SetStringField(TEXT("type"), TEXT("possessable"));
            BindingJson->SetStringField(TEXT("bound_actor_label"), Name);
            BindingsArray.Add(MakeShared<FJsonValueObject>(BindingJson));
        }

        const int32 SpawnableCount = Scene->GetSpawnableCount();
        for (int32 i = 0; i < SpawnableCount; ++i)
        {
            FMovieSceneSpawnable& Spawn = Scene->GetSpawnable(i);
            const FGuid Guid = Spawn.GetGuid();
            const FString Name = Spawn.GetName();
            BindingNamesByGuid.Add(Guid, Name);

            TSharedRef<FJsonObject> BindingJson = MakeShared<FJsonObject>();
            BindingJson->SetStringField(TEXT("guid"), Guid.ToString(EGuidFormats::DigitsWithHyphens));
            BindingJson->SetStringField(TEXT("name"), Name);
            BindingJson->SetStringField(TEXT("type"), TEXT("spawnable"));
            // Spawnables don't have a "bound" actor in the level — the actor
            // is instantiated per-sequence-instance from a template. Omit
            // bound_actor_label rather than misleading the reader.
            BindingsArray.Add(MakeShared<FJsonValueObject>(BindingJson));
        }
        Out->SetArrayField(TEXT("bindings"), BindingsArray);

        // --- tracks (master tracks + per-binding tracks) --------------------

        TArray<TSharedPtr<FJsonValue>> TracksArray;

        // Master tracks: not attached to any binding. UE 5.7 spells this
        // GetTracks() (returns the master-tracks array on UMovieScene),
        // confirmed at MovieScene.h:702.
        for (UMovieSceneTrack* Track : Scene->GetTracks())
        {
            if (!Track) { continue; }
            TSharedRef<FJsonObject> TrackJson = MakeShared<FJsonObject>();
            TrackJson->SetStringField(TEXT("name"), Track->GetName());
            TrackJson->SetStringField(TEXT("class"), Track->GetClass()->GetName());
            TrackJson->SetNumberField(TEXT("section_count"),
                static_cast<double>(Track->GetAllSections().Num()));
            // Master tracks have no binding; emit empty string for clarity.
            TrackJson->SetStringField(TEXT("binding_guid"), TEXT(""));
            TracksArray.Add(MakeShared<FJsonValueObject>(TrackJson));
        }

        // Per-binding tracks: each FMovieSceneBinding holds its tracks plus
        // the binding's GUID. Confirmed at MovieSceneBinding.h:118 (GetTracks)
        // and :84 (GetObjectGuid). UE 5.7 deprecated the non-const
        // UMovieScene::GetBindings overload (MovieScene.h:778); the const
        // overload (line 773) is the supported path.
        const UMovieScene* ConstScene = Scene;
        for (const FMovieSceneBinding& Binding : ConstScene->GetBindings())
        {
            const FString BindingGuidStr =
                Binding.GetObjectGuid().ToString(EGuidFormats::DigitsWithHyphens);
            for (UMovieSceneTrack* Track : Binding.GetTracks())
            {
                if (!Track) { continue; }
                TSharedRef<FJsonObject> TrackJson = MakeShared<FJsonObject>();
                TrackJson->SetStringField(TEXT("name"), Track->GetName());
                TrackJson->SetStringField(TEXT("class"), Track->GetClass()->GetName());
                TrackJson->SetNumberField(TEXT("section_count"),
                    static_cast<double>(Track->GetAllSections().Num()));
                TrackJson->SetStringField(TEXT("binding_guid"), BindingGuidStr);
                TracksArray.Add(MakeShared<FJsonValueObject>(TrackJson));
            }
        }
        Out->SetArrayField(TEXT("tracks"), TracksArray);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectSequence()
{
    return MakeShared<FHandler_InspectSequence>();
}
