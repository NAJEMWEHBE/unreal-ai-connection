// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// place_actors_raycast - raycast straight down onto level geometry at a set of
// XY targets (explicit points OR a generated grid) and spawn one actor of a
// given class at each surface hit. The studio-builder use case is "drop a book
// into every arched niche / scatter props onto a shelf surface" without the
// caller pre-computing surface Z heights.
//
// For each target (X, Y): trace from (X, Y, trace_start_z) downward to
// (X, Y, -trace_start_z) using UWorld::LineTraceSingleByChannel on ECC_Visibility.
// On a blocking hit, spawn the actor at hit.ImpactPoint + (0,0,z_offset). When
// align_to_normal is set, the actor's up-axis is rotated to the surface normal.
//
// Verified against UE 5.7 source:
//   UWorld::LineTraceSingleByChannel(FHitResult&, const FVector& Start,
//       const FVector& End, ECollisionChannel, ...)  -- Engine/World.h:2069
//   ECC_Visibility -- Engine/EngineTypes.h:1093
//   FHitResult::ImpactPoint -- Engine/HitResult.h:53
//   FHitResult::ImpactNormal -- Engine/HitResult.h:69
//   FHitResult::GetActor() -- Engine/HitResult.h:211
//
// Error format: "place_actors_raycast: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, invalid_class_path,
// class_not_spawnable, no_targets, invalid_grid, too_many_targets, no_world.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Engine/HitResult.h"
#include "Engine/EngineTypes.h"
#include "CollisionQueryParams.h"
#include "GameFramework/Actor.h"

namespace
{
    // Cap total targets so a runaway grid (count_x * count_y) can't try to spawn
    // tens of thousands of actors in one editor transaction.
    constexpr int32 kMaxTargets = 4096;

    struct FXYTarget
    {
        double X = 0.0;
        double Y = 0.0;
    };
}

