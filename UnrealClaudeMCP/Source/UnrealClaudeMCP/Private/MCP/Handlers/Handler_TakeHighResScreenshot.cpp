// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// take_high_res_screenshot - trigger UE's HighResShot via console command.
// Output goes to Saved/Screenshots/WindowsEditor/HighresScreenshot00000.png
// (or sequential numbers).

#include "MCP/MCPHandler.h"

#include "Editor.h"
#include "UnrealClient.h"

class FHandler_TakeHighResScreenshot : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("take_high_res_screenshot"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor) { OutError = TEXT("GEditor is null"); return nullptr; }
        FViewport* VP = GEditor->GetActiveViewport();
        if (!VP) { OutError = TEXT("No active viewport"); return nullptr; }

        double Multiplier = 1.0;
        if (Params.IsValid()) { Params->TryGetNumberField(TEXT("multiplier"), Multiplier); }
        if (Multiplier < 1.0) Multiplier = 1.0;
        if (Multiplier > 8.0) Multiplier = 8.0;

        const FString Cmd = FString::Printf(TEXT("HighResShot %d"), static_cast<int32>(Multiplier));
        UWorld* World = GEditor->GetEditorWorldContext().World();
        GEditor->Exec(World, *Cmd);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("command"), Cmd);
        Out->SetNumberField(TEXT("multiplier"), Multiplier);
        Out->SetStringField(TEXT("output_dir_hint"), TEXT("<Project>/Saved/Screenshots/WindowsEditor/"));
        Out->SetBoolField(TEXT("dispatched"), true);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_TakeHighResScreenshot()
{
    return MakeShared<FHandler_TakeHighResScreenshot>();
}
