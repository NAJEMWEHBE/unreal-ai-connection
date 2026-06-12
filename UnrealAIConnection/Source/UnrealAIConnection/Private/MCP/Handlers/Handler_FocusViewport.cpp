// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// focus_viewport - aim the active level-editor viewport, either by FRAMING a
// named actor or by SNAPPING the camera to an explicit location + orientation
// (and optional FOV). Closes the "look at the result" loop so an MCP client can
// frame the thing it just spawned/edited before taking a screenshot.
//
// Relationship to focus_actor (distinct, not a duplicate):
//   focus_actor SELECTS an actor and frames it (MoveViewportCamerasToActor),
//   and ONLY does the named-actor case. focus_viewport adds the
//   location+orientation branch (set the camera transform directly) AND keeps
//   the named-actor framing branch, without changing selection unless an actor
//   is targeted. Exactly one of {actor} or {location} must be supplied.
//
// Mode selection:
//   - "actor" present  -> frame that actor (label or unique name).
//   - "location" object present -> snap camera to that location; optional
//     "rotation" {pitch,yaw,roll} orients it (defaults to looking down -X if
//     omitted, i.e. UE's identity-forward), optional "fov" overrides the FOV.
//
// Error format: "focus_viewport: <error_code>: <human-readable detail>".
// Stable error codes: no_editor, no_world, bad_param, ambiguous_target,
// missing_target, actor_not_found, no_level_editor, no_viewport.

#include "MCP/MCPHandler.h"

#if WITH_EDITOR

#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "EngineUtils.h"
#include "LevelEditor.h"
#include "SLevelViewport.h"
#include "LevelEditorViewport.h"
#include "EditorViewportClient.h"
#include "Modules/ModuleManager.h"

class FHandler_FocusViewport : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("focus_viewport"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor)
        {
            OutError = TEXT("focus_viewport: no_editor: GEditor is null (editor build only)");
            return nullptr;
        }
        UWorld* World = GEditor->GetEditorWorldContext().World();
        if (!World)
        {
            OutError = TEXT("focus_viewport: no_world: no active editor world");
            return nullptr;
        }
        if (!Params.IsValid())
        {
            OutError = TEXT("focus_viewport: missing_target: supply exactly one of 'actor' or 'location'");
            return nullptr;
        }

        FString ActorTarget;
        const bool bHasActor = Params->TryGetStringField(TEXT("actor"), ActorTarget) && !ActorTarget.IsEmpty();

        const TSharedPtr<FJsonObject>* LocObj = nullptr;
        const bool bHasLocation = Params->TryGetObjectField(TEXT("location"), LocObj) && LocObj && (*LocObj).IsValid();

        if (bHasActor && bHasLocation)
        {
            OutError = TEXT("focus_viewport: ambiguous_target: supply only one of 'actor' or 'location', not both");
            return nullptr;
        }
        if (!bHasActor && !bHasLocation)
        {
            OutError = TEXT("focus_viewport: missing_target: supply exactly one of 'actor' or 'location'");
            return nullptr;
        }

        if (bHasActor)
        {
            return FocusOnActor(World, ActorTarget, OutError);
        }
        return FocusOnLocation(Params, *LocObj, OutError);
    }

