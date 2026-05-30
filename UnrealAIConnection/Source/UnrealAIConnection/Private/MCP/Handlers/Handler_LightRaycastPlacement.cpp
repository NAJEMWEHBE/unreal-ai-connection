// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// light_raycast_placement - sweep a line (or an explicit point list) through the
// scene, raycast each sample onto level geometry, and spawn + configure a light
// pushed out along the surface normal at each hit. The studio-builder use case
// is "run a row of rect lights along this wall / shelf" without hand-placing
// each fixture and computing wall offsets.
//
// light_type selects the actor class: "point" -> APointLight, "rect" ->
// ARectLight, "spot" -> ASpotLight. Each sample point is traced toward the
// nearest surface; on a hit, the light is placed at hit.ImpactPoint +
// ImpactNormal * surface_offset and configured (intensity / color / radius).
//
// Sampling: with `sweep {start,end,count}`, `count` points are spaced evenly
// from start to end (inclusive). Each sweep sample is traced DOWN by default
// (along -Z from the sample point), which matches the common "lights above a
// floor surface" case; callers who need an arbitrary trace direction can fall
// back to explicit `points` with their own offsets.
//
// Verified against UE 5.7 source:
//   APointLight::PointLightComponent  -- Engine/PointLight.h:16
//   ARectLight::RectLightComponent    -- Engine/RectLight.h:16
//   ASpotLight::SpotLightComponent    -- Engine/SpotLight.h:16
//   ULightComponent::SetIntensity(float)                 -- Components/LightComponent.h:283
//   ULightComponent::SetLightColor(FLinearColor, bool)   -- Components/LightComponent.h:293
//   ULocalLightComponent::SetAttenuationRadius(float)    -- Components/LocalLightComponent.h:52
//   UWorld::LineTraceSingleByChannel(...)                -- Engine/World.h:2069
//   FHitResult::ImpactPoint / ImpactNormal               -- Engine/HitResult.h:53 / :69
//
// Error format: "light_raycast_placement: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, invalid_light_type,
// no_samples, invalid_sweep, too_many_lights, no_world.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Engine/HitResult.h"
#include "Engine/EngineTypes.h"
#include "CollisionQueryParams.h"
#include "GameFramework/Actor.h"
#include "Engine/PointLight.h"
#include "Engine/RectLight.h"
#include "Engine/SpotLight.h"
#include "Components/PointLightComponent.h"
#include "Components/RectLightComponent.h"
#include "Components/SpotLightComponent.h"
#include "Components/LightComponent.h"
#include "Components/LocalLightComponent.h"

namespace
{
    constexpr int32 kMaxLights = 1024;

    static bool ReadVector(const TSharedPtr<FJsonObject>& Obj, FVector& Out)
    {
        if (!Obj.IsValid()) { return false; }
        double X = 0, Y = 0, Z = 0;
        Obj->TryGetNumberField(TEXT("x"), X);
        Obj->TryGetNumberField(TEXT("y"), Y);
        Obj->TryGetNumberField(TEXT("z"), Z);
        Out = FVector(X, Y, Z);
        return true;
    }
}

