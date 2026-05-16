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
// UNVERIFIED-COMPILE (Phase H FImageUtils PNG cluster): PNG encode routed
// through UCMCPCompat::EncodePngFColor -- PNGCompressImageArray/TArray64
// (>=5.1) vs legacy CompressImageArray/TArray (<=5.0). Source-authored only;
// no 5.0/5.1 host build this session.
#include "UCMCPCompat.h"

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

        // UNVERIFIED-COMPILE: cross-engine PNG encode.
        //   >=5.1 : FImageUtils::PNGCompressImageArray(W,H,TConstArrayView64<FColor>,TArray64<uint8>&)
        //   <=5.0 : FImageUtils::CompressImageArray(W,H,const TArray<FColor>&,TArray<uint8>&)
        // 5.7 ground truth (Engine/Source/Runtime/Engine/Public/ImageUtils.h)
        // matches the >=5.1 branch. Shim lives in UCMCPCompat.h.
        TArray64<uint8> PngBytes;
        UCMCPCompat::EncodePngFColor(Size.X, Size.Y, Pixels, PngBytes);

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
