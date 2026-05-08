// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// configure_texture - adjust SRGB / CompressionSettings / LODGroup / Filter
// on an existing UTexture asset. Validates enums, runs the documented
// PreEditChange / Modify / set / PostEditChange / UpdateResource /
// SaveLoadedAsset dance, returns an `applied` map of what changed.
//
// Error format: "configure_texture: <error_code>: <human-readable detail>".
// Stable error codes (parseable by clients): missing_params,
// missing_required_field, no_changes_specified, asset_not_found,
// unknown_enum_value, save_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Engine/Texture.h"
#include "UObject/UObjectGlobals.h"
#include "Engine/TextureDefines.h"     // TextureCompressionSettings, TextureGroup, TextureFilter
#include "EditorAssetLibrary.h"         // UEditorAssetLibrary::SaveLoadedAsset

// ---------------------------------------------------------------------------
// File-static enum parser helpers (verified against UE 5.7 TextureDefines.h)
// ---------------------------------------------------------------------------

static bool ParseCompression(const FString& In, TextureCompressionSettings& Out)
{
    // Verified against Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h @ 5.7
    // Deviations from plan:
    //   - TC_BC4 and TC_BC5 do NOT exist in UE 5.7; removed.
    //   - TC_HDR_F32 exists in UE 5.7 but was not in the plan; added as "HDR_F32".
    //   - TC_EditorIcon IS the correct constant for the "UserInterface2D" user-facing key.
    static const TMap<FString, TextureCompressionSettings> M = {
        {TEXT("Default"),                   TC_Default},
        {TEXT("Normalmap"),                 TC_Normalmap},
        {TEXT("Masks"),                     TC_Masks},
        {TEXT("Grayscale"),                 TC_Grayscale},
        {TEXT("Displacementmap"),           TC_Displacementmap},
        {TEXT("VectorDisplacementmap"),     TC_VectorDisplacementmap},
        {TEXT("HDR"),                       TC_HDR},
        {TEXT("UserInterface2D"),           TC_EditorIcon},   // TC_EditorIcon is the UE 5.7 constant
        {TEXT("BC7"),                       TC_BC7},
        {TEXT("HalfFloat"),                 TC_HalfFloat},
        {TEXT("SingleFloat"),               TC_SingleFloat},
        {TEXT("Alpha"),                     TC_Alpha},
        {TEXT("DistanceFieldFont"),         TC_DistanceFieldFont},
        {TEXT("HDR_Compressed"),            TC_HDR_Compressed},
        {TEXT("HDR_F32"),                   TC_HDR_F32},      // Exists in UE 5.7; not in plan — added
    };
    if (const TextureCompressionSettings* V = M.Find(In)) { Out = *V; return true; }
    return false;
}

static bool ParseFilter(const FString& In, TextureFilter& Out)
{
    // Verified: TF_Nearest, TF_Bilinear, TF_Trilinear, TF_Default all confirmed in UE 5.7.
    if (In == TEXT("Nearest"))    { Out = TF_Nearest;   return true; }
    if (In == TEXT("Bilinear"))   { Out = TF_Bilinear;  return true; }
    if (In == TEXT("Trilinear"))  { Out = TF_Trilinear; return true; }
    if (In == TEXT("Default"))    { Out = TF_Default;   return true; }
    return false;
}

static bool ParseLodGroup(const FString& In, TextureGroup& Out)
{
    // Verified against Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h @ 5.7
    // Deviations from plan:
    //   - TEXTUREGROUP_Bake does NOT exist in UE 5.7; removed.
    //   - All other 24 entries from the plan are confirmed present.
    static const TMap<FString, TextureGroup> M = {
        {TEXT("World"),              TEXTUREGROUP_World},
        {TEXT("WorldNormalMap"),     TEXTUREGROUP_WorldNormalMap},
        {TEXT("WorldSpecular"),      TEXTUREGROUP_WorldSpecular},
        {TEXT("Character"),          TEXTUREGROUP_Character},
        {TEXT("CharacterNormalMap"), TEXTUREGROUP_CharacterNormalMap},
        {TEXT("CharacterSpecular"),  TEXTUREGROUP_CharacterSpecular},
        {TEXT("Weapon"),             TEXTUREGROUP_Weapon},
        {TEXT("WeaponNormalMap"),    TEXTUREGROUP_WeaponNormalMap},
        {TEXT("WeaponSpecular"),     TEXTUREGROUP_WeaponSpecular},
        {TEXT("Vehicle"),            TEXTUREGROUP_Vehicle},
        {TEXT("VehicleNormalMap"),   TEXTUREGROUP_VehicleNormalMap},
        {TEXT("VehicleSpecular"),    TEXTUREGROUP_VehicleSpecular},
        {TEXT("Cinematic"),          TEXTUREGROUP_Cinematic},
        {TEXT("Effects"),            TEXTUREGROUP_Effects},
        {TEXT("EffectsNotFiltered"), TEXTUREGROUP_EffectsNotFiltered},
        {TEXT("Skybox"),             TEXTUREGROUP_Skybox},
        {TEXT("UI"),                 TEXTUREGROUP_UI},
        {TEXT("Lightmap"),           TEXTUREGROUP_Lightmap},
        {TEXT("Shadowmap"),          TEXTUREGROUP_Shadowmap},
        {TEXT("RenderTarget"),       TEXTUREGROUP_RenderTarget},
        {TEXT("MobileFlattened"),    TEXTUREGROUP_MobileFlattened},
        {TEXT("IESLightProfile"),    TEXTUREGROUP_IESLightProfile},
        {TEXT("Pixels2D"),           TEXTUREGROUP_Pixels2D},
        {TEXT("HierarchicalLOD"),    TEXTUREGROUP_HierarchicalLOD},
        // TEXTUREGROUP_Bake was in the plan but does NOT exist in UE 5.7 — omitted.
    };
    if (const TextureGroup* V = M.Find(In)) { Out = *V; return true; }
    return false;
}

