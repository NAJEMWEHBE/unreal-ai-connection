// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// sequence_snapshot - crash-safety checkpoint: duplicate the current editor
// level (its UWorld asset) and optionally named Level Sequence assets into a
// timestamped folder under /Game/_Snapshots/, so a risky edit can be rolled
// back without losing hours of work.
//
// Verified against UE 5.7 source:
//   GEditor->GetEditorWorldContext(bool)         -- UnrealEd/Classes/Editor/EditorEngine.h:2525
//   FWorldContext::World() const                 -- Engine/Classes/Engine/Engine.h:460
//   UObject::GetOutermost() const -> UPackage*   -- CoreUObject/Public/UObject/UObjectBaseUtility.h:505
//   UObject::GetName() const -> FString          -- CoreUObject/Public/UObject/UObjectBaseUtility.h:439
//   FDateTime::Now() static                      -- Core/Public/Misc/DateTime.h:683
//   FDateTime::ToString(const TCHAR*) const      -- Core/Public/Misc/DateTime.h:527
//     Format tokens: %Y=year, %m=month, %d=day, %H=hour, %M=minute, %S=second
//   UEditorAssetSubsystem::DuplicateAsset(Source, Dest) -> UObject*
//                                                -- UnrealEd/Public/Subsystems/EditorAssetSubsystem.h:185
//   GEditor->GetEditorSubsystem<T>() template    -- UnrealEd/Classes/Editor/EditorEngine.h:3292
//
// is_mutating = false: DuplicateAsset creates new assets; it does not edit
// actor state or level state and must not be wrapped in a FScopedTransaction.
//
// Error format: "sequence_snapshot: <error_code>: <detail>".
// Stable error codes: no_world, no_level_path, duplicate_failed, invalid_field.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Engine/World.h"
#include "Misc/DateTime.h"
#include "Subsystems/EditorAssetSubsystem.h"

namespace
{
    // Build a clean /Game/-rooted asset path for a duplicate.
    // SourceAssetPath is the package name of the source (e.g. /Game/Maps/MyLevel).
    // DestFolder is the destination /Game/ folder (e.g. /Game/_Snapshots/snap_20260530_123456).
    // Returns the full destination path (e.g. /Game/_Snapshots/snap_20260530_123456/MyLevel).
    static FString BuildDestPath(const FString& SourceAssetPath, const FString& DestFolder)
    {
        // Extract the leaf name from SourceAssetPath (everything after the last '/').
        int32 LastSlash = INDEX_NONE;
        SourceAssetPath.FindLastChar(TEXT('/'), LastSlash);
        const FString LeafName = (LastSlash != INDEX_NONE)
            ? SourceAssetPath.Mid(LastSlash + 1)
            : SourceAssetPath;

        // Strip any .Name suffix the caller may have appended (package paths should
        // not have it, but guard defensively).
        int32 DotIndex = INDEX_NONE;
        const FString CleanLeaf = LeafName.FindLastChar(TEXT('.'), DotIndex)
            ? LeafName.Left(DotIndex)
            : LeafName;

        return DestFolder / CleanLeaf;
    }
}

