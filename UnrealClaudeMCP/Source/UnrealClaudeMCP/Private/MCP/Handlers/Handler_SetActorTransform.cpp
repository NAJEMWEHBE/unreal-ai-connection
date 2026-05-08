// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// set_actor_transform - move/rotate/scale an existing actor by name.
//
// Error format: "set_actor_transform: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field,
// actor_not_found, ambiguous_actor, no_changes_specified.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "GameFramework/Actor.h"

static FString JoinStrings(const TArray<FString>& In, const TCHAR* Sep)
{
    FString Out;
    for (int32 i = 0; i < In.Num(); ++i)
    {
        if (i > 0) Out += Sep;
        Out += In[i];
    }
    return Out;
}

class FHandler_SetActorTransform : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("set_actor_transform"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("set_actor_transform: missing_params: request had no params object");
            return nullptr;
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("set_actor_transform: missing_required_field: 'name' is required");
            return nullptr;
        }

        const bool bHasLocation = Params->HasField(TEXT("location"));
        const bool bHasRotation = Params->HasField(TEXT("rotation"));
        const bool bHasScale = Params->HasField(TEXT("scale"));
        if (!bHasLocation && !bHasRotation && !bHasScale)
        {
            OutError = TEXT("set_actor_transform: no_changes_specified: provide at least one of location / rotation / scale");
            return nullptr;
        }

        bool bRelative = false;
        Params->TryGetBoolField(TEXT("relative"), bRelative);

        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Result = UCMCP::ActorIdentity::Resolve(Name, Actor, Ambiguous);
        if (Result == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(TEXT("set_actor_transform: actor_not_found: no actor matches '%s'"), *Name);
            return nullptr;
        }
        if (Result == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("set_actor_transform: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *Name, Ambiguous.Num(), *JoinStrings(Ambiguous, TEXT(", ")));
            return nullptr;
        }

        FVector NewLocation = Actor->GetActorLocation();
        FRotator NewRotation = Actor->GetActorRotation();
        FVector NewScale = Actor->GetActorScale3D();

        TSharedPtr<FJsonObject> Applied = MakeShared<FJsonObject>();

        if (bHasLocation)
        {
            const TSharedPtr<FJsonObject>* L = nullptr;
            Params->TryGetObjectField(TEXT("location"), L);
            if (L && (*L).IsValid())
            {
                double X = 0, Y = 0, Z = 0;
                (*L)->TryGetNumberField(TEXT("x"), X);
                (*L)->TryGetNumberField(TEXT("y"), Y);
                (*L)->TryGetNumberField(TEXT("z"), Z);
                FVector V(X, Y, Z);
                NewLocation = bRelative ? (NewLocation + V) : V;
                TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
                A->SetNumberField(TEXT("x"), NewLocation.X);
                A->SetNumberField(TEXT("y"), NewLocation.Y);
                A->SetNumberField(TEXT("z"), NewLocation.Z);
                Applied->SetObjectField(TEXT("location"), A);
            }
        }
        if (bHasRotation)
        {
            const TSharedPtr<FJsonObject>* R = nullptr;
            Params->TryGetObjectField(TEXT("rotation"), R);
            if (R && (*R).IsValid())
            {
                double Pitch = 0, Yaw = 0, Roll = 0;
                (*R)->TryGetNumberField(TEXT("pitch"), Pitch);
                (*R)->TryGetNumberField(TEXT("yaw"), Yaw);
                (*R)->TryGetNumberField(TEXT("roll"), Roll);
                FRotator Rot(Pitch, Yaw, Roll);
                NewRotation = bRelative ? (NewRotation + Rot) : Rot;
                TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
                A->SetNumberField(TEXT("pitch"), NewRotation.Pitch);
                A->SetNumberField(TEXT("yaw"), NewRotation.Yaw);
                A->SetNumberField(TEXT("roll"), NewRotation.Roll);
                Applied->SetObjectField(TEXT("rotation"), A);
            }
        }
        if (bHasScale)
        {
            const TSharedPtr<FJsonObject>* S = nullptr;
            Params->TryGetObjectField(TEXT("scale"), S);
            if (S && (*S).IsValid())
            {
                double X = 1, Y = 1, Z = 1;
                (*S)->TryGetNumberField(TEXT("x"), X);
                (*S)->TryGetNumberField(TEXT("y"), Y);
                (*S)->TryGetNumberField(TEXT("z"), Z);
                FVector V(X, Y, Z);
                NewScale = bRelative ? (NewScale * V) : V;
                TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
                A->SetNumberField(TEXT("x"), NewScale.X);
                A->SetNumberField(TEXT("y"), NewScale.Y);
                A->SetNumberField(TEXT("z"), NewScale.Z);
                Applied->SetObjectField(TEXT("scale"), A);
            }
        }

        FTransform NewTransform(NewRotation, NewLocation, NewScale);
        Actor->SetActorTransform(NewTransform);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Actor->GetFName().ToString());
        Out->SetObjectField(TEXT("applied"), Applied);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SetActorTransform()
{
    return MakeShared<FHandler_SetActorTransform>();
}
