// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// spawn_niagara_attached - attach an editor-PERSISTENT UNiagaraComponent (running
// a given UNiagaraSystem) to an existing actor in the editor world, resolved by
// label or FName, optionally to a named socket on a named parent component, with
// an optional relative transform. Persistent instance component (saved with the
// actor, undoable).
//
// Replicates Handler_AddComponent's editor-persistent component flow
// (NewObject + AddInstanceComponent + RegisterComponent + AttachToComponent),
// then UNiagaraComponent::SetAsset. Deliberately does NOT use the transient
// UNiagaraFunctionLibrary::SpawnSystemAttached path (that produces a runtime
// component, not a level-persistent instance component).
//
// UE 5.7 surface used (verified against F:/UE_5.7 source + in-repo helpers):
//   - UCMCP::ActorIdentity::Resolve(...)                      ActorIdentity.h:44 (in-repo)
//   - NewObject<UNiagaraComponent>(Actor, Class, FName)       (mirrors Handler_AddComponent.cpp:94)
//   - AActor::AddInstanceComponent / UActorComponent::RegisterComponent
//                                                              (Handler_AddComponent.cpp:101-102)
//   - USceneComponent::AttachToComponent(Parent, FAttachmentTransformRules::KeepRelativeTransform, Socket)
//                                                              (Handler_AddComponent.cpp:153)
//   - USceneComponent::DoesSocketExist(FName)                 (Handler_AddComponent.cpp:141)
//   - USceneComponent::SetRelativeTransform(const FTransform&) (Handler_AddComponent.cpp:186)
//   - UNiagaraComponent : UFXSystemComponent : UPrimitiveComponent (a USceneComponent)
//                                                              NiagaraComponent.h:54
//   - void UNiagaraComponent::SetAsset(UNiagaraSystem*, bool)  NiagaraComponent.h:292
//   - void UNiagaraComponent::Activate(bool) / Deactivate()    NiagaraComponent.h:223
//   - UEditorAssetLibrary::LoadAsset(ObjectPath) + Cast<UNiagaraSystem>
//                                                              (Handler_InspectNiagaraSystem.cpp:106-120)
//
// Error format: "spawn_niagara_attached: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, actor_not_found,
// ambiguous_actor, system_not_found, not_a_niagara_system,
// attach_target_not_found, socket_not_found, create_failed.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "Dom/JsonObject.h"
#include "Components/SceneComponent.h"
#include "EditorAssetLibrary.h"
#include "GameFramework/Actor.h"
#include "NiagaraComponent.h"
#include "NiagaraSystem.h"

