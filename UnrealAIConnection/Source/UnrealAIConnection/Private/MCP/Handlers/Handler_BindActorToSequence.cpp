// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// bind_actor_to_sequence - add an existing level actor as a possessable
// binding on a Level Sequence. Reuses UCMCP::ActorIdentity::Resolve for
// hybrid label/FName lookup with ambiguity detection.
//
// Error format: "bind_actor_to_sequence: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found, not_a_sequence,
// actor_not_found, ambiguous_actor, bind_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "GameFramework/Actor.h"
#include "Engine/World.h"
#include "Editor.h"
#include "MCP/ActorIdentity.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_BindActorToSequence : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("bind_actor_to_sequence"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("bind_actor_to_sequence: missing_required_field: 'sequence_path' and 'actor_name' are required");
            return nullptr;
        }

        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("bind_actor_to_sequence: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        FString ActorName;
        if (!Params->TryGetStringField(TEXT("actor_name"), ActorName) || ActorName.IsEmpty())
        {
            OutError = TEXT("bind_actor_to_sequence: missing_required_field: 'actor_name' is required and must not be empty");
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
                    TEXT("bind_actor_to_sequence: asset_not_found: '%s' is not in the asset registry"),
                    *SequencePath);
            }
            else
            {
                OutError = FString::Printf(
                    TEXT("bind_actor_to_sequence: not_a_sequence: '%s' could not be loaded as a ULevelSequence"),
                    *SequencePath);
            }
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(LoadedAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("bind_actor_to_sequence: not_a_sequence: '%s' is a %s, not a ULevelSequence"),
                *SequencePath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("bind_actor_to_sequence: not_a_sequence: sequence has no MovieScene");
            return nullptr;
        }

        // --- resolve the actor ---------------------------------------------
        //
        // UCMCP::ActorIdentity::Resolve handles the hybrid label/FName lookup
        // with explicit ambiguity reporting (matches v0.3.0 patterns from
        // set_actor_transform, delete_actor, etc.).
        AActor* Actor = nullptr;
        TArray<FString> AmbiguousFNames;
        const UCMCP::ActorIdentity::EResolveResult Result =
            UCMCP::ActorIdentity::Resolve(ActorName, Actor, AmbiguousFNames);

        if (Result == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            const FString CandidateList = FString::Join(AmbiguousFNames, TEXT(", "));
            OutError = FString::Printf(
                TEXT("bind_actor_to_sequence: ambiguous_actor: '%s' matched %d actors: [%s]. Pass an FName instead of the label to disambiguate."),
                *ActorName, AmbiguousFNames.Num(), *CandidateList);
            return nullptr;
        }
        if (Result == UCMCP::ActorIdentity::EResolveResult::NotFound || !Actor)
        {
            OutError = FString::Printf(
                TEXT("bind_actor_to_sequence: actor_not_found: no actor in the editor world matches label or FName '%s'"),
                *ActorName);
            return nullptr;
        }

        // --- create the binding --------------------------------------------
        //
        // AddPossessable returns a fresh FGuid. UE 5.7 confirmed at
        // MovieScene.h:448 (single-arg form: Name + Class).
        // Using GetActorLabel() so the binding name in the sequencer matches
        // the actor's World Outliner label — this is the convention
        // inspect_sequence relies on for bound_actor_label.
        const FString ActorLabel = Actor->GetActorLabel();
        const FGuid Guid = Scene->AddPossessable(ActorLabel, Actor->GetClass());
        if (!Guid.IsValid())
        {
            OutError = FString::Printf(
                TEXT("bind_actor_to_sequence: bind_failed: AddPossessable returned an invalid GUID for actor '%s'"),
                *ActorLabel);
            return nullptr;
        }

        // Wire the binding GUID to the live actor. World context is the
        // editor world (so the resolution looks up the actor in the right
        // place). Confirmed at LevelSequence.h:43.
        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        Sequence->BindPossessableObject(Guid, *Actor, World);

        // --- save the sequence ---------------------------------------------
        //
        // Codex review on PR #15 (P2): the original code ignored SaveAsset's
        // bool return, so SCC-checkout failures or read-only files would
        // leave the binding only in-memory while the handler still reported
        // ok=true. Now we surface save failures explicitly so callers know
        // the binding was created in memory but not persisted to disk.
        if (!UEditorAssetLibrary::SaveAsset(SequenceObjectPath, /*bForceSave=*/false))
        {
            OutError = FString::Printf(
                TEXT("bind_actor_to_sequence: save_failed: UEditorAssetLibrary::SaveAsset returned false for '%s' (likely SCC checkout failure or read-only file). Binding was added in memory but not persisted to disk."),
                *SequenceObjectPath);
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);
        Out->SetStringField(TEXT("binding_guid"), Guid.ToString(EGuidFormats::DigitsWithHyphens));
        Out->SetStringField(TEXT("actor_label"), ActorLabel);
        Out->SetStringField(TEXT("binding_type"), TEXT("possessable"));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_BindActorToSequence()
{
    return MakeShared<FHandler_BindActorToSequence>();
}
