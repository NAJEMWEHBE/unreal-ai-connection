// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// nanite_collision_toggle - flip a UStaticMesh's Nanite-enabled flag and trigger
// the settings-changed rebuild. The documented studio-builder gotcha this
// addresses: line traces against a Nanite mesh hit the coarse *fallback* mesh,
// not the full-detail Nanite geometry, so a raycast-placement pass (see
// place_actors_raycast / light_raycast_placement) can land props in the wrong
// spot on a 4K-triangle Nanite asset. Toggling Nanite off makes traces hit the
// real source geometry; toggle it back on afterward to restore rendering perf.
//
// Resolves the target mesh two ways:
//   - asset_path: a /Game StaticMesh path (loaded directly), OR
//   - actor:      an actor label/FName whose StaticMeshComponent's mesh is used.
//
// UE 5.7 deprecated direct UStaticMesh::NaniteSettings member access
// (StaticMesh.h:734 "use the various NaniteSettings accessor functions"), so we
// go through GetNaniteSettings()/NotifyNaniteSettingsChanged() rather than
// touching the member. NotifyNaniteSettingsChanged() internally fires
// PostEditChangeProperty on the NaniteSettings property, which is what kicks the
// mesh rebuild (StaticMesh.h:855-860).
//
// <=5.6 has NEITHER accessor (verified vs F:\UE_5.6 StaticMesh.h:741-742 - only
// the public `FMeshNaniteSettings NaniteSettings` member exists, bEnabled is a
// uint8:1 bitfield, EngineTypes.h:2986-2992), so pre-5.7 builds touch the member
// directly and fire PostEditChangeProperty on the NaniteSettings FProperty
// ourselves - the same event NotifyNaniteSettingsChanged() builds internally.
//
// Verified against UE 5.7 source:
//   UStaticMesh::GetNaniteSettings() -> FMeshNaniteSettings&  -- Engine/StaticMesh.h:840
//   UStaticMesh::NotifyNaniteSettingsChanged()                -- Engine/StaticMesh.h:855
//   FMeshNaniteSettings::bEnabled (uint8 bitfield)            -- Engine/EngineTypes.h:3045
//   UStaticMeshComponent::GetStaticMesh() const               -- Components/StaticMeshComponent.h:434
//   AStaticMeshActor::GetStaticMeshComponent() const          -- Engine/StaticMeshActor.h:75
//
// is_mutating = false: this edits ASSET content, not level/actor state on the
// editor undo stack, so the dispatcher's actor-oriented FScopedTransaction would
// only wrap an empty (discarded) transaction. We Modify() + MarkPackageDirty()
// the mesh ourselves so the change persists to a Save All. (Mirrors the
// configure_texture asset-content handler, which is likewise non-mutating.)
//
// Error format: "nanite_collision_toggle: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, no_target,
// invalid_field, asset_not_found, asset_wrong_type, actor_not_found, ambiguous_actor,
// actor_has_no_mesh, no_world.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"
#include "UCMCPCompat.h"            // UCMCP_ENGINE_AT_LEAST

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "GameFramework/Actor.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/EngineTypes.h"

namespace
{
    static FString ToObjectPath(const FString& InPath)
    {
        int32 DotIndex = INDEX_NONE;
        if (InPath.FindLastChar('.', DotIndex)) { return InPath; }
        int32 SlashIndex = INDEX_NONE;
        if (!InPath.FindLastChar('/', SlashIndex)) { return InPath; }
        return InPath + TEXT(".") + InPath.Mid(SlashIndex + 1);
    }

    static FString JoinFNamesList(const TArray<FString>& In)
    {
        FString Out;
        for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
        return Out;
    }
}

