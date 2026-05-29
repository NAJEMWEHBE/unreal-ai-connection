// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_input_mappings - dump the project's legacy input mappings
// (UInputSettings) as structured JSON. Action mappings: name + key +
// modifier flags (shift / ctrl / alt / cmd). Axis mappings: name + key
// + scale. Also reports whether Enhanced Input is being used in the
// project (DefaultPlayerInputClass / DefaultInputComponentClass).
//
// Closes the "what input does this project use?" gap. Mentioned in the
// 2026-05-13 deep-research review as the #1 beginner migration blocker
// (legacy InputSettings vs Enhanced Input). The LLM almost always needs
// input context before touching gameplay code.
//
// UE 5.7 surface used (cited against engine source):
//   - UInputSettings::GetDefault<UInputSettings>() — CDO, no instance load
//   - UInputSettings::GetActionMappings() const — InputSettings.h:281,
//     returns `const TArray<FInputActionKeyMapping>&`. There is NO
//     by-filter overload; do not call GetActionMappings(NAME_None, ...).
//   - UInputSettings::GetAxisMappings() const — InputSettings.h:283,
//     returns `const TArray<FInputAxisKeyMapping>&`. Same shape.
//   - FInputActionKeyMapping {ActionName, Key, bShift, bCtrl, bAlt, bCmd}
//   - FInputAxisKeyMapping {AxisName, Key, Scale}
//   - Enhanced Input detection: resolve `/Script/EnhancedInput.EnhancedPlayerInput`
//     via FindObject<UClass>(nullptr, ...) (load-safe; returns null if
//     the plugin module is not loaded into the running editor) and
//     IsChildOf to handle custom subclasses that do not contain the
//     literal "EnhancedPlayerInput" token in their name.
//
// Error format: this handler has no error paths — Handle always returns
// a successful FJsonObject result. The OutError parameter is unused
// (marked /*OutError*/). Empty mapping sets are NOT errors — they return
// as count:0 with empty arrays.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "GameFramework/InputSettings.h"
#include "GameFramework/PlayerInput.h"
#include "InputCoreTypes.h"

class FHandler_InspectInputMappings : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_input_mappings"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& /*OutError*/) override
    {
        const UInputSettings* Settings = GetDefault<UInputSettings>();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);

        if (!Settings)
        {
            Out->SetNumberField(TEXT("action_mapping_count"), 0);
            Out->SetNumberField(TEXT("axis_mapping_count"), 0);
            Out->SetArrayField(TEXT("action_mappings"), TArray<TSharedPtr<FJsonValue>>{});
            Out->SetArrayField(TEXT("axis_mappings"), TArray<TSharedPtr<FJsonValue>>{});
            Out->SetStringField(TEXT("note"), TEXT("UInputSettings CDO unavailable (unexpected in editor builds)."));
            return Out;
        }

        // Action mappings: discrete press/release events bound to a key
        // + optional modifier-key flags. UInputSettings exposes only the
        // no-arg accessor returning a const ref to its internal array
        // (InputSettings.h:281); there is no (NAME_None, OutArray) overload.
        const TArray<FInputActionKeyMapping>& ActionMappings = Settings->GetActionMappings();

        TArray<TSharedPtr<FJsonValue>> ActionArr;
        ActionArr.Reserve(ActionMappings.Num());
        for (const FInputActionKeyMapping& M : ActionMappings)
        {
            const TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
            J->SetStringField(TEXT("action"), M.ActionName.ToString());
            J->SetStringField(TEXT("key"), M.Key.ToString());
            J->SetBoolField(TEXT("shift"), M.bShift);
            J->SetBoolField(TEXT("ctrl"), M.bCtrl);
            J->SetBoolField(TEXT("alt"), M.bAlt);
            J->SetBoolField(TEXT("cmd"), M.bCmd);
            ActionArr.Add(MakeShared<FJsonValueObject>(J));
        }
        Out->SetNumberField(TEXT("action_mapping_count"), ActionArr.Num());
        Out->SetArrayField(TEXT("action_mappings"), ActionArr);

        // Axis mappings: continuous-value input (gamepad sticks, mouse,
        // keyboard "as axis") + scale multiplier. Same no-arg const-ref
        // accessor pattern as actions (InputSettings.h:283).
        const TArray<FInputAxisKeyMapping>& AxisMappings = Settings->GetAxisMappings();

        TArray<TSharedPtr<FJsonValue>> AxisArr;
        AxisArr.Reserve(AxisMappings.Num());
        for (const FInputAxisKeyMapping& M : AxisMappings)
        {
            const TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
            J->SetStringField(TEXT("axis"), M.AxisName.ToString());
            J->SetStringField(TEXT("key"), M.Key.ToString());
            J->SetNumberField(TEXT("scale"), M.Scale);
            AxisArr.Add(MakeShared<FJsonValueObject>(J));
        }
        Out->SetNumberField(TEXT("axis_mapping_count"), AxisArr.Num());
        Out->SetArrayField(TEXT("axis_mappings"), AxisArr);

        // Enhanced Input detection: resolve the EnhancedPlayerInput class
        // by path and IsChildOf-test against DefaultPlayerInputClass. This
        // is correct for arbitrary custom subclasses (e.g.
        // UMyGamePlayerInput : UEnhancedPlayerInput) whose names do not
        // contain the literal "EnhancedPlayerInput" token. FindObject is
        // load-safe — returns null if the EnhancedInput plugin module is
        // not loaded, in which case we conservatively report false.
        const UClass* PlayerInputClass = Settings->GetDefaultPlayerInputClass();
        Out->SetStringField(TEXT("default_player_input_class"),
            PlayerInputClass ? PlayerInputClass->GetPathName() : TEXT(""));
        bool bUsingEnhancedInput = false;
        if (PlayerInputClass)
        {
            if (const UClass* EnhancedBase = FindObject<UClass>(nullptr, TEXT("/Script/EnhancedInput.EnhancedPlayerInput")))
            {
                bUsingEnhancedInput = PlayerInputClass->IsChildOf(EnhancedBase);
            }
        }
        Out->SetBoolField(TEXT("uses_enhanced_input"), bUsingEnhancedInput);

        if (bUsingEnhancedInput)
        {
            Out->SetStringField(TEXT("note"), TEXT("Project uses Enhanced Input (UE 5.1+). Legacy action_mappings / axis_mappings may be empty or stale; Enhanced Input asset references (UInputAction, UInputMappingContext) are stored as project assets and not surfaced by this handler. Use find_assets with class_path /Script/EnhancedInput.InputAction to enumerate them."));
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectInputMappings()
{
    return MakeShared<FHandler_InspectInputMappings>();
}
