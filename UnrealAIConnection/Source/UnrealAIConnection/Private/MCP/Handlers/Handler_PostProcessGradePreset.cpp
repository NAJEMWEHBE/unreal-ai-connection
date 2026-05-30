// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// post_process_grade_preset - capture the color grade of a PostProcessVolume to a
// JSON file on disk, or load such a file back onto a volume. Studio look-dev use:
// save a "time of day" / "warm interview" grade once, reapply it later or push the
// same look onto another volume without re-dialling the color wheels by hand.
//
// Handles the 8 global color-grade fields of FPostProcessSettings. Each field has a
// paired bOverride_<Name> bitfield; a PostProcessVolume *ignores* any field whose
// override flag is 0, so on load we set both the value and the override flag.
//
// Verified against UE 5.7 source:
//   APostProcessVolume : public AVolume (an AActor)              -- Engine/PostProcessVolume.h:22
//   APostProcessVolume::Settings (public, FPostProcessSettings)  -- Engine/PostProcessVolume.h:28
//   FPostProcessSettings::{WhiteTemp,WhiteTint}                  -- Engine/Scene.h:1445,1448
//   FPostProcessSettings::{ColorSaturation,Contrast,Gamma,Gain,Offset} (FVector4) -- Scene.h:1452-1464
//   FPostProcessSettings::AutoExposureBias                       -- Engine/Scene.h:1860
//   FPostProcessSettings::bOverride_* (uint8:1 bitfields)        -- Engine/Scene.h:701-715,913
//   FVector4 == TVector4<double>, members .X/.Y/.Z/.W            -- Math/Vector4.h:43
//   FJsonObject::TryGetObjectField/ArrayField/NumberField        -- Dom/JsonObject.h:293,274,154
//   FJsonValue::TryGetNumber(double&)                            -- Dom/JsonValue.h:44
//   FFileHelper::SaveStringToFile / LoadFileToString             -- Misc/FileHelper.h
//
// is_mutating = true: the load path edits actor state (PostProcessVolume settings),
// which belongs on the editor undo stack, so the dispatcher wraps Handle() in a
// FScopedTransaction. We also Modify()/MarkPackageDirty() the volume so the change
// persists to a Save All. On the save path the transaction is simply empty.
//
// Error format: "post_process_grade_preset: <error_code>: <detail>".
// Stable error codes: missing_params, missing_required_field, invalid_action,
// actor_not_found, ambiguous_actor, not_post_process_volume, file_write_failed,
// file_read_failed, json_parse_failed.

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/PostProcessVolume.h"
#include "Engine/Scene.h"
#include "GameFramework/Actor.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonWriter.h"

namespace
{
    static FString JoinFNamesList(const TArray<FString>& In)
    {
        FString Out;
        for (int32 i = 0; i < In.Num(); ++i) { if (i > 0) Out += TEXT(", "); Out += In[i]; }
        return Out;
    }

    // SAVE helpers: emit { "value": <num | [x,y,z,w]>, "override": <bool> } under Key.
    static void WriteFloatField(const TSharedRef<FJsonObject>& Grade, const TCHAR* Key, float Value, bool bOver)
    {
        TSharedRef<FJsonObject> F = MakeShared<FJsonObject>();
        F->SetNumberField(TEXT("value"), Value);
        F->SetBoolField(TEXT("override"), bOver);
        Grade->SetObjectField(Key, F);
    }

    static void WriteVectorField(const TSharedRef<FJsonObject>& Grade, const TCHAR* Key, const FVector4& V, bool bOver)
    {
        TArray<TSharedPtr<FJsonValue>> Arr;
        Arr.Add(MakeShared<FJsonValueNumber>(V.X));
        Arr.Add(MakeShared<FJsonValueNumber>(V.Y));
        Arr.Add(MakeShared<FJsonValueNumber>(V.Z));
        Arr.Add(MakeShared<FJsonValueNumber>(V.W));
        TSharedRef<FJsonObject> F = MakeShared<FJsonObject>();
        F->SetArrayField(TEXT("value"), Arr);
        F->SetBoolField(TEXT("override"), bOver);
        Grade->SetObjectField(Key, F);
    }
}

