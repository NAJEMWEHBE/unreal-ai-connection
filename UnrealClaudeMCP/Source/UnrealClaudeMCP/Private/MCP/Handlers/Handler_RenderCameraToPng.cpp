// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// render_camera_to_png - force a synchronous render of the editor viewport (or
// an off-screen SceneCapture2D at arbitrary resolution) and write the result to
// an absolute filesystem path as a PNG.
//
// ROOT-CAUSE THIS FIXES:
//   Under headless / bridge automation with the editor backgrounded, UE does not
//   pump its tick / draw loop.  Every DEFERRED screenshot path (HighResShot,
//   FScreenshotRequest, AutomationLibrary, Python SceneCapture) is never fulfilled
//   and returns blank pixels or never resolves.  This handler is called
//   synchronously on the game thread so it forces the exact chain:
//     Invalidate -> RedrawLevelEditingViewports(true)
//     -> Viewport->Draw(false) -> FlushRenderingCommands -> ReadPixels -> encode.
//
// UE 5.7: host-compiled + runtime-verified 2026-05-17 (Build.bat Result:
// Succeeded, MSVC 14.44; live editor wrote a 2,059,202-byte PNG via this
// handler -- the inert-handler state is cleared).
//
// Hardened against UE 5.7 source since first authoring:
//   - viewport acquisition now uses SLevelViewport::GetSharedActiveViewport()
//     (TSharedPtr<FSceneViewport>, upcast to FViewport*) -- verified accessor.
//   - PNG encode now uses FImageUtils::PNGCompressImageArray (TArray64), matching
//     the 5.7-verified Handler_GetViewportScreenshot.cpp (CompressImageArray is
//     UE_DEPRECATED(5.1)).
// The FEditorViewportClient::ViewFOV member access (used below) compiled
// clean on the 2026-05-17 UE 5.7 host build -- no longer a residual risk.

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
#include "ImageUtils.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
// Phase H FImageUtils PNG cluster: PNG encode routed through
// UCMCPCompat::EncodePngFColor -- PNGCompressImageArray/TArray64 (>=5.1) vs
// legacy CompressImageArray/TArray (<=5.0). The >=5.1 path is host-verified
// (5.7 build 2026-05-17; 5.1 T2 build in the 31st-note window); the <=5.0
// path is still source-only (no 5.0 host build).
#include "UCMCPCompat.h"
#include "Modules/ModuleManager.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Engine/SceneCapture2D.h"
#include "EngineUtils.h"

class FHandler_RenderCameraToPng : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("render_camera_to_png"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor)
        {
            OutError = TEXT("render_camera_to_png: no_editor: GEditor is null (editor build only)");
            return nullptr;
        }

        FString OutPath;
        if (!Params.IsValid() || !Params->TryGetStringField(TEXT("out_path"), OutPath) || OutPath.IsEmpty())
        {
            OutError = TEXT("render_camera_to_png: bad_param: out_path is required and must be a non-empty string");
            return nullptr;
        }
        if (FPaths::IsRelative(OutPath))
        {
            OutError = TEXT("render_camera_to_png: bad_param: out_path must be an absolute filesystem path");
            return nullptr;
        }

        int32 ReqWidth  = 0;
        int32 ReqHeight = 0;
        FString CameraLabel;
        double  FovDeg  = 0.0;

        if (Params.IsValid())
        {
            Params->TryGetNumberField(TEXT("width"),  ReqWidth);
            Params->TryGetNumberField(TEXT("height"), ReqHeight);
            Params->TryGetStringField(TEXT("camera_label"), CameraLabel);
            Params->TryGetNumberField(TEXT("fov"), FovDeg);
        }

        const bool bHasCamera = !CameraLabel.IsEmpty();
        const bool bHasSize   = (ReqWidth > 0 && ReqHeight > 0);
        const bool bUsePathB  = bHasCamera || bHasSize;

        if (!bUsePathB)
        {
            return CaptureViewport(OutPath, FovDeg, OutError);
        }
        return CaptureOffscreen(OutPath, ReqWidth, ReqHeight, CameraLabel, FovDeg, OutError);
    }

