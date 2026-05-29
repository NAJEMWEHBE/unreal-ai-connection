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
// UE 5.7 surface used (cited against engine source):
//   - FindObject<UClass>(nullptr, *ClassPath) — ANY_PACKAGE was
//     deprecated in 5.1; nullptr-outer + fully-qualified class path
//     ('/Script/Engine.RendererSettings') is the canonical UE 5.7
//     replacement.
//   - GetDefault<UDeveloperSettings>(C) — CDO read; UDeveloperSettings
//     classes are always populated from .ini config at module load, so
//     no LoadConfig() bootstrap is needed here for reflection.
//   - TFieldIterator<FProperty>(C) — iterate properties; UE 5.7 still
//     supports this with EFieldIterationFlags::Default semantics.
//   - FProperty::PropertyFlags & CPF_Edit — Class.h flag indicating a
//     property is editor-editable (UPROPERTY(EditAnywhere/EditDefaultsOnly/
//     EditInstanceOnly/EditFixedSize)). We filter on this so bulk mode
//     returns only what the Project Settings panel would surface.
//   - FObjectPropertyBase::GetObjectPropertyValue — UnrealType.h:2844,
//     non-loading for hard refs; FSoftObjectProperty::GetObjectPropertyValue
//     (UnrealType.h:3382) DOES synchronously load. We dispatch on the
//     concrete property type so soft refs emit GetPathName() off the
//     un-resolved FSoftObjectPtr instead of triggering a load on the
//     game thread.
//
// Stringification is shallow (mirrors inspect_data_asset's heuristic):
//   - Scalars + strings → ExportText form
//   - Containers (TArray/TMap/TSet) → "<container:<typename>>"
//   - Hard-object pointers → asset path via GetPathName() (no load)
//   - Soft-object pointers → asset path via FSoftObjectPtr::ToString()
//     (no load — this matters for cooked-but-unloaded settings refs)
//   - Everything else → "<unsupported>"
//
// Error format: "inspect_project_setting: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, settings_class_not_found,
// not_a_developer_settings, property_not_found, property_not_editable.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/DeveloperSettings.h"
#include "UObject/Class.h"
#include "UObject/SoftObjectPtr.h"
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

        // Soft-object pointer: read the FSoftObjectPtr's stored path
        // directly. FSoftObjectProperty's GetObjectPropertyValue override
        // (UnrealType.h:3382) returns a UObject* by way of FSoftObjectPtr::Get(),
        // which Resolves+may-load when the asset is registered-but-unloaded.
        // The dedicated LoadObjectPropertyValue path (UnrealType.h:2831)
        // is the explicit-load variant we never want for shallow inspection.
        // Going through GetPropertyValuePtr_InContainer + ToString() reads
        // the SoftObjectPath the property already holds with no resolution
        // attempt at all — safe on the game thread regardless of asset size.
        if (const FSoftObjectProperty* Soft = CastField<FSoftObjectProperty>(Prop))
        {
            const FSoftObjectPtr* SoftPtr =
                Soft->GetPropertyValuePtr_InContainer(Container);
            return SoftPtr ? SoftPtr->ToString() : FString();
        }

        // Hard-object pointer (UObject*, TObjectPtr, etc.). Use the
        // non-loading GetObjectPropertyValue accessor (UnrealType.h:2844)
        // — returns the currently-resolved UObject* without forcing
        // load. Null is fine; we emit empty string for unset refs.
        if (const FObjectPropertyBase* Obj = CastField<FObjectPropertyBase>(Prop))
        {
            const void* ValuePtr = Obj->ContainerPtrToValuePtr<void>(Container);
            if (const UObject* Val = Obj->GetObjectPropertyValue(ValuePtr))
            {
                return Val->GetPathName();
            }
            return FString();
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

        // Single-property mode. Apply the same editor-editable filter as
        // bulk mode so the two modes do not diverge silently: a property
        // bulk hides should error out by name too, not return a value.
        FString SinglePropertyName;
        if (Params->TryGetStringField(TEXT("property"), SinglePropertyName) && !SinglePropertyName.IsEmpty())
        {
            FProperty* Prop = SettingsClass->FindPropertyByName(FName(*SinglePropertyName));
            if (!Prop)
            {
                OutError = FString::Printf(
                    TEXT("inspect_project_setting: property_not_found: '%s' is not a property of '%s'"),
                    *SinglePropertyName, *ClassPath);
                return nullptr;
            }
            if (!Prop->HasAnyPropertyFlags(CPF_Edit) || Prop->HasAnyPropertyFlags(CPF_EditConst))
            {
                OutError = FString::Printf(
                    TEXT("inspect_project_setting: property_not_editable: '%s' on '%s' is visible-only or otherwise not editor-editable (CPF_Edit unset or CPF_EditConst set)"),
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

        // Bulk mode: enumerate editor-editable properties on the
        // developer-settings class. TFieldIterator surfaces inherited
        // UPROPERTY()s too, which matters for settings classes that
        // inherit configuration from a base (e.g. RendererSettings).
        // Filter: CPF_Edit (ObjectMacros.h:419) AND NOT CPF_EditConst
        // (ObjectMacros.h:436). VisibleAnywhere sets both flags, which
        // is why CPF_Edit alone is insufficient — it would include
        // visible-but-not-editable properties the Project Settings
        // panel renders read-only and which the tool contract excludes.
        // Output is alphabetised by name so the result is deterministic
        // across iterator-order vagaries.
        struct FPropRow
        {
            FString Name;
            FString Type;
            FString Value;
        };
        TArray<FPropRow> Rows;
        for (TFieldIterator<FProperty> It(SettingsClass); It; ++It)
        {
            const FProperty* Prop = *It;
            if (!Prop) continue;
            if (!Prop->HasAnyPropertyFlags(CPF_Edit)) continue;
            if (Prop->HasAnyPropertyFlags(CPF_EditConst)) continue;
            Rows.Add({ Prop->GetName(), Prop->GetClass()->GetName(), StringifyProperty(Prop, CDO) });
        }
        Rows.Sort([](const FPropRow& A, const FPropRow& B) { return A.Name < B.Name; });

        TArray<TSharedPtr<FJsonValue>> PropArr;
        PropArr.Reserve(Rows.Num());
        for (const FPropRow& R : Rows)
        {
            const TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
            Entry->SetStringField(TEXT("name"), R.Name);
            Entry->SetStringField(TEXT("type"), R.Type);
            Entry->SetStringField(TEXT("value"), R.Value);
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
