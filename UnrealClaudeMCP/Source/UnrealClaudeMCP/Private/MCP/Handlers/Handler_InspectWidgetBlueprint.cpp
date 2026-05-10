// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_widget_blueprint - read the UWidgetBlueprint-specific surface that
// neither inspect_blueprint nor inspect_widget_tree covers: animations,
// delegate bindings, palette category, named slots from parent, and property-
// binding count.  Cross-link to those handlers by passing the same path.
//
// UE 5.7 surface used (header:line citations for reviewer traceability):
//   WidgetBlueprint.h:220  -- UWidgetBlueprint class declaration
//   WidgetBlueprint.h:228  -- TArray<FDelegateEditorBinding> Bindings;           (WITH_EDITORONLY_DATA)
//   WidgetBlueprint.h:231  -- TArray<TObjectPtr<UWidgetAnimation>> Animations;   (WITH_EDITORONLY_DATA)
//   WidgetBlueprint.h:249  -- FString PaletteCategory;                           (WITH_EDITORONLY_DATA, AssetRegistrySearchable)
//   WidgetBlueprint.h:257  -- bool bCanCallInitializedWithoutPlayerContext;       (WITH_EDITORONLY_DATA)
//   WidgetBlueprint.h:325  -- bool ArePropertyBindingsAllowed() const;            (public)
//   WidgetBlueprint.h:328  -- TArray<FName> GetInheritedAvailableNamedSlots() const; (public)
//   WidgetBlueprint.h:331  -- TSet<FName> GetInheritedNamedSlotsWithContentInSameTree() const; (public)
//   WidgetBlueprint.h:370  -- int32 PropertyBindings;                             (public, AssetRegistrySearchable)
//
//   FDelegateEditorBinding fields (WidgetBlueprint.h approx lines 125-165):
//     FString ObjectName   -- the member widget the binding is on
//     FName   PropertyName -- property being bound
//     FName   FunctionName -- generated binding function name
//
//   WidgetAnimation.h:40   -- const FString& GetDisplayLabel() const              (WITH_EDITOR)
//   WidgetAnimation.h:59   -- float GetStartTime() const                          (UMG_API)
//   WidgetAnimation.h:68   -- float GetEndTime() const                            (UMG_API)
//   WidgetAnimation.h:147  -- TArray<FWidgetAnimationBinding> AnimationBindings;  (public UPROPERTY)
//
//   Blueprint.h:412  -- ParentClass   (inherited from UBlueprint)
//   Blueprint.h:504  -- Status (EBlueprintStatus) (inherited from UBlueprint)
//
// Error format: "inspect_widget_blueprint: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_widget_blueprint

#include "WidgetBlueprint.h"
#include "Animation/WidgetAnimation.h"
#include "Animation/WidgetAnimationBinding.h"
#include "Engine/Blueprint.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
    static FString BlueprintStatusToString(EBlueprintStatus Status)
    {
        switch (Status)
        {
        case BS_UpToDate:
            return TEXT("UpToDate");
        case BS_UpToDateWithWarnings:
            return TEXT("UpToDateWithWarnings");
        case BS_Dirty:
            return TEXT("Dirty");
        case BS_Error:
            // Compile-failed blueprint -- must be explicit (PR #52->#53 lesson:
            // prior version had no BS_Error case and silently fell through to
            // "Unknown", masking real compile errors in responses).
            return TEXT("Error");
        case BS_Unknown:
        default:
            return TEXT("Unknown");
        }
    }
}

