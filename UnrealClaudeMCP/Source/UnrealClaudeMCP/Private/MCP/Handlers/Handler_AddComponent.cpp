// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// add_component - attach a component to an existing actor at runtime.
//
// Error format: "add_component: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field,
// actor_not_found, ambiguous_actor, invalid_component_class,
// attach_target_not_found, socket_not_found.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "Components/ActorComponent.h"
#include "Components/SceneComponent.h"
#include "GameFramework/Actor.h"

static FString JoinFNamesAC(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_AddComponent : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("add_component"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("add_component: missing_params: request had no params object");
            return nullptr;
        }

        FString ActorName;
        if (!Params->TryGetStringField(TEXT("actor_name"), ActorName) || ActorName.IsEmpty())
        {
            OutError = TEXT("add_component: missing_required_field: 'actor_name' is required");
            return nullptr;
        }
        FString ClassPath;
        if (!Params->TryGetStringField(TEXT("class_path"), ClassPath) || ClassPath.IsEmpty())
        {
            OutError = TEXT("add_component: missing_required_field: 'class_path' is required");
            return nullptr;
        }

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(ActorName, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("add_component: actor_not_found: no actor matches '%s'"), *ActorName);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("add_component: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *ActorName, Ambiguous.Num(), *JoinFNamesAC(Ambiguous));
            return nullptr;
        }

        UClass* CompClass = LoadClass<UActorComponent>(nullptr, *ClassPath);
        if (!CompClass)
        {
            OutError = FString::Printf(TEXT("add_component: invalid_component_class: '%s' did not resolve"), *ClassPath);
            return nullptr;
        }
        if (CompClass->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated))
        {
            OutError = FString::Printf(TEXT("add_component: invalid_component_class: '%s' is abstract or deprecated"), *ClassPath);
            return nullptr;
        }

        // Determine FName for the new component
        FString CompName;
        Params->TryGetStringField(TEXT("component_name"), CompName);
        FName CompFName = CompName.IsEmpty() ? NAME_None : FName(*CompName);

        UActorComponent* NewComp = NewObject<UActorComponent>(Actor, CompClass, CompFName);
        if (!NewComp)
        {
            OutError = FString::Printf(TEXT("add_component: invalid_component_class: NewObject returned null for '%s'"), *ClassPath);
            return nullptr;
        }

        Actor->AddInstanceComponent(NewComp);
        NewComp->RegisterComponent();

        FString AttachedToName = TEXT("");

        // If it's a scene component, attach to a parent
        if (USceneComponent* SceneComp = Cast<USceneComponent>(NewComp))
        {
            USceneComponent* Parent = Actor->GetRootComponent();
            FString AttachTo;
            if (Params->TryGetStringField(TEXT("attach_to"), AttachTo) && !AttachTo.IsEmpty())
            {
                // Find a component on the actor matching the name
                USceneComponent* Found = nullptr;
                TInlineComponentArray<USceneComponent*> SceneComps;
                Actor->GetComponents<USceneComponent>(SceneComps);
                for (USceneComponent* SC : SceneComps)
                {
                    if (SC && SC != SceneComp && SC->GetFName().ToString() == AttachTo)
                    {
                        Found = SC;
                        break;
                    }
                }
                if (!Found)
                {
                    OutError = FString::Printf(
                        TEXT("add_component: attach_target_not_found: no component '%s' on actor '%s'"),
                        *AttachTo, *Actor->GetFName().ToString());
                    NewComp->DestroyComponent();
                    return nullptr;
                }
                Parent = Found;
            }

            FString Socket;
            FName SocketFName = NAME_None;
            if (Params->TryGetStringField(TEXT("socket"), Socket) && !Socket.IsEmpty())
            {
                SocketFName = FName(*Socket);
                if (Parent && !Parent->DoesSocketExist(SocketFName))
                {
                    OutError = FString::Printf(
                        TEXT("add_component: socket_not_found: parent '%s' has no socket '%s'"),
                        *Parent->GetFName().ToString(), *Socket);
                    NewComp->DestroyComponent();
                    return nullptr;
                }
            }

            if (Parent)
            {
                SceneComp->AttachToComponent(Parent, FAttachmentTransformRules::KeepRelativeTransform, SocketFName);
                AttachedToName = Parent->GetFName().ToString();
            }

            // Apply optional relative transform
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
                SceneComp->SetRelativeTransform(FTransform(Rot, Loc, Scale));
            }
        }

        Actor->Modify();
        Actor->RerunConstructionScripts();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("actor"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("component"), NewComp->GetFName().ToString());
        Out->SetStringField(TEXT("class"), NewComp->GetClass()->GetName());
        Out->SetStringField(TEXT("attached_to"), AttachedToName);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_AddComponent()
{
    return MakeShared<FHandler_AddComponent>();
}
