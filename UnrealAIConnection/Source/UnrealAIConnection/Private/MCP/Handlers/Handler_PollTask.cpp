// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// poll_task - read the current state of a task previously started via
// any start_*_task handler. Non-blocking: returns the registry's snapshot
// and never waits for the task to advance.
//
// Status values (from UCMCPTaskStatus):
//   pending    - registered but worker hasn't started yet
//   running    - worker is actively executing
//   completed  - finished successfully; result populated
//   cancelled  - cancellation was requested AND the worker observed it
//   failed     - worker hit an error; error populated
//
// Error format: "poll_task: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, task_not_found.

#include "MCP/MCPHandler.h"
#include "MCP/TaskRegistry.h"
#include "Dom/JsonObject.h"

class FHandler_PollTask : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("poll_task"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("poll_task: missing_required_field: 'task_id' is required");
            return nullptr;
        }

        FString TaskId;
        if (!Params->TryGetStringField(TEXT("task_id"), TaskId) || TaskId.IsEmpty())
        {
            OutError = TEXT("poll_task: missing_required_field: 'task_id' is required and must not be empty");
            return nullptr;
        }

        FUCMCPTaskInfo Info;
        if (!FUCMCPTaskRegistry::Get().GetTask(TaskId, Info))
        {
            OutError = FString::Printf(
                TEXT("poll_task: task_not_found: '%s' is not a registered task "
                     "(was it started? did the editor restart?)"),
                *TaskId);
            return nullptr;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("task_id"), Info.Id);
        Out->SetStringField(TEXT("type"), Info.Type);
        Out->SetStringField(TEXT("status"), Info.Status);
        Out->SetStringField(TEXT("start_time"), Info.StartTime);
        Out->SetStringField(TEXT("end_time"), Info.EndTime);  // empty if not terminal
        Out->SetBoolField(TEXT("cancel_requested"), Info.bCancelRequested);

        if (Info.Result.IsValid())
        {
            Out->SetObjectField(TEXT("result"), Info.Result);
        }
        if (!Info.Error.IsEmpty())
        {
            Out->SetStringField(TEXT("error"), Info.Error);
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_PollTask()
{
    return MakeShared<FHandler_PollTask>();
}
