// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// take_screenshot - capture the active level-editor viewport as a PNG and
// write it to a caller-supplied path that MUST resolve to a location under the
// UE project directory, with hard width/height caps.
//
// Relationship to the existing screenshot tools (distinct, not a duplicate):
//   - get_viewport_screenshot : base64 PNG inline, no file, no sizing.
//   - render_camera_to_png    : writes to an ABSOLUTE path ANYWHERE on disk
//                               (no project containment), arbitrary size.
//   - take_high_res_screenshot: HighResShot to Saved/Screenshots (async, fixed
//                               output dir, multiplier only).
//   - take_screenshot (this)  : path is constrained UNDER the project dir
//                               (rejects traversal / outside writes) and the
//                               width/height are clamped to a hard cap. Closes
//                               the "see the result" loop with a safe,
//                               project-relative output the caller names.
//
// Capture mechanics mirror the host-verified Handler_RenderCameraToPng.cpp
// (synchronous redraw + ReadPixels for the live viewport; transient
// SceneCapture2D for an explicit size), so the rendering surface is the proven
// chain rather than a deferred screenshot path that never resolves headless.
//
// Error format: "take_screenshot: <error_code>: <human-readable detail>".
// Stable error codes: no_editor, bad_param, path_escapes_project, no_world,
// no_level_editor, no_viewport, read_failed, encode_failed, write_failed,
// dir_create_failed.

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
#include "HAL/FileManager.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/SceneCapture2D.h"

class FHandler_TakeScreenshot : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("take_screenshot"); }

    // Hard pixel ceiling for an explicit-size capture. Matches the
    // render-target clamp used by render_camera_to_png; documented in the
    // schema as the width/height cap.
    static constexpr int32 kMaxDim = 7680;  // 8K-wide ceiling

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor)
        {
            OutError = TEXT("take_screenshot: no_editor: GEditor is null (editor build only)");
            return nullptr;
        }

        FString OutPath;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("out_path"), OutPath) || OutPath.IsEmpty())
        {
            OutError = TEXT("take_screenshot: bad_param: out_path is required and must be a non-empty string");
            return nullptr;
        }

        // --- Resolve + confine the path under the project directory ---------
        // Relative paths are resolved against the project dir; absolute paths
        // are normalized then checked for containment. CollapseRelativeDirectories
        // folds any ".." segments BEFORE the containment test so a crafted
        // "<project>/../outside.png" cannot escape.
        const FString ProjectDir = FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
        FString FullPath = OutPath;
        if (FPaths::IsRelative(FullPath))
        {
            FullPath = FPaths::Combine(ProjectDir, FullPath);
        }
        FullPath = FPaths::ConvertRelativePathToFull(FullPath);
        FPaths::CollapseRelativeDirectories(FullPath);

        if (!FPaths::IsUnderDirectory(FullPath, ProjectDir))
        {
            OutError = FString::Printf(
                TEXT("take_screenshot: path_escapes_project: out_path must resolve under the project dir (%s); got '%s'"),
                *ProjectDir, *FullPath);
            return nullptr;
        }

        // Ensure a .png extension so callers don't silently write headerless data.
        if (!FullPath.EndsWith(TEXT(".png"), ESearchCase::IgnoreCase))
        {
            FullPath += TEXT(".png");
        }

        // --- Optional explicit size (clamped to the hard cap) --------------
        int32 ReqWidth  = 0;
        int32 ReqHeight = 0;
        double FovDeg   = 0.0;
        Params->TryGetNumberField(TEXT("width"),  ReqWidth);
        Params->TryGetNumberField(TEXT("height"), ReqHeight);
        Params->TryGetNumberField(TEXT("fov"), FovDeg);

        const bool bHasSize = (ReqWidth > 0 && ReqHeight > 0);

        // Make sure the destination directory exists before we render.
        const FString DestDir = FPaths::GetPath(FullPath);
        if (!DestDir.IsEmpty() && !IFileManager::Get().DirectoryExists(*DestDir))
        {
            if (!IFileManager::Get().MakeDirectory(*DestDir, /*Tree=*/true))
            {
                OutError = FString::Printf(
                    TEXT("take_screenshot: dir_create_failed: could not create output directory '%s'"), *DestDir);
                return nullptr;
            }
        }

        if (bHasSize)
        {
            return CaptureOffscreen(FullPath, ReqWidth, ReqHeight, FovDeg, OutError);
        }
        return CaptureViewport(FullPath, FovDeg, OutError);
    }

