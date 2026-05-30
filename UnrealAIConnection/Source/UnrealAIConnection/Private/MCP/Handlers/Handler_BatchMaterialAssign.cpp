// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// batch_material_assign - assign one UMaterialInterface to the mesh-component
// material slots of many actors in a single call. Resolves the target actor set
// three ways (by_label list, by_folder World Outliner path prefix, or
// by_name_regex), then for each actor walks its UMeshComponent(s) and calls
// SetMaterial(slot, mat). The studio-builder use case is "retexture every wall
// in the /Set/Walls folder to the new marble material" in one shot — the bulk
// extension of the single-actor material_auto_remap synthetic.
//
// Verified against UE 5.7 source:
//   UMeshComponent::SetMaterial(int32, UMaterialInterface*)     -- Components/MeshComponent.h:116
//   UMeshComponent::GetNumMaterials() const                     -- Components/MeshComponent.h:113
//   AActor::GetComponents<T>(TArray<T*>&)                       -- GameFramework/Actor.h:3981
//   AActor::GetFolderPath() const -> FName                      -- GameFramework/Actor.h:2757
//   AActor::GetActorLabel(bool) const                           -- GameFramework/Actor.h:2733
//   FRegexPattern / FRegexMatcher                               -- Core: Internationalization/Regex.h
//
// Error format: "batch_material_assign: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// material_not_found, material_wrong_type, invalid_regex, no_world,
// no_actors_matched.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "Components/MeshComponent.h"
#include "Materials/MaterialInterface.h"
#include "Internationalization/Regex.h"
#include "UObject/SoftObjectPath.h"

namespace
{
    // Normalize a /Game asset path to UE's "object path" form (append .Name when
    // the caller passed a bare package path) so LoadObject resolves either form.
    static FString ToObjectPath(const FString& InPath)
    {
        int32 DotIndex = INDEX_NONE;
        if (InPath.FindLastChar('.', DotIndex))
        {
            return InPath;  // already has a .Name suffix
        }
        int32 SlashIndex = INDEX_NONE;
        if (!InPath.FindLastChar('/', SlashIndex))
        {
            return InPath;
        }
        return InPath + TEXT(".") + InPath.Mid(SlashIndex + 1);
    }
}