class FHandler_ConfigureTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("configure_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("configure_texture: missing_params: request had no params object");
            return nullptr;
        }

        FString Path;
        if (!Params->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
        {
            OutError = TEXT("configure_texture: missing_required_field: 'path' is required");
            return nullptr;
        }

        const bool bHasSrgb        = Params->HasField(TEXT("srgb"));
        const bool bHasCompression = Params->HasField(TEXT("compression"));
        const bool bHasLodGroup    = Params->HasField(TEXT("lod_group"));
        const bool bHasFilter      = Params->HasField(TEXT("filter"));
        if (!bHasSrgb && !bHasCompression && !bHasLodGroup && !bHasFilter)
        {
            OutError = TEXT("configure_texture: no_changes_specified: "
                            "provide at least one of srgb / compression / lod_group / filter");
            return nullptr;
        }

        UTexture* Tex = LoadObject<UTexture>(nullptr, *Path);
        if (!Tex)
        {
            OutError = FString::Printf(TEXT("configure_texture: asset_not_found: no asset at '%s'"), *Path);
            return nullptr;
        }

        // Pre-validate all enum values BEFORE any mutation, so unknown_enum_value
        // can't leave the asset half-modified.
        //
        // Codex review on PR #3 (P1) caught a related defect: HasField checks
        // presence but TryGetBoolField / TryGetStringField only succeed when
        // the JSON value is the right type. If a caller sent {"srgb": null} or
        // {"compression": 123}, the parse step silently failed and the field
        // retained its default (false / TC_Default / etc.), which the mutation
        // step at the bottom then wrote to the asset. Now we treat a parse
        // failure on a present field as an explicit invalid_value_type error.
        bool bSrgb = false;
        TextureCompressionSettings Compression = TC_Default;
        TextureGroup LodGroup = TEXTUREGROUP_World;
        TextureFilter Filter = TF_Default;
        FString CompressionStr, LodGroupStr, FilterStr;

        if (bHasSrgb && !Params->TryGetBoolField(TEXT("srgb"), bSrgb))
        {
            OutError = TEXT("configure_texture: invalid_value_type: 'srgb' must be a boolean");
            return nullptr;
        }
        if (bHasCompression)
        {
            if (!Params->TryGetStringField(TEXT("compression"), CompressionStr))
            {
                OutError = TEXT("configure_texture: invalid_value_type: 'compression' must be a string");
                return nullptr;
            }
            if (!ParseCompression(CompressionStr, Compression))
            {
                OutError = FString::Printf(
                    TEXT("configure_texture: unknown_enum_value: 'compression'='%s' (not a valid TextureCompressionSettings value)"), *CompressionStr);
                return nullptr;
            }
        }
        if (bHasLodGroup)
        {
            if (!Params->TryGetStringField(TEXT("lod_group"), LodGroupStr))
            {
                OutError = TEXT("configure_texture: invalid_value_type: 'lod_group' must be a string");
                return nullptr;
            }
            if (!ParseLodGroup(LodGroupStr, LodGroup))
            {
                OutError = FString::Printf(
                    TEXT("configure_texture: unknown_enum_value: 'lod_group'='%s' (not a valid TextureGroup value)"), *LodGroupStr);
                return nullptr;
            }
        }
        if (bHasFilter)
        {
            if (!Params->TryGetStringField(TEXT("filter"), FilterStr))
            {
                OutError = TEXT("configure_texture: invalid_value_type: 'filter' must be a string");
                return nullptr;
            }
            if (!ParseFilter(FilterStr, Filter))
            {
                OutError = FString::Printf(
                    TEXT("configure_texture: unknown_enum_value: 'filter'='%s' (not in [Nearest, Bilinear, Trilinear, Default])"), *FilterStr);
                return nullptr;
            }
        }

        bool bCompress = true;
        Params->TryGetBoolField(TEXT("compress"), bCompress);

        // ---- Mutation: PreEdit / Modify / set / PostEdit ---------------------------
        Tex->PreEditChange(nullptr);
        Tex->Modify();

        TSharedPtr<FJsonObject> Applied = MakeShared<FJsonObject>();
        if (bHasSrgb)        { Tex->SRGB = bSrgb;                      Applied->SetBoolField(TEXT("srgb"), bSrgb); }
        if (bHasCompression) { Tex->CompressionSettings = Compression;  Applied->SetStringField(TEXT("compression"), CompressionStr); }
        if (bHasLodGroup)    { Tex->LODGroup = LodGroup;               Applied->SetStringField(TEXT("lod_group"), LodGroupStr); }
        if (bHasFilter)      { Tex->Filter = Filter;                   Applied->SetStringField(TEXT("filter"), FilterStr); }

        FPropertyChangedEvent EmptyEvent(nullptr);
        Tex->PostEditChangeProperty(EmptyEvent);

        if (bCompress)
        {
            Tex->UpdateResource();
        }

        if (!UEditorAssetLibrary::SaveLoadedAsset(Tex))
        {
            OutError = TEXT("configure_texture: save_failed: UEditorAssetLibrary::SaveLoadedAsset returned false");
            return nullptr;
        }

        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("path"), Path);
        Result->SetObjectField(TEXT("applied"), Applied);
        Result->SetStringField(TEXT("message"), TEXT("Settings applied; resource rebuilt and saved."));
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ConfigureTexture()
{
    return MakeShared<FHandler_ConfigureTexture>();
}
