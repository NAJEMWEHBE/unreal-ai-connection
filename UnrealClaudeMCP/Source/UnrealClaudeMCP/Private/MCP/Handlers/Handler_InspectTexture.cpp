// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_texture - read structural properties of a UTexture asset:
// dimensions (size, mips, imported size), pixel format, sRGB flag,
// compression settings, filter, LOD group, LOD bias, mip-gen settings,
// composite-texture link, virtual-texture streaming flag, never-stream
// flag. Pairs with the existing configure_texture handler (which mutates
// these fields) and import_texture (which creates the asset).
//
// Subclass coverage: handles UTexture base + UTexture2D specifics
// (size_x / size_y / num_mips / pixel_format / imported_size_x|y are
// UTexture2D-only, conditionally emitted). UTextureCube / UTextureRenderTarget /
// UTexture2DArray fall back to the surface_width / surface_height /
// surface_depth virtual accessors which are defined on UTexture base.
//
// UE 5.7 surface used (header:line citations for reviewer traceability):
//   Texture.h:1156  TEnumAsByte<TextureCompressionSettings> CompressionSettings
//   Texture.h:1385  TEnumAsByte<TextureMipGenSettings> MipGenSettings
//   Texture.h:1394  TObjectPtr<UTexture> CompositeTexture
//   Texture.h:1466  int32 LODBias
//   Texture.h:1474  TEnumAsByte<TextureFilter> Filter
//   Texture.h:1502  TEnumAsByte<TextureGroup> LODGroup
//   Texture.h:1531  uint8 SRGB : 1
//   Texture.h:1569  uint8 VirtualTextureStreaming : 1
//   Texture.h:1742  GetMaterialType() (PURE_VIRTUAL)
//   Texture.h:1961  GetSurfaceWidth() (PURE_VIRTUAL, float)
//   Texture.h:1964  GetSurfaceHeight() (PURE_VIRTUAL, float)
//   Texture.h:1967  GetSurfaceDepth() (PURE_VIRTUAL, float)
//   Texture.h:2044  static GetTextureGroupString(TextureGroup)
//   Texture.h:2047  static GetMipGenSettingsString(TextureMipGenSettings)
//   Texture.h:2155  static GetPixelFormatEnum()
//   Texture2D.h:155 GetSizeX() / :156 GetSizeY() / :157 GetNumMips()
//   Texture2D.h:158 GetPixelFormat()
//   Texture2D.h:40  GetImportedSize() -> FIntPoint
//
// Error format: "inspect_texture: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, asset_not_found, not_a_texture

#include "Engine/Texture.h"
#include "Engine/Texture2D.h"
#include "Engine/TextureDefines.h"
#include "PixelFormat.h"
#include "EditorAssetLibrary.h"
#include "MCP/MCPHandler.h"
#include "MCP/Handlers/AssetPathUtil.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "UObject/Class.h"

namespace
{
    // CompressionSettings has no built-in string helper in UE 5.7 -- enumerate
    // every TC_ value declared in TextureDefines.h. Mirrors the parse table in
    // Handler_ConfigureTexture.cpp's ParseCompression(); kept here so the same
    // (string -> enum) and (enum -> string) round-trip covers every value.
    // Default fallback returns "Unknown" so callers never see a silent gap.
    static FString CompressionToString(TextureCompressionSettings Value)
    {
        switch (Value)
        {
        case TC_Default:                 return TEXT("Default");
        case TC_Normalmap:               return TEXT("Normalmap");
        case TC_Masks:                   return TEXT("Masks");
        case TC_Grayscale:               return TEXT("Grayscale");
        case TC_Displacementmap:         return TEXT("Displacementmap");
        case TC_VectorDisplacementmap:   return TEXT("VectorDisplacementmap");
        case TC_HDR:                     return TEXT("HDR");
        case TC_EditorIcon:              return TEXT("UserInterface2D");  // matches ConfigureTexture's parse key
        case TC_BC7:                     return TEXT("BC7");
        case TC_HalfFloat:               return TEXT("HalfFloat");
        case TC_SingleFloat:             return TEXT("SingleFloat");
        case TC_Alpha:                   return TEXT("Alpha");
        case TC_DistanceFieldFont:       return TEXT("DistanceFieldFont");
        case TC_HDR_Compressed:          return TEXT("HDR_Compressed");
        case TC_HDR_F32:                 return TEXT("HDR_F32");
        default:                         return TEXT("Unknown");
        }
    }

    static FString FilterToString(TextureFilter Value)
    {
        switch (Value)
        {
        case TF_Nearest:    return TEXT("Nearest");
        case TF_Bilinear:   return TEXT("Bilinear");
        case TF_Trilinear:  return TEXT("Trilinear");
        case TF_Default:    return TEXT("Default");
        default:            return TEXT("Unknown");
        }
    }

