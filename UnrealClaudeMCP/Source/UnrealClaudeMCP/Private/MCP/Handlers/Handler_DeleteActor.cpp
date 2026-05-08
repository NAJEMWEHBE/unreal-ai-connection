// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// delete_actor - remove an actor from the editor world by name.
// Children are detached, not destroyed (UE's default behavior).
// The 'force' flag overrides the children-attached safety check.
//
// Error format: "delete_actor: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field,
// actor_not_found, ambiguous_actor, has_children.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"

static FString JoinFNames(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_DeleteActor : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("delete_actor"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("delete_actor: missing_params: request had no params object");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("delete_actor: missing_required_field: 'name' is required");
            return nullptr;
        }

        bool bForce = false;
        Params->TryGetBoolField(TEXT("force"), bForce);

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(Name, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("delete_actor: actor_not_found: no actor matches '%s'"), *Name);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("delete_actor: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *Name, Ambiguous.Num(), *JoinFNames(Ambiguous));
            return nullptr;
        }

        if (!bForce)
        {
            TArray<AActor*> Children;
            Actor->GetAttachedActors(Children);
            if (Children.Num() > 0)
            {
                OutError = FString::Printf(
                    TEXT("delete_actor: has_children: actor has %d attached children; pass force=true to delete anyway"),
                    Children.Num());
                return nullptr;
            }
        }

        const FString FNameStr = Actor->GetFName().ToString();

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (World)
        {
            World->DestroyActor(Actor);
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), FNameStr);
        Out->SetBoolField(TEXT("deleted"), true);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_DeleteActor()
{
    return MakeShared<FHandler_DeleteActor>();
}