class FHandler_PlaceActorsRaycast : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("place_actors_raycast"); }

    // Spawns are recorded by the dispatcher's FScopedTransaction (the world
    // captures actor adds while a transaction is open); Modify() on each spawned
    // actor folds its post-spawn label/transform edits into the same undo step.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("place_actors_raycast: missing_params: request had no params object");
            return nullptr;
        }

        FString ClassPath;
        if (!Params->TryGetStringField(TEXT("class_path"), ClassPath) || ClassPath.IsEmpty())
        {
            OutError = TEXT("place_actors_raycast: missing_required_field: 'class_path' is required and must be non-empty");
            return nullptr;
        }

        UClass* Class = LoadClass<AActor>(nullptr, *ClassPath);
        if (!Class)
        {
            OutError = FString::Printf(TEXT("place_actors_raycast: invalid_class_path: '%s' did not resolve to a UClass"), *ClassPath);
            return nullptr;
        }
        if (Class->HasAnyClassFlags(CLASS_Abstract | CLASS_Deprecated))
        {
            OutError = FString::Printf(TEXT("place_actors_raycast: class_not_spawnable: '%s' is abstract or deprecated"), *ClassPath);
            return nullptr;
        }
        if (!Class->IsChildOf(AActor::StaticClass()))
        {
            OutError = FString::Printf(TEXT("place_actors_raycast: class_not_spawnable: '%s' is not an AActor subclass"), *ClassPath);
            return nullptr;
        }

        // Optional scalars (initialize so missing JSON keys never leak garbage).
        double TraceStartZ = 100000.0;
        Params->TryGetNumberField(TEXT("trace_start_z"), TraceStartZ);
        if (TraceStartZ <= 0.0)
        {
            OutError = TEXT("place_actors_raycast: invalid_grid: 'trace_start_z' must be > 0");
            return nullptr;
        }

        double ZOffset = 0.0;
        Params->TryGetNumberField(TEXT("z_offset"), ZOffset);

        bool bAlignToNormal = false;
        Params->TryGetBoolField(TEXT("align_to_normal"), bAlignToNormal);

        FString LabelPrefix;
        Params->TryGetStringField(TEXT("label_prefix"), LabelPrefix);

        // Build the XY target list from either explicit `points` or a `grid`.
        TArray<FXYTarget> Targets;
        if (!BuildTargets(Params, Targets, OutError))
        {
            return nullptr;  // OutError already set
        }
        if (Targets.Num() == 0)
        {
            OutError = TEXT("place_actors_raycast: no_targets: supply non-empty 'points' [{x,y},...] or a 'grid' object");
            return nullptr;
        }
        if (Targets.Num() > kMaxTargets)
        {
            OutError = FString::Printf(
                TEXT("place_actors_raycast: too_many_targets: %d targets exceeds the %d cap"),
                Targets.Num(), kMaxTargets);
            return nullptr;
        }

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("place_actors_raycast: no_world: no editor world available");
            return nullptr;
        }

        FCollisionQueryParams TraceParams(SCENE_QUERY_STAT(MCPPlaceActorsRaycast), /*bTraceComplex=*/true);

        TArray<TSharedPtr<FJsonValue>> Placed;
        TArray<TSharedPtr<FJsonValue>> Missed;

        for (int32 i = 0; i < Targets.Num(); ++i)
        {
            const FVector Start(Targets[i].X, Targets[i].Y, TraceStartZ);
            const FVector End(Targets[i].X, Targets[i].Y, -TraceStartZ);

            FHitResult Hit;
            const bool bHit = World->LineTraceSingleByChannel(
                Hit, Start, End, ECC_Visibility, TraceParams);

            if (!bHit)
            {
                TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
                M->SetNumberField(TEXT("x"), Targets[i].X);
                M->SetNumberField(TEXT("y"), Targets[i].Y);
                Missed.Add(MakeShared<FJsonValueObject>(M));
                continue;
            }

            const FVector SpawnLoc = Hit.ImpactPoint + FVector(0, 0, ZOffset);
            FRotator SpawnRot = FRotator::ZeroRotator;
            if (bAlignToNormal)
            {
                // Rotate the actor's up-axis (Z) onto the surface normal. UE's
                // FRotationMatrix::MakeFromZ builds an orientation whose +Z maps
                // to the supplied direction; ImpactNormal points out of the
                // surface, so the actor stands "on" the hit face.
                SpawnRot = FRotationMatrix::MakeFromZ(Hit.ImpactNormal).Rotator();
            }

            FActorSpawnParameters SpawnParams;
            SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

            AActor* Actor = World->SpawnActor<AActor>(Class, SpawnLoc, SpawnRot, SpawnParams);
            if (!Actor)
            {
                // Treat a spawn failure for one target as a miss rather than
                // aborting the whole batch; record it so the caller can see it.
                TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
                M->SetNumberField(TEXT("x"), Targets[i].X);
                M->SetNumberField(TEXT("y"), Targets[i].Y);
                M->SetStringField(TEXT("error"), TEXT("spawn_failed"));
                Missed.Add(MakeShared<FJsonValueObject>(M));
                continue;
            }

            // Fold the post-spawn label edit into the dispatcher's transaction.
            Actor->Modify();
            if (!LabelPrefix.IsEmpty())
            {
                Actor->SetActorLabel(FString::Printf(TEXT("%s%d"), *LabelPrefix, i));
            }

            TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
            P->SetStringField(TEXT("name"), Actor->GetFName().ToString());
            P->SetStringField(TEXT("label"), Actor->GetActorLabel());

            TSharedRef<FJsonObject> Loc = MakeShared<FJsonObject>();
            Loc->SetNumberField(TEXT("x"), SpawnLoc.X);
            Loc->SetNumberField(TEXT("y"), SpawnLoc.Y);
            Loc->SetNumberField(TEXT("z"), SpawnLoc.Z);
            P->SetObjectField(TEXT("location"), Loc);

            TSharedRef<FJsonObject> Nrm = MakeShared<FJsonObject>();
            Nrm->SetNumberField(TEXT("x"), Hit.ImpactNormal.X);
            Nrm->SetNumberField(TEXT("y"), Hit.ImpactNormal.Y);
            Nrm->SetNumberField(TEXT("z"), Hit.ImpactNormal.Z);
            P->SetObjectField(TEXT("hit_normal"), Nrm);

            const AActor* HitActor = Hit.GetActor();
            P->SetStringField(TEXT("hit_actor"),
                HitActor ? HitActor->GetActorLabel() : TEXT(""));

            Placed.Add(MakeShared<FJsonValueObject>(P));
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("class"), Class->GetName());
        Result->SetNumberField(TEXT("requested"), Targets.Num());
        Result->SetNumberField(TEXT("placed_count"), Placed.Num());
        Result->SetNumberField(TEXT("missed_count"), Missed.Num());
        Result->SetArrayField(TEXT("placed"), Placed);
        Result->SetArrayField(TEXT("missed"), Missed);
        return Result;
    }

