// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// rename_actor - change an actor's World Outliner display label.
//
// UE 5.7 surface (verified): AActor::SetActorLabel(const FString&, bool bMarkDirty=true)
// (GameFramework/Actor.h:2741). This sets the *label* (the human-readable
// Outliner name), not the FName — UE keeps the FName stable. Target resolved
// via the shared hybrid label/FName ActorIdentity helper.
//
// Error format: "rename_actor: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, actor_not_found,
// ambiguous_actor.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "GameFramework/Actor.h"

static FString JoinFNamesRename(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_RenameActor : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("rename_actor"); }

    // Mutates the actor label; Modify() below + the dispatcher transaction make
    // it undoable.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("rename_actor: missing_params: request had no params object");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("rename_actor: missing_required_field: 'name' is required");
            return nullptr;
        }

        FString NewLabel;
        if (!Params->TryGetStringField(TEXT("label"), NewLabel) || NewLabel.IsEmpty())
        {
            OutError = TEXT("rename_actor: missing_required_field: 'label' is required");
            return nullptr;
        }

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(Name, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("rename_actor: actor_not_found: no actor matches '%s'"), *Name);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("rename_actor: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *Name, Ambiguous.Num(), *JoinFNamesRename(Ambiguous));
            return nullptr;
        }

        const FString OldLabel = Actor->GetActorLabel();
        Actor->Modify();
        Actor->SetActorLabel(NewLabel);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("old_label"), OldLabel);
        Out->SetStringField(TEXT("new_label"), Actor->GetActorLabel());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_RenameActor()
{
    return MakeShared<FHandler_RenameActor>();
}