class FHandler_PostProcessGradePreset : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("post_process_grade_preset"); }

    // Load edits actor/level state -> belongs on the undo stack.
    virtual bool IsMutating() const override { return true; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("post_process_grade_preset: missing_params: request had no params object");
            return nullptr;
        }

        FString Action;
        if (!Params->TryGetStringField(TEXT("action"), Action) || Action.IsEmpty())
        {
            OutError = TEXT("post_process_grade_preset: missing_required_field: 'action' (string 'save'|'load') is required");
            return nullptr;
        }
        if (Action != TEXT("save") && Action != TEXT("load"))
        {
            OutError = FString::Printf(
                TEXT("post_process_grade_preset: invalid_action: '%s' is not 'save' or 'load'"), *Action);
            return nullptr;
        }

        FString TargetName;
        if (!Params->TryGetStringField(TEXT("target"), TargetName) || TargetName.IsEmpty())
        {
            OutError = TEXT("post_process_grade_preset: missing_required_field: 'target' (PostProcessVolume label) is required");
            return nullptr;
        }

        FString PresetPath;
        if (!Params->TryGetStringField(TEXT("preset_path"), PresetPath) || PresetPath.IsEmpty())
        {
            OutError = TEXT("post_process_grade_preset: missing_required_field: 'preset_path' (json file path) is required");
            return nullptr;
        }

        // Resolve the target actor and confirm it is a PostProcessVolume.
        AActor* Actor = nullptr;
        TArray<FString> Ambiguous;
        const auto Res = UCMCP::ActorIdentity::Resolve(TargetName, Actor, Ambiguous);
        if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
        {
            OutError = FString::Printf(
                TEXT("post_process_grade_preset: actor_not_found: no actor matches '%s'"), *TargetName);
            return nullptr;
        }
        if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
        {
            OutError = FString::Printf(
                TEXT("post_process_grade_preset: ambiguous_actor: '%s' matched %d actors: [%s]"),
                *TargetName, Ambiguous.Num(), *JoinFNamesList(Ambiguous));
            return nullptr;
        }

        APostProcessVolume* Volume = Cast<APostProcessVolume>(Actor);
        if (!Volume)
        {
            OutError = FString::Printf(
                TEXT("post_process_grade_preset: not_post_process_volume: '%s' is a %s, not an APostProcessVolume"),
                *TargetName, Actor ? *Actor->GetClass()->GetName() : TEXT("null"));
            return nullptr;
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("action"), Action);
        Result->SetStringField(TEXT("target"), Volume->GetActorLabel());
        Result->SetStringField(TEXT("preset_path"), PresetPath);

        if (Action == TEXT("save"))
        {
            const FPostProcessSettings& S = Volume->Settings;
            TSharedRef<FJsonObject> GradeObj = MakeShared<FJsonObject>();

            WriteFloatField(GradeObj, TEXT("white_temp"), S.WhiteTemp, S.bOverride_WhiteTemp != 0);
            WriteFloatField(GradeObj, TEXT("white_tint"), S.WhiteTint, S.bOverride_WhiteTint != 0);
            WriteVectorField(GradeObj, TEXT("color_saturation"), S.ColorSaturation, S.bOverride_ColorSaturation != 0);
            WriteVectorField(GradeObj, TEXT("color_contrast"), S.ColorContrast, S.bOverride_ColorContrast != 0);
            WriteVectorField(GradeObj, TEXT("color_gamma"), S.ColorGamma, S.bOverride_ColorGamma != 0);
            WriteVectorField(GradeObj, TEXT("color_gain"), S.ColorGain, S.bOverride_ColorGain != 0);
            WriteVectorField(GradeObj, TEXT("color_offset"), S.ColorOffset, S.bOverride_ColorOffset != 0);
            WriteFloatField(GradeObj, TEXT("auto_exposure_bias"), S.AutoExposureBias, S.bOverride_AutoExposureBias != 0);

            TSharedRef<FJsonObject> RootObj = MakeShared<FJsonObject>();
            RootObj->SetStringField(TEXT("tool"), TEXT("post_process_grade_preset"));
            RootObj->SetNumberField(TEXT("version"), 1);
            RootObj->SetObjectField(TEXT("grade"), GradeObj);

            FString OutStr;
            TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutStr);
            if (!FJsonSerializer::Serialize(RootObj, Writer))
            {
                OutError = TEXT("post_process_grade_preset: file_write_failed: JSON serialization failed");
                return nullptr;
            }
            if (!FFileHelper::SaveStringToFile(OutStr, *PresetPath))
            {
                OutError = FString::Printf(
                    TEXT("post_process_grade_preset: file_write_failed: could not write '%s' (does the directory exist?)"),
                    *PresetPath);
                return nullptr;
            }

            Result->SetNumberField(TEXT("fields"), 8);
            Result->SetStringField(TEXT("note"),
                TEXT("Grade preset written. Re-run with action='load' (and any target volume) to apply it."));
            return Result;
        }

        // --- action == "load" ---
        FString InStr;
        if (!FFileHelper::LoadFileToString(InStr, *PresetPath))
        {
            OutError = FString::Printf(
                TEXT("post_process_grade_preset: file_read_failed: could not read '%s'"), *PresetPath);
            return nullptr;
        }

        TSharedPtr<FJsonObject> ParsedRoot;
        TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(InStr);
        if (!FJsonSerializer::Deserialize(Reader, ParsedRoot) || !ParsedRoot.IsValid())
        {
            OutError = TEXT("post_process_grade_preset: json_parse_failed: preset file is not valid JSON");
            return nullptr;
        }

        const TSharedPtr<FJsonObject>* GradeObjPtr = nullptr;
        if (!ParsedRoot->TryGetObjectField(TEXT("grade"), GradeObjPtr) || !GradeObjPtr || !(*GradeObjPtr).IsValid())
        {
            Result->SetNumberField(TEXT("fields"), 0);
            Result->SetArrayField(TEXT("applied"), TArray<TSharedPtr<FJsonValue>>());
            Result->SetStringField(TEXT("note"), TEXT("Preset had no 'grade' object; nothing applied."));
            return Result;
        }
        const TSharedPtr<FJsonObject>& GradeObj = *GradeObjPtr;

        Volume->Modify();
        int32 AppliedCount = 0;
        TArray<TSharedPtr<FJsonValue>> AppliedKeys;

        // Per-field load. Two type-specific macros (NOT if-constexpr in a non-template
        // function, which would semantic-check the dead branch). bOverride_##Field
        // token-pastes onto the bitfield. Override flag defaults to true when absent.
