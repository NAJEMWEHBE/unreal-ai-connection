// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// spawn_niagara_at_location - place an editor-PERSISTENT Niagara system actor
// (ANiagaraActor) in the current editor world at a world location/rotation/scale,
// assign the UNiagaraSystem asset to its embedded UNiagaraComponent, and
// optionally set the actor label. Persistent = appears in the World Outliner,
// saved with the level, on the undo stack. This is distinct from the
// transient/PIE UNiagaraFunctionLibrary::SpawnSystemAtLocation, which creates a
// bare world-registered component (no Outliner actor, not level-saved).
//
// UE 5.7 surface used (verified against F:/UE_5.7 source):
//   - ANiagaraActor : public AActor                          NiagaraActor.h:17
//       (UCLASS MinimalAPI, ComponentWrapperClass; spawnable via StaticClass())
//   - UNiagaraComponent* ANiagaraActor::GetNiagaraComponent() NiagaraActor.h:64
//   - void UNiagaraComponent::SetAsset(UNiagaraSystem*, bool)  NiagaraComponent.h:292
//   - void UNiagaraComponent::Activate(bool)                   NiagaraComponent.h:223
//   - void UNiagaraComponent::Deactivate()                     NiagaraComponent.h (UFXSystemComponent)
//   - UWorld::SpawnActor<T>(UClass*, const FVector&, const FRotator&, const FActorSpawnParameters&)
//                                                              Engine/World.h (mirrors Handler_SpawnActor.cpp:92)
//   - UEditorAssetLibrary::LoadAsset(ObjectPath) + Cast<UNiagaraSystem>
//                                                              (mirrors Handler_InspectNiagaraSystem.cpp:106-120)
//   - AActor::SetActorRelativeScale3D (SpawnActor sets loc+rot only; scale applied separately)
//
// Error format: "spawn_niagara_at_location: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, system_not_found,
// not_a_niagara_system, spawn_failed.

#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "EditorAssetLibrary.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "NiagaraActor.h"
#include "NiagaraComponent.h"
#include "NiagaraSystem.h"

class FHandler_SpawnNiagaraAtLocation : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("spawn_niagara_at_location"); }

    // Spawn is recorded by the dispatcher's FScopedTransaction (the world captures
    // actor add/remove while a transaction is open); Modify() after spawn folds the
    // asset/label/scale edits into the same single undo step.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("spawn_niagara_at_location: missing_params: request had no params object");
            return nullptr;
        }

        FString SystemPath;
        if (!Params->TryGetStringField(TEXT("system"), SystemPath) || SystemPath.IsEmpty())
        {
            OutError = TEXT("spawn_niagara_at_location: missing_required_field: 'system' is required and must be non-empty");
            return nullptr;
        }

        // Resolve the UNiagaraSystem. Niagara systems are LazyOnDemand; LoadAsset
        // forces a full load (same rationale as inspect_niagara_system).
        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(SystemPath);
        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("spawn_niagara_at_location: system_not_found: '%s' is not in the asset registry"), *SystemPath);
            return nullptr;
        }
        UNiagaraSystem* System = Cast<UNiagaraSystem>(Loaded);
        if (!System)
        {
            OutError = FString::Printf(
                TEXT("spawn_niagara_at_location: not_a_niagara_system: '%s' is a %s, not a UNiagaraSystem"),
                *SystemPath, *Loaded->GetClass()->GetName());
            return nullptr;
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

        // Scale — default (1,1,1); SpawnActor only sets loc+rot, so applied after spawn.
        FVector Scale(1, 1, 1);
        bool bHasScale = false;
        const TSharedPtr<FJsonObject>* ScaleObj = nullptr;
        if (Params->TryGetObjectField(TEXT("scale"), ScaleObj) && ScaleObj && (*ScaleObj).IsValid())
        {
            double X = 1, Y = 1, Z = 1;
            (*ScaleObj)->TryGetNumberField(TEXT("x"), X); Scale.X = X;
            (*ScaleObj)->TryGetNumberField(TEXT("y"), Y); Scale.Y = Y;
            (*ScaleObj)->TryGetNumberField(TEXT("z"), Z); Scale.Z = Z;
            bHasScale = true;
        }

        // auto_activate defaults true (ANiagaraActor's component bAutoActivate is true).
        bool bAutoActivate = true;
        Params->TryGetBoolField(TEXT("auto_activate"), bAutoActivate);

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("spawn_niagara_at_location: spawn_failed: no editor world available");
            return nullptr;
        }

        // Mirror Handler_SpawnActor's collision handling so placement never silently
        // fails on overlap (SpawnActor.cpp:90).
        FActorSpawnParameters SpawnParams;
        SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

        ANiagaraActor* Actor = World->SpawnActor<ANiagaraActor>(
            ANiagaraActor::StaticClass(), Location, Rotation, SpawnParams);
        if (!Actor)
        {
            OutError = TEXT("spawn_niagara_at_location: spawn_failed: World::SpawnActor returned null for ANiagaraActor");
            return nullptr;
        }

        // The spawn itself is recorded by the dispatcher's open transaction; Modify()
        // here folds the asset/label/scale edits below into the same single undo step.
        Actor->Modify();

        UNiagaraComponent* NiagaraComp = Actor->GetNiagaraComponent();
        if (!NiagaraComp)
        {
            // Malformed actor (should not happen for ANiagaraActor, but the accessor
            // can theoretically be null). Clean up the orphan and report.
            Actor->Destroy();
            OutError = TEXT("spawn_niagara_at_location: spawn_failed: spawned ANiagaraActor had no UNiagaraComponent");
            return nullptr;
        }

        NiagaraComp->SetAsset(System);

        // Apply scale separately (SpawnActor set only loc+rot).
        if (bHasScale)
        {
            Actor->SetActorRelativeScale3D(Scale);
        }

        // Activation: bAutoActivate defaults true, so an active component previews the
        // FX in-viewport. If the caller asked for auto_activate=false, deactivate.
        if (bAutoActivate)
        {
            NiagaraComp->Activate(true);
        }
        else
        {
            NiagaraComp->Deactivate();
        }

        // Apply optional label after the asset is set.
        FString Label;
        if (Params->TryGetStringField(TEXT("label"), Label) && !Label.IsEmpty())
        {
            Actor->SetActorLabel(Label);
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("name"), Actor->GetFName().ToString());
        Result->SetStringField(TEXT("label"), Actor->GetActorLabel());
        Result->SetStringField(TEXT("system"), ObjectPath);
        Result->SetStringField(TEXT("component"), NiagaraComp->GetFName().ToString());

        TSharedRef<FJsonObject> Loc = MakeShared<FJsonObject>();
        Loc->SetNumberField(TEXT("x"), Location.X);
        Loc->SetNumberField(TEXT("y"), Location.Y);
        Loc->SetNumberField(TEXT("z"), Location.Z);
        Result->SetObjectField(TEXT("location"), Loc);

        TSharedRef<FJsonObject> Rot = MakeShared<FJsonObject>();
        Rot->SetNumberField(TEXT("pitch"), Rotation.Pitch);
        Rot->SetNumberField(TEXT("yaw"), Rotation.Yaw);
        Rot->SetNumberField(TEXT("roll"), Rotation.Roll);
        Result->SetObjectField(TEXT("rotation"), Rot);

        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SpawnNiagaraAtLocation()
{
    return MakeShared<FHandler_SpawnNiagaraAtLocation>();
}