class FHandler_NaniteCollisionToggle : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("nanite_collision_toggle"); }

    // Asset-content edit (not actor/level undo state) -> not mutating in the
    // dispatcher-transaction sense; we dirty the package ourselves below.
    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("nanite_collision_toggle: missing_params: request had no params object");
            return nullptr;
        }

        bool bEnabledIn = false;
        if (!Params->TryGetBoolField(TEXT("enabled"), bEnabledIn))
        {
            OutError = TEXT("nanite_collision_toggle: missing_required_field: 'enabled' (bool) is required");
            return nullptr;
        }

        FString AssetPath;
        const bool bHaveAsset = Params->TryGetStringField(TEXT("asset_path"), AssetPath) && !AssetPath.IsEmpty();
        FString ActorName;
        const bool bHaveActor = Params->TryGetStringField(TEXT("actor"), ActorName) && !ActorName.IsEmpty();

        if (bHaveAsset && bHaveActor)
        {
            OutError = TEXT("nanite_collision_toggle: invalid_field: supply either 'asset_path' or 'actor', not both");
            return nullptr;
        }
        if (!bHaveAsset && !bHaveActor)
        {
            OutError = TEXT("nanite_collision_toggle: no_target: supply either 'asset_path' (/Game StaticMesh) or 'actor' (label)");
            return nullptr;
        }

        UStaticMesh* Mesh = nullptr;
        FString ResolvedVia;
        FString ResolvedActorLabel;

        if (bHaveAsset)
        {
            UObject* Loaded = LoadObject<UObject>(nullptr, *ToObjectPath(AssetPath));
            if (!Loaded)
            {
                OutError = FString::Printf(
                    TEXT("nanite_collision_toggle: asset_not_found: '%s' did not resolve to an asset"), *AssetPath);
                return nullptr;
            }
            Mesh = Cast<UStaticMesh>(Loaded);
            if (!Mesh)
            {
                OutError = FString::Printf(
                    TEXT("nanite_collision_toggle: asset_wrong_type: '%s' is a %s, not a UStaticMesh"),
                    *AssetPath, *Loaded->GetClass()->GetName());
                return nullptr;
            }
            ResolvedVia = TEXT("asset_path");
        }
        else
        {
            AActor* Actor = nullptr;
            TArray<FString> Ambiguous;
            const auto Res = UCMCP::ActorIdentity::Resolve(ActorName, Actor, Ambiguous);
            if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
            {
                OutError = FString::Printf(
                    TEXT("nanite_collision_toggle: actor_not_found: no actor matches '%s'"), *ActorName);
                return nullptr;
            }
            if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
            {
                OutError = FString::Printf(
                    TEXT("nanite_collision_toggle: ambiguous_actor: '%s' matched %d actors: [%s]"),
                    *ActorName, Ambiguous.Num(), *JoinFNamesList(Ambiguous));
                return nullptr;
            }

            // Pull the mesh off the actor's first UStaticMeshComponent. Cover the
            // AStaticMeshActor fast path plus any actor that carries an SMC.
            UStaticMeshComponent* SMC = nullptr;
            if (AStaticMeshActor* SMA = Cast<AStaticMeshActor>(Actor))
            {
                SMC = SMA->GetStaticMeshComponent();
            }
            if (!SMC)
            {
                SMC = Actor->FindComponentByClass<UStaticMeshComponent>();
            }
            if (!SMC || !SMC->GetStaticMesh())
            {
                OutError = FString::Printf(
                    TEXT("nanite_collision_toggle: actor_has_no_mesh: '%s' has no StaticMeshComponent with an assigned mesh"),
                    *ActorName);
                return nullptr;
            }
            Mesh = SMC->GetStaticMesh();
            ResolvedVia = TEXT("actor");
            ResolvedActorLabel = Actor->GetActorLabel();
        }

#if UCMCP_ENGINE_AT_LEAST(5, 7)
        // Read prior state, mutate via the 5.7 accessor, fire the rebuild.
        const bool bPrior = (Mesh->GetNaniteSettings().bEnabled != 0);

        Mesh->Modify();
        Mesh->GetNaniteSettings().bEnabled = bEnabledIn ? 1 : 0;
        // NotifyNaniteSettingsChanged() builds a FPropertyChangedEvent for the
        // NaniteSettings property and calls PostEditChangeProperty, which is the
        // engine's own path for "Nanite settings were edited; rebuild" — so a
        // separate Build() call would be redundant (and slower).
        Mesh->NotifyNaniteSettingsChanged();
        Mesh->MarkPackageDirty();

        const bool bNew = (Mesh->GetNaniteSettings().bEnabled != 0);
#else
        // <=5.6: no accessors; the public member + a manual
        // PostEditChangeProperty on the NaniteSettings FProperty replicate what
        // NotifyNaniteSettingsChanged() does internally on 5.7.
        const bool bPrior = (Mesh->NaniteSettings.bEnabled != 0);

        Mesh->Modify();
        Mesh->NaniteSettings.bEnabled = bEnabledIn ? 1 : 0;
        FProperty* NaniteProp = FindFieldChecked<FProperty>(
            UStaticMesh::StaticClass(), GET_MEMBER_NAME_CHECKED(UStaticMesh, NaniteSettings));
        FPropertyChangedEvent NaniteChangedEvent(NaniteProp);
        Mesh->PostEditChangeProperty(NaniteChangedEvent);
        Mesh->MarkPackageDirty();

        const bool bNew = (Mesh->NaniteSettings.bEnabled != 0);
#endif

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("mesh"), Mesh->GetPathName());
        Result->SetStringField(TEXT("resolved_via"), ResolvedVia);
        if (!ResolvedActorLabel.IsEmpty())
        {
            Result->SetStringField(TEXT("actor_label"), ResolvedActorLabel);
        }
        Result->SetBoolField(TEXT("prior_enabled"), bPrior);
        Result->SetBoolField(TEXT("new_enabled"), bNew);
        Result->SetStringField(TEXT("note"),
            TEXT("Asset content changed; run save_dirty_assets to persist. With Nanite OFF, line traces hit the full source geometry instead of the coarse fallback."));
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_NaniteCollisionToggle()
{
    return MakeShared<FHandler_NaniteCollisionToggle>();
}
