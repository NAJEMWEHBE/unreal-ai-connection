// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// create_level - create a brand-new empty level (UWorld) asset under /Game/.
//
// UE 5.7 surface (verified against engine source):
//   ULevelEditorSubsystem::NewLevel(const FString& AssetPath,
//     bool bIsPartitionedWorld = false) -> bool
//   (Editor/LevelEditor/Public/LevelEditorSubsystem.h:106). The subsystem is
//   fetched via GEditor->GetEditorSubsystem<ULevelEditorSubsystem>(). NewLevel
//   creates the level package at AssetPath and opens it in the editor. Returns
//   true on success.
//
// IsMutating() is false: this creates an asset (and switches the active level)
// rather than mutating actors on the editor undo stack, so an FScopedTransaction
// wrapper would be discarded. Level creation is not a Ctrl+Z step.
//
// Error format: "create_level: <error_code>: <human-readable detail>".
// Stable error codes: missing_params, missing_required_field, invalid_field,
// editor_unavailable, create_failed.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "LevelEditorSubsystem.h"

class FHandler_CreateLevel : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("create_level"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("create_level: missing_params: request had no params object");
            return nullptr;
        }

        FString Path;
        if (!Params->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
        {
            OutError = TEXT("create_level: missing_required_field: 'path' is required");
            return nullptr;
        }
        if (!Path.StartsWith(TEXT("/Game/")))
        {
            OutError = FString::Printf(
                TEXT("create_level: invalid_field: 'path' must start with /Game/, got '%s'"),
                *Path);
            return nullptr;
        }
        if (Path.Contains(TEXT("..")) || Path.Contains(TEXT("\\")))
        {
            OutError = FString::Printf(
                TEXT("create_level: invalid_field: 'path' must not contain '..' or backslash, got '%s'"),
                *Path);
            return nullptr;
        }

        // Strip a trailing slash so NewLevel doesn't create an empty-named
        // package (e.g. "/Game/Maps/" -> create at "/Game/Maps").
        Path.RemoveFromEnd(TEXT("/"));

        ULevelEditorSubsystem* LevelSS =
            GEditor ? GEditor->GetEditorSubsystem<ULevelEditorSubsystem>() : nullptr;
        if (!LevelSS)
        {
            OutError = TEXT("create_level: editor_unavailable: LevelEditorSubsystem not available");
            return nullptr;
        }

        // NewLevel creates the package at Path and opens it as the active level.
        if (!LevelSS->NewLevel(Path))
        {
            OutError = FString::Printf(
                TEXT("create_level: create_failed: ULevelEditorSubsystem::NewLevel returned false for '%s' (path may already exist or be invalid)"),
                *Path);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("path"), Path);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CreateLevel()
{
    return MakeShared<FHandler_CreateLevel>();
}
