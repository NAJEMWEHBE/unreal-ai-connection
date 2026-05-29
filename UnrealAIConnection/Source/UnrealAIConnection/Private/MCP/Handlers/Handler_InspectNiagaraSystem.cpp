// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_niagara_system - read the runtime-safe structural properties of a
// UNiagaraSystem asset: emitter handles, user-exposed parameters, warmup
// settings, fixed bounds, effect type, looping, and GPU-emitter presence.
// Pairs with inspect_asset (which reads registry-level metadata) and the
// existing Inspect* handlers for asset-introspection workflows.
//
// Part of the language-shim experiment (PR #46): "C++ canonical" handler.
// Niagara systems are LazyOnDemand assets, so C++ is the safest place to force
// a full load before reading emitter handles or exposed parameters.
//
// UE 5.7 surface used:
//   - UNiagaraSystem::EnsureFullyLoaded                         NiagaraSystem.h:526
//   - UNiagaraSystem::GetEmitterHandles                         NiagaraSystem.h:310
//   - FNiagaraEmitterHandle::GetName                            NiagaraEmitterHandle.h:56
//   - FNiagaraEmitterHandle::GetIsEnabled                       NiagaraEmitterHandle.h:62
//   - FNiagaraEmitterHandle::GetEmitterMode                     NiagaraEmitterHandle.h:95
//   - UNiagaraSystem::GetExposedParameters                      NiagaraSystem.h:364
//   - FNiagaraUserRedirectionParameterStore::GetUserParameters  NiagaraUserRedirectionParameterStore.h:32
//   - UNiagaraSystem::NeedsWarmup                               NiagaraSystem.h:398
//   - UNiagaraSystem::GetWarmupTickCount/GetWarmupTime/GetWarmupTickDelta NiagaraSystem.h:400-401
//   - UNiagaraSystem::IsLooping                                 NiagaraSystem.h:528
//   - UNiagaraSystem::HasAnyGPUEmitters                         NiagaraSystem.h:772
//   - UNiagaraSystem::GetFixedBounds                            NiagaraSystem.h:775
//   - UNiagaraSystem::bFixedBounds                              NiagaraSystem.h:808
//   - UNiagaraSystem::GetEffectType                             NiagaraSystem.h:789
//   - FNiagaraVariable::GetType / FNiagaraTypeDefinition::GetName NiagaraTypes.h
//
// Error format: "inspect_niagara_system: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_niagara_system.

#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "NiagaraEmitterHandle.h"
#include "NiagaraSystem.h"
#include "NiagaraTypes.h"
#include "NiagaraUserRedirectionParameterStore.h"
#include "UCMCPCompat.h"

namespace
{
    // Per-file unique name: UE 5.1 buckets unity builds differently than 5.7
    // and merges this TU with the other Inspect* handlers (Landscape /
    // StaticMesh / SkeletalMesh) that declare an identically-named anon-
    // namespace VectorToJson into one unity blob -> ODR clash
    // (round-1 C2084 at this line). Unique per-file names are
    // version-agnostic; behavior is unchanged.
    static TSharedPtr<FJsonObject> VectorToJson_InspectNiagara(const FVector& V)
    {
        TSharedPtr<FJsonObject> Obj = MakeShared<FJsonObject>();
        Obj->SetNumberField(TEXT("x"), V.X);
        Obj->SetNumberField(TEXT("y"), V.Y);
        Obj->SetNumberField(TEXT("z"), V.Z);
        return Obj;
    }

#if UCMCP_ENGINE_AT_LEAST(5, 2)
    // >=5.x verified-correct path. ENiagaraEmitterMode enum at
    // F:\UE_5.7\Engine\Plugins\FX\Niagara\Source\Niagara\Classes\NiagaraEmitterHandle.h:16
    // (Standard / Stateless); FNiagaraEmitterHandle::GetEmitterMode() at
    // NiagaraEmitterHandle.h:95.
    static FString EmitterModeToString(ENiagaraEmitterMode Mode)
    {
        return Mode == ENiagaraEmitterMode::Stateless ? TEXT("Stateless") : TEXT("Standard");
    }
#endif
    // UE 5.1: ENiagaraEmitterMode and FNiagaraEmitterHandle::GetEmitterMode()
    // are BOTH ABSENT (verified: F:\UE_5.1\Engine\Plugins\FX\Niagara\Source\Niagara\Classes\NiagaraEmitterHandle.h
    // exposes only GetName():45, GetIsEnabled():51, GetInstance():62,
    // GetEmitterData():65 -- no GetEmitterMode, no ENiagaraEmitterMode). The
    // Stateless-emitter feature did not exist on 5.1; every 5.1 emitter is
    // "Standard" by definition, so the JSON "mode" field is preserved with
    // that constant value on 5.1 rather than dropped.
    // UNVERIFIED-COMPILE: the 5.2..5.6 ENiagaraEmitterMode introduction
    // boundary is not verifiable here (no 5.2..5.6 engine on disk); boundary
    // set to 5.2 so every >=5.2 engine -- including the verified-correct
    // 5.7 -- keeps the original GetEmitterMode() call unchanged.
}