private:
    // Populate Targets from `grid` (preferred when present) or `points`.
    // Returns false + sets OutError on a malformed grid; returns true with an
    // empty Targets array when neither is supplied (caller maps that to
    // no_targets).
    static bool BuildTargets(const TSharedPtr<FJsonObject>& Params, TArray<FXYTarget>& Targets, FString& OutError)
    {
        const TSharedPtr<FJsonObject>* GridObj = nullptr;
        if (Params->TryGetObjectField(TEXT("grid"), GridObj) && GridObj && (*GridObj).IsValid())
        {
            double MinX = 0, MinY = 0, MaxX = 0, MaxY = 0;
            int32 CountX = 0, CountY = 0;
            const bool bHaveMinX = (*GridObj)->TryGetNumberField(TEXT("min_x"), MinX);
            const bool bHaveMinY = (*GridObj)->TryGetNumberField(TEXT("min_y"), MinY);
            const bool bHaveMaxX = (*GridObj)->TryGetNumberField(TEXT("max_x"), MaxX);
            const bool bHaveMaxY = (*GridObj)->TryGetNumberField(TEXT("max_y"), MaxY);
            const bool bHaveCX = (*GridObj)->TryGetNumberField(TEXT("count_x"), CountX);
            const bool bHaveCY = (*GridObj)->TryGetNumberField(TEXT("count_y"), CountY);

            if (!(bHaveMinX && bHaveMinY && bHaveMaxX && bHaveMaxY && bHaveCX && bHaveCY))
            {
                OutError = TEXT("place_actors_raycast: invalid_grid: 'grid' requires min_x, min_y, max_x, max_y, count_x, count_y");
                return false;
            }
            if (CountX < 1 || CountY < 1)
            {
                OutError = TEXT("place_actors_raycast: invalid_grid: count_x and count_y must each be >= 1");
                return false;
            }
            // Evenly distribute samples across [min,max] on each axis. With
            // count==1 on an axis, sample the midpoint of that span.
            for (int32 iy = 0; iy < CountY; ++iy)
            {
                const double ty = (CountY == 1) ? 0.5 : static_cast<double>(iy) / (CountY - 1);
                for (int32 ix = 0; ix < CountX; ++ix)
                {
                    const double tx = (CountX == 1) ? 0.5 : static_cast<double>(ix) / (CountX - 1);
                    FXYTarget T;
                    T.X = MinX + (MaxX - MinX) * tx;
                    T.Y = MinY + (MaxY - MinY) * ty;
                    Targets.Add(T);
                }
            }
            return true;
        }

        const TArray<TSharedPtr<FJsonValue>>* PointsArr = nullptr;
        if (Params->TryGetArrayField(TEXT("points"), PointsArr) && PointsArr)
        {
            for (const TSharedPtr<FJsonValue>& V : *PointsArr)
            {
                const TSharedPtr<FJsonObject>* PtObj = nullptr;
                if (V.IsValid() && V->TryGetObject(PtObj) && PtObj && (*PtObj).IsValid())
                {
                    FXYTarget T;
                    (*PtObj)->TryGetNumberField(TEXT("x"), T.X);
                    (*PtObj)->TryGetNumberField(TEXT("y"), T.Y);
                    Targets.Add(T);
                }
            }
        }
        return true;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_PlaceActorsRaycast()
{
    return MakeShared<FHandler_PlaceActorsRaycast>();
}