    // Pixel format enum -> string. UE provides UEnum* via UTexture::GetPixelFormatEnum();
    // safer than a hard-coded switch because EPixelFormat has 200+ values and the
    // engine may add new ones across versions.
    static FString PixelFormatToString(EPixelFormat Value)
    {
        if (UEnum* EnumPtr = UTexture::GetPixelFormatEnum())
        {
            return EnumPtr->GetNameStringByValue(static_cast<int64>(Value));
        }
        return FString::Printf(TEXT("PF_%d"), static_cast<int32>(Value));
    }
}

class FHandler_InspectTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_texture: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_texture: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);

        UObject* Loaded = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!Loaded)
        {
            OutError = FString::Printf(
                TEXT("inspect_texture: asset_not_found: '%s' is not in the asset registry"), *InputPath);
            return nullptr;
        }

        UTexture* Texture = Cast<UTexture>(Loaded);
        if (!Texture)
        {
            OutError = FString::Printf(
                TEXT("inspect_texture: not_a_texture: '%s' is a %s, not a UTexture"),
                *InputPath, *Loaded->GetClass()->GetName());
            return nullptr;
        }

        // --- response ---

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Texture->GetName());
        Out->SetStringField(TEXT("path"), ObjectPath);
        Out->SetStringField(TEXT("texture_class"), Texture->GetClass()->GetName());

        // --- UTexture base fields ---

        Out->SetStringField(TEXT("compression_settings"),
            CompressionToString(static_cast<TextureCompressionSettings>(Texture->CompressionSettings)));
        Out->SetStringField(TEXT("filter"),
            FilterToString(static_cast<TextureFilter>(Texture->Filter)));
        // GetTextureGroupString / GetMipGenSettingsString return const TCHAR*;
        // SetStringField accepts that directly -- skip the redundant FString
        // ctor (PR #70 Gemini cleanup).
        Out->SetStringField(TEXT("lod_group"),
            UTexture::GetTextureGroupString(static_cast<TextureGroup>(Texture->LODGroup.GetValue())));
        Out->SetStringField(TEXT("mip_gen_settings"),
            UTexture::GetMipGenSettingsString(static_cast<TextureMipGenSettings>(Texture->MipGenSettings.GetValue())));

        Out->SetNumberField(TEXT("lod_bias"), static_cast<double>(Texture->LODBias));
        // Bit-field flags: explicit `!= 0` for unambiguous bool conversion.
        Out->SetBoolField(TEXT("srgb"), Texture->SRGB != 0);
        Out->SetBoolField(TEXT("virtual_texture_streaming"), Texture->VirtualTextureStreaming != 0);
        Out->SetBoolField(TEXT("never_stream"), Texture->NeverStream != 0);

        // Composite texture cross-link (asset path) -- conditional on non-null.
        // GetCompositeTexture() is the UE 5.7 deprecation-clean accessor; the
        // direct `CompositeTexture` field emits a C4996 warning that fails
        // build under -Werror. (Build log on cold compile flagged this.)
        if (UTexture* Composite = Texture->GetCompositeTexture())
        {
            Out->SetStringField(TEXT("composite_texture"), Composite->GetPathName());
        }

        // Surface dimensions via the virtual accessors -- defined on every
        // concrete UTexture subclass (Texture2D, Cube, RenderTarget, ...).
        // Note: GetSurfaceWidth/Height/Depth are PURE_VIRTUAL on the base
        // (Texture.h:1961-1967); calling on the abstract base would crash,
        // but Cast<UTexture> only succeeds for a concrete subclass at runtime.
        Out->SetNumberField(TEXT("surface_width"),  static_cast<double>(Texture->GetSurfaceWidth()));
        Out->SetNumberField(TEXT("surface_height"), static_cast<double>(Texture->GetSurfaceHeight()));
        Out->SetNumberField(TEXT("surface_depth"),  static_cast<double>(Texture->GetSurfaceDepth()));

        // --- UTexture2D specifics (conditional) ---

        if (UTexture2D* Texture2D = Cast<UTexture2D>(Texture))
        {
            Out->SetNumberField(TEXT("size_x"), static_cast<double>(Texture2D->GetSizeX()));
            Out->SetNumberField(TEXT("size_y"), static_cast<double>(Texture2D->GetSizeY()));
            Out->SetNumberField(TEXT("num_mips"), static_cast<double>(Texture2D->GetNumMips()));
            Out->SetStringField(TEXT("pixel_format"),
                PixelFormatToString(Texture2D->GetPixelFormat(0)));

            const FIntPoint ImportedSize = Texture2D->GetImportedSize();
            Out->SetNumberField(TEXT("imported_size_x"), static_cast<double>(ImportedSize.X));
            Out->SetNumberField(TEXT("imported_size_y"), static_cast<double>(ImportedSize.Y));
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectTexture()
{
    return MakeShared<FHandler_InspectTexture>();
}