class FHandler_InspectNiagaraSystem : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_niagara_system"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_niagara_system: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_niagara_system: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_niagara_system: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }
        UNiagaraSystem* System = Cast<UNiagaraSystem>(Loaded);
        if (!System)
        {
            OutError = FString::Printf(
                TEXT("inspect_niagara_system: not_a_niagara_system: '%s' is a %s, not a UNiagaraSystem"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }
        System->EnsureFullyLoaded(); // NiagaraSystem.h:526; required before LazyOnDemand reads.

        // --- emitters ----------------------------------------------------

        const TArray<FNiagaraEmitterHandle>& EmitterHandles = System->GetEmitterHandles();
        TArray<TSharedPtr<FJsonValue>> EmitterArray;
        EmitterArray.Reserve(EmitterHandles.Num());
        for (const FNiagaraEmitterHandle& EmitterHandle : EmitterHandles)
        {
            TSharedPtr<FJsonObject> EmitterObj = MakeShared<FJsonObject>();
            EmitterObj->SetStringField(TEXT("name"), EmitterHandle.GetName().ToString());
            EmitterObj->SetBoolField(TEXT("enabled"), EmitterHandle.GetIsEnabled());
#if UCMCP_ENGINE_AT_LEAST(5, 2)
            // >=5.x verified-correct path (NiagaraEmitterHandle.h:95).
            EmitterObj->SetStringField(TEXT("mode"), EmitterModeToString(EmitterHandle.GetEmitterMode()));
#else
            // UE 5.1: no emitter "mode" concept; all emitters are Standard.
            // Field preserved with the only valid 5.1 value (see namespace
            // comment above for the on-disk verification).
            EmitterObj->SetStringField(TEXT("mode"), TEXT("Standard"));
#endif
            EmitterArray.Add(MakeShared<FJsonValueObject>(EmitterObj));
        }

        // --- user-exposed parameters ------------------------------------

        TArray<FNiagaraVariable> UserParameters;
        System->GetExposedParameters().GetUserParameters(UserParameters);

        TArray<TSharedPtr<FJsonValue>> UserParameterArray;
        UserParameterArray.Reserve(UserParameters.Num());
        for (const FNiagaraVariable& Var : UserParameters)
        {
            TSharedPtr<FJsonObject> ParamObj = MakeShared<FJsonObject>();
            ParamObj->SetStringField(TEXT("name"), Var.GetName().ToString());
            ParamObj->SetStringField(TEXT("type"), Var.GetType().GetName());
            UserParameterArray.Add(MakeShared<FJsonValueObject>(ParamObj));
        }

        // --- response ----------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), System->GetName());
        // Renamed from "path" to "package_path" for consistency with
        // inspect_static_mesh and inspect_asset. PR #51 Gemini medium review.
        Out->SetStringField(TEXT("package_path"), ObjectPath);
        Out->SetBoolField(TEXT("is_looping"), System->IsLooping());
        Out->SetBoolField(TEXT("has_gpu_emitters"), System->HasAnyGPUEmitters());

        const bool bNeedsWarmup = System->NeedsWarmup();
        Out->SetBoolField(TEXT("needs_warmup"), bNeedsWarmup);
        if (bNeedsWarmup)
        {
            Out->SetNumberField(TEXT("warmup_tick_count"), static_cast<double>(System->GetWarmupTickCount()));
            Out->SetNumberField(TEXT("warmup_time"), System->GetWarmupTime());
            Out->SetNumberField(TEXT("warmup_tick_delta"), System->GetWarmupTickDelta());
        }

        if (System->bFixedBounds)
        {
            // Mirror inspect_static_mesh's bounds shape (min, max, size, center)
            // for sibling consistency. PR #51 Gemini medium review.
            const FBox Bounds = System->GetFixedBounds();
            TSharedPtr<FJsonObject> BoundsObj = MakeShared<FJsonObject>();
            BoundsObj->SetObjectField(TEXT("min"), VectorToJson_InspectNiagara(Bounds.Min));
            BoundsObj->SetObjectField(TEXT("max"), VectorToJson_InspectNiagara(Bounds.Max));
            BoundsObj->SetObjectField(TEXT("size"), VectorToJson_InspectNiagara(Bounds.GetSize()));
            BoundsObj->SetObjectField(TEXT("center"), VectorToJson_InspectNiagara(Bounds.GetCenter()));
            Out->SetObjectField(TEXT("fixed_bounds"), BoundsObj);
        }

        if (UNiagaraEffectType* EffectType = System->GetEffectType())
        {
            // GetPathName returns the asset's full path (e.g. "/Game/FX/EffectTypes/EFT_Hero.EFT_Hero"),
            // not the literal class name "NiagaraEffectType" -- LLM consumers need the asset identity
            // to look it up, not the class taxonomy.
            Out->SetStringField(TEXT("effect_type"), EffectType->GetPathName());
        }

        Out->SetNumberField(TEXT("emitter_count"), static_cast<double>(EmitterHandles.Num()));
        Out->SetArrayField(TEXT("emitters"), EmitterArray);
        Out->SetNumberField(TEXT("user_parameter_count"), static_cast<double>(UserParameters.Num()));
        Out->SetArrayField(TEXT("user_parameters"), UserParameterArray);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectNiagaraSystem()
{
    return MakeShared<FHandler_InspectNiagaraSystem>();
}
