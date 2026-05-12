// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// pie_control - start / stop / query Play-In-Editor sessions from MCP.
// Closes the canonical "did my edit actually work?" feedback loop: the
// plugin previously could edit assets + actors but had no way to
// validate live behaviour. LLM can now scaffold a gameplay change,
// trigger PIE, observe the running state, then stop PIE — all without
// human keyboard input.
//
// UE 5.7 surface used (cited against engine source):
//   - GEditor->IsPlayingSessionInEditor() — EditorEngine.h:1803,
//     returns true only when a PIE/SIE session is already RUNNING.
//     Goes false again the tick after RequestPlaySession; does NOT
//     reflect a queued-but-not-yet-started request.
//   - GEditor->IsPlaySessionRequestQueued() — EditorEngine.h:1806,
//     returns true when RequestPlaySession has been called but the
//     editor has not yet ticked StartQueuedPlaySessionRequest().
//   - GEditor->IsPlaySessionInProgress() — EditorEngine.h:1808,
//     OR of the two above. This is the correct guard for "should I
//     reject a new start request?" — closes the back-to-back
//     start-call race that the more limited IsPlayingSessionInEditor
//     check leaves open.
//   - GEditor->RequestPlaySession(FRequestPlaySessionParams) —
//     EditorEngine.h:1834 (StartQueuedPlaySessionRequest fires it on
//     the next tick). FRequestPlaySessionParams (PlayInEditorDataTypes.h:126)
//     defaults SessionDestination = EPlaySessionDestinationType::InProcess
//     (PlayInEditorDataTypes.h:131) — same in-process viewport behaviour
//     as the editor toolbar Play button.
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
        // IsPlaySessionInProgress() = playing OR queued; needed for the
        // start-call race guard (back-to-back starts both pass an
        // is-playing-only check because the queued request hasn't
        // ticked yet). EditorEngine.h:1808.
        const bool bPieInProgress = GEditor->IsPlaySessionInProgress();
        // Cached so the in-progress/queued/active triple stays consistent
        // for the duration of one handler call; avoids three reads of
        // IsPlaySessionRequestQueued() across the query/stop branches.
        const bool bPieQueued = GEditor->IsPlaySessionRequestQueued();

        if (Action == TEXT("query"))
        {
            TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
            Out->SetBoolField(TEXT("ok"), true);
            Out->SetStringField(TEXT("action"), TEXT("query"));
            Out->SetBoolField(TEXT("is_playing"), bPieActive);
            Out->SetBoolField(TEXT("is_play_queued"), bPieQueued);
            // IsSimulatingInEditor() accessor (EditorEngine.h:1811) —
            // bIsSimulatingInEditor on the engine is in the deprecated-
            // variables region (EditorEngine.h:3329) and the accessor is
            // the supported entry point.
            Out->SetBoolField(TEXT("is_simulating"), GEditor->IsSimulatingInEditor());
            return Out;
        }

        if (Action == TEXT("stop"))
        {
            // Symmetric with the start-call race guard: if a play
            // request is queued but the editor has not yet ticked
            // StartQueuedPlaySessionRequest, cancel the queued request
            // (EditorEngine.h:1786) rather than letting it proceed and
            // misleading the caller with `pie_not_active`. Both cases
            // (running OR queued) end with no PIE session pending.
            if (!bPieInProgress)
            {
                OutError = TEXT("pie_control: pie_not_active: no PIE session running or queued; nothing to stop");
                return nullptr;
            }
            if (!bPieActive && bPieQueued)
            {
                GEditor->CancelRequestPlaySession();
                TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
                Out->SetBoolField(TEXT("ok"), true);
                Out->SetStringField(TEXT("action"), TEXT("stop"));
                Out->SetStringField(TEXT("note"), TEXT("Cancelled a queued PIE start request that had not yet ticked. No active session to end."));
                return Out;
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
            // Use IsPlaySessionInProgress() so a queued-but-not-yet-started
            // request rejects subsequent rapid start calls. Plain
            // IsPlayingSessionInEditor() goes true only after the editor
            // ticks the queued request, leaving a window where a client
            // retry could enqueue a duplicate launch.
            if (bPieInProgress)
            {
                OutError = TEXT("pie_control: pie_already_active: a PIE session is already running or queued; call action=stop or wait for the queued request to start before retrying");
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
