// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// decal_scatter - raycast onto level geometry at a set of XY targets (explicit
// `points`, a generated `grid`, or `bounds`+`count` random scatter) and spawn one
// ADecalActor at each surface hit, oriented to project into the surface, with
// per-decal seeded scale/rotation jitter. Studio-builder use case: scatter grime,
// posters, signage, footprints, or wear decals across a floor/wall without the
// caller pre-computing surface positions.
//
// For each target (X, Y): trace from (X, Y, trace_start_z) downward to
// (X, Y, -trace_start_z) via UWorld::LineTraceSingleByChannel on ECC_Visibility.
// On a blocking hit, spawn an ADecalActor at hit.ImpactPoint and orient its
// projection (-X axis) along the surface, then jitter roll + scale by a
// deterministic FRandomStream(seed).
//
// Verified against UE 5.7 source:
//   ADecalActor                          -- Engine/DecalActor.h:22
//   ADecalActor::GetDecal()              -- Engine/DecalActor.h:77 (UDecalComponent*)
//   UDecalComponent::SetDecalMaterial    -- Components/DecalComponent.h:117
//   UDecalComponent::DecalSize (FVector) -- Components/DecalComponent.h:82
//   UDecalComponent::SetSortOrder(int32) -- Components/DecalComponent.h:109
//   UWorld::LineTraceSingleByChannel     -- Engine/World.h:2069
//   FRotationMatrix::MakeFromX           -- Math/RotationMatrix.h (decals project +X)
//   FRandomStream                        -- Math/RandomStream.h (deterministic, no FMath::Rand)
//
// Decals project along their local +X axis (unlike mesh actors which stand on +Z),
// so we map +X to the INWARD surface direction (-ImpactNormal); MakeFromZ (as used
// by place_actors_raycast) would point the decal sideways.
//
// Error format: "decal_scatter: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, invalid_material_path,
// no_targets, invalid_grid, invalid_bounds, invalid_field, too_many_targets, no_world.

#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Engine/HitResult.h"
#include "Engine/EngineTypes.h"
#include "CollisionQueryParams.h"
#include "Engine/DecalActor.h"
#include "Components/DecalComponent.h"
#include "Materials/MaterialInterface.h"
#include "Math/RandomStream.h"
#include "Math/RotationMatrix.h"
#include "UObject/UObjectGlobals.h"

namespace
{
    // Cap total targets so a runaway grid/count can't try to spawn tens of
    // thousands of decals in one editor transaction.
    constexpr int32 kMaxTargets = 4096;

    struct FXYTarget
    {
        double X = 0.0;
        double Y = 0.0;
    };
}