private:

    // Live active-editor viewport: synchronous redraw + ReadPixels.
    TSharedPtr<FJsonObject> CaptureViewport(const FString& OutPath, double FovDeg, FString& OutError)
    {
        check(IsInGameThread());
        FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>("LevelEditor");
        if (!LEModule) { OutError = TEXT("take_screenshot: no_level_editor: LevelEditor module unavailable"); return nullptr; }
        TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
        if (!LV.IsValid()) { OutError = TEXT("take_screenshot: no_viewport: no active level viewport found"); return nullptr; }
        TSharedPtr<FSceneViewport> SharedVP = LV->GetSharedActiveViewport();
        FViewport* Viewport = SharedVP.IsValid() ? SharedVP.Get() : GEditor->GetActiveViewport();
        if (!Viewport) { OutError = TEXT("take_screenshot: no_viewport: could not obtain FViewport*"); return nullptr; }

        FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
        const FVector  SavedLoc = VC.GetViewLocation();
        const FRotator SavedRot = VC.GetViewRotation();
        const float    SavedFov = VC.ViewFOV;
        if (FovDeg > 0.0) { VC.ViewFOV = static_cast<float>(FovDeg); }

        VC.Invalidate();
        GEditor->RedrawLevelEditingViewports(/*bInvalidateHitProxies=*/true);
        Viewport->Draw(/*bShouldPresent=*/false);
        FlushRenderingCommands();

        const FIntPoint Size = Viewport->GetSizeXY();
        TArray<FColor> Bitmap;
        FReadSurfaceDataFlags Flags;
        Flags.SetLinearToGamma(false);
        const bool bReadOk = Viewport->ReadPixels(Bitmap, Flags);

        // Restore the FOV override (location/rotation were untouched here, but
        // restore symmetrically for safety).
        if (FovDeg > 0.0)
        {
            VC.SetViewLocation(SavedLoc);
            VC.SetViewRotation(SavedRot);
            VC.ViewFOV = SavedFov;
            VC.Invalidate();
        }

        if (!bReadOk || Bitmap.Num() == 0)
        {
            OutError = TEXT("take_screenshot: read_failed: ReadPixels returned false or empty bitmap");
            return nullptr;
        }
        for (FColor& C : Bitmap) { C.A = 255; }
        return EncodeToPng(Bitmap, Size.X, Size.Y, OutPath, OutError);
    }

    // Explicit size: off-screen transient SceneCapture2D matching the active
    // viewport's camera, rendered synchronously.
    TSharedPtr<FJsonObject> CaptureOffscreen(const FString& OutPath, int32 W, int32 H, double FovDeg, FString& OutError)
    {
        check(IsInGameThread());
        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World) { OutError = TEXT("take_screenshot: no_world: editor world is null"); return nullptr; }

        W = FMath::Clamp(W, 1, kMaxDim);
        H = FMath::Clamp(H, 1, kMaxDim);

        UTextureRenderTarget2D* RT = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
        if (!RT) { OutError = TEXT("take_screenshot: read_failed: could not create UTextureRenderTarget2D"); return nullptr; }
        RT->AddToRoot();
        RT->InitCustomFormat(W, H, PF_B8G8R8A8, /*bInLinearColorSpace=*/false);
        RT->UpdateResourceImmediate();

        FActorSpawnParameters SpawnParams;
        SpawnParams.ObjectFlags |= RF_Transient;
        ASceneCapture2D* CaptureActor = World->SpawnActor<ASceneCapture2D>(
            ASceneCapture2D::StaticClass(), FTransform::Identity, SpawnParams);
        if (!CaptureActor)
        {
            RT->RemoveFromRoot(); RT->ReleaseResource();
            OutError = TEXT("take_screenshot: read_failed: could not spawn transient ASceneCapture2D");
            return nullptr;
        }
        USceneCaptureComponent2D* Comp = CaptureActor->GetCaptureComponent2D();
        if (!Comp)
        {
            CaptureActor->Destroy(); RT->RemoveFromRoot(); RT->ReleaseResource();
            OutError = TEXT("take_screenshot: read_failed: missing USceneCaptureComponent2D");
            return nullptr;
        }

        auto Cleanup = [&]
        {
            if (CaptureActor) { CaptureActor->Destroy(); CaptureActor = nullptr; }
            if (RT) { RT->RemoveFromRoot(); RT->ReleaseResource(); RT = nullptr; }
        };

        Comp->TextureTarget = RT;
        Comp->CaptureSource = SCS_FinalColorLDR;
        Comp->bCaptureEveryFrame = false;
        Comp->bCaptureOnMovement = false;
        Comp->bAlwaysPersistRenderingState = true;

        // Match the active viewport camera, with an optional FOV override.
        if (FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>("LevelEditor"))
        {
            TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
            if (LV.IsValid())
            {
                FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
                CaptureActor->SetActorLocation(VC.GetViewLocation());
                CaptureActor->SetActorRotation(VC.GetViewRotation());
                Comp->FOVAngle = (FovDeg > 0.0) ? static_cast<float>(FovDeg) : VC.ViewFOV;
            }
        }
        if (FovDeg > 0.0) { Comp->FOVAngle = static_cast<float>(FovDeg); }

        Comp->CaptureScene();
        FlushRenderingCommands();

        TArray<FColor> Bitmap;
        FRenderTarget* RTResource = RT->GameThread_GetRenderTargetResource();
        if (!RTResource)
        {
            Cleanup();
            OutError = TEXT("take_screenshot: read_failed: GameThread_GetRenderTargetResource() returned null");
            return nullptr;
        }
        const bool bReadOk = RTResource->ReadPixels(Bitmap);
        Cleanup();
        if (!bReadOk || Bitmap.Num() == 0)
        {
            OutError = TEXT("take_screenshot: read_failed: ReadPixels from render target returned false or empty");
            return nullptr;
        }
        for (FColor& C : Bitmap) { C.A = 255; }
        return EncodeToPng(Bitmap, W, H, OutPath, OutError);
    }

    TSharedPtr<FJsonObject> EncodeToPng(const TArray<FColor>& Bitmap, int32 W, int32 H, const FString& OutPath, FString& OutError)
    {
        // ReadPixels can return a short/empty buffer on a failed or resized
        // readback; encoding a mismatched buffer would read out of bounds.
        if (Bitmap.Num() != W * H)
        {
            OutError = FString::Printf(TEXT("take_screenshot: encode_failed: bitmap size mismatch (expected %d pixels for %dx%d, got %d)"), W * H, W, H, Bitmap.Num());
            return nullptr;
        }
        TArray64<uint8> PngData;
        UCMCPCompat::EncodePngFColor(W, H, Bitmap, PngData);
        if (PngData.Num() == 0)
        {
            OutError = TEXT("take_screenshot: encode_failed: PNG compression produced empty output");
            return nullptr;
        }
        if (!FFileHelper::SaveArrayToFile(PngData, *OutPath))
        {
            OutError = FString::Printf(TEXT("take_screenshot: write_failed: could not write PNG to %s"), *OutPath);
            return nullptr;
        }
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField  (TEXT("ok"),     true);
        Out->SetStringField(TEXT("path"),   OutPath);
        Out->SetNumberField(TEXT("width"),  W);
        Out->SetNumberField(TEXT("height"), H);
        Out->SetNumberField(TEXT("bytes"),  PngData.Num());
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_TakeScreenshot()
{
    return MakeShared<FHandler_TakeScreenshot>();
}

#endif // WITH_EDITOR
