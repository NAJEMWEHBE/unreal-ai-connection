// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_cine_camera_to_sequence - spawn an ACineCameraActor into the editor
// world, add it as a POSSESSABLE binding on a Level Sequence, and return the
// binding GUID (ready to feed into add_camera_cut_track). Mirrors
// Handler_BindActorToSequence's possessable path (AddPossessable +
// BindPossessableObject) but creates the actor first.
//
// is_mutating = TRUE: spawning a real actor into the editor world IS an
// undo-stack level edit (matches Handler_SpawnActor.cpp:26). The sequence-asset
// binding edit rides along. We call Actor->Modify() after spawn so the label
// edit joins the dispatcher's transaction (Handler_SpawnActor.cpp:102).
//
// SPAWNABLE mode is intentionally NOT offered. Per the verified spec,
// ULevelSequenceEditorSubsystem::CreateCamera/AddSpawnableFromClass/Instance
// all early-return when GetActiveSequencer()==null, and the
// UMovieSceneSequenceExtensions spawnable adds are UE_DEPRECATED(5.5)
// redirects to that subsystem — there is no supported headless spawnable add.
//
// UE 5.7 surface used (verified against F:/UE_5.7 source):
//   - ACineCameraActor : AActor                              CineCameraActor.h:1 (module CinematicCamera)
//   - UCineCameraComponent                                   CineCameraComponent.h:21 (CINEMATICCAMERA_API)
//   - UWorld::SpawnActor<ACineCameraActor>(...)              (pattern Handler_SpawnActor.cpp:92;
//                                                            engine FSequencerUtilities::CreateCamera
//                                                            SequencerUtilities.cpp:982)
//   - GEditor->GetEditorWorldContext().World()               (Handler_SpawnActor.cpp:82 /
//                                                            Handler_BindActorToSequence.cpp:136)
//   - UMovieScene::AddPossessable(const FString&, UClass*) -> FGuid
//                                                            MovieScene.h:448 (Handler_BindActorToSequence.cpp:124)
//   - ULevelSequence::BindPossessableObject(const FGuid&, UObject&, UObject* Context)
//                                                            (Handler_BindActorToSequence.cpp:137)
//   - AActor::SetActorLabel / GetActorLabel                  (Handler_SpawnActor.cpp:108 / :123)
//   - UEditorAssetLibrary::LoadAsset / SaveAsset
//
// Error format: "add_cine_camera_to_sequence: <error_code>: <detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_sequence,
// no_editor_world, spawn_failed, bind_failed, save_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "GameFramework/Actor.h"
#include "Engine/World.h"
#include "Editor.h"
#include "CineCameraActor.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_AddCineCameraToSequence : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_cine_camera_to_sequence"); }

    // Spawns a real actor into the editor world; the dispatcher's
    // FScopedTransaction captures the actor add as a single undo step.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("add_cine_camera_to_sequence: missing_required_field: 'sequence_path' is required");
            return nullptr;
        }

        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("add_cine_camera_to_sequence: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        // --- optional params ------------------------------------------------

        FString Label;
        Params->TryGetStringField(TEXT("label"), Label);
        if (Label.IsEmpty())
        {
            Label = TEXT("CineCameraActor");
        }

        // Location — initialize to (0,0,0) so missing JSON keys don't leak garbage.
        FVector Location(0, 0, 0);
        const TSharedPtr<FJsonObject>* LocObj = nullptr;
        if (Params->TryGetObjectField(TEXT("location"), LocObj) && LocObj && (*LocObj).IsValid())
        {
            double X = 0, Y = 0, Z = 0;
            (*LocObj)->TryGetNumberField(TEXT("x"), X); Location.X = X;
            (*LocObj)->TryGetNumberField(TEXT("y"), Y); Location.Y = Y;
            (*LocObj)->TryGetNumberField(TEXT("z"), Z); Location.Z = Z;
        }

        // Rotation — same initialization discipline.
        FRotator Rotation(0, 0, 0);
        const TSharedPtr<FJsonObject>* RotObj = nullptr;
        if (Params->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj && (*RotObj).IsValid())
        {
            double Pitch = 0, Yaw = 0, Roll = 0;
            (*RotObj)->TryGetNumberField(TEXT("pitch"), Pitch); Rotation.Pitch = Pitch;
            (*RotObj)->TryGetNumberField(TEXT("yaw"), Yaw); Rotation.Yaw = Yaw;
            (*RotObj)->TryGetNumberField(TEXT("roll"), Roll); Rotation.Roll = Roll;
        }

        // --- resolve to ULevelSequence (mirrors bind_actor_to_sequence) -----

        const FString SequenceObjectPath = UCMCPAssetPath::ToObjectPath(SequencePath);

        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(SequenceObjectPath);
        if (!LoadedAsset)
        {
            if (!UEditorAssetLibrary::DoesAssetExist(SequenceObjectPath))
            {
                OutError = FString::Printf(
                    TEXT("add_cine_camera_to_sequence: asset_not_found: '%s' is not in the asset registry"),
                    *SequencePath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("add_cine_camera_to_sequence: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *SequencePath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("add_cine_camera_to_sequence: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *SequencePath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("add_cine_camera_to_sequence: not_a_sequence: sequence has no MovieScene");
            return nullptr;
        }

        // --- spawn the CineCameraActor into the editor world ----------------

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("add_cine_camera_to_sequence: no_editor_world: no editor world available to spawn the camera into");
            return nullptr;
        }

        // Mirror Handler_SpawnActor's collision handling so placement never
        // silently fails on overlap (SpawnActor.cpp:90).
        FActorSpawnParameters SpawnParams;
        SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

        ACineCameraActor* Camera = World->SpawnActor<ACineCameraActor>(
            ACineCameraActor::StaticClass(), Location, Rotation, SpawnParams);
        if (!Camera)
        {
            OutError = TEXT("add_cine_camera_to_sequence: spawn_failed: World::SpawnActor returned null for ACineCameraActor");
            return nullptr;
        }

        // The spawn itself is recorded by the dispatcher's open transaction;
        // Modify() folds the label edit below into the same single undo step.
        Camera->Modify();
        Camera->SetActorLabel(Label);

        // --- create the possessable binding ---------------------------------
        //
        // AddPossessable returns a fresh FGuid (single-arg form: Name + Class,
        // MovieScene.h:448). Use the actor's resolved label as the binding name
        // so inspect_sequence reports it as bound_actor_label.
        const FString ActorLabel = Camera->GetActorLabel();
        const FGuid Guid = Scene->AddPossessable(ActorLabel, Camera->GetClass());
        if (!Guid.IsValid())
        {
            // Roll back the spawned actor so a bind failure doesn't strand an
            // orphan camera in the level.
            Camera->Destroy();
            OutError = FString::Printf(
                TEXT("add_cine_camera_to_sequence: bind_failed: AddPossessable returned an invalid GUID for camera '%s'"),
                *ActorLabel);
            return nullptr;
        }

        // Wire the binding GUID to the just-spawned actor. Context must be the
        // editor world so the GUID resolves to that actor
        // (Handler_BindActorToSequence.cpp:136-137).
        Sequence->BindPossessableObject(Guid, *Camera, World);

        // --- save the SEQUENCE asset (persists the binding) -----------------
        //
        // The spawned actor persists with the level on a separate save; here we
        // persist the sequence's new binding. Surface SaveAsset's bool as
        // save_failed (Handler_BindActorToSequence.cpp:146).
        if (!UEditorAssetLibrary::SaveAsset(SequenceObjectPath, /*bForceSave=*/false))
        {
            // Roll back the in-memory mutations so a retry is idempotent: without
            // this, the orphaned possessable binding + spawned camera persist and
            // a second attempt strands a duplicate binding/camera. Unwind in
            // reverse order — remove the possessable from the MovieScene
            // (MovieScene.h:463), then destroy the actor (mirrors the bind-failure
            // rollback at the AddPossessable guard above).
            Scene->RemovePossessable(Guid);
            Camera->Destroy();
            OutError = FString::Printf(
                TEXT("add_cine_camera_to_sequence: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). The camera and binding were rolled back so a retry starts clean."),
                *SequenceObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);
        Out->SetStringField(TEXT("binding_guid"), Guid.ToString(EGuidFormats::DigitsWithHyphens));
        Out->SetStringField(TEXT("actor_label"), ActorLabel);
        Out->SetStringField(TEXT("actor_name"), Camera->GetFName().ToString());
        Out->SetStringField(TEXT("class"), TEXT("CineCameraActor"));
        Out->SetStringField(TEXT("binding_type"), TEXT("possessable"));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddCineCameraToSequence()
{
    return MakeShared<FHandler_AddCineCameraToSequence>();
}