class FHandler_DecalScatter : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("decal_scatter"); }

    // Spawns + post-spawn edits are captured by the dispatcher's FScopedTransaction;
    // Modify() on each spawned actor/component folds edits into the same undo step.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("decal_scatter: missing_params: request had no params object");
            return nullptr;
        }

        FString MaterialPath;
        if (!Params->TryGetStringField(TEXT("decal_material"), MaterialPath) || MaterialPath.IsEmpty())
        {
            OutError = TEXT("decal_scatter: missing_required_field: 'decal_material' is required and must be non-empty");
            return nullptr;
        }

        UMaterialInterface* DecalMaterial =
            LoadObject<UMaterialInterface>(nullptr, *UCMCPAssetPath::ToObjectPath(MaterialPath));
        if (!DecalMaterial)
        {
            OutError = FString::Printf(
                TEXT("decal_scatter: invalid_material_path: '%s' did not resolve to a UMaterialInterface"),
                *MaterialPath);
            return nullptr;
        }

        // Optional scalars (initialize so missing JSON keys never leak garbage).
        double TraceStartZ = 100000.0;
        Params->TryGetNumberField(TEXT("trace_start_z"), TraceStartZ);
        if (TraceStartZ <= 0.0)
        {
            OutError = TEXT("decal_scatter: invalid_field: 'trace_start_z' must be > 0");
            return nullptr;
        }

        // Base decal half-extents. X = projection depth (how far the decal projects
        // into the surface); Y/Z = footprint on the surface.
        FVector BaseSize(16.0, 64.0, 64.0);
        const TSharedPtr<FJsonObject>* SizeObj = nullptr;
        if (Params->TryGetObjectField(TEXT("decal_size"), SizeObj) && SizeObj && (*SizeObj).IsValid())
        {
            (*SizeObj)->TryGetNumberField(TEXT("x"), BaseSize.X);
            (*SizeObj)->TryGetNumberField(TEXT("y"), BaseSize.Y);
            (*SizeObj)->TryGetNumberField(TEXT("z"), BaseSize.Z);
        }

        double ScaleMin = 1.0, ScaleMax = 1.0;
        Params->TryGetNumberField(TEXT("scale_min"), ScaleMin);
        Params->TryGetNumberField(TEXT("scale_max"), ScaleMax);
        if (ScaleMin <= 0.0 || ScaleMax < ScaleMin)
        {
            OutError = TEXT("decal_scatter: invalid_field: require 0 < scale_min <= scale_max");
            return nullptr;
        }

        double RotationJitterDeg = 0.0;
        Params->TryGetNumberField(TEXT("rotation_jitter_deg"), RotationJitterDeg);

        int32 SortOrder = 0;
        Params->TryGetNumberField(TEXT("sort_order"), SortOrder);

        int32 Seed = 0;
        Params->TryGetNumberField(TEXT("seed"), Seed);
        FRandomStream Stream(Seed);

        FString LabelPrefix;
        Params->TryGetStringField(TEXT("label_prefix"), LabelPrefix);

        // Build the XY target list from `grid`, `points`, or `bounds`+`count`.
        TArray<FXYTarget> Targets;
        if (!BuildTargets(Params, Stream, Targets, OutError))
        {
            return nullptr;  // OutError already set
        }
        if (Targets.Num() == 0)
        {
            OutError = TEXT("decal_scatter: no_targets: supply non-empty 'points' [{x,y},...], a 'grid' object, or a 'bounds' object with 'count'");
            return nullptr;
        }
        if (Targets.Num() > kMaxTargets)
        {
            OutError = FString::Printf(
                TEXT("decal_scatter: too_many_targets: %d targets exceeds the %d cap"),
                Targets.Num(), kMaxTargets);
            return nullptr;
        }

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("decal_scatter: no_world: no editor world available");
            return nullptr;
        }

        FCollisionQueryParams TraceParams(SCENE_QUERY_STAT(MCPDecalScatter), /*bTraceComplex=*/true);

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

            // Decals project along +X; aim +X into the surface (-ImpactNormal),
            // then add a random spin about that projection axis (roll).
            FRotator SpawnRot = FRotationMatrix::MakeFromX(-Hit.ImpactNormal).Rotator();
            if (RotationJitterDeg != 0.0)
            {
                SpawnRot.Roll += Stream.FRandRange(-RotationJitterDeg, RotationJitterDeg);
            }
            const double ScaleFactor = Stream.FRandRange(ScaleMin, ScaleMax);

            FActorSpawnParameters SpawnParams;
            SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

            ADecalActor* Decal = World->SpawnActor<ADecalActor>(Hit.ImpactPoint, SpawnRot, SpawnParams);
            if (!Decal)
            {
                TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
                M->SetNumberField(TEXT("x"), Targets[i].X);
                M->SetNumberField(TEXT("y"), Targets[i].Y);
                M->SetStringField(TEXT("error"), TEXT("spawn_failed"));
                Missed.Add(MakeShared<FJsonValueObject>(M));
                continue;
            }
            TraceParams.AddIgnoredActor(Decal);

            // Fold post-spawn edits into the dispatcher's transaction.
            Decal->Modify();
            UDecalComponent* Comp = Decal->GetDecal();
            if (Comp)
            {
                Comp->Modify();
                Comp->SetDecalMaterial(DecalMaterial);
                Comp->DecalSize = BaseSize * ScaleFactor;
                Comp->SetSortOrder(SortOrder);
                Comp->MarkRenderStateDirty();
            }
            if (!LabelPrefix.IsEmpty())
            {
                Decal->SetActorLabel(FString::Printf(TEXT("%s%d"), *LabelPrefix, i));
            }

            TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
            P->SetStringField(TEXT("name"), Decal->GetFName().ToString());
            P->SetStringField(TEXT("label"), Decal->GetActorLabel());

            TSharedRef<FJsonObject> Loc = MakeShared<FJsonObject>();
            Loc->SetNumberField(TEXT("x"), Hit.ImpactPoint.X);
            Loc->SetNumberField(TEXT("y"), Hit.ImpactPoint.Y);
            Loc->SetNumberField(TEXT("z"), Hit.ImpactPoint.Z);
            P->SetObjectField(TEXT("location"), Loc);

            P->SetNumberField(TEXT("scale"), ScaleFactor);

            TSharedRef<FJsonObject> Nrm = MakeShared<FJsonObject>();
            Nrm->SetNumberField(TEXT("x"), Hit.ImpactNormal.X);
            Nrm->SetNumberField(TEXT("y"), Hit.ImpactNormal.Y);
            Nrm->SetNumberField(TEXT("z"), Hit.ImpactNormal.Z);
            P->SetObjectField(TEXT("hit_normal"), Nrm);

            const AActor* HitActor = Hit.GetActor();
            P->SetStringField(TEXT("hit_actor"), HitActor ? HitActor->GetActorLabel() : TEXT(""));

            Placed.Add(MakeShared<FJsonValueObject>(P));
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("decal_material"), MaterialPath);
        Result->SetNumberField(TEXT("requested"), Targets.Num());
        Result->SetNumberField(TEXT("placed_count"), Placed.Num());
        Result->SetNumberField(TEXT("missed_count"), Missed.Num());
        Result->SetNumberField(TEXT("seed"), Seed);
        Result->SetArrayField(TEXT("placed"), Placed);
        Result->SetArrayField(TEXT("missed"), Missed);
        return Result;
    }

