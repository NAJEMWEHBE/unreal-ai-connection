// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// material_blend_override - set/override a named material parameter (scalar OR
// color) across many actors' mesh materials via dynamic material instances.
// Time-of-day / look-dev use case: push "GoldenHour_Intensity" or
// "SkyColor" across every prop in /Set/Exterior with a single call.
//
// Verified against UE 5.7 source:
//   UPrimitiveComponent::CreateDynamicMaterialInstance(int32, UMaterialInterface*, FName)
//                                                          -- Components/PrimitiveComponent.h:1546
//   UMaterialInstanceDynamic::SetScalarParameterValue(FName, float)
//                                                          -- Materials/MaterialInstanceDynamic.h:24
//   UMaterialInstanceDynamic::SetVectorParameterValue(FName, FLinearColor)
//                                                          -- Materials/MaterialInstanceDynamic.h:109
//   UMeshComponent::GetNumMaterials() const                -- Components/MeshComponent.h:113
//   FLinearColor(float InR, float InG, float InB, float InA)
//                                                          -- Math/Color.h:73
//   AActor::GetComponents<T>(TArray<T*>&)                  -- GameFramework/Actor.h:3981
//   AActor::GetFolderPath() const -> FName                 -- GameFramework/Actor.h:2757
//   AActor::GetActorLabel(bool) const                      -- GameFramework/Actor.h:2733
//   FRegexPattern / FRegexMatcher                          -- Internationalization/Regex.h
//   GEditor->GetEditorWorldContext().World()               -- Editor.h / UnrealEdGlobals.h
//
// is_mutating = true: CreateDynamicMaterialInstance and parameter writes are
// component-level mutations that belong on the undo stack. The dispatcher wraps
// Handle() in an FScopedTransaction. We call Comp->Modify() before any mutation
// on each component so the transaction records the before-state.
//
// Error format: "material_blend_override: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, no_target,
// invalid_field, no_actors_matched.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "Components/MeshComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Internationalization/Regex.h"

