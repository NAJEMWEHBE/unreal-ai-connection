// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// take_high_res_screenshot - trigger UE's HighResShot via console command.
// Output goes to Saved/Screenshots/<PlatformEditor>/HighresScreenshot00000.png
// (e.g. WindowsEditor on Windows, MacEditor on Mac, LinuxEditor on Linux).
//
// v0.10 rework (dive PR): HighResShot only resolves on the next viewport
// draw. With the editor backgrounded, Slate throttling suppresses redraws and
// the shot never flushed (production sessions needed a SetForegroundWindow
// hack). The handler now forces a synchronous redraw after dispatching the
// command, then does ONE non-blocking scan of the output dir for the new
// file. The PNG write itself can still be async (ImageWriteQueue), so callers
// should poll the directory when "found" is false.
//
// Error format: "take_high_res_screenshot: <error_code>: <detail>".
// Stable error codes: no_editor, no_viewport.

#include "MCP/MCPHandler.h"

#if WITH_EDITOR

#include "Editor.h"
#include "UnrealClient.h"
#include "HAL/PlatformProperties.h"
#include "LevelEditor.h"
#include "SLevelViewport.h"
#include "LevelEditorViewport.h"
#include "EditorViewportClient.h"
#include "Slate/SceneViewport.h"
#include "RenderingThread.h"
#include "Modules/ModuleManager.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"

class FHandler_TakeHighResScreenshot : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("take_high_res_screenshot"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        check(IsInGameThread());

        if (!GEditor)
        {
            OutError = TEXT("take_high_res_screenshot: no_editor: GEditor is null");
            return nullptr;
        }

        FViewport* VP = GEditor->GetActiveViewport();
        if (!VP)
        {
            OutError = TEXT("take_high_res_screenshot: no_viewport: no active viewport");
            return nullptr;
        }

        double Multiplier = 1.0;
        if (Params.IsValid()) { Params->TryGetNumberField(TEXT("multiplier"), Multiplier); }
        if (Multiplier < 1.0) Multiplier = 1.0;
        if (Multiplier > 8.0) Multiplier = 8.0;

        const FString Cmd = FString::Printf(TEXT("HighResShot %d"), static_cast<int32>(Multiplier));
        UWorld* World = GEditor->GetEditorWorldContext().World();
        const FDateTime StartTime = FDateTime::UtcNow();
        GEditor->Exec(World, *Cmd);

        // Force a synchronous redraw so the queued HighResShot is processed
        // even when the editor is backgrounded and Slate-throttled. Same
        // chain as Handler_TakeScreenshot.cpp.
        bool bFlushed = false;
        if (FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>(TEXT("LevelEditor")))
        {
            TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
            if (LV.IsValid())
            {
                FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
                VC.Invalidate();
                GEditor->RedrawLevelEditingViewports(/*bInvalidateHitProxies=*/true);
                TSharedPtr<FSceneViewport> SceneVP = LV->GetSharedActiveViewport();
                FViewport* DrawVP = SceneVP.IsValid() ? static_cast<FViewport*>(SceneVP.Get()) : VP;
                DrawVP->Draw(/*bShouldPresent=*/false);
                FlushRenderingCommands();
                bFlushed = true;
            }
        }
        if (!bFlushed)
        {
            VP->Draw(/*bShouldPresent=*/false);
            FlushRenderingCommands();
            bFlushed = true;
        }

        // Cross-platform output path. UE writes to Saved/Screenshots/<Platform>Editor/.
        const FString PlatformEditor = FString::Printf(
            TEXT("%sEditor"), ANSI_TO_TCHAR(FPlatformProperties::PlatformName()));
        const FString OutputDir = FPaths::Combine(
            FPaths::ConvertRelativePathToFull(FPaths::ProjectDir()),
            TEXT("Saved/Screenshots"), PlatformEditor);
        const FString OutputDirHint = FString::Printf(
            TEXT("<Project>/Saved/Screenshots/%s/"), *PlatformEditor);

        // ONE non-blocking scan for a PNG newer than dispatch time. The PNG
        // write can be async (ImageWriteQueue) so absence is not failure.
        FString FoundPath;
        bool bFound = false;
        if (IFileManager::Get().DirectoryExists(*OutputDir))
        {
            TArray<FString> Files;
            IFileManager::Get().FindFiles(Files, *OutputDir, TEXT(".png"));
            FDateTime NewestTime = StartTime;
            for (const FString& File : Files)
            {
                const FString Candidate = FPaths::Combine(OutputDir, File);
                const FDateTime FileTime = IFileManager::Get().GetTimeStamp(*Candidate);
                if (FileTime >= NewestTime)
                {
                    NewestTime = FileTime;
                    FoundPath = Candidate;
                    bFound = true;
                }
            }
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetStringField(TEXT("command"), Cmd);
        Out->SetNumberField(TEXT("multiplier"), Multiplier);
        Out->SetStringField(TEXT("output_dir_hint"), OutputDirHint);
        Out->SetBoolField(TEXT("dispatched"), true);
        Out->SetBoolField(TEXT("flushed"), bFlushed);
        Out->SetBoolField(TEXT("found"), bFound);
        if (bFound)
        {
            Out->SetStringField(TEXT("path"), FoundPath);
        }
        Out->SetStringField(TEXT("note"),
            TEXT("Viewport redraw forced (throttle-proof). PNG write may still be asynchronous; if 'found' is false, poll output_dir_hint for the new file."));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_TakeHighResScreenshot()
{
    return MakeShared<FHandler_TakeHighResScreenshot>();
}

#endif // WITH_EDITOR
