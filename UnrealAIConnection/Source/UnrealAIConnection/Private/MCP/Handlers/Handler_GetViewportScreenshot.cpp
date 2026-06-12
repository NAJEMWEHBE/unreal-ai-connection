// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_viewport_screenshot - capture the active editor viewport as a PNG and
// write it to DISK, returning the path (+ optional small inline thumbnail).
//
// v0.10 rework (dive PR): this tool used to return the full PNG base64-inline,
// which produced 0.7-2.7 MB tool results that blow MCP client token limits
// (measured: 5x oversized results in one production session). It now:
//   1. forces a fresh frame before ReadPixels (Invalidate -> RedrawLevel
//      EditingViewports -> Draw(false) -> FlushRenderingCommands - the
//      host-verified chain from Handler_TakeScreenshot.cpp), so captures are
//      correct even when the editor is backgrounded and Slate-throttled;
//   2. writes the PNG under the project dir (out_path confined exactly like
//      take_screenshot; default Saved/AIConnection/Screenshots/<utc>.png);
//   3. optionally returns a small base64 thumbnail (include_thumb +
//      thumb_max_dim, clamped 64..1024, default 320) for quick look checks.
//
// Error format: "get_viewport_screenshot: <error_code>: <detail>".
// Stable error codes: no_editor, no_viewport, zero_size, read_failed,
// encode_failed, write_failed, dir_create_failed, path_escapes_project.

#include "MCP/MCPHandler.h"

#if WITH_EDITOR

#include "Editor.h"
#include "LevelEditor.h"
#include "SLevelViewport.h"
#include "LevelEditorViewport.h"
#include "EditorViewportClient.h"
#include "UnrealClient.h"
#include "Slate/SceneViewport.h"
#include "RenderingThread.h"
#include "UCMCPCompat.h"            // EncodePngFColor (PNG encode, cross-engine)
#include "Modules/ModuleManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/Base64.h"
#include "HAL/FileManager.h"
#include "ImageUtils.h"