private:

    TSharedPtr<FJsonObject> FocusOnActor(UWorld* World, const FString& NameOrLabel, FString& OutError)
    {
        AActor* Found = nullptr;
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* A = *It;
            if (!A) { continue; }
            if (A->GetName() == NameOrLabel || A->GetActorLabel() == NameOrLabel)
            {
                Found = A;
                break;
            }
        }
        if (!Found)
        {
            OutError = FString::Printf(TEXT("focus_viewport: actor_not_found: no actor with name/label '%s'"), *NameOrLabel);
            return nullptr;
        }

        // Frame the actor in the active viewport. Mirrors focus_actor: select
        // then move the camera to it.
        GEditor->SelectNone(/*bNoteSelectionChange=*/false, /*bDeselectBSPSurfs=*/true, /*WarnAboutManyActors=*/false);
        GEditor->SelectActor(Found, /*bInSelected=*/true, /*bNotify=*/true, /*bSelectEvenIfHidden=*/true);
        GEditor->NoteSelectionChange();
        GEditor->MoveViewportCamerasToActor(*Found, /*bActiveViewportOnly=*/false);

        const FVector Loc = Found->GetActorLocation();
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField  (TEXT("ok"),       true);
        Out->SetStringField(TEXT("mode"),     TEXT("actor"));
        Out->SetStringField(TEXT("focused"),  Found->GetActorLabel());
        Out->SetStringField(TEXT("name"),     Found->GetName());
        TSharedRef<FJsonObject> LocJson = MakeShared<FJsonObject>();
        LocJson->SetNumberField(TEXT("x"), Loc.X);
        LocJson->SetNumberField(TEXT("y"), Loc.Y);
        LocJson->SetNumberField(TEXT("z"), Loc.Z);
        Out->SetObjectField(TEXT("location"), LocJson);
        return Out;
    }

    TSharedPtr<FJsonObject> FocusOnLocation(const TSharedPtr<FJsonObject>& Params, const TSharedPtr<FJsonObject>& LocObj, FString& OutError)
    {
        FVector Location(0, 0, 0);
        {
            double X = 0, Y = 0, Z = 0;
            LocObj->TryGetNumberField(TEXT("x"), X); Location.X = X;
            LocObj->TryGetNumberField(TEXT("y"), Y); Location.Y = Y;
            LocObj->TryGetNumberField(TEXT("z"), Z); Location.Z = Z;
        }

        FRotator Rotation(0, 0, 0);
        const TSharedPtr<FJsonObject>* RotObj = nullptr;
        if (Params->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj && (*RotObj).IsValid())
        {
            double Pitch = 0, Yaw = 0, Roll = 0;
            (*RotObj)->TryGetNumberField(TEXT("pitch"), Pitch); Rotation.Pitch = Pitch;
            (*RotObj)->TryGetNumberField(TEXT("yaw"),   Yaw);   Rotation.Yaw   = Yaw;
            (*RotObj)->TryGetNumberField(TEXT("roll"),  Roll);  Rotation.Roll  = Roll;
        }

        double FovDeg = 0.0;
        const bool bHasFov = Params->TryGetNumberField(TEXT("fov"), FovDeg) && FovDeg > 0.0;

        FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>("LevelEditor");
        if (!LEModule) { OutError = TEXT("focus_viewport: no_level_editor: LevelEditor module unavailable"); return nullptr; }
        TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
        if (!LV.IsValid()) { OutError = TEXT("focus_viewport: no_viewport: no active level viewport found"); return nullptr; }

        FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
        VC.SetViewLocation(Location);
        VC.SetViewRotation(Rotation);
        if (bHasFov) { VC.ViewFOV = static_cast<float>(FovDeg); }
        VC.Invalidate();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField  (TEXT("ok"),   true);
        Out->SetStringField(TEXT("mode"), TEXT("location"));
        TSharedRef<FJsonObject> LocJson = MakeShared<FJsonObject>();
        LocJson->SetNumberField(TEXT("x"), Location.X);
        LocJson->SetNumberField(TEXT("y"), Location.Y);
        LocJson->SetNumberField(TEXT("z"), Location.Z);
        Out->SetObjectField(TEXT("location"), LocJson);
        TSharedRef<FJsonObject> RotJson = MakeShared<FJsonObject>();
        RotJson->SetNumberField(TEXT("pitch"), Rotation.Pitch);
        RotJson->SetNumberField(TEXT("yaw"),   Rotation.Yaw);
        RotJson->SetNumberField(TEXT("roll"),  Rotation.Roll);
        Out->SetObjectField(TEXT("rotation"), RotJson);
        Out->SetNumberField(TEXT("fov"), bHasFov ? FovDeg : VC.ViewFOV);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_FocusViewport()
{
    return MakeShared<FHandler_FocusViewport>();
}

#endif // WITH_EDITOR
