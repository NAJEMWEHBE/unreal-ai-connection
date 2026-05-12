// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// get_viewport_screenshot - capture the active editor viewport as a PNG and
// return it base64-encoded inline.
//
// Error format: free-form OutError strings (legacy surface — predates the canonical
// "<tool_name>: <error_code>: <detail>" convention used by later handlers). Migration
// is deferred; bridge consumers treat OutError as human-readable text rather than
// parsing for a code prefix.

#include "MCP/MCPHandler.h"

#include "Editor.h"
#include "UnrealClient.h"
#include "ImageUtils.h"
#include "Misc/Base64.h"

class FHandler_GetViewportScreenshot : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("get_viewport_screenshot"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& OutError) override
    {
        if (!GEditor)
        {
            OutError = TEXT("GEditor is null (call from editor build only)");
            return nullptr;
        }

        FViewport* VP = GEditor->GetActiveViewport();
        if (!VP)
        {
            OutError = TEXT("No active viewport (open a level or focus the editor viewport)");
            return nullptr;
        }

        const FIntPoint Size = VP->GetSizeXY();
        if (Size.X <= 0 || Size.Y <= 0)
        {
            OutError = TEXT("Viewport size is zero");
            return nullptr;
        }

        TArray<FColor> Pixels;
        FReadSurfaceDataFlags Flags;
        Flags.SetLinearToGamma(false);
        if (!VP->ReadPixels(Pixels, Flags))
        {
            OutError = TEXT("ReadPixels failed");
            return nullptr;
        }

        // Force alpha to 255 (viewport often returns 0 alpha)
        for (FColor& C : Pixels) { C.A = 255; }

        // UE 5.7 ground truth (verified in Engine/Source/Runtime/Engine/Public/ImageUtils.h):
        //   ENGINE_API static void PNGCompressImageArray(int32 W, int32 H,
        //       const TArrayView64<const FColor>& Src, TArray64<uint8>& Dst);
        TArray64<uint8> PngBytes;
        FImageUtils::PNGCompressImageArray(Size.X, Size.Y, Pixels, PngBytes);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetNumberField(TEXT("width"), Size.X);
        Out->SetNumberField(TEXT("height"), Size.Y);
        Out->SetNumberField(TEXT("png_bytes"), PngBytes.Num());
        Out->SetStringField(TEXT("png_base64"), FBase64::Encode(PngBytes.GetData(), PngBytes.Num()));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_GetViewportScreenshot()
{
    return MakeShared<FHandler_GetViewportScreenshot>();
}