class FHandler_GetViewportScreenshot : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_viewport_screenshot"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        check(IsInGameThread());

        if (!GEditor)
        {
            OutError = TEXT("get_viewport_screenshot: no_editor: GEditor is null (editor build only)");
            return nullptr;
        }

        FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>(TEXT("LevelEditor"));
        if (!LEModule)
        {
            OutError = TEXT("get_viewport_screenshot: no_editor: LevelEditor module unavailable");
            return nullptr;
        }

        TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
        if (!LV.IsValid())
        {
            OutError = TEXT("get_viewport_screenshot: no_viewport: no active level viewport found");
            return nullptr;
        }

        TSharedPtr<FSceneViewport> SharedVP = LV->GetSharedActiveViewport();
        FViewport* Viewport = SharedVP.IsValid() ? SharedVP.Get() : GEditor->GetActiveViewport();
        if (!Viewport)
        {
            OutError = TEXT("get_viewport_screenshot: no_viewport: could not obtain FViewport*");
            return nullptr;
        }

        const FIntPoint Size = Viewport->GetSizeXY();
        if (Size.X <= 0 || Size.Y <= 0)
        {
            OutError = TEXT("get_viewport_screenshot: zero_size: viewport size is zero");
            return nullptr;
        }

        // Force a fresh frame so the capture is correct even when the editor
        // window is backgrounded and Slate throttling has frozen redraws.
        // Same chain as Handler_TakeScreenshot.cpp / Handler_RenderCameraToPng.cpp.
        FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
        VC.Invalidate();
        GEditor->RedrawLevelEditingViewports(/*bInvalidateHitProxies=*/true);
        Viewport->Draw(/*bShouldPresent=*/false);
        FlushRenderingCommands();

        TArray<FColor> Pixels;
        FReadSurfaceDataFlags Flags;
        Flags.SetLinearToGamma(false);
        if (!Viewport->ReadPixels(Pixels, Flags))
        {
            OutError = TEXT("get_viewport_screenshot: read_failed: ReadPixels failed");
            return nullptr;
        }

        // Force alpha to 255 (viewport often returns 0 alpha)
        for (FColor& C : Pixels) { C.A = 255; }

        // --- Resolve + confine the output path (mirrors take_screenshot) ----
        FString OutPath;
        if (Params.IsValid())
        {
            Params->TryGetStringField(TEXT("out_path"), OutPath);
        }

        const FString ProjectDir = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
        FString FullPath;
        if (OutPath.IsEmpty())
        {
            // %s = milliseconds in FDateTime::ToString (DateTime.cpp token table).
            const FString Timestamp = FDateTime::UtcNow().ToString(TEXT("%Y.%m.%d-%H.%M.%S_%s"));
            FullPath = FPaths::Combine(ProjectDir, TEXT("Saved/AIConnection/Screenshots"),
                FString::Printf(TEXT("viewport_%s.png"), *Timestamp));
        }
        else
        {
            FullPath = OutPath;
            if (FPaths::IsRelative(FullPath))
            {
                FullPath = FPaths::Combine(ProjectDir, FullPath);
            }
            FullPath = FPaths::ConvertRelativePathToFull(FullPath);
            FPaths::CollapseRelativeDirectories(FullPath);

            if (!FPaths::IsUnderDirectory(FullPath, ProjectDir))
            {
                OutError = FString::Printf(
                    TEXT("get_viewport_screenshot: path_escapes_project: out_path must resolve under the project dir (%s); got '%s'"),
                    *ProjectDir, *FullPath);
                return nullptr;
            }

            if (!FullPath.EndsWith(TEXT(".png"), ESearchCase::IgnoreCase))
            {
                FullPath += TEXT(".png");
            }
        }

        const FString DestDir = FPaths::GetPath(FullPath);
        if (!DestDir.IsEmpty() && !IFileManager::Get().DirectoryExists(*DestDir))
        {
            if (!IFileManager::Get().MakeDirectory(*DestDir, /*Tree=*/true))
            {
                OutError = FString::Printf(
                    TEXT("get_viewport_screenshot: dir_create_failed: could not create output directory '%s'"), *DestDir);
                return nullptr;
            }
        }

        TArray64<uint8> PngBytes;
        UCMCPCompat::EncodePngFColor(Size.X, Size.Y, Pixels, PngBytes);
        if (PngBytes.Num() == 0)
        {
            OutError = TEXT("get_viewport_screenshot: encode_failed: PNG compression produced empty output");
            return nullptr;
        }

        if (!FFileHelper::SaveArrayToFile(PngBytes, *FullPath))
        {
            OutError = FString::Printf(TEXT("get_viewport_screenshot: write_failed: could not write PNG to '%s'"), *FullPath);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), FullPath);
        Out->SetNumberField(TEXT("width"), Size.X);
        Out->SetNumberField(TEXT("height"), Size.Y);
        Out->SetNumberField(TEXT("bytes"), PngBytes.Num());

        // --- Optional small inline thumbnail --------------------------------
        bool bIncludeThumb = false;
        int32 ThumbMaxDim = 320;
        if (Params.IsValid())
        {
            Params->TryGetBoolField(TEXT("include_thumb"), bIncludeThumb);
            double RawThumbDim = 0.0;
            if (Params->TryGetNumberField(TEXT("thumb_max_dim"), RawThumbDim))
            {
                ThumbMaxDim = FMath::Clamp(static_cast<int32>(RawThumbDim), 64, 1024);
            }
        }

        if (bIncludeThumb)
        {
            const float Scale = static_cast<float>(ThumbMaxDim)
                / static_cast<float>(FMath::Max(Size.X, Size.Y));

            TArray64<uint8> ThumbPng;
            int32 ThumbW = Size.X;
            int32 ThumbH = Size.Y;
            if (Scale < 1.0f)
            {
                ThumbW = FMath::Max(1, FMath::RoundToInt(Size.X * Scale));
                ThumbH = FMath::Max(1, FMath::RoundToInt(Size.Y * Scale));
                // 5.6 ImageUtils.h: ImageResize(int32 SrcWidth, int32 SrcHeight,
                //   const TArray<FColor>& SrcData, int32 DstWidth, int32 DstHeight,
                //   TArray<FColor>& DstData, bool bResizeSRGBinLinearSpace,
                //   bool bForceOpaqueOutput = true).
                // Viewport pixels are gamma-space; resize directly (false).
                TArray<FColor> ThumbPixels;
                FImageUtils::ImageResize(Size.X, Size.Y, Pixels, ThumbW, ThumbH,
                    ThumbPixels, /*bResizeSRGBinLinearSpace=*/false, /*bForceOpaqueOutput=*/true);
                UCMCPCompat::EncodePngFColor(ThumbW, ThumbH, ThumbPixels, ThumbPng);
            }
            else
            {
                // Viewport already at or below the requested thumb size.
                ThumbPng = PngBytes;
            }

            if (ThumbPng.Num() > 0)
            {
                Out->SetStringField(TEXT("thumb_base64"), FBase64::Encode(ThumbPng.GetData(), ThumbPng.Num()));
                Out->SetNumberField(TEXT("thumb_width"), ThumbW);
                Out->SetNumberField(TEXT("thumb_height"), ThumbH);
            }
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetViewportScreenshot()
{
    return MakeShared<FHandler_GetViewportScreenshot>();
}

#endif // WITH_EDITOR