class FHandler_SequenceSnapshot : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("sequence_snapshot"); }

    // Creates new assets only  -  does not touch actor/level undo state.
    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // label (optional, default "snapshot")
        FString Label = TEXT("snapshot");
        if (Params.IsValid())
        {
            FString ParamLabel;
            if (Params->TryGetStringField(TEXT("label"), ParamLabel) && !ParamLabel.IsEmpty())
            {
                // Sanitize: replace characters that are illegal in UE package path segments.
                // Keep alphanumeric, underscore, hyphen.
                FString Sanitized;
                for (TCHAR Ch : ParamLabel)
                {
                    if (FChar::IsAlnum(Ch) || Ch == TEXT('_') || Ch == TEXT('-'))
                    {
                        Sanitized.AppendChar(Ch);
                    }
                    else
                    {
                        Sanitized.AppendChar(TEXT('_'));
                    }
                }
                if (!Sanitized.IsEmpty())
                {
                    Label = Sanitized;
                }
            }
        }

        // Validate label  -  must not be empty after sanitization (already guaranteed above).
        if (Label.IsEmpty())
        {
            OutError = TEXT("sequence_snapshot: invalid_field: 'label' resolved to an empty string after sanitization");
            return nullptr;
        }

        // Collect optional sequence_paths array.
        TArray<FString> SequencePaths;
        if (Params.IsValid())
        {
            const TArray<TSharedPtr<FJsonValue>>* SeqArr = nullptr;
            if (Params->TryGetArrayField(TEXT("sequence_paths"), SeqArr) && SeqArr)
            {
                for (const TSharedPtr<FJsonValue>& V : *SeqArr)
                {
                    FString S;
                    if (V.IsValid() && V->TryGetString(S) && !S.IsEmpty())
                    {
                        SequencePaths.Add(S);
                    }
                }
            }
        }

        // Validate each sequence path has a /Game/ prefix.
        for (const FString& SP : SequencePaths)
        {
            if (!SP.StartsWith(TEXT("/Game/")))
            {
                OutError = FString::Printf(
                    TEXT("sequence_snapshot: invalid_field: sequence_paths entry '%s' must start with /Game/"),
                    *SP);
                return nullptr;
            }
        }

        // Resolve the current editor world.
        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("sequence_snapshot: no_world: no editor world is currently open");
            return nullptr;
        }

        // Derive the world's package path (e.g. /Game/Maps/MyLevel).
        UPackage* WorldPkg = World->GetOutermost();
        if (!WorldPkg)
        {
            OutError = TEXT("sequence_snapshot: no_level_path: current world has no outer package");
            return nullptr;
        }
        const FString LevelPackagePath = WorldPkg->GetName();
        if (!LevelPackagePath.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(
                TEXT("sequence_snapshot: no_level_path: current level package '%s' is not under /Game/  -  transient or unsaved levels cannot be duplicated"),
                *LevelPackagePath);
            return nullptr;
        }

        // Build the timestamped destination folder:
        //   /Game/_Snapshots/<label>_<YYYYmmdd_HHMMSS>
        const FString Timestamp = FDateTime::Now().ToString(TEXT("%Y%m%d_%H%M%S"));
        const FString DestFolder = FString::Printf(
            TEXT("/Game/_Snapshots/%s_%s"), *Label, *Timestamp);

        // Obtain the EditorAssetSubsystem.
        UEditorAssetSubsystem* AssetSub =
            GEditor ? GEditor->GetEditorSubsystem<UEditorAssetSubsystem>() : nullptr;
        if (!AssetSub)
        {
            OutError = TEXT("sequence_snapshot: no_world: UEditorAssetSubsystem is unavailable (editor not fully initialized?)");
            return nullptr;
        }

        // --- Duplicate the level asset ---
        const FString LevelDestPath = BuildDestPath(LevelPackagePath, DestFolder);
        UObject* DupLevel = AssetSub->DuplicateAsset(LevelPackagePath, LevelDestPath);
        if (!DupLevel)
        {
            OutError = FString::Printf(
                TEXT("sequence_snapshot: duplicate_failed: could not duplicate level '%s' to '%s'"),
                *LevelPackagePath, *LevelDestPath);
            return nullptr;
        }

        // --- Duplicate each sequence asset ---
        TArray<TSharedPtr<FJsonValue>> SequenceResults;
        int32 SequencesSnapped = 0;

        for (const FString& SeqPath : SequencePaths)
        {
            // Strip any trailing .AssetName suffix so DuplicateAsset gets a clean
            // package path (the subsystem accepts both forms but a clean path is safer).
            FString CleanSeqPath = SeqPath;
            {
                int32 DotIdx = INDEX_NONE;
                if (CleanSeqPath.FindLastChar(TEXT('.'), DotIdx))
                {
                    CleanSeqPath = CleanSeqPath.Left(DotIdx);
                }
            }

            const FString SeqDestPath = BuildDestPath(CleanSeqPath, DestFolder);
            UObject* DupSeq = AssetSub->DuplicateAsset(CleanSeqPath, SeqDestPath);

            TSharedRef<FJsonObject> Entry = MakeShared<FJsonObject>();
            Entry->SetStringField(TEXT("source"), CleanSeqPath);
            Entry->SetStringField(TEXT("snapshot"), SeqDestPath);
            Entry->SetBoolField(TEXT("ok"), DupSeq != nullptr);
            if (!DupSeq)
            {
                Entry->SetStringField(TEXT("error"),
                    FString::Printf(TEXT("DuplicateAsset returned null for '%s'"), *CleanSeqPath));
            }
            else
            {
                ++SequencesSnapped;
            }
            SequenceResults.Add(MakeShared<FJsonValueObject>(Entry));
        }

        // Build result.
        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("snapshot_folder"), DestFolder);
        Result->SetStringField(TEXT("level_snapshot"), LevelDestPath);
        Result->SetArrayField(TEXT("sequences"), SequenceResults);
        Result->SetNumberField(TEXT("count"), 1 + SequencesSnapped);
        Result->SetStringField(TEXT("note"),
            TEXT("Snapshot created. Run save_dirty_assets to flush new packages to disk, "
                 "or the duplicates will be lost on editor restart."));
        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SequenceSnapshot()
{
    return MakeShared<FHandler_SequenceSnapshot>();
}