private:
    // Populate Targets from `grid` (preferred), else `points`, else `bounds`+count
    // (random scatter via the seeded Stream). Returns false + sets OutError on a
    // malformed grid/bounds; returns true with an empty Targets array when none is
    // supplied (caller maps that to no_targets).
    static bool BuildTargets(const TSharedPtr<FJsonObject>& Params, FRandomStream& Stream,
                             TArray<FXYTarget>& Targets, FString& OutError)
    {
        // --- grid ---
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
                OutError = TEXT("decal_scatter: invalid_grid: 'grid' requires min_x, min_y, max_x, max_y, count_x, count_y");
                return false;
            }
            if (CountX < 1 || CountY < 1)
            {
                OutError = TEXT("decal_scatter: invalid_grid: count_x and count_y must each be >= 1");
                return false;
            }
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

        // --- points ---
        const TArray<TSharedPtr<FJsonValue>>* PointsArr = nullptr;
        if (Params->TryGetArrayField(TEXT("points"), PointsArr) && PointsArr)
        {
            for (int32 PointIndex = 0; PointIndex < PointsArr->Num(); ++PointIndex)
            {
                const TSharedPtr<FJsonValue>& V = (*PointsArr)[PointIndex];
                const TSharedPtr<FJsonObject>* PtObj = nullptr;
                if (V.IsValid() && V->TryGetObject(PtObj) && PtObj && (*PtObj).IsValid())
                {
                    FXYTarget T;
                    const bool bHaveX = (*PtObj)->TryGetNumberField(TEXT("x"), T.X);
                    const bool bHaveY = (*PtObj)->TryGetNumberField(TEXT("y"), T.Y);
                    if (!bHaveX || !bHaveY)
                    {
                        OutError = FString::Printf(
                            TEXT("decal_scatter: invalid_field: points[%d] requires numeric x and y"),
                            PointIndex);
                        return false;
                    }
                    Targets.Add(T);
                }
            }
            return true;
        }

        // --- bounds + count (random scatter) ---
        const TSharedPtr<FJsonObject>* BoundsObj = nullptr;
        if (Params->TryGetObjectField(TEXT("bounds"), BoundsObj) && BoundsObj && (*BoundsObj).IsValid())
        {
            double MinX = 0, MinY = 0, MaxX = 0, MaxY = 0;
            int32 Count = 0;
            const bool bHaveMinX = (*BoundsObj)->TryGetNumberField(TEXT("min_x"), MinX);
            const bool bHaveMinY = (*BoundsObj)->TryGetNumberField(TEXT("min_y"), MinY);
            const bool bHaveMaxX = (*BoundsObj)->TryGetNumberField(TEXT("max_x"), MaxX);
            const bool bHaveMaxY = (*BoundsObj)->TryGetNumberField(TEXT("max_y"), MaxY);
            const bool bHaveCount = (*BoundsObj)->TryGetNumberField(TEXT("count"), Count);

            if (!(bHaveMinX && bHaveMinY && bHaveMaxX && bHaveMaxY && bHaveCount))
            {
                OutError = TEXT("decal_scatter: invalid_bounds: 'bounds' requires min_x, min_y, max_x, max_y, count");
                return false;
            }
            if (Count < 1)
            {
                OutError = TEXT("decal_scatter: invalid_bounds: 'count' must be >= 1");
                return false;
            }
            if (MaxX < MinX || MaxY < MinY)
            {
                OutError = TEXT("decal_scatter: invalid_bounds: require max_x >= min_x and max_y >= min_y");
                return false;
            }
            for (int32 i = 0; i < Count; ++i)
            {
                FXYTarget T;
                T.X = Stream.FRandRange(MinX, MaxX);
                T.Y = Stream.FRandRange(MinY, MaxY);
                Targets.Add(T);
            }
            return true;
        }

        return true;  // nothing supplied -> empty -> caller emits no_targets
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_DecalScatter()
{
    return MakeShared<FHandler_DecalScatter>();
}
