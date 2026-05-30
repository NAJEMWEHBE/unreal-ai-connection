// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// export_actor_as_gltf - export selected or named actor(s) to a .gltf/.glb
// file for Blender/Aximmetry handoff. Wraps the Epic GLTFExporter plugin's
// public UGLTFExporter::ExportToGLTF static API which operates on a UWorld
// with an optional actor filter set.
//
// Verified against UE 5.7 source:
//   UGLTFExporter::ExportToGLTF(UObject*, const FString&,
//       const UGLTFExportOptions*, const TSet<AActor*>&,
//       FGLTFExportMessages&) -> bool              -- Exporters/GLTFExporter.h:52
//                                                  -- GLTFExporter.cpp:84
//   Object=nullptr auto-resolves to editor world  -- GLTFExporter.cpp:86-101
//   TSet<AActor*> empty = export all actors        -- GLTFExporter.cpp:115
//   FGLTFExportMessages::{Suggestions,Warnings,Errors} -- GLTFExporter.h:16-27
//   IGLTFExporterModule::IsAvailable()             -- GLTFExporterModule.h:39
//   GLTFEXPORTER_MODULE_NAME = "GLTFExporter"      -- GLTFExporterModule.h:9
//   USelection::GetSelectedObjects<T>(TArray<T*>&) -- Selection.h:298
//   GEditor->GetSelectedActors() -> USelection*    -- EditorEngine.h:1935
//   USelection::Num() const -> int32               -- Selection.h:86
//   FPaths::GetExtension(const FString&)           -- Misc/Paths.h:533
//   GEditor->GetEditorWorldContext().World()        -- EditorEngine.h (used in GLTFExporter.cpp:92)
//   UCMCP::ActorIdentity::Resolve(...)             -- MCP/ActorIdentity.h:44
//
// is_mutating = false: export writes to disk only, no actor/level state
// is touched; the dispatcher does NOT wrap this in a FScopedTransaction.
//
// Error format: "export_actor_as_gltf: <error_code>: <detail>".
// Stable error codes: no_actors, invalid_path, exporter_unavailable,
//                     export_failed.
// Also: ambiguous_actor (per UCMCP::ActorIdentity contract).

#include "MCP/MCPHandler.h"
#include "MCP/ActorIdentity.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "Exporters/GLTFExporter.h"
#include "GLTFExporterModule.h"
#include "Options/GLTFExportOptions.h"
#include "GameFramework/Actor.h"
#include "Misc/Paths.h"
#include "Selection.h"

namespace
{
    // Collect actor pointers from a JSON string-array field. Returns the
    // number of names that failed to resolve so the caller can decide whether
    // to abort or continue.
    static int32 ResolveActorList(
        const TArray<TSharedPtr<FJsonValue>>& Labels,
        TArray<AActor*>& OutActors,
        TArray<FString>& OutErrors)
    {
        int32 FailCount = 0;
        for (const TSharedPtr<FJsonValue>& V : Labels)
        {
            FString Label;
            if (!V.IsValid() || !V->TryGetString(Label) || Label.IsEmpty())
            {
                continue;
            }

            AActor* Actor = nullptr;
            TArray<FString> Ambiguous;
            const auto Res = UCMCP::ActorIdentity::Resolve(Label, Actor, Ambiguous);

            if (Res == UCMCP::ActorIdentity::EResolveResult::Ambiguous)
            {
                FString Candidates;
                for (int32 i = 0; i < Ambiguous.Num(); ++i)
                {
                    if (i > 0) { Candidates += TEXT(", "); }
                    Candidates += Ambiguous[i];
                }
                OutErrors.Add(FString::Printf(
                    TEXT("export_actor_as_gltf: ambiguous_actor: '%s' matched multiple actors: [%s]"),
                    *Label, *Candidates));
                ++FailCount;
            }
            else if (Res == UCMCP::ActorIdentity::EResolveResult::NotFound)
            {
                OutErrors.Add(FString::Printf(
                    TEXT("export_actor_as_gltf: no_actors: actor '%s' not found in the current level"),
                    *Label));
                ++FailCount;
            }
            else
            {
                OutActors.AddUnique(Actor);
            }
        }
        return FailCount;
    }
}