static FString JoinFNamesNiagaraAttach(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_SpawnNiagaraAttached : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("spawn_niagara_attached"); }

    // Attaches an instance component to an existing actor; the handler calls
    // Actor->Modify() before the mutation, which the dispatcher's
    // FScopedTransaction records as a single undo step.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("spawn_niagara_attached: missing_params: request had no params object");
            return nullptr;
        }

        FString ActorName;
        if (!Params->TryGetStringField(TEXT("actor_name"), ActorName) || ActorName.IsEmpty())
        {
            OutError = TEXT("spawn_niagara_attached: missing_required_field: 'actor_name' is required");
            return nullptr;
        }
        FString SystemPath;
        if (!Params->TryGetStringField(TEXT("system"), SystemPath) || SystemPath.IsEmpty())
        {
            OutError = TEXT("spawn_niagara_attached: missing_required_field: 'system' is required");
            return nullptr;
        }

        // Resolve the owning actor (label-first, then FName) — mirrors AddComponent.
        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(ActorName, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("spawn_niagara_attached: actor_not_found: no actor matches '%s'"), *ActorName);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("spawn_niagara_attached: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *ActorName, Ambiguous.Num(), *JoinFNamesNiagaraAttach(Ambiguous));
            return nullptr;
        }

        // Resolve the UNiagaraSystem (LazyOnDemand -> LoadAsset forces full load).
        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(SystemPath);
        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("spawn_niagara_attached: system_not_found: '%s' is not in the asset registry"), *SystemPath);
            return nullptr;
        }
        UNiagaraSystem* System = Cast<UNiagaraSystem>(Loaded);
        if (!System)
        {
            OutError = FString::Printf(
                TEXT("spawn_niagara_attached: not_a_niagara_system: '%s' is a %s, not a UNiagaraSystem"),
                *SystemPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // Determine FName for the new component (default auto).
        FString CompName;
        Params->TryGetStringField(TEXT("component_name"), CompName);
        FName CompFName = CompName.IsEmpty() ? NAME_None : FName(*CompName);

        // Snapshot the actor BEFORE adding the component so the dispatcher's
        // transaction records the addition (a Modify() after the fact would
        // snapshot the already-modified actor and undo would not remove the
        // component). Mirrors Handler_AddComponent.cpp:92.
        Actor->Modify();

        UNiagaraComponent* NewComp = NewObject<UNiagaraComponent>(Actor, UNiagaraComponent::StaticClass(), CompFName);
        if (!NewComp)
        {
            OutError = TEXT("spawn_niagara_attached: create_failed: NewObject<UNiagaraComponent> returned null");
            return nullptr;
        }

        Actor->AddInstanceComponent(NewComp);
        NewComp->RegisterComponent();

        // Resolve the parent component (default = root; or a named USceneComponent).
        USceneComponent* Parent = Actor->GetRootComponent();
        FString AttachTo;
        if (Params->TryGetStringField(TEXT("attach_to"), AttachTo) && !AttachTo.IsEmpty())
        {
            USceneComponent* Found = nullptr;
            TInlineComponentArray<USceneComponent*> SceneComps;
            Actor->GetComponents<USceneComponent>(SceneComps);
            for (USceneComponent* SC : SceneComps)
            {
                if (SC && SC != NewComp && SC->GetFName().ToString() == AttachTo)
                {
                    Found = SC;
                    break;
                }
            }
            if (!Found)
            {
                OutError = FString::Printf(
                    TEXT("spawn_niagara_attached: attach_target_not_found: no component '%s' on actor '%s'"),
                    *AttachTo, *Actor->GetFName().ToString());
                NewComp->DestroyComponent();
                return nullptr;
            }
            Parent = Found;
        }

        // Validate socket against the parent before attaching.
        FString Socket;
        FName SocketFName = NAME_None;
        if (Params->TryGetStringField(TEXT("socket"), Socket) && !Socket.IsEmpty())
        {
            SocketFName = FName(*Socket);
            if (Parent && !Parent->DoesSocketExist(SocketFName))
            {
                OutError = FString::Printf(
                    TEXT("spawn_niagara_attached: socket_not_found: parent '%s' has no socket '%s'"),
                    *Parent->GetFName().ToString(), *Socket);
                NewComp->DestroyComponent();
                return nullptr;
            }
        }

        FString AttachedToName = TEXT("");
        if (Parent)
        {
            NewComp->AttachToComponent(Parent, FAttachmentTransformRules::KeepRelativeTransform, SocketFName);
            AttachedToName = Parent->GetFName().ToString();
        }

        // Apply optional relative transform (same {location, rotation, scale} shape
        // as add_component).
        const TSharedPtr<FJsonObject>* RelObj = nullptr;
        if (Params->TryGetObjectField(TEXT("relative_transform"), RelObj) && RelObj && (*RelObj).IsValid())
        {
            FVector Loc(0, 0, 0); FRotator Rot(0, 0, 0); FVector Scale(1, 1, 1);
            const TSharedPtr<FJsonObject>* L = nullptr;
            if ((*RelObj)->TryGetObjectField(TEXT("location"), L) && L && (*L).IsValid())
            {
                double X = 0, Y = 0, Z = 0;
                (*L)->TryGetNumberField(TEXT("x"), X); Loc.X = X;
                (*L)->TryGetNumberField(TEXT("y"), Y); Loc.Y = Y;
                (*L)->TryGetNumberField(TEXT("z"), Z); Loc.Z = Z;
            }
            const TSharedPtr<FJsonObject>* R = nullptr;
            if ((*RelObj)->TryGetObjectField(TEXT("rotation"), R) && R && (*R).IsValid())
            {
                double P = 0, Y = 0, RR = 0;
                (*R)->TryGetNumberField(TEXT("pitch"), P); Rot.Pitch = P;
                (*R)->TryGetNumberField(TEXT("yaw"), Y); Rot.Yaw = Y;
                (*R)->TryGetNumberField(TEXT("roll"), RR); Rot.Roll = RR;
            }
            const TSharedPtr<FJsonObject>* S = nullptr;
            if ((*RelObj)->TryGetObjectField(TEXT("scale"), S) && S && (*S).IsValid())
            {
                double X = 1, Y = 1, Z = 1;
                (*S)->TryGetNumberField(TEXT("x"), X); Scale.X = X;
                (*S)->TryGetNumberField(TEXT("y"), Y); Scale.Y = Y;
                (*S)->TryGetNumberField(TEXT("z"), Z); Scale.Z = Z;
            }
            NewComp->SetRelativeTransform(FTransform(Rot, Loc, Scale));
        }

        // Set the asset AFTER RegisterComponent so the override-parameter store is
        // initialized against a registered component (spec gotcha).
        NewComp->SetAsset(System);

        // auto_activate defaults true; deactivate if explicitly false.
        bool bAutoActivate = true;
        Params->TryGetBoolField(TEXT("auto_activate"), bAutoActivate);
        if (bAutoActivate)
        {
            NewComp->Activate(true);
        }
        else
        {
            NewComp->Deactivate();
        }

        // Mirror add_component's construction-script rerun for consistency.
        Actor->RerunConstructionScripts();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("actor"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("component"), NewComp->GetFName().ToString());
        Out->SetStringField(TEXT("system"), ObjectPath);
        Out->SetStringField(TEXT("attached_to"), AttachedToName);
        Out->SetStringField(TEXT("socket"), Socket);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SpawnNiagaraAttached()
{
    return MakeShared<FHandler_SpawnNiagaraAttached>();
}
