// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// edit_widget_tree - mutate a WidgetBlueprint / EditorUtilityWidgetBlueprint
// widget hierarchy. Solves the UE 5.7 EUW WidgetTree blocker that Python
// reflection can't reach.
//
// Persistence: every call marks the widget tree dirty + saves the asset.
// Pass "compile": true on the LAST edit in a batch to recompile the BP;
// compiling per-call has caused editor crashes when many edits arrive in
// quick succession.

#include "MCP/MCPHandler.h"

#include "WidgetBlueprint.h"
#include "Blueprint/WidgetTree.h"
#include "Components/PanelWidget.h"
#include "Components/VerticalBox.h"
#include "Components/HorizontalBox.h"
#include "Components/CanvasPanel.h"
#include "Components/TextBlock.h"
#include "Components/Button.h"
#include "Components/Border.h"
#include "Components/Image.h"
#include "Components/Spacer.h"
#include "Components/EditableTextBox.h"
#include "EditorAssetLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "UObject/Class.h"

class FHandler_EditWidgetTree : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("edit_widget_tree"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString Path, Op;
        if (!Params.IsValid()
            || !Params->TryGetStringField(TEXT("path"), Path)
            || !Params->TryGetStringField(TEXT("op"), Op))
        {
            OutError = TEXT("Missing required params: 'path' and 'op'");
            return nullptr;
        }

        UWidgetBlueprint* WBP = LoadObject<UWidgetBlueprint>(nullptr, *Path);
        if (!WBP)
        {
            OutError = FString::Printf(TEXT("Widget Blueprint not found at: %s"), *Path);
            return nullptr;
        }
        UWidgetTree* WT = WBP->WidgetTree;
        if (!WT)
        {
            OutError = TEXT("WidgetTree is null");
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("op"), Op);

        if (Op == TEXT("set_root"))
        {
            FString ClassName, WidgetName;
            Params->TryGetStringField(TEXT("class"), ClassName);
            Params->TryGetStringField(TEXT("name"), WidgetName);

            UClass* Cls = ResolveWidgetClass(ClassName);
            if (!Cls) { OutError = FString::Printf(TEXT("Unknown widget class: %s"), *ClassName); return nullptr; }

            const FName SafeName = WidgetName.IsEmpty() ? FName(*ClassName) : FName(*WidgetName);
            UWidget* W = WT->ConstructWidget<UWidget>(Cls, SafeName);
            if (!W) { OutError = TEXT("ConstructWidget returned null"); return nullptr; }
            WT->RootWidget = W;
            Out->SetStringField(TEXT("created"), W->GetName());
        }
        else if (Op == TEXT("add_child"))
        {
            FString ParentName, ClassName, WidgetName;
            Params->TryGetStringField(TEXT("parent"), ParentName);
            Params->TryGetStringField(TEXT("class"), ClassName);
            Params->TryGetStringField(TEXT("name"), WidgetName);

            UWidget* ParentRaw = WT->FindWidget(FName(*ParentName));
            UPanelWidget* Parent = Cast<UPanelWidget>(ParentRaw);
            if (!Parent) { OutError = FString::Printf(TEXT("Parent panel widget not found or not a panel: %s"), *ParentName); return nullptr; }

            UClass* Cls = ResolveWidgetClass(ClassName);
            if (!Cls) { OutError = FString::Printf(TEXT("Unknown widget class: %s"), *ClassName); return nullptr; }

            const FName SafeName = WidgetName.IsEmpty() ? FName(*ClassName) : FName(*WidgetName);
            UWidget* W = WT->ConstructWidget<UWidget>(Cls, SafeName);
            if (!W) { OutError = TEXT("ConstructWidget returned null"); return nullptr; }
            Parent->AddChild(W);
            Out->SetStringField(TEXT("created"), W->GetName());
        }
        else if (Op == TEXT("set_property"))
        {
            FString WidgetName, PropName, Value;
            Params->TryGetStringField(TEXT("widget"), WidgetName);
            Params->TryGetStringField(TEXT("property"), PropName);
            Params->TryGetStringField(TEXT("value"), Value);

            UWidget* W = WT->FindWidget(FName(*WidgetName));
            if (!W) { OutError = FString::Printf(TEXT("Widget not found: %s"), *WidgetName); return nullptr; }

            if (UTextBlock* T = Cast<UTextBlock>(W); T && PropName == TEXT("text"))
            {
                T->SetText(FText::FromString(Value));
                Out->SetStringField(TEXT("set"), FString::Printf(TEXT("%s.text"), *WidgetName));
            }
            else if (UEditableTextBox* E = Cast<UEditableTextBox>(W); E && PropName == TEXT("text"))
            {
                E->SetText(FText::FromString(Value));
                Out->SetStringField(TEXT("set"), FString::Printf(TEXT("%s.text"), *WidgetName));
            }
            else
            {
                FProperty* Prop = W->GetClass()->FindPropertyByName(FName(*PropName));
                if (!Prop) { OutError = FString::Printf(TEXT("Property not found: %s on %s"), *PropName, *W->GetClass()->GetName()); return nullptr; }
                if (FStrProperty* StrProp = CastField<FStrProperty>(Prop))
                {
                    StrProp->SetPropertyValue_InContainer(W, Value);
                }
                else if (FFloatProperty* FloatProp = CastField<FFloatProperty>(Prop))
                {
                    FloatProp->SetPropertyValue_InContainer(W, FCString::Atof(*Value));
                }
                else if (FIntProperty* IntProp = CastField<FIntProperty>(Prop))
                {
                    IntProp->SetPropertyValue_InContainer(W, FCString::Atoi(*Value));
                }
                else if (FBoolProperty* BoolProp = CastField<FBoolProperty>(Prop))
                {
                    BoolProp->SetPropertyValue_InContainer(W, Value.ToBool());
                }
                else
                {
                    OutError = FString::Printf(TEXT("Property type not yet supported: %s (property=%s)"), *Prop->GetClass()->GetName(), *PropName);
                    return nullptr;
                }
                Out->SetStringField(TEXT("set"), FString::Printf(TEXT("%s.%s"), *WidgetName, *PropName));
            }
        }
        else
        {
            OutError = FString::Printf(TEXT("Unknown op: %s (allowed: set_root, add_child, set_property)"), *Op);
            return nullptr;
        }

        // Persistence: mark dirty + save. Compile is opt-in via 'compile: true'.
        WT->Modify();
        WT->MarkPackageDirty();
        FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(WBP);

        bool bCompileNow = false;
        Params->TryGetBoolField(TEXT("compile"), bCompileNow);
        if (bCompileNow)
        {
            FKismetEditorUtilities::CompileBlueprint(WBP);
        }

        UEditorAssetLibrary::SaveLoadedAsset(WBP);
        return Out;
    }

private:
    static UClass* ResolveWidgetClass(const FString& Name)
    {
        if (Name == TEXT("VerticalBox"))     return UVerticalBox::StaticClass();
        if (Name == TEXT("HorizontalBox"))   return UHorizontalBox::StaticClass();
        if (Name == TEXT("CanvasPanel"))     return UCanvasPanel::StaticClass();
        if (Name == TEXT("TextBlock"))       return UTextBlock::StaticClass();
        if (Name == TEXT("Button"))          return UButton::StaticClass();
        if (Name == TEXT("Border"))          return UBorder::StaticClass();
        if (Name == TEXT("Image"))           return UImage::StaticClass();
        if (Name == TEXT("Spacer"))          return USpacer::StaticClass();
        if (Name == TEXT("EditableTextBox")) return UEditableTextBox::StaticClass();
        return FindObject<UClass>(nullptr, *Name);
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_EditWidgetTree()
{
    return MakeShared<FHandler_EditWidgetTree>();
}
