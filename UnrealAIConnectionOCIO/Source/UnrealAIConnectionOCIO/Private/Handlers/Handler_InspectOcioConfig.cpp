// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// inspect_ocio_config - read-only inspector for a UOpenColorIOConfiguration asset.
// Reports the configuration file path, the project-curated Desired* color spaces /
// display-views and the OCIO Context map (always, from UPROPERTYs), plus the full set
// of color spaces / displays / views enumerated from the underlying .ocio file via the
// OpenColorIO wrapper (when the library is available and the file loaded).
//
// Discovery tool: pair with find_assets to locate OCIO configs, then this to learn the
// available color spaces and display-views before wiring up a color conversion.
//
// Error format: "inspect_ocio_config: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, asset_not_found, wrong_asset_type.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EditorAssetLibrary.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "OpenColorIOConfiguration.h"
#include "OpenColorIOColorSpace.h"
#if WITH_OCIO
#include "OpenColorIOWrapper.h"
#endif

namespace
{
    // Convert a TArray<FString> to a sorted JSON string array. Sorting gives
    // stable output across calls - useful for snapshot diffs and LLM
    // pattern-matching.
    TArray<TSharedPtr<FJsonValue>> StringsToSortedJsonArray(TArray<FString> Values)
    {
        Values.Sort([](const FString& A, const FString& B) {
            return A.Compare(B) < 0;
        });
        TArray<TSharedPtr<FJsonValue>> Result;
        Result.Reserve(Values.Num());
        for (const FString& V : Values)
        {
            Result.Add(MakeShared<FJsonValueString>(V));
        }
        return Result;
    }
}