private:

    // Path A: synchronous redraw of the active level-editor viewport.
    // Only reached when no camera_label and no explicit size were requested
    // (bUsePathB false), so camera-label handling lives solely in Path B.
    TSharedPtr<FJsonObject> CaptureViewport(
        const FString& OutPath, double FovDeg, FString& OutError)
    {
        check(IsInGameThread());
        FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>("LevelEditor");
        if (!LEModule) { OutError = TEXT("render_camera_to_png: no_level_editor: LevelEditor module unavailable"); return nullptr; }
        TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
        if (!LV.IsValid()) { OutError = TEXT("render_camera_to_png: no_viewport: no active level viewport found"); return nullptr; }
        TSharedPtr<FSceneViewport> SharedVP = LV->GetSharedActiveViewport();
        FViewport* Viewport = SharedVP.IsValid() ? SharedVP.Get() : GEditor->GetActiveViewport();
        if (!Viewport) { OutError = TEXT("render_camera_to_png: no_viewport: could not obtain FViewport*"); return nullptr; }
        FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
        FVector  SavedLoc = VC.GetViewLocation();
        FRotator SavedRot = VC.GetViewRotation();
        // UE 5.7 host-verified 2026-05-17: public member ViewFOV compiles
        // clean (no SetViewFOV() needed).
        float SavedFov = VC.ViewFOV;
        if (FovDeg > 0.0) { VC.ViewFOV = static_cast<float>(FovDeg); }
        VC.Invalidate();
        GEditor->RedrawLevelEditingViewports(/*bInvalidateHitProxies=*/true);
        Viewport->Draw(/*bShouldPresent=*/false);
        FlushRenderingCommands();
        const FIntPoint Size = Viewport->GetSizeXY();
        TArray<FColor> Bitmap;
        FReadSurfaceDataFlags Flags; Flags.SetLinearToGamma(false);
        bool bReadOk = Viewport->ReadPixels(Bitmap, Flags);
        if (FovDeg > 0.0) {
            VC.SetViewLocation(SavedLoc); VC.SetViewRotation(SavedRot); VC.ViewFOV = SavedFov; VC.Invalidate();
        }
        if (!bReadOk || Bitmap.Num() == 0) { OutError = TEXT("render_camera_to_png: read_failed: ReadPixels returned false or empty bitmap"); return nullptr; }
        for (FColor& C : Bitmap) { C.A = 255; }
        return EncodeToPng(Bitmap, Size.X, Size.Y, OutPath, OutError);
    }

    // Path B: off-screen render via transient SceneCapture2D actor.
    TSharedPtr<FJsonObject> CaptureOffscreen(
        const FString& OutPath, int32 W, int32 H, const FString& CameraLabel, double FovDeg, FString& OutError)
    {
        check(IsInGameThread());
        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World) { OutError = TEXT("render_camera_to_png: no_world: editor world is null"); return nullptr; }
        if (W <= 0) W = 1920; if (H <= 0) H = 1080;
        W = FMath::Clamp(W, 1, 8192); H = FMath::Clamp(H, 1, 8192);
        UTextureRenderTarget2D* RT = NewObject<UTextureRenderTarget2D>(GetTransientPackage());
        if (!RT) { OutError = TEXT("render_camera_to_png: rt_alloc_failed: could not create UTextureRenderTarget2D"); return nullptr; }
        RT->AddToRoot();
        RT->InitCustomFormat(W, H, PF_B8G8R8A8, /*bInLinearColorSpace=*/false);
        RT->UpdateResourceImmediate();
        FActorSpawnParameters SpawnParams; SpawnParams.ObjectFlags |= RF_Transient;
        ASceneCapture2D* CaptureActor = World->SpawnActor<ASceneCapture2D>(ASceneCapture2D::StaticClass(), FTransform::Identity, SpawnParams);
        if (!CaptureActor) { RT->RemoveFromRoot(); OutError = TEXT("render_camera_to_png: spawn_failed: could not spawn transient ASceneCapture2D"); return nullptr; }
        USceneCaptureComponent2D* Comp = CaptureActor->GetCaptureComponent2D();
        if (!Comp) { CaptureActor->Destroy(); RT->RemoveFromRoot(); RT->ReleaseResource(); OutError = TEXT("render_camera_to_png: no_capture_comp: missing USceneCaptureComponent2D"); return nullptr; }
        // Single teardown for every path past this point (idempotent: nulls handles
        // so the success path's final call cannot double-free).
        auto Cleanup = [&]
        {
            if (CaptureActor) { CaptureActor->Destroy(); CaptureActor = nullptr; }
            if (RT) { RT->RemoveFromRoot(); RT->ReleaseResource(); RT = nullptr; }
        };
        Comp->TextureTarget = RT; Comp->CaptureSource = SCS_FinalColorLDR;
        Comp->bCaptureEveryFrame = false; Comp->bCaptureOnMovement = false; Comp->bAlwaysPersistRenderingState = true;
        if (FovDeg > 0.0) { Comp->FOVAngle = static_cast<float>(FovDeg); }
        if (!CameraLabel.IsEmpty()) {
            AActor* A = FindActorByLabel(CameraLabel);
            if (!A) { Cleanup(); OutError = FString::Printf(TEXT("render_camera_to_png: actor_not_found: no actor with label '%s'"), *CameraLabel); return nullptr; }
            CaptureActor->SetActorTransform(A->GetActorTransform());
        } else if (GEditor) {
            FLevelEditorModule* LEModule = FModuleManager::GetModulePtr<FLevelEditorModule>("LevelEditor");
            if (LEModule) {
                TSharedPtr<SLevelViewport> LV = LEModule->GetFirstActiveLevelViewport();
                if (LV.IsValid()) {
                    FLevelEditorViewportClient& VC = LV->GetLevelViewportClient();
                    CaptureActor->SetActorLocation(VC.GetViewLocation());
                    CaptureActor->SetActorRotation(VC.GetViewRotation());
                    if (FovDeg <= 0.0) { Comp->FOVAngle = VC.ViewFOV; }  // UE 5.7 host-verified 2026-05-17 -- member access, see Path A
                }
            }
        }
        // Immediate synchronous capture -- NOT CaptureSceneDeferred.
        Comp->CaptureScene(); FlushRenderingCommands();
        TArray<FColor> Bitmap;
        FRenderTarget* RTResource = RT->GameThread_GetRenderTargetResource();
        if (!RTResource) { Cleanup(); OutError = TEXT("render_camera_to_png: no_rt_resource: GameThread_GetRenderTargetResource() returned null"); return nullptr; }
        bool bReadOk = RTResource->ReadPixels(Bitmap);
        Cleanup();
        if (!bReadOk || Bitmap.Num() == 0) { OutError = TEXT("render_camera_to_png: read_failed: ReadPixels from render target returned false or empty"); return nullptr; }
        for (FColor& C : Bitmap) { C.A = 255; }
        return EncodeToPng(Bitmap, W, H, OutPath, OutError);
    }

    // Shared: encode TArray<FColor> to PNG and save to disk.
    TSharedPtr<FJsonObject> EncodeToPng(const TArray<FColor>& Bitmap, int32 W, int32 H, const FString& OutPath, FString& OutError)
    {
        // Cross-engine PNG encode (>=5.1 path host-verified 2026-05-17 on
        // 5.7; <=5.0 path source-only):
        //   >=5.1 : FImageUtils::PNGCompressImageArray(W,H,TConstArrayView64<FColor>,TArray64<uint8>&)
        //   <=5.0 : FImageUtils::CompressImageArray(W,H,const TArray<FColor>&,TArray<uint8>&)
        // 5.7 ground truth (Engine/Source/Runtime/Engine/Public/ImageUtils.h)
        // matches the >=5.1 branch; mirrors Handler_GetViewportScreenshot.cpp.
        TArray64<uint8> PngData;
        UCMCPCompat::EncodePngFColor(W, H, Bitmap, PngData);

        if (PngData.Num() == 0) { OutError = TEXT("render_camera_to_png: encode_failed: PNG compression produced empty output"); return nullptr; }
        if (!FFileHelper::SaveArrayToFile(PngData, *OutPath))
        {
            OutError = FString::Printf(TEXT("render_camera_to_png: write_failed: could not write PNG to %s"), *OutPath);
            return nullptr;
        }
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField  (TEXT("ok"),     true);
        Out->SetStringField(TEXT("path"),   OutPath);
        Out->SetNumberField(TEXT("width"),  W);
        Out->SetNumberField(TEXT("height"), H);
        return Out;
    }

    AActor* FindActorByLabel(const FString& Label)
    {
        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World) { return nullptr; }
        for (TActorIterator<AActor> It(World); It; ++It)
        {
            if ((*It)->GetActorLabel() == Label) { return *It; }
        }
        return nullptr;
    }
};  // class FHandler_RenderCameraToPng

TSharedRef<IUCMCPHandler> Make_Handler_RenderCameraToPng()
{
    return MakeShared<FHandler_RenderCameraToPng>();
}

#endif // WITH_EDITOR