class FHandler_ExportActorAsGltf : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("export_actor_as_gltf"); }

    // Export to disk only  -  no actor or level state is modified.
    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("export_actor_as_gltf: no_actors: request had no params object");
            return nullptr;
        }

        // --- output_path (required) -------------------------------------------
        FString OutputPath;
        if (!Params->TryGetStringField(TEXT("output_path"), OutputPath) || OutputPath.IsEmpty())
        {
            OutError = TEXT("export_actor_as_gltf: invalid_path: 'output_path' is required and must be non-empty");
            return nullptr;
        }

        const FString Ext = FPaths::GetExtension(OutputPath).ToLower();
        if (Ext != TEXT("gltf") && Ext != TEXT("glb"))
        {
            OutError = FString::Printf(
                TEXT("export_actor_as_gltf: invalid_path: 'output_path' must end in .gltf or .glb, got '.%s'"),
                *Ext);
            return nullptr;
        }

        // --- Guard: GLTFExporter plugin must be loaded ------------------------
        if (!IGLTFExporterModule::IsAvailable())
        {
            OutError = TEXT("export_actor_as_gltf: exporter_unavailable: the GLTFExporter plugin is not loaded;"
                " enable it in the host project's .uproject and rebuild");
            return nullptr;
        }

        // --- Build the actor filter set ---------------------------------------
        // Priority:
        //   1. 'actors' array param (explicit list by label)
        //   2. 'selected_only' = true -> current editor selection
        //   3. Neither provided (or 'actors' is empty + selected_only false)
        //      -> empty TSet, which exports the entire visible level.

        TSet<AActor*> SelectedActors;
        TArray<FString> ResolveErrors;

        const TArray<TSharedPtr<FJsonValue>>* ActorsArr = nullptr;
        const bool bHaveActorsList =
            Params->TryGetArrayField(TEXT("actors"), ActorsArr) &&
            ActorsArr != nullptr &&
            ActorsArr->Num() > 0;

        if (bHaveActorsList)
        {
            TArray<AActor*> Resolved;
            const int32 FailCount = ResolveActorList(*ActorsArr, Resolved, ResolveErrors);

            if (FailCount > 0 && Resolved.Num() == 0)
            {
                // Every name failed  -  surface the first error and abort.
                OutError = ResolveErrors.IsValidIndex(0)
                    ? ResolveErrors[0]
                    : TEXT("export_actor_as_gltf: no_actors: all named actors failed to resolve");
                return nullptr;
            }

            for (AActor* A : Resolved)
            {
                SelectedActors.Add(A);
            }
        }
        else
        {
            bool bSelectedOnly = false;
            Params->TryGetBoolField(TEXT("selected_only"), bSelectedOnly);

            if (bSelectedOnly)
            {
                if (!GEditor)
                {
                    OutError = TEXT("export_actor_as_gltf: no_actors: GEditor is null; cannot read selection");
                    return nullptr;
                }

                TArray<AActor*> SelectionArr;
                GEditor->GetSelectedActors()->GetSelectedObjects<AActor>(SelectionArr);

                if (SelectionArr.Num() == 0)
                {
                    OutError = TEXT("export_actor_as_gltf: no_actors:"
                        " selected_only=true but no actors are currently selected in the editor");
                    return nullptr;
                }

                for (AActor* A : SelectionArr)
                {
                    if (A)
                    {
                        SelectedActors.Add(A);
                    }
                }
            }
            // else: both 'actors' and 'selected_only' absent/false -> empty set -> export whole level
        }

        // --- Run the export ---------------------------------------------------
        // Pass nullptr as Object so ExportToGLTF resolves to the editor world
        // (see GLTFExporter.cpp:86-101). The SelectedActors set filters which
        // actors are included; an empty set exports all actors in the level.
        // Build export options with material baking + texture export DISABLED. In an
        // editor with rendering enabled (CanEverRender()==true), the exporter's
        // material-bake path renders materials to textures and can access-violate on
        // some content (SanitizeExportOptions only auto-disables it when
        // !CanEverRender()). Disabling it here keeps the export robust (geometry +
        // material parameters, no render-baked textures) and avoids the GLTFCore fault.
        UGLTFExportOptions* Options = NewObject<UGLTFExportOptions>();
        Options->ResetToDefault();
        Options->BakeMaterialInputs = EGLTFMaterialBakeMode::Disabled;
        Options->TextureImageFormat = EGLTFTextureImageFormat::None;

        FGLTFExportMessages Messages;
        const bool bSuccess = UGLTFExporter::ExportToGLTF(
            /*Object=*/nullptr,
            OutputPath,
            Options,
            SelectedActors,
            Messages);

        if (!bSuccess)
        {
            // Collect glTF exporter errors into a single diagnostic string.
            FString Diag;
            for (const FString& E : Messages.Errors)
            {
                if (!Diag.IsEmpty()) { Diag += TEXT("; "); }
                Diag += E;
            }
            if (Diag.IsEmpty()) { Diag = TEXT("unknown export failure"); }
            OutError = FString::Printf(
                TEXT("export_actor_as_gltf: export_failed: %s"), *Diag);
            return nullptr;
        }

        // --- Build result object ----------------------------------------------
        TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
        Result->SetBoolField(TEXT("ok"), true);
        Result->SetStringField(TEXT("output_path"), OutputPath);
        Result->SetStringField(TEXT("format"), Ext);
        Result->SetNumberField(TEXT("actors_in_filter"), static_cast<double>(SelectedActors.Num()));

        // Attach any non-fatal warnings/suggestions the exporter emitted.
        if (Messages.Warnings.Num() > 0)
        {
            TArray<TSharedPtr<FJsonValue>> WarnArr;
            for (const FString& W : Messages.Warnings)
            {
                WarnArr.Add(MakeShared<FJsonValueString>(W));
            }
            Result->SetArrayField(TEXT("warnings"), WarnArr);
        }

        if (Messages.Suggestions.Num() > 0)
        {
            TArray<TSharedPtr<FJsonValue>> SuggArr;
            for (const FString& S : Messages.Suggestions)
            {
                SuggArr.Add(MakeShared<FJsonValueString>(S));
            }
            Result->SetArrayField(TEXT("suggestions"), SuggArr);
        }

        // Surface partial-resolution errors (some named actors found, some not)
        // as non-fatal warnings in the result.
        if (ResolveErrors.Num() > 0)
        {
            const TArray<TSharedPtr<FJsonValue>>* ExistingWarns = nullptr;
            // Merge into the existing warnings array if present.
            TArray<TSharedPtr<FJsonValue>> MergedWarns;
            if (Result->TryGetArrayField(TEXT("warnings"), ExistingWarns) && ExistingWarns)
            {
                MergedWarns = *ExistingWarns;
            }
            for (const FString& RE : ResolveErrors)
            {
                MergedWarns.Add(MakeShared<FJsonValueString>(RE));
            }
            Result->SetArrayField(TEXT("warnings"), MergedWarns);
        }

        FString Note;
        if (SelectedActors.Num() == 0)
        {
            Note = TEXT("Exported entire visible level (no actor filter applied).");
        }
        else
        {
            Note = FString::Printf(
                TEXT("Exported %d actor(s). Run get_viewport_screenshot to preview."),
                SelectedActors.Num());
        }
        Result->SetStringField(TEXT("note"), Note);

        return Result;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ExportActorAsGltf()
{
    return MakeShared<FHandler_ExportActorAsGltf>();
}
