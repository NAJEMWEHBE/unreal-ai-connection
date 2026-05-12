// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// save_dirty_assets - persist every in-memory-modified asset + map to disk.
// Closes the gap where edit-side tools (set_actor_property, set_mi_parameter,
// edit_widget_tree, configure_texture, etc.) mutated UObjects but left them
// dirty in memory — callers had no batch save affordance and the editor's
// "Save All" UI button was unreachable from MCP.
//
// UE 5.7 surface used:
//   - UEditorLoadingAndSavingUtils::SaveDirtyPackages(bSaveMapPackages,
//     bSaveContentPackages) at EditorLoadingAndSavingUtils.h. Bool flags
//     control whether maps + content packages are saved (default both
//     true, matching the editor's "Save All" behaviour). Returns true on
//     success — does not enumerate per-asset results because the underlying
//     API is intentionally coarse-grained.
//
// Error format: this handler has no error paths — Handle always returns a
// successful FJsonObject result. The OutError parameter is unused (marked
// /*OutError*/). save_dirty_assets is safe to call even when nothing is
// dirty (it's a no-op).

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "EditorLoadingAndSavingUtils.h"

class FHandler_SaveDirtyAssets : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("save_dirty_assets"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& /*OutError*/) override
    {
        // Both flags default to true (matches editor "Save All").
        bool bSaveMapPackages = true;
        bool bSaveContentPackages = true;
        if (Params.IsValid())
        {
            Params->TryGetBoolField(TEXT("include_levels"), bSaveMapPackages);
            Params->TryGetBoolField(TEXT("include_content"), bSaveContentPackages);
        }

        // UEditorLoadingAndSavingUtils::SaveDirtyPackages is the same call
        // the editor's "File > Save All" menu invokes. It iterates the
        // package transient state, finds dirty UPackages, and writes them
        // through UPackage::SavePackage. With bPromptToConfirm=false it
        // proceeds without UI; checked-out / non-writable files surface
        // as failures in the underlying log but the call returns true if
        // at least the attempt was made.
        const bool bAttempted = UEditorLoadingAndSavingUtils::SaveDirtyPackages(
            bSaveMapPackages,
            bSaveContentPackages);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), bAttempted);
        Out->SetBoolField(TEXT("include_levels"), bSaveMapPackages);
        Out->SetBoolField(TEXT("include_content"), bSaveContentPackages);
        Out->SetStringField(TEXT("note"), TEXT("SaveDirtyPackages is coarse-grained — it returns true if the save loop completed but does not enumerate per-asset results. Check the UE Output Log for any individual SavePackage failures (typically read-only files or source-control checkouts)."));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_SaveDirtyAssets()
{
    return MakeShared<FHandler_SaveDirtyAssets>();
}
