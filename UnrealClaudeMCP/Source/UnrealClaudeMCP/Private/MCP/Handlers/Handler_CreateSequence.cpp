// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_sequence - create + initialize a new ULevelSequence asset.
//
// Error format: "create_sequence: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field, invalid_path,
// invalid_asset_name, dest_exists, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorAssetLibrary.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Modules/ModuleManager.h"
#include "LevelSequence.h"
#include "MovieScene.h"
#include "Misc/FrameRate.h"
#include "Misc/FrameNumber.h"
#include "MCP/Handlers/AssetPathUtil.h"

class FHandler_CreateSequence : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_sequence"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- validate required params ---------------------------------------

        if (!Params.IsValid())
        {
            OutError = TEXT("create_sequence: missing_required_field: 'path' and 'name' are required");
            return nullptr;
        }

        FString DestPath;
        if (!Params->TryGetStringField(TEXT("path"), DestPath) || DestPath.IsEmpty())
        {
            OutError = TEXT("create_sequence: missing_required_field: 'path' is required and must not be empty");
            return nullptr;
        }
        if (!DestPath.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(
                TEXT("create_sequence: invalid_path: 'path' must start with /Game/, got '%s'"),
                *DestPath);
            return nullptr;
        }
        // Trim trailing slash so we always concatenate `Path + "/" + Name`.
        if (DestPath.EndsWith(TEXT("/")))
        {
            DestPath = DestPath.LeftChop(1);
        }

        FString Name;
        if (!Params->TryGetStringField(TEXT("name"), Name) || Name.IsEmpty())
        {
            OutError = TEXT("create_sequence: missing_required_field: 'name' is required and must not be empty");
            return nullptr;
        }
        if (!UCMCPAssetPath::IsValidLeafName(Name))
        {
            OutError = FString::Printf(
                TEXT("create_sequence: invalid_asset_name: 'name' must be a non-empty string with no '/' or '.', got '%s'"),
                *Name);
            return nullptr;
        }

        // --- optional params ------------------------------------------------

        double DisplayRateFps = 30.0;
        Params->TryGetNumberField(TEXT("display_rate_fps"), DisplayRateFps);
        if (DisplayRateFps <= 0.0)
        {
            // Defensive — degenerate frame rates would cause UE math to NaN.
            DisplayRateFps = 30.0;
        }

        int32 PlaybackEndFrames = 240;
        Params->TryGetNumberField(TEXT("playback_end_frames"), PlaybackEndFrames);
        if (PlaybackEndFrames < 1)
        {
            PlaybackEndFrames = 1;
        }

        // --- check destination ----------------------------------------------

        const FString DestObjectPath = DestPath + TEXT("/") + Name + TEXT(".") + Name;
        if (UEditorAssetLibrary::DoesAssetExist(DestObjectPath))
        {
            OutError = FString::Printf(
                TEXT("create_sequence: dest_exists: an asset already exists at '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        // --- create the asset -----------------------------------------------
        //
        // IAssetTools::CreateAsset auto-resolves the factory by class when the
        // factory pointer is null. UE matches the factory by AssetClass.
        // Confirmed at IAssetTools.h:348 (UE 5.7).
        FAssetToolsModule& AssetToolsModule =
            FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
        IAssetTools& AssetTools = AssetToolsModule.Get();

        UObject* NewAsset = AssetTools.CreateAsset(
            Name, DestPath, ULevelSequence::StaticClass(), nullptr);
        if (!NewAsset)
        {
            OutError = FString::Printf(
                TEXT("create_sequence: create_failed: UAssetTools::CreateAsset returned null for '%s'"),
                *DestObjectPath);
            return nullptr;
        }

        ULevelSequence* Sequence = Cast<ULevelSequence>(NewAsset);
        if (!Sequence)
        {
            OutError = FString::Printf(
                TEXT("create_sequence: create_failed: created asset is %s, not ULevelSequence"),
                *NewAsset->GetClass()->GetName());
            return nullptr;
        }

        // Initialize() creates the underlying UMovieScene.
        // Verified at LevelSequence.h:38 (UE 5.7).
        Sequence->Initialize();
        UMovieScene* Scene = Sequence->GetMovieScene();
        if (!Scene)
        {
            OutError = TEXT("create_sequence: create_failed: Sequence->Initialize() did not produce a MovieScene");
            return nullptr;
        }

        // --- configure frame rate + playback range --------------------------
        //
        // FFrameRate uses Numerator/Denominator integer rationals so non-integer
        // rates like 23.976 (24000/1001) and 29.97 (30000/1001) round-trip
        // exactly. We use a 1000 denominator for arbitrary user input — this
        // gives ~3 decimal places of precision (30.0 → 30000/1000) and avoids
        // floating-point imprecision in the rational form.
        const FFrameRate DisplayRate(
            static_cast<uint32>(FMath::RoundToInt(DisplayRateFps * 1000.0)), 1000);
        Scene->SetDisplayRate(DisplayRate);

        // Convert end frames (in display rate) to ticks (in tick resolution).
        // FFrameRate::TransformTime is the canonical converter.
        const FFrameRate TickResolution = Scene->GetTickResolution();
        const FFrameTime EndTimeInTicks = FFrameRate::TransformTime(
            FFrameTime(FFrameNumber(PlaybackEndFrames)),
            DisplayRate, TickResolution);
        Scene->SetPlaybackRange(0, EndTimeInTicks.FrameNumber.Value);

        // --- save -----------------------------------------------------------

        UEditorAssetLibrary::SaveAsset(DestObjectPath, /*bForceSave=*/false);

        // --- build result ---------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("asset_path"), DestObjectPath);
        Out->SetStringField(TEXT("package_path"), DestPath + TEXT("/") + Name);
        Out->SetNumberField(TEXT("display_rate_fps"), DisplayRate.AsDecimal());

        TSharedRef<FJsonObject> RangeJson = MakeShared<FJsonObject>();
        RangeJson->SetNumberField(TEXT("start_frames"), 0.0);
        RangeJson->SetNumberField(TEXT("end_frames"),
            static_cast<double>(EndTimeInTicks.FrameNumber.Value));
        Out->SetObjectField(TEXT("playback_range"), RangeJson);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateSequence()
{
    return MakeShared<FHandler_CreateSequence>();
}
