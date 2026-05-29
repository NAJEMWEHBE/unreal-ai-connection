// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// build_lighting - invoke a static-lighting build on the active editor world.
//
// UE 5.7 surface (verified against engine source):
//   FEditorBuildUtils::EditorBuild(UWorld* InWorld, FName Id,
//     bool bAllowLightingDialog = true) -> bool
//   (Editor/UnrealEd/Public/EditorBuildUtils.h:149). The build "Id" selects
//   which build runs; FBuildOptions::BuildLighting (same header, line 22) is
//   the static-named FName for a lighting build. We pass
//   bAllowLightingDialog=false so the call is non-interactive for automation.
//   The active world is GEditor->GetEditorWorldContext().World().
//
// IsMutating() is false: a lighting build updates baked lighting data on the
// world; it is not a discrete actor edit on the undo stack, so an
// FScopedTransaction wrapper would be discarded.
//
// Error format: "build_lighting: <error_code>: <human-readable detail>".
// Stable error codes: editor_unavailable.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EditorBuildUtils.h"

class FHandler_BuildLighting : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("build_lighting"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& OutError) override
    {
        UWorld* World = GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
        if (!World)
        {
            OutError = TEXT("build_lighting: editor_unavailable: no editor world available");
            return nullptr;
        }

        // bAllowLightingDialog=false keeps the call non-interactive. EditorBuild
        // returns true if the build kicked off (it can run asynchronously for
        // large levels), so we surface that as ok.
        const bool bBuilt = FEditorBuildUtils::EditorBuild(
            World, FBuildOptions::BuildLighting, /*bAllowLightingDialog=*/false);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), bBuilt);
        Out->SetStringField(TEXT("note"), TEXT("lighting build invoked; may take time for large levels"));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_BuildLighting()
{
    return MakeShared<FHandler_BuildLighting>();
}
