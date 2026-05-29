// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// duplicate_actor - clone an existing level actor, optionally offset + relabel.
//
// UE 5.7 surface (verified): UEditorActorSubsystem::DuplicateActor(AActor*,
// UWorld* = nullptr, FVector Offset = ZeroVector) -> AActor*
// (Subsystems/EditorActorSubsystem.h:111). Target actor resolved via the shared
// hybrid label/FName ActorIdentity helper. IsMutating() -> the dispatcher wraps
// it in an editor transaction, so the duplicate is a single Ctrl+Z step.
//
// Error format: "duplicate_actor: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, actor_not_found,
// ambiguous_actor, editor_unavailable, duplicate_failed.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Subsystems/EditorActorSubsystem.h"
#include "GameFramework/Actor.h"

static FString JoinFNamesDup(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_DuplicateActor : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("duplicate_actor"); }

    // Spawns a clone into the level — captured by the dispatcher's transaction.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("duplicate_actor: missing_params: request had no params object");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("duplicate_actor: missing_required_field: 'name' is required");
            return nullptr;
        }

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(Name, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("duplicate_actor: actor_not_found: no actor matches '%s'"), *Name);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("duplicate_actor: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *Name, Ambiguous.Num(), *JoinFNamesDup(Ambiguous));
            return nullptr;
        }

        // Optional world-space offset for the clone (default zero = same spot).
        FVector Offset(0, 0, 0);
        const TSharedPtr<FJsonObject>* OffObj = nullptr;
        if (Params->TryGetObjectField(TEXT("offset"), OffObj) && OffObj && (*OffObj).IsValid())
        {
            double X = 0, Y = 0, Z = 0;
            (*OffObj)->TryGetNumberField(TEXT("x"), X); Offset.X = X;
            (*OffObj)->TryGetNumberField(TEXT("y"), Y); Offset.Y = Y;
            (*OffObj)->TryGetNumberField(TEXT("z"), Z); Offset.Z = Z;
        }

        UEditorActorSubsystem* ActorSS = GEditor ? GEditor->GetEditorSubsystem<UEditorActorSubsystem>() : nullptr;
        if (!ActorSS)
        {
            OutError = TEXT("duplicate_actor: editor_unavailable: EditorActorSubsystem not available");
            return nullptr;
        }

        AActor* NewActor = ActorSS->DuplicateActor(Actor, nullptr, Offset);
        if (!NewActor)
        {
            OutError = FString::Printf(TEXT("duplicate_actor: duplicate_failed: DuplicateActor returned null for '%s'"), *Name);
            return nullptr;
        }

        // Optional label override for the clone.
        FString Label;
        if (Params->TryGetStringField(TEXT("label"), Label) && !Label.IsEmpty())
        {
            NewActor->SetActorLabel(Label);
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("source"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("name"), NewActor->GetFName().ToString());
        Out->SetStringField(TEXT("label"), NewActor->GetActorLabel());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_DuplicateActor()
{
    return MakeShared<FHandler_DuplicateActor>();
}
