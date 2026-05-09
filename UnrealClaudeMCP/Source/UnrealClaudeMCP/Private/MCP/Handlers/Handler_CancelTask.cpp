// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// cancel_task - request cooperative cancellation of a running task.
//
// Sets the task's atomic cancellation flag. The worker observes the flag
// on its next polling iteration (typical cadence 50ms) and exits cleanly,
// transitioning the task to status="cancelled". Cancellation is
// COOPERATIVE -- workers that don't poll the flag will run to completion
// regardless. UE 5.7's only safe alternative (forced thread termination)
// risks corrupting game state, so we don't expose it.
//
// Returns ok=true with `accepted=false` for unknown ids and for tasks
// already in a terminal state. Idempotent.
//
// Error format: "cancel_task: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field.

#include "MCP/MCPHandler.h"
#include "MCP/TaskRegistry.h"
#include "Dom/JsonObject.h"

class FHandler_CancelTask : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("cancel_task"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("cancel_task: missing_required_field: 'task_id' is required");
            return nullptr;
        }

        FString TaskId;
        if (!Params->TryGetStringField(TEXT("task_id"), TaskId) || TaskId.IsEmpty())
        {
            OutError = TEXT("cancel_task: missing_required_field: 'task_id' is required and must not be empty");
            return nullptr;
        }

        // RequestCancel returns false for unknown id OR already-terminal status.
        // We don't distinguish those here -- both are "no-op cancellation",
        // semantically equivalent from the caller's perspective.
        const bool bAccepted = FUCMCPTaskRegistry::Get().RequestCancel(TaskId);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("task_id"), TaskId);
        Out->SetBoolField(TEXT("accepted"), bAccepted);
        if (!bAccepted)
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Cancellation not accepted: task id is unknown OR the task is already in a terminal state "
                     "(completed/cancelled/failed). Use poll_task to read the current state."));
        }
        else
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Cancellation requested. Worker will observe within ~50ms (cooperative polling) and "
                     "transition to status='cancelled'. Verify via poll_task after a brief delay."));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_CancelTask()
{
    return MakeShared<FHandler_CancelTask>();
}