class FHandler_InspectOcioConfig : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("inspect_ocio_config"); }

    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("inspect_ocio_config: missing_required_field: 'path' is required");
            return nullptr;
        }

        FString InputPath;
        if (!Params->TryGetStringField(TEXT("path"), InputPath) || InputPath.IsEmpty())
        {
            OutError = TEXT("inspect_ocio_config: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }

        // --- load and cast asset --------------------------------------------

        const FString ObjectPath = UCMCPAssetPath::ToObjectPath(InputPath);
        const FString PackagePath = UCMCPAssetPath::ToPackagePath(InputPath);

        // Load via UEditorAssetLibrary (resolves the registry + loads from disk if the
        // package is not yet in memory) then cast - same pattern as
        // Handler_InspectNdisplayConfig. A typed LoadObject<T> can return null on a
        // not-yet-loaded asset and would then misreport a right-type asset as wrong_asset_type.
        UObject* LoadedAsset = UEditorAssetLibrary::LoadAsset(ObjectPath);
        if (!LoadedAsset)
        {
            OutError = FString::Printf(
                TEXT("inspect_ocio_config: asset_not_found: '%s' is not in the asset registry"),
                *InputPath);
            return nullptr;
        }

        UOpenColorIOConfiguration* Config = Cast<UOpenColorIOConfiguration>(LoadedAsset);
        if (!Config)
        {
            OutError = FString::Printf(
                TEXT("inspect_ocio_config: wrong_asset_type: '%s' is a %s, not a UOpenColorIOConfiguration"),
                *InputPath, *LoadedAsset->GetClass()->GetName());
            return nullptr;
        }

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("name"), Config->GetName());
        Out->SetStringField(TEXT("package_path"), PackagePath);
        Out->SetStringField(TEXT("config_file"), Config->ConfigurationFile.FilePath);

        // Project-curated "Desired" color spaces (the subset the asset exposes to
        // the engine). Always available from the UPROPERTY, even without the library.
        {
            TArray<TSharedPtr<FJsonValue>> DesiredColorSpaces;
            DesiredColorSpaces.Reserve(Config->DesiredColorSpaces.Num());
            for (const FOpenColorIOColorSpace& CS : Config->DesiredColorSpaces)
            {
                TSharedPtr<FJsonObject> Entry = MakeShared<FJsonObject>();
                Entry->SetStringField(TEXT("name"), CS.ColorSpaceName);
                Entry->SetStringField(TEXT("family"), CS.FamilyName);
                DesiredColorSpaces.Add(MakeShared<FJsonValueObject>(Entry));
            }
            Out->SetArrayField(TEXT("desired_color_spaces"), DesiredColorSpaces);
        }

        // Project-curated "Desired" display-views.
        {
            TArray<TSharedPtr<FJsonValue>> DesiredDisplayViews;
            DesiredDisplayViews.Reserve(Config->DesiredDisplayViews.Num());
            for (const FOpenColorIODisplayView& DV : Config->DesiredDisplayViews)
            {
                TSharedPtr<FJsonObject> Entry = MakeShared<FJsonObject>();
                Entry->SetStringField(TEXT("display"), DV.Display);
                Entry->SetStringField(TEXT("view"), DV.View);
                DesiredDisplayViews.Add(MakeShared<FJsonValueObject>(Entry));
            }
            Out->SetArrayField(TEXT("desired_display_views"), DesiredDisplayViews);
        }

        // OCIO context key-value map (shot-specific look overrides). UPROPERTY.
        {
            TSharedPtr<FJsonObject> ContextObj = MakeShared<FJsonObject>();
            for (const TPair<FString, FString>& Pair : Config->Context)
            {
                ContextObj->SetStringField(Pair.Key, Pair.Value);
            }
            Out->SetObjectField(TEXT("context"), ContextObj);
        }

        // Full enumeration of every color space / display / view in the .ocio file.
        // This requires the OpenColorIO library (WITH_OCIO) and a successfully loaded
        // config wrapper. Degrade gracefully: if either is unavailable we still return
        // the Desired*/Context data above, emit empty arrays here, and attach a note.
        TArray<TSharedPtr<FJsonValue>> AllColorSpaces;
        TArray<TSharedPtr<FJsonValue>> AllDisplays;
        TSharedPtr<FJsonObject> AllViews = MakeShared<FJsonObject>();
        FString Note;

#if WITH_OCIO
        // GetOrCreateConfigWrapper() forces a load if the config was not auto-loaded
        // (e.g. cooked / non-editor contexts), which is exactly what we want here.
        if (FOpenColorIOWrapperConfig* Wrapper = Config->GetOrCreateConfigWrapper())
        {
            TArray<FString> ColorSpaceNames;
            const int32 NumColorSpaces = Wrapper->GetNumColorSpaces();
            ColorSpaceNames.Reserve(NumColorSpaces);
            for (int32 Index = 0; Index < NumColorSpaces; ++Index)
            {
                ColorSpaceNames.Add(Wrapper->GetColorSpaceName(Index));
            }
            AllColorSpaces = StringsToSortedJsonArray(MoveTemp(ColorSpaceNames));

            TArray<FString> DisplayNames;
            const int32 NumDisplays = Wrapper->GetNumDisplays();
            DisplayNames.Reserve(NumDisplays);
            for (int32 DisplayIndex = 0; DisplayIndex < NumDisplays; ++DisplayIndex)
            {
                DisplayNames.Add(Wrapper->GetDisplayName(DisplayIndex));
            }

            // Per-display view lists, keyed by display name. Sort each list for
            // stable output; the display keys themselves come from the (sorted)
            // all_displays array.
            for (const FString& DisplayName : DisplayNames)
            {
                TArray<FString> ViewNames;
                const int32 NumViews = Wrapper->GetNumViews(*DisplayName);
                ViewNames.Reserve(NumViews);
                for (int32 ViewIndex = 0; ViewIndex < NumViews; ++ViewIndex)
                {
                    ViewNames.Add(Wrapper->GetViewName(*DisplayName, ViewIndex));
                }
                AllViews->SetArrayField(DisplayName, StringsToSortedJsonArray(MoveTemp(ViewNames)));
            }

            AllDisplays = StringsToSortedJsonArray(MoveTemp(DisplayNames));
        }
        else
        {
            Note = TEXT("OCIO config wrapper unavailable (the .ocio file may have failed to load); "
                        "all_color_spaces / all_displays / all_views are empty. The desired_color_spaces, "
                        "desired_display_views and context fields above come from the asset and are unaffected.");
        }
#else
        Note = TEXT("Built without the OpenColorIO library (WITH_OCIO=0); all_color_spaces / all_displays / "
                    "all_views are empty. The desired_color_spaces, desired_display_views and context fields "
                    "above come from the asset and are unaffected.");
#endif

        Out->SetArrayField(TEXT("all_color_spaces"), AllColorSpaces);
        Out->SetArrayField(TEXT("all_displays"), AllDisplays);
        Out->SetObjectField(TEXT("all_views"), AllViews);
        if (!Note.IsEmpty())
        {
            Out->SetStringField(TEXT("note"), Note);
        }

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_InspectOcioConfig()
{
    return MakeShared<FHandler_InspectOcioConfig>();
}