class FHandler_InspectWidgetBlueprint : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_widget_blueprint"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_widget_blueprint: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_widget_blueprint: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_widget_blueprint: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        UWidgetBlueprint* WBP = Cast<UWidgetBlueprint>(Loaded);
        if (!WBP)
        {
            OutError = FString::Printf(
                TEXT("inspect_widget_blueprint: not_a_widget_blueprint: '%s' is a %s, not a UWidgetBlueprint"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- parent class + compile status (inherited from UBlueprint) ---

        const FString ParentClassName = WBP->ParentClass ? WBP->ParentClass->GetName() : TEXT("");
        const FString StatusString    = BlueprintStatusToString(static_cast<EBlueprintStatus>(WBP->Status));

        // --- public (non-editor-only) surface ---

        const bool  bPropertyBindingsAllowed = WBP->ArePropertyBindingsAllowed();
        const int32 PropertyBindingsCount    = WBP->PropertyBindings;

        // Named slots -- public methods, no editor-only guard required on the call site.
        TArray<FName> InheritedSlots = WBP->GetInheritedAvailableNamedSlots();
        TArray<TSharedPtr<FJsonValue>> InheritedSlotsArray;
        InheritedSlotsArray.Reserve(InheritedSlots.Num());
        for (const FName& SlotName : InheritedSlots)
        {
            InheritedSlotsArray.Add(MakeShared<FJsonValueString>(SlotName.ToString()));
        }

        TSet<FName> SlotsWithContentSet = WBP->GetInheritedNamedSlotsWithContentInSameTree();
        // Sort for stable output -- TSet iteration order is unspecified.
        TArray<FString> SlotsWithContentSorted;
        SlotsWithContentSorted.Reserve(SlotsWithContentSet.Num());
        for (const FName& SlotName : SlotsWithContentSet)
        {
            SlotsWithContentSorted.Add(SlotName.ToString());
        }
        SlotsWithContentSorted.Sort();
        TArray<TSharedPtr<FJsonValue>> SlotsWithContentArray;
        SlotsWithContentArray.Reserve(SlotsWithContentSorted.Num());
        for (const FString& S : SlotsWithContentSorted)
        {
            SlotsWithContentArray.Add(MakeShared<FJsonValueString>(S));
        }

        // --- editor-only surface (Bindings, Animations, PaletteCategory, bCanCallInitializedWithoutPlayerContext) ---

#if WITH_EDITORONLY_DATA

        // Bindings: FDelegateEditorBinding.ObjectName (FString), .PropertyName (FName), .FunctionName (FName)
        int32 ValidBindingCount = 0;
        TArray<TSharedPtr<FJsonValue>> BindingsArray;
        for (const FDelegateEditorBinding& Binding : WBP->Bindings)
        {
            ++ValidBindingCount;
            TSharedPtr<FJsonObject> BindObj = MakeShared<FJsonObject>();
            BindObj->SetStringField(TEXT("object_name"),   Binding.ObjectName);
            BindObj->SetStringField(TEXT("property_name"), Binding.PropertyName.ToString());
            BindObj->SetStringField(TEXT("function_name"), Binding.FunctionName.ToString());
            BindingsArray.Add(MakeShared<FJsonValueObject>(BindObj));
        }

        // Null-skip TObjectPtr entries (PR #55->#57 lesson: TObjectPtr can be
        // null after deletes/reimports; count and emit only valid entries).
        int32 ValidAnimCount = 0;
        TArray<TSharedPtr<FJsonValue>> AnimationsArray;
        for (const TObjectPtr<UWidgetAnimation>& AnimPtr : WBP->Animations)
        {
            UWidgetAnimation* Anim = AnimPtr.Get();
            if (!Anim) { continue; }
            ++ValidAnimCount;

            TSharedPtr<FJsonObject> AnimObj = MakeShared<FJsonObject>();
            AnimObj->SetStringField(TEXT("name"), Anim->GetName());

#if WITH_EDITOR
            // GetDisplayLabel() is guarded by WITH_EDITOR in WidgetAnimation.h:40
            AnimObj->SetStringField(TEXT("display_label"), Anim->GetDisplayLabel());
#else
            AnimObj->SetStringField(TEXT("display_label"), Anim->GetName());
#endif
            const float StartTime = Anim->GetStartTime();
            const float EndTime   = Anim->GetEndTime();
            AnimObj->SetNumberField(TEXT("start_time"),     static_cast<double>(StartTime));
            AnimObj->SetNumberField(TEXT("end_time"),       static_cast<double>(EndTime));
            AnimObj->SetNumberField(TEXT("length_seconds"), static_cast<double>(EndTime - StartTime));
            AnimObj->SetNumberField(TEXT("binding_count"),  static_cast<double>(Anim->AnimationBindings.Num()));
            AnimationsArray.Add(MakeShared<FJsonValueObject>(AnimObj));
        }

        const bool    bCanInitWithoutPlayer = WBP->bCanCallInitializedWithoutPlayerContext;
        const FString PaletteCategory       = WBP->PaletteCategory;

#endif // WITH_EDITORONLY_DATA

        // --- assemble response ---

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"),               true);
        Out->SetStringField(TEXT("name"),           WBP->GetName());
        Out->SetStringField(TEXT("path"),           ObjectPath);
        Out->SetStringField(TEXT("parent_class"),   ParentClassName);
        Out->SetStringField(TEXT("blueprint_status"), StatusString);

#if WITH_EDITORONLY_DATA
        if (!PaletteCategory.IsEmpty())
        {
            Out->SetStringField(TEXT("palette_category"), PaletteCategory);
        }
        Out->SetBoolField(TEXT("can_init_without_player_context"), bCanInitWithoutPlayer);
#endif

        Out->SetBoolField(TEXT("property_bindings_allowed"), bPropertyBindingsAllowed);
        Out->SetNumberField(TEXT("property_bindings_count"), static_cast<double>(PropertyBindingsCount));

#if WITH_EDITORONLY_DATA
        Out->SetNumberField(TEXT("binding_count"), static_cast<double>(ValidBindingCount));
        Out->SetArrayField(TEXT("bindings"),       BindingsArray);
        Out->SetNumberField(TEXT("animation_count"), static_cast<double>(ValidAnimCount));
        Out->SetArrayField(TEXT("animations"),       AnimationsArray);
#endif

        Out->SetNumberField(TEXT("inherited_named_slot_count"), static_cast<double>(InheritedSlots.Num()));
        Out->SetArrayField(TEXT("inherited_named_slots"),        InheritedSlotsArray);
        Out->SetArrayField(TEXT("inherited_slots_with_content"), SlotsWithContentArray);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectWidgetBlueprint()
{
    return MakeShared<FHandler_InspectWidgetBlueprint>();
}
