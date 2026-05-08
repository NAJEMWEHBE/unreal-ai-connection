// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_actor_property - mutate any UPROPERTY on an actor.
// Supports v0.3.0 type list per PropertyCoercion module.
//
// Error format: "set_actor_property: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field,
// actor_not_found, ambiguous_actor, property_not_found,
// unsupported_property_type, value_coercion_failed.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"
#include "MCP/PropertyCoercion.h"

#include "Dom/JsonObject.h"
#include "GameFramework/Actor.h"
#include "UObject/UnrealType.h"

static FString JoinFNamesList(const TArray<FString>& In)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
    return Out;
}

class FHandler_SetActorProperty : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_actor_property"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("set_actor_property: missing_params: request had no params object");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("set_actor_property: missing_required_field: 'name' is required");
            return nullptr;
        }

        FString PropertyName;
        if (!Params->TryGetStringField(TEXT("property"), PropertyName) || PropertyName.IsEmpty())
        {
            OutError = TEXT("set_actor_property: missing_required_field: 'property' is required");
            return nullptr;
        }

        TSharedPtr<FJsonValue> Value = Params->TryGetField(TEXT("value"));
        if (!Value.IsValid())
        {
            OutError = TEXT("set_actor_property: missing_required_field: 'value' is required");
            return nullptr;
        }

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(Name, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("set_actor_property: actor_not_found: no actor matches '%s'"), *Name);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("set_actor_property: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *Name, Ambiguous.Num(), *JoinFNamesList(Ambiguous));
            return nullptr;
        }

        FProperty* Prop = UCMCP::PropertyCoercion::FindProperty(Actor, PropertyName);
        if (!Prop)
        {
            OutError = FString::Printf(
                TEXT("set_actor_property: property_not_found: '%s' not on class '%s'"),
                *PropertyName, *Actor->GetClass()->GetName());
            return nullptr;
        }

        // Capture old value before mutation
        TSharedPtr<FJsonValue> OldValue = UCMCP::PropertyCoercion::GetProperty(Actor, Prop);

        UCMCP::PropertyCoercion::FCoerceOutcome Outcome =
            UCMCP::PropertyCoercion::SetProperty(Actor, Prop, Value);

        if (Outcome.Result == UCMCP::PropertyCoercion::ECoerceResult::Unsupported)
        {
            OutError = FString::Printf(
                TEXT("set_actor_property: unsupported_property_type: %s — %s"),
                *Outcome.FPropertyClass, *Outcome.Detail);
            return nullptr;
        }
        if (Outcome.Result != UCMCP::PropertyCoercion::ECoerceResult::Success)
        {
            OutError = FString::Printf(
                TEXT("set_actor_property: value_coercion_failed: %s"),
                *Outcome.Detail);
            return nullptr;
        }

        // Fire UE's edit cascade for property mutations
        FPropertyChangedEvent ChangedEvent(Prop);
        Actor->PostEditChangeProperty(ChangedEvent);

        TSharedPtr<FJsonValue> NewValue = UCMCP::PropertyCoercion::GetProperty(Actor, Prop);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Actor->GetFName().ToString());
        Out->SetStringField(TEXT("property"), PropertyName);
        Out->SetField(TEXT("old_value"), OldValue);
        Out->SetField(TEXT("new_value"), NewValue);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetActorProperty()
{
    return MakeShared<FHandler_SetActorProperty>();
}