#define PPG_LOAD_FLOAT(KeyLit, Field) \
        { \
            const TSharedPtr<FJsonObject>* FO = nullptr; \
            if (GradeObj->TryGetObjectField(TEXT(KeyLit), FO) && FO && (*FO).IsValid()) \
            { \
                double Num = 0.0; \
                if ((*FO)->TryGetNumberField(TEXT("value"), Num)) \
                { \
                    Volume->Settings.Field = static_cast<float>(Num); \
                    bool bOv = true; (*FO)->TryGetBoolField(TEXT("override"), bOv); \
                    Volume->Settings.bOverride_##Field = bOv ? 1 : 0; \
                    AppliedKeys.Add(MakeShared<FJsonValueString>(TEXT(KeyLit))); ++AppliedCount; \
                } \
            } \
        }

#define PPG_LOAD_VEC(KeyLit, Field) \
        { \
            const TSharedPtr<FJsonObject>* FO = nullptr; \
            if (GradeObj->TryGetObjectField(TEXT(KeyLit), FO) && FO && (*FO).IsValid()) \
            { \
                const TArray<TSharedPtr<FJsonValue>>* Arr = nullptr; \
                if ((*FO)->TryGetArrayField(TEXT("value"), Arr) && Arr && Arr->Num() == 4) \
                { \
                    double C0 = 0, C1 = 0, C2 = 0, C3 = 0; \
                    (*Arr)[0]->TryGetNumber(C0); (*Arr)[1]->TryGetNumber(C1); \
                    (*Arr)[2]->TryGetNumber(C2); (*Arr)[3]->TryGetNumber(C3); \
                    Volume->Settings.Field = FVector4(C0, C1, C2, C3); \
                    bool bOv = true; (*FO)->TryGetBoolField(TEXT("override"), bOv); \
                    Volume->Settings.bOverride_##Field = bOv ? 1 : 0; \
                    AppliedKeys.Add(MakeShared<FJsonValueString>(TEXT(KeyLit))); ++AppliedCount; \
                } \
            } \
        }

        PPG_LOAD_FLOAT("white_temp", WhiteTemp);
        PPG_LOAD_FLOAT("white_tint", WhiteTint);
        PPG_LOAD_VEC("color_saturation", ColorSaturation);
        PPG_LOAD_VEC("color_contrast", ColorContrast);
        PPG_LOAD_VEC("color_gamma", ColorGamma);
        PPG_LOAD_VEC("color_gain", ColorGain);
        PPG_LOAD_VEC("color_offset", ColorOffset);
        PPG_LOAD_FLOAT("auto_exposure_bias", AutoExposureBias);

#undef PPG_LOAD_FLOAT
#undef PPG_LOAD_VEC

        Volume->MarkPackageDirty();

        Result->SetNumberField(TEXT("fields"), AppliedCount);
        Result->SetArrayField(TEXT("applied"), AppliedKeys);
        Result->SetStringField(TEXT("note"),
            TEXT("Grade applied to the volume. Run save_dirty_assets to persist the level."));
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_PostProcessGradePreset()
{
    return MakeShared<FHandler_PostProcessGradePreset>();
}
