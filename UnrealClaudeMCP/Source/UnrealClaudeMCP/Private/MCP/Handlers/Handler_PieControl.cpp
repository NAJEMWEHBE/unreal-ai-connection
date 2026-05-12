// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// pie_control - start / stop / query Play-In-Editor sessions from MCP.
// Closes the canonical "did my edit actually work?" feedback loop: the
// plugin previously could edit assets + actors but had no way to
// validate live behaviour. LLM can now scaffold a gameplay change,
// trigger PIE, observe the running state, then stop PIE — all without
// human keyboard input.
//
// UE 5.7 surface used:
//   - GEditor->IsPlayingSessionInEditor() — canonical state query;
//     supersedes the older GEditor->PlayWorld != nullptr check (still
//     works but less reliable across PIE start/end transitions).
//     (Flagged by the pre-flight multi-agent review as a BLOCKER if
//     the older form had been used.)
//   - GEditor->RequestPlaySession(FRequestPlaySessionParams) for start.
//     FRequestPlaySessionParams is the flexible canonical PIE-launch
//     API; ULevelEditorSubsystem::EditorPlaySimulate() is a high-level
//     wrapper around it. We use the underlying API for explicit
//     simulate-vs-play mode control.
//   - GEditor->RequestEndPlayMap() for stop. Defers to the next tick;
//     does NOT block the game thread.
//
// Threading: all GEditor calls are game-thread only. Handler dispatcher
// runs on the FTSTicker callback (game thread) per ARCHITECTURE.md, so
// the call is safe.
//
// Error format: "pie_control: <error_code>: <human-readable detail>".
// Stable error codes: missing_required_field (when action is absent),
// invalid_action (when action is not start/stop/query),
// invalid_mode (when mode is not play/simulate on a start action),
// editor_unavailable (GEditor null — non-editor build),
// pie_already_active (action=start while session running),
// pie_not_active (action=stop while no session).

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Editor/EditorEngine.h"

class FHandler_PieControl : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("pie_control"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!GEditor)
        {
            OutError = TEXT("pie_control: editor_unavailable: GEditor is null (call from editor build only)");
            return nullptr;
        }

        if (!Params.IsValid())
        {
            OutError = TEXT("pie_control: missing_required_field: 'action' is required (one of: start, stop, query)");
            return nullptr;
        }

        FString Action;
        if (!Params->TryGetStringField(TEXT("action"), Action) || Action.IsEmpty())
        {
            OutError = TEXT("pie_control: missing_required_field: 'action' is required (one of: start, stop, query)");
            return nullptr;
        }

        const bool bPieActive = GEditor->IsPlayingSessionInEditor();

        if (Action == TEXT("query"))
        {
            TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
            Out->SetBoolField(TEXT("ok"), true);
            Out->SetStringField(TEXT("action"), TEXT("query"));
            Out->SetBoolField(TEXT("is_playing"), bPieActive);
            // bIsSimulatingInEditor flag distinguishes simulate-mode from
            // standalone play sessions while a session is active.
            Out->SetBoolField(TEXT("is_simulating"), GEditor->bIsSimulatingInEditor);
            return Out;
        }

        if (Action == TEXT("stop"))
        {
            if (!bPieActive)
            {
                OutError = TEXT("pie_control: pie_not_active: no PIE session running; nothing to stop");
                return nullptr;
            }
            // Defers end-play to the next editor tick. Safe to call from
            // game-thread handlers; PIE shutdown happens asynchronously.
            GEditor->RequestEndPlayMap();
            TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
            Out->SetBoolField(TEXT("ok"), true);
            Out->SetStringField(TEXT("action"), TEXT("stop"));
            Out->SetStringField(TEXT("note"), TEXT("RequestEndPlayMap is asynchronous; query pie_control with action=query on the next tick to confirm shutdown."));
            return Out;
        }

        if (Action == TEXT("start"))
        {
            if (bPieActive)
            {
                OutError = TEXT("pie_control: pie_already_active: a PIE session is already running; call action=stop first");
                return nullptr;
            }

            FString Mode = TEXT("play");
            Params->TryGetStringField(TEXT("mode"), Mode);
            if (Mode != TEXT("play") && Mode != TEXT("simulate"))
            {
                OutError = FString::Printf(
                    TEXT("pie_control: invalid_mode: '%s' must be 'play' or 'simulate'"),
                    *Mode);
                return nullptr;
            }

            FRequestPlaySessionParams PlayParams;
            // Default destination is "use the active editor viewport for
            // PIE rendering" — the same affordance as the editor's
            // toolbar Play button. We do not override.
            //
            // Simulate-in-editor: bSimulateInEditor=true makes the world
            // tick without spawning a Player Controller / focused viewport.
            // Play: leave default (PlayInActiveViewport mode).
            const bool bSimulate = (Mode == TEXT("simulate"));
            if (bSimulate)
            {
                PlayParams.WorldType = EPlaySessionWorldType::SimulateInEditor;
            }

            GEditor->RequestPlaySession(PlayParams);

            TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
            Out->SetBoolField(TEXT("ok"), true);
            Out->SetStringField(TEXT("action"), TEXT("start"));
            Out->SetStringField(TEXT("mode"), Mode);
            Out->SetStringField(TEXT("note"), TEXT("RequestPlaySession is dispatched on the next editor tick; query pie_control with action=query on a subsequent tick to confirm the session is active."));
            return Out;
        }

        OutError = FString::Printf(
            TEXT("pie_control: invalid_action: '%s' must be one of: start, stop, query"),
            *Action);
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_PieControl()
{
    return MakeShared<FHandler_PieControl>();
}