class FHandler_LightRaycastPlacement : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("light_raycast_placement"); }

    // Spawns recorded by the dispatcher's FScopedTransaction; Modify() on each
    // spawned light folds its config edits into the same undo step.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("light_raycast_placement: missing_params: request had no params object");
            return nullptr;
        }

        FString LightType;
        if (!Params->TryGetStringField(TEXT("light_type"), LightType) || LightType.IsEmpty())
        {
            OutError = TEXT("light_raycast_placement: missing_required_field: 'light_type' is required (point|rect|spot)");
            return nullptr;
        }

        UClass* LightClass = nullptr;
        if (LightType.Equals(TEXT("point"), ESearchCase::IgnoreCase))      { LightClass = APointLight::StaticClass(); }
        else if (LightType.Equals(TEXT("rect"), ESearchCase::IgnoreCase))  { LightClass = ARectLight::StaticClass(); }
        else if (LightType.Equals(TEXT("spot"), ESearchCase::IgnoreCase))  { LightClass = ASpotLight::StaticClass(); }
        else
        {
            OutError = FString::Printf(
                TEXT("light_raycast_placement: invalid_light_type: '%s' must be one of point, rect, spot"), *LightType);
            return nullptr;
        }

        // Optional config (defaults chosen to be visibly lit out of the box).
        double SurfaceOffset = 50.0;
        Params->TryGetNumberField(TEXT("surface_offset"), SurfaceOffset);

        // Intensity left unset (-1 sentinel) means "leave the light's class
        // default" rather than forcing 0 (which would spawn a dark light).
        double Intensity = -1.0;
        const bool bHaveIntensity = Params->TryGetNumberField(TEXT("intensity"), Intensity);

        FLinearColor Color = FLinearColor::White;
        bool bHaveColor = false;
        const TSharedPtr<FJsonObject>* ColObj = nullptr;
        if (Params->TryGetObjectField(TEXT("light_color"), ColObj) && ColObj && (*ColObj).IsValid())
        {
            double R = 1, G = 1, B = 1;
            (*ColObj)->TryGetNumberField(TEXT("r"), R);
            (*ColObj)->TryGetNumberField(TEXT("g"), G);
            (*ColObj)->TryGetNumberField(TEXT("b"), B);
            Color = FLinearColor(static_cast<float>(R), static_cast<float>(G), static_cast<float>(B), 1.0f);
            bHaveColor = true;
        }

        double AttenuationRadius = -1.0;
        const bool bHaveAtten = Params->TryGetNumberField(TEXT("attenuation_radius"), AttenuationRadius);

        FString LabelPrefix;
        Params->TryGetStringField(TEXT("label_prefix"), LabelPrefix);

        // Build the sample point list from `sweep` (preferred) or `points`.
        TArray<FVector> Samples;
        if (!BuildSamples(Params, Samples, OutError))
        {
            return nullptr;
        }
        if (Samples.Num() == 0)
        {
            OutError = TEXT("light_raycast_placement: no_samples: supply a 'sweep' {start,end,count} or non-empty 'points'");
            return nullptr;
        }
        if (Samples.Num() > kMaxLights)
        {
            OutError = FString::Printf(
                TEXT("light_raycast_placement: too_many_lights: %d samples exceeds the %d cap"),
                Samples.Num(), kMaxLights);
            return nullptr;
        }

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("light_raycast_placement: no_world: no editor world available");
            return nullptr;
        }

        FCollisionQueryParams TraceParams(SCENE_QUERY_STAT(MCPLightRaycastPlacement), /*bTraceComplex=*/true);

        // Trace distance: reach far enough below each sample to hit a floor even
        // for high samples. Reuse the largest |Z| among samples plus a margin.
        double TraceReach = 100000.0;

        TArray<TSharedPtr<FJsonValue>> Placed;
        TArray<TSharedPtr<FJsonValue>> Missed;

        for (int32 i = 0; i < Samples.Num(); ++i)
        {
            const FVector Start = Samples[i];
            const FVector End = Start - FVector(0, 0, TraceReach);

            FHitResult Hit;
            const bool bHit = World->LineTraceSingleByChannel(
                Hit, Start, End, ECC_Visibility, TraceParams);

            if (!bHit)
            {
                TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
                M->SetNumberField(TEXT("x"), Start.X);
                M->SetNumberField(TEXT("y"), Start.Y);
                M->SetNumberField(TEXT("z"), Start.Z);
                Missed.Add(MakeShared<FJsonValueObject>(M));
                continue;
            }

            const FVector SpawnLoc = Hit.ImpactPoint + Hit.ImpactNormal * SurfaceOffset;

            FActorSpawnParameters SpawnParams;
            SpawnParams.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;

            AActor* LightActor = World->SpawnActor<AActor>(LightClass, SpawnLoc, FRotator::ZeroRotator, SpawnParams);
            if (!LightActor)
            {
                TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
                M->SetNumberField(TEXT("x"), Start.X);
                M->SetNumberField(TEXT("y"), Start.Y);
                M->SetNumberField(TEXT("z"), Start.Z);
                M->SetStringField(TEXT("error"), TEXT("spawn_failed"));
                Missed.Add(MakeShared<FJsonValueObject>(M));
                continue;
            }

            LightActor->Modify();

            // Resolve the light component (all three light actors expose a
            // ULightComponent-derived component; intensity/color live on the
            // ULightComponent base, attenuation radius on ULocalLightComponent).
            ULightComponent* LightComp = ResolveLightComponent(LightActor);
            if (LightComp)
            {
                if (bHaveIntensity && Intensity >= 0.0)
                {
                    LightComp->SetIntensity(static_cast<float>(Intensity));
                }
                if (bHaveColor)
                {
                    LightComp->SetLightColor(Color);
                }
                if (bHaveAtten && AttenuationRadius > 0.0)
                {
                    if (ULocalLightComponent* LocalLight = Cast<ULocalLightComponent>(LightComp))
                    {
                        LocalLight->SetAttenuationRadius(static_cast<float>(AttenuationRadius));
                    }
                }
            }

            if (!LabelPrefix.IsEmpty())
            {
                LightActor->SetActorLabel(FString::Printf(TEXT("%s%d"), *LabelPrefix, i));
            }

            TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
            P->SetStringField(TEXT("name"), LightActor->GetFName().ToString());
            P->SetStringField(TEXT("label"), LightActor->GetActorLabel());

            TSharedRef<FJsonObject> Loc = MakeShared<FJsonObject>();
            Loc->SetNumberField(TEXT("x"), SpawnLoc.X);
            Loc->SetNumberField(TEXT("y"), SpawnLoc.Y);
            Loc->SetNumberField(TEXT("z"), SpawnLoc.Z);
            P->SetObjectField(TEXT("location"), Loc);

            Placed.Add(MakeShared<FJsonValueObject>(P));
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("light_type"), LightType.ToLower());
        Result->SetNumberField(TEXT("requested"), Samples.Num());
        Result->SetNumberField(TEXT("placed_count"), Placed.Num());
        Result->SetNumberField(TEXT("missed_count"), Missed.Num());
        Result->SetArrayField(TEXT("placed"), Placed);
        Result->SetArrayField(TEXT("missed"), Missed);
        return Result;
    }

