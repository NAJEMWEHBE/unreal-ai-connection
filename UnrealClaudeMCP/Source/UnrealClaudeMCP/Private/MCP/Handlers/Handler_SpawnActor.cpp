// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// spawn_actor - create an actor in the editor world at a location with
// optional rotation, label, and initial properties.
//
// Error format: "spawn_actor: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field,
// invalid_class_path, class_not_spawnable, spawn_failed,
// property_application_failed.

#include "MCP/MCPHandler.h"
#include "MCP/PropertyCoercion.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"

class FHandler_SpawnActor : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("spawn_actor"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("spawn_actor: missing_params: request had no params object");
            return nullptr;
        }

        FString ClassPath;
        if (!Params->TryGetStringField(TEXT("class_path"), ClassPath) || ClassPath.IsEmpty())
        {
            OutError = TEXT("spawn_actor: missing_required_field: 'class_path' is required and must be non-empty");
            return nullptr;
        }

        UClass* Class = LoadClass<AActor>(nullptr, *ClassPath);
        if (!Class)
        {
            OutError = FString::Printf(TEXT("spawn_actor: invalid_class_path: '%s' did not resolve to a UClass"), *ClassPath);
            return nullptr;
        }
        if (Class->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated))
        {
            OutError = FString::Printf(TEXT("spawn_actor: class_not_spawnable: '%s' is abstract or deprecated"), *ClassPath);
            return nullptr;
        }
        if (!Class->IsChildOf(AActor::StaticClass()))
        {
            OutError = FString::Printf(TEXT("spawn_actor: class_not_spawnable: '%s' is not an AActor subclass"), *ClassPath);
            return nullptr;
        }

        // Location — initialize to (0,0,0) so missing JSON keys don't leak garbage
        FVector Location(0, 0, 0);
        const TSharedPtr<FJsonObject>* LocObj = nullptr;
        if (Params->TryGetObjectField(TEXT("location"), LocObj) && LocObj && (*LocObj).IsValid())
        {
            double X = 0, Y = 0, Z = 0;
            (*LocObj)->TryGetNumberField(TEXT("x"), X); Location.X = X;
            (*LocObj)->TryGetNumberField(TEXT("y"), Y); Location.Y = Y;
            (*LocObj)->TryGetNumberField(TEXT("z"), Z); Location.Z = Z;
        }

        // Rotation — same initialization discipline
        FRotator Rotation(0, 0, 0);
        const TSharedPtr<FJsonObject>* RotObj = nullptr;
        if (Params->TryGetObjectField(TEXT("rotation"), RotObj) && RotObj && (*RotObj).IsValid())
        {
            double Pitch = 0, Yaw = 0, Roll = 0;
            (*RotObj)->TryGetNumberField(TEXT("pitch"), Pitch); Rotation.Pitch = Pitch;
            (*RotObj)->TryGetNumberField(TEXT("yaw"), Yaw); Rotation.Yaw = Yaw;
            (*RotObj)->TryGetNumberField(TEXT("roll"), Roll); Rotation.Roll = Roll;
        }

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("spawn_actor: spawn_failed: no editor world available");
            return nullptr;
        }

        FActorSpawnParameters SpawnParams;
        SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

        AActor* Actor = World->SpawnActor<AActor>(Class, Location, Rotation, SpawnParams);
        if (!Actor)
        {
            OutError = FString::Printf(TEXT("spawn_actor: spawn_failed: World::SpawnActor returned null for '%s'"), *ClassPath);
            return nullptr;
        }

        // Apply optional label
        FString Label;
        if (Params->TryGetStringField(TEXT("label"), Label) && !Label.IsEmpty())
        {
            Actor->SetActorLabel(Label);
        }

        // Apply optional properties via PropertyCoercion (v0.4.0: dotted path traversal).
        FString FailedProperty;
        const TSharedPtr<FJsonObject>* PropsObj = nullptr;
        if (Params->TryGetObjectField(TEXT("properties"), PropsObj) && PropsObj && (*PropsObj).IsValid())
        {
            for (const auto& Pair : (*PropsObj)->Values)
            {
                UCMCP::PropertyCoercion::FResolvedProperty Resolved;
                UCMCP::PropertyCoercion::FCoerceOutcome ResolveOutcome =
                    UCMCP::PropertyCoercion::ResolvePropertyPath(Actor, Pair.Key, Resolved);
                if (ResolveOutcome.Result != UCMCP::PropertyCoercion::ECoerceResult::Success)
                {
                    FailedProperty = FString::Printf(TEXT("'%s' (%s)"), *Pair.Key, *ResolveOutcome.Detail);
                    break;
                }
                UCMCP::PropertyCoercion::FCoerceOutcome Outcome =
                    UCMCP::PropertyCoercion::SetProperty(
                        Actor, Resolved.Property, Resolved.PropAddr, Pair.Value,
                        TEXT(".") + Resolved.ResolvedPath, 0);
                if (Outcome.Result != UCMCP::PropertyCoercion::ECoerceResult::Success)
                {
                    FailedProperty = FString::Printf(TEXT("'%s' (%s)"), *Pair.Key, *Outcome.Detail);
                    break;
                }
            }
        }

        if (!FailedProperty.IsEmpty())
        {
            OutError = FString::Printf(
                TEXT("spawn_actor: property_application_failed: actor was spawned but property %s failed; call delete_actor to clean up"),
                *FailedProperty);
            // Return error AND keep the actor — caller decides whether to delete.
            return nullptr;
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("name"), Actor->GetFName().ToString());
        Result->SetStringField(TEXT("label"), Actor->GetActorLabel());
        Result->SetStringField(TEXT("class"), Actor->GetClass()->GetName());

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

TSharedRef<IUCMCPHandler> Make_Handler_SpawnActor()
{
    return MakeShared<FHandler_SpawnActor>();
}