class FHandler_BatchMaterialAssign : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("batch_material_assign"); }

    // Mutates existing components; Modify() on each component below records the
    // change into the dispatcher's FScopedTransaction for a single-step undo.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("batch_material_assign: missing_params: request had no params object");
            return nullptr;
        }

        FString MaterialPath;
        if (!Params->TryGetStringField(TEXT("material"), MaterialPath) || MaterialPath.IsEmpty())
        {
            OutError = TEXT("batch_material_assign: missing_required_field: 'material' is required and must be non-empty");
            return nullptr;
        }

        UObject* Loaded = LoadObject<UObject>(nullptr, *ToObjectPath(MaterialPath));
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("batch_material_assign: material_not_found: '%s' did not resolve to an asset"), *MaterialPath);
            return nullptr;
        }
        UMaterialInterface* Material = Cast<UMaterialInterface>(Loaded);
        if (!Material)
        {
            OutError = FString::Printf(
                TEXT("batch_material_assign: material_wrong_type: '%s' is a %s, not a UMaterialInterface"),
                *MaterialPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // slot: -1 (default) = all slots; otherwise the single slot index.
        int32 Slot = -1;
        Params->TryGetNumberField(TEXT("slot"), Slot);
        if (Slot < -1)
        {
            OutError = TEXT("batch_material_assign: invalid_field: 'slot' must be -1 for all slots or a non-negative slot index");
            return nullptr;
        }

        // Parse the targets selector (exactly one of by_label / by_folder /
        // by_name_regex). All three resolve to a predicate over the world.
        const TSharedPtr<FJsonObject>* TargetsObj = nullptr;
        if (!Params->TryGetObjectField(TEXT("targets"), TargetsObj) || !TargetsObj || !(*TargetsObj).IsValid())
        {
            OutError = TEXT("batch_material_assign: missing_required_field: 'targets' object is required");
            return nullptr;
        }

        TSet<FString> ByLabel;
        const TArray<TSharedPtr<FJsonValue>>* LabelArr = nullptr;
        if ((*TargetsObj)->TryGetArrayField(TEXT("by_label"), LabelArr) && LabelArr)
        {
            for (const TSharedPtr<FJsonValue>& V : *LabelArr)
            {
                FString S;
                if (V.IsValid() && V->TryGetString(S) && !S.IsEmpty())
                {
                    ByLabel.Add(S);
                }
            }
        }

        FString ByFolder;
        (*TargetsObj)->TryGetStringField(TEXT("by_folder"), ByFolder);

        FString ByNameRegex;
        (*TargetsObj)->TryGetStringField(TEXT("by_name_regex"), ByNameRegex);

        const bool bHaveLabel = ByLabel.Num() > 0;
        const bool bHaveFolder = !ByFolder.IsEmpty();
        const bool bHaveRegex = !ByNameRegex.IsEmpty();
        const int32 SelectorCount = (bHaveLabel ? 1 : 0) + (bHaveFolder ? 1 : 0) + (bHaveRegex ? 1 : 0);
        if (SelectorCount != 1)
        {
            OutError = TEXT("batch_material_assign: invalid_field: 'targets' must set exactly one of by_label, by_folder, or by_name_regex");
            return nullptr;
        }
        const FRegexPattern NamePattern(ByNameRegex);

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("batch_material_assign: no_world: no editor world available");
            return nullptr;
        }

        // Resolve the actor set. by_folder matches the actor's World Outliner
        // folder path as a prefix (so "/Set/Walls" also catches "/Set/Walls/Sub").
        TArray<AActor*> MatchedActors;
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* Actor = *It;
            if (!Actor) { continue; }

            bool bMatch = false;
            if (bHaveLabel && ByLabel.Contains(Actor->GetActorLabel()))
            {
                bMatch = true;
            }
            if (!bMatch && bHaveFolder)
            {
                const FString FolderPath = Actor->GetFolderPath().ToString();
                if (FolderPath == ByFolder || FolderPath.StartsWith(ByFolder + TEXT("/")))
                {
                    bMatch = true;
                }
            }
            if (!bMatch && bHaveRegex)
            {
                // Match against both label and FName. The pattern is compiled
                // once above; each input still needs its own matcher.
                FRegexMatcher LabelMatcher(NamePattern, Actor->GetActorLabel());
                FRegexMatcher NameMatcher(NamePattern, Actor->GetFName().ToString());
                if (LabelMatcher.FindNext() || NameMatcher.FindNext())
                {
                    bMatch = true;
                }
            }

            if (bMatch)
            {
                MatchedActors.Add(Actor);
            }
        }

        if (MatchedActors.Num() == 0)
        {
            OutError = TEXT("batch_material_assign: no_actors_matched: the targets selector matched zero actors in the current level");
            return nullptr;
        }

        int32 ActorsTouched = 0;
        int32 ComponentsTouched = 0;
        int32 SlotsAssigned = 0;
        TArray<TSharedPtr<FJsonValue>> PerActor;

        for (AActor* Actor : MatchedActors)
        {
            TArray<UMeshComponent*> MeshComps;
            Actor->GetComponents<UMeshComponent>(MeshComps);
            if (MeshComps.Num() == 0)
            {
                continue;  // actor matched the selector but carries no mesh
            }

            int32 ActorSlots = 0;
            for (UMeshComponent* Comp : MeshComps)
            {
                if (!Comp) { continue; }
                const int32 NumMats = Comp->GetNumMaterials();
                if (NumMats <= 0) { continue; }

                Comp->Modify();
                if (Slot < 0)
                {
                    for (int32 s = 0; s < NumMats; ++s)
                    {
                        Comp->SetMaterial(s, Material);
                        ++ActorSlots;
                        ++SlotsAssigned;
                    }
                }
                else if (Slot < NumMats)
                {
                    Comp->SetMaterial(Slot, Material);
                    ++ActorSlots;
                    ++SlotsAssigned;
                }
                // slot >= NumMats on this component: silently skip (a sibling
                // component may have that slot; per-actor count reflects reality).
                ++ComponentsTouched;
            }

            if (ActorSlots > 0)
            {
                ++ActorsTouched;
                TSharedRef<FJsonObject> A = MakeShared<FJsonObject>();
                A->SetStringField(TEXT("label"), Actor->GetActorLabel());
                A->SetStringField(TEXT("name"), Actor->GetFName().ToString());
                A->SetNumberField(TEXT("slots_assigned"), ActorSlots);
                PerActor.Add(MakeShared<FJsonValueObject>(A));
            }
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("material"), Material->GetPathName());
        Result->SetNumberField(TEXT("slot"), Slot);
        Result->SetNumberField(TEXT("actors_matched"), MatchedActors.Num());
        Result->SetNumberField(TEXT("actors_assigned"), ActorsTouched);
        Result->SetNumberField(TEXT("components_touched"), ComponentsTouched);
        Result->SetNumberField(TEXT("slots_assigned"), SlotsAssigned);
        Result->SetArrayField(TEXT("actors"), PerActor);
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_BatchMaterialAssign()
{
    return MakeShared<FHandler_BatchMaterialAssign>();
}
