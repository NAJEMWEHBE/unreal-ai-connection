// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_project_setting - reflect any UDeveloperSettings subclass
// (RendererSettings, PhysicsSettings, InputSettings, GameMapsSettings,
// PhysXVehiclesSettings, etc.) and dump editable UPROPERTY values as
// JSON. Closes the gap where the plugin could read get_project_summary
// for top-level project metadata but had no access to the per-system
// settings UE exposes via Project Settings panel.
//
// Two modes:
//   - Bulk: omit 'property' → returns every editable UPROPERTY on the
//     class as a {name, type, value} record.
//   - Single: pass 'property' → returns just that one property's value.
//
// UE 5.7 surface used:
//   - FindObject<UClass>(nullptr, *ClassPath) — ANY_PACKAGE was
//     deprecated in 5.1; nullptr-outer + fully-qualified class path
//     ('/Script/Engine.RendererSettings') is the canonical UE 5.7
//     replacement. (Flagged MAJOR by the pre-flight multi-agent
//     review before code was written.)
//   - GetDefault<UDeveloperSettings>(C) — CDO read; UDeveloperSettings
//     classes are always populated from .ini config at module load, so
//     no LoadConfig() bootstrap is needed here for reflection.
//   - TFieldIterator<FProperty>(C) — iterate properties; UE 5.7 still
//     supports this with EFieldIterationFlags::Default semantics.
//
// Stringification is shallow (mirrors inspect_data_asset's heuristic):
//   - Scalars + strings → ExportText form
//   - Containers (TArray/TMap/TSet) → "<container:<typename>>"
//   - Object pointers → asset path via GetPathName()
//   - Everything else → "<unsupported>"
//
// Error format: "inspect_project_setting: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, settings_class_not_found,
// not_a_developer_settings, property_not_found.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/DeveloperSettings.h"
#include "UObject/Class.h"
#include "UObject/UnrealType.h"
#include "UObject/Field.h"

namespace
{
    /** Stringify a single FProperty value on a CDO. Best-effort; never
     *  throws. Returns ExportText for scalars/strings, container sentinel
     *  for TArray/TMap/TSet, asset path for FObjectProperty, "<unsupported>"
     *  for anything we don't recognize. Mirrors inspect_data_asset's
     *  shallow value stringifier (no recursion). */
    FString StringifyProperty(const FProperty* Prop, const void* Container)
    {
        if (!Prop || !Container) return TEXT("<unsupported>");

        if (Prop->IsA<FArrayProperty>() ||
            Prop->IsA<FMapProperty>()   ||
            Prop->IsA<FSetProperty>())
        {
            return FString::Printf(TEXT("<container:%s>"), *Prop->GetClass()->GetName());
        }

        if (const FObjectPropertyBase* Obj = CastField<FObjectPropertyBase>(Prop))
        {
            if (const UObject* Val = Obj->LoadObjectPropertyValue(Obj->ContainerPtrToValuePtr<void>(Container)))
            {
                return Val->GetPathName();
            }
            return TEXT("");
        }

        // Scalars, strings, enums, structs (FName, FVector, etc.) all
        // support ExportText_Direct via the FProperty surface. Catches
        // bool / int / float / FString / FName / FText / enum / FVector /
        // FRotator / etc.
        FString Out;
        Prop->ExportText_InContainer(0, Out, Container, Container, nullptr, PPF_None);
        return Out;
    }
}

class FHandler_InspectProjectSetting : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_project_setting"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_project_setting: missing_required_field: 'settings_class' is required (e.g. '/Script/Engine.RendererSettings')");
            return nullptr;
        }

        FString ClassPath;
        if (!Params->TryGetStringField(TEXT("settings_class"), ClassPath) || ClassPath.IsEmpty())
        {
            OutError = TEXT("inspect_project_setting: missing_required_field: 'settings_class' is required (e.g. '/Script/Engine.RendererSettings')");
            return nullptr;
        }

        // ANY_PACKAGE was deprecated in UE 5.1; nullptr-outer + fully-
        // qualified class path is the canonical 5.7 replacement.
        UClass* SettingsClass = FindObject<UClass>(nullptr, *ClassPath);
        if (!SettingsClass)
        {
            OutError = FString::Printf(
                TEXT("inspect_project_setting: settings_class_not_found: '%s' could not be resolved; ensure full path like '/Script/Engine.RendererSettings'"),
                *ClassPath);
            return nullptr;
        }

        if (!SettingsClass->IsChildOf(UDeveloperSettings::StaticClass()))
        {
            OutError = FString::Printf(
                TEXT("inspect_project_setting: not_a_developer_settings: '%s' resolved but is not a UDeveloperSettings subclass"),
                *ClassPath);
            return nullptr;
        }

        // GetDefault returns the CDO. UDeveloperSettings classes are
        // populated from .ini config at module load, so no LoadConfig()
        // bootstrap is needed for reflection.
        const UObject* CDO = SettingsClass->GetDefaultObject();
        if (!CDO)
        {
            OutError = FString::Printf(
                TEXT("inspect_project_setting: settings_class_not_found: '%s' has no CDO (engine subsystem may not have initialized)"),
                *ClassPath);
            return nullptr;
        }

        // Single-property mode.
        FString SinglePropertyName;
        if (Params->TryGetStringField(TEXT("property"), SinglePropertyName) && !SinglePropertyName.IsEmpty())
        {
            FProperty* Prop = SettingsClass->FindPropertyByName(FName(*SinglePropertyName));
            if (!Prop)
            {
                OutError = FString::Printf(
                    TEXT("inspect_project_setting: property_not_found: '%s' is not a property of %s"),
                    *SinglePropertyName, *ClassPath);
                return nullptr;
            }
            TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
            Out->SetBoolField(TEXT("ok"), true);
            Out->SetStringField(TEXT("settings_class"), ClassPath);
            Out->SetStringField(TEXT("property"), SinglePropertyName);
            Out->SetStringField(TEXT("type"), Prop->GetClass()->GetName());
            Out->SetStringField(TEXT("value"), StringifyProperty(Prop, CDO));
            return Out;
        }

        // Bulk mode: enumerate every property the developer-settings
        // class declares. EFieldIterationFlags::Default surfaces both
        // public + inherited properties — the latter matters for
        // RendererSettings (inherits from UObject + UEngineBaseTypes
        // hierarchy of UPROPERTY()s).
        TArray<TSharedPtr<FJsonValue>> PropArr;
        for (TFieldIterator<FProperty> It(SettingsClass); It; ++It)
        {
            const FProperty* Prop = *It;
            if (!Prop) continue;

            const TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
            Entry->SetStringField(TEXT("name"), Prop->GetName());
            Entry->SetStringField(TEXT("type"), Prop->GetClass()->GetName());
            Entry->SetStringField(TEXT("value"), StringifyProperty(Prop, CDO));
            PropArr.Add(MakeShared<FJsonValueObject>(Entry));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("settings_class"), ClassPath);
        Out->SetNumberField(TEXT("property_count"), PropArr.Num());
        Out->SetArrayField(TEXT("properties"), PropArr);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectProjectSetting()
{
    return MakeShared<FHandler_InspectProjectSetting>();
}
