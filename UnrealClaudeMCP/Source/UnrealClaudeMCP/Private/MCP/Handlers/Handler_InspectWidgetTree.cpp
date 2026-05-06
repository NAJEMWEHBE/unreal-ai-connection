// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_widget_tree - read the widget hierarchy of a UWidgetBlueprint /
// UEditorUtilityWidgetBlueprint. Bypasses the UE 5.7 Python reflection limit
// on UWidgetBlueprint::WidgetTree (which is UPROPERTY() without EditAnywhere)
// by accessing the field directly in C++.

#include "MCP/MCPHandler.h"

#include "WidgetBlueprint.h"
#include "Blueprint/WidgetTree.h"
#include "Components/PanelWidget.h"
#include "Components/Widget.h"

class FHandler_InspectWidgetTree : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_widget_tree"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString Path;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("path"), Path))
        {
            OutError = TEXT("Missing required string param: 'path'");
            return nullptr;
        }

        UWidgetBlueprint* WBP = LoadObject<UWidgetBlueprint>(nullptr, *Path);
        if (!WBP)
        {
            OutError = FString::Printf(TEXT("Widget Blueprint not found at: %s"), *Path);
            return nullptr;
        }

        UWidgetTree* WT = WBP->WidgetTree;  // direct C++ access
        if (!WT)
        {
            OutError = TEXT("WidgetTree is null on this asset");
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("path"), Path);
        Out->SetStringField(TEXT("blueprint_class"), WBP->GetClass()->GetName());
        Out->SetStringField(TEXT("root"), WT->RootWidget ? WT->RootWidget->GetName() : TEXT(""));
        Out->SetStringField(TEXT("root_class"), WT->RootWidget ? WT->RootWidget->GetClass()->GetName() : TEXT(""));

        TArray<TSharedPtr<FJsonValue>> AllWidgets;
        WT->ForEachWidget([&AllWidgets](UWidget* W)
        {
            if (!W) return;
            const TSharedRef<FJsonObject> J = MakeShared<FJsonObject>();
            J->SetStringField(TEXT("name"), W->GetName());
            J->SetStringField(TEXT("class"), W->GetClass()->GetName());
            J->SetStringField(TEXT("parent"),
                W->GetParent() ? W->GetParent()->GetName() : TEXT(""));
            AllWidgets.Add(MakeShared<FJsonValueObject>(J));
        });
        Out->SetArrayField(TEXT("widgets"), AllWidgets);
        Out->SetNumberField(TEXT("widget_count"), AllWidgets.Num());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectWidgetTree()
{
    return MakeShared<FHandler_InspectWidgetTree>();
}