class FHandler_MaterialBlendOverride : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("material_blend_override"); }

    // Writes dynamic material instance parameters on mesh components -> undo stack.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("material_blend_override: missing_params: request had no params object");
            return nullptr;
        }

        // --- Required: parameter name ---
        FString ParameterName;
        if (!Params->TryGetStringField(TEXT("parameter"), ParameterName) || ParameterName.IsEmpty())
        {
            OutError = TEXT("material_blend_override: missing_required_field: 'parameter' (string) is required");
            return nullptr;
        }

        // --- Required: exactly one of scalar | color ---
        double ScalarValue = 0.0;
        bool bHaveScalar = Params->TryGetNumberField(TEXT("scalar"), ScalarValue);

        const TSharedPtr<FJsonObject>* ColorObjPtr = nullptr;
        bool bHaveColor = Params->TryGetObjectField(TEXT("color"), ColorObjPtr)
                          && ColorObjPtr && (*ColorObjPtr).IsValid();

        if (!bHaveScalar && !bHaveColor)
        {
            OutError = TEXT("material_blend_override: missing_required_field: exactly one of 'scalar' (number) or 'color' ({r,g,b,a}) is required");
            return nullptr;
        }
        if (bHaveScalar && bHaveColor)
        {
            OutError = TEXT("material_blend_override: invalid_field: supply exactly one of 'scalar' or 'color', not both");
            return nullptr;
        }

        FLinearColor ColorValue(0.f, 0.f, 0.f, 1.f);
        if (bHaveColor)
        {
            double R = 0.0, G = 0.0, B = 0.0, A = 1.0;
            (*ColorObjPtr)->TryGetNumberField(TEXT("r"), R);
            (*ColorObjPtr)->TryGetNumberField(TEXT("g"), G);
            (*ColorObjPtr)->TryGetNumberField(TEXT("b"), B);
            (*ColorObjPtr)->TryGetNumberField(TEXT("a"), A);
            ColorValue = FLinearColor(
                static_cast<float>(R),
                static_cast<float>(G),
                static_cast<float>(B),
                static_cast<float>(A));
        }

        // --- Optional: slot (-1 = all slots) ---
        int32 Slot = -1;
        {
            double SlotNum = -1.0;
            if (Params->TryGetNumberField(TEXT("slot"), SlotNum))
            {
                Slot = static_cast<int32>(SlotNum);
            }
        }
        if (Slot < -1)
        {
            OutError = TEXT("material_blend_override: invalid_field: 'slot' must be -1 for all slots or a non-negative slot index");
            return nullptr;
        }

        // --- Required: targets object (exactly one of by_label / by_folder / by_name_regex) ---
        const TSharedPtr<FJsonObject>* TargetsObjPtr = nullptr;
        if (!Params->TryGetObjectField(TEXT("targets"), TargetsObjPtr) || !TargetsObjPtr || !(*TargetsObjPtr).IsValid())
        {
            OutError = TEXT("material_blend_override: missing_required_field: 'targets' object is required");
            return nullptr;
        }
        const TSharedPtr<FJsonObject>& TargetsObj = *TargetsObjPtr;

        // Parse by_label (string OR array of string)
        TSet<FString> ByLabel;
        {
            FString SingleLabel;
            const TArray<TSharedPtr<FJsonValue>>* LabelArr = nullptr;
            if (TargetsObj->TryGetStringField(TEXT("by_label"), SingleLabel) && !SingleLabel.IsEmpty())
            {
                ByLabel.Add(SingleLabel);
            }
            else if (TargetsObj->TryGetArrayField(TEXT("by_label"), LabelArr) && LabelArr)
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
        }

        FString ByFolder;
        TargetsObj->TryGetStringField(TEXT("by_folder"), ByFolder);

        FString ByNameRegex;
        TargetsObj->TryGetStringField(TEXT("by_name_regex"), ByNameRegex);

        const bool bHaveLabel  = ByLabel.Num() > 0;
        const bool bHaveFolder = !ByFolder.IsEmpty();
        const bool bHaveRegex  = !ByNameRegex.IsEmpty();
        const int32 SelectorCount = (bHaveLabel ? 1 : 0) + (bHaveFolder ? 1 : 0) + (bHaveRegex ? 1 : 0);
        if (SelectorCount == 0)
        {
            OutError = TEXT("material_blend_override: no_target: 'targets' must set exactly one of by_label, by_folder, or by_name_regex");
            return nullptr;
        }
        if (SelectorCount > 1)
        {
            OutError = TEXT("material_blend_override: invalid_field: 'targets' must set exactly one of by_label, by_folder, or by_name_regex (not multiple)");
            return nullptr;
        }

        // Compile regex once if needed (FRegexPattern on empty string is safe but
        // we only use it when bHaveRegex; keep it unconditional to satisfy the
        // compiler  -  the pattern object is trivially constructed).
        const FRegexPattern NamePattern(ByNameRegex);

        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("material_blend_override: no_target: no editor world available");
            return nullptr;
        }

        // --- Resolve matching actors ---
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
            OutError = TEXT("material_blend_override: no_actors_matched: the targets selector matched zero actors in the current level");
            return nullptr;
        }

        // --- Apply dynamic material instance parameter to each matched actor ---
        const FName ParamFName(*ParameterName);
        int32 ComponentsTouched = 0;
        int32 MIDsCreated      = 0;
        int32 SlotsSet         = 0;

        for (AActor* Actor : MatchedActors)
        {
            TArray<UMeshComponent*> MeshComps;
            Actor->GetComponents<UMeshComponent>(MeshComps);

            for (UMeshComponent* Comp : MeshComps)
            {
                if (!Comp) { continue; }
                const int32 NumMats = Comp->GetNumMaterials();
                if (NumMats <= 0) { continue; }

                // Record before-state for the undo transaction.
                Comp->Modify();
                ++ComponentsTouched;

                if (Slot < 0)
                {
                    // All slots
                    for (int32 s = 0; s < NumMats; ++s)
                    {
                        UMaterialInstanceDynamic* MID =
                            Comp->CreateDynamicMaterialInstance(s, nullptr, NAME_None);
                        if (!MID) { continue; }
                        ++MIDsCreated;

                        if (bHaveScalar)
                        {
                            MID->SetScalarParameterValue(ParamFName, static_cast<float>(ScalarValue));
                        }
                        else
                        {
                            MID->SetVectorParameterValue(ParamFName, ColorValue);
                        }
                        ++SlotsSet;
                    }
                }
                else if (Slot < NumMats)
                {
                    UMaterialInstanceDynamic* MID =
                        Comp->CreateDynamicMaterialInstance(Slot, nullptr, NAME_None);
                    if (MID)
                    {
                        ++MIDsCreated;
                        if (bHaveScalar)
                        {
                            MID->SetScalarParameterValue(ParamFName, static_cast<float>(ScalarValue));
                        }
                        else
                        {
                            MID->SetVectorParameterValue(ParamFName, ColorValue);
                        }
                        ++SlotsSet;
                    }
                }
                // Slot >= NumMats on this component: silently skip.
            }
        }

        // Build the value echo for the result.
        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("parameter"), ParameterName);

        if (bHaveScalar)
        {
            Result->SetNumberField(TEXT("value"), ScalarValue);
        }
        else
        {
            TSharedRef<FJsonObject> ColorOut = MakeShared<FJsonObject>();
            ColorOut->SetNumberField(TEXT("r"), ColorValue.R);
            ColorOut->SetNumberField(TEXT("g"), ColorValue.G);
            ColorOut->SetNumberField(TEXT("b"), ColorValue.B);
            ColorOut->SetNumberField(TEXT("a"), ColorValue.A);
            Result->SetObjectField(TEXT("value"), ColorOut);
        }

        Result->SetNumberField(TEXT("slot"), Slot);
        Result->SetNumberField(TEXT("actors_matched"), MatchedActors.Num());
        Result->SetNumberField(TEXT("components_touched"), ComponentsTouched);
        Result->SetNumberField(TEXT("mids_created"), MIDsCreated);
        Result->SetNumberField(TEXT("slots_set"), SlotsSet);
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_MaterialBlendOverride()
{
    return MakeShared<FHandler_MaterialBlendOverride>();
}