private:
    static ULightComponent* ResolveLightComponent(AActor* LightActor)
    {
        if (APointLight* PL = Cast<APointLight>(LightActor)) { return PL->PointLightComponent; }
        if (ARectLight* RL = Cast<ARectLight>(LightActor))   { return RL->RectLightComponent; }
        if (ASpotLight* SL = Cast<ASpotLight>(LightActor))   { return SL->SpotLightComponent; }
        return nullptr;
    }

    static bool BuildSamples(const TSharedPtr<FJsonObject>& Params, TArray<FVector>& Samples, FString& OutError)
    {
        const TSharedPtr<FJsonObject>* SweepObj = nullptr;
        if (Params->TryGetObjectField(TEXT("sweep"), SweepObj) && SweepObj && (*SweepObj).IsValid())
        {
            const TSharedPtr<FJsonObject>* StartObj = nullptr;
            const TSharedPtr<FJsonObject>* EndObj = nullptr;
            FVector Start, End;
            const bool bHaveStart = (*SweepObj)->TryGetObjectField(TEXT("start"), StartObj) && StartObj && ReadVector(*StartObj, Start);
            const bool bHaveEnd = (*SweepObj)->TryGetObjectField(TEXT("end"), EndObj) && EndObj && ReadVector(*EndObj, End);
            int32 Count = 0;
            const bool bHaveCount = (*SweepObj)->TryGetNumberField(TEXT("count"), Count);

            if (!bHaveStart || !bHaveEnd || !bHaveCount)
            {
                OutError = TEXT("light_raycast_placement: invalid_sweep: 'sweep' requires start{x,y,z}, end{x,y,z}, count");
                return false;
            }
            if (Count < 1)
            {
                OutError = TEXT("light_raycast_placement: invalid_sweep: 'count' must be >= 1");
                return false;
            }
            for (int32 i = 0; i < Count; ++i)
            {
                const double t = (Count == 1) ? 0.5 : static_cast<double>(i) / (Count - 1);
                Samples.Add(Start + (End - Start) * t);
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
                    FVector P;
                    ReadVector(*PtObj, P);
                    Samples.Add(P);
                }
            }
        }
        return true;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_LightRaycastPlacement()
{
    return MakeShared<FHandler_LightRaycastPlacement>();
}
