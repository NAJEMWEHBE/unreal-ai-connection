// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// list_tasks - enumerate the current task registry snapshot. Non-blocking:
// returns all tasks visible at one registry lock point, then applies optional
// caller-side filtering and limiting.
//
// Status values (from UCMCPTaskStatus):
//   pending    - registered but worker hasn't started yet
//   running    - worker is actively executing
//   completed  - finished successfully; result populated
//   cancelled  - cancellation was requested AND the worker observed it
//   failed     - worker hit an error; error populated
//
// Error format: "list_tasks: <error_code>: <human-readable detail>"
// Stable error codes: unknown_status_value, invalid_value_shape.

#include "MCP/MCPHandler.h"
#include "MCP/TaskRegistry.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Math/UnrealMathUtility.h"

namespace
{
    static bool IsKnownTaskStatus(const FString& Status)
    {
        return Status == UCMCPTaskStatus::Pending
            || Status == UCMCPTaskStatus::Running
            || Status == UCMCPTaskStatus::Completed
            || Status == UCMCPTaskStatus::Cancelled
            || Status == UCMCPTaskStatus::Failed;
    }
}

class FHandler_ListTasks : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("list_tasks"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        FString StatusFilter;
        bool bHasStatusFilter = false;

        if (Params.IsValid())
        {
            const TSharedPtr<FJsonValue> StatusValue = Params->TryGetField(TEXT("status_filter"));
            if (StatusValue.IsValid() && StatusValue->Type != EJson::Null)
            {
                if (StatusValue->Type != EJson::String)
                {
                    OutError = FString::Printf(
                        TEXT("list_tasks: unknown_status_value: 'status_filter' must be one of pending, running, completed, cancelled, failed (got json type %d)"),
                        static_cast<int32>(StatusValue->Type));
                    return nullptr;
                }

                StatusFilter = StatusValue->AsString();
                if (!IsKnownTaskStatus(StatusFilter))
                {
                    OutError = FString::Printf(
                        TEXT("list_tasks: unknown_status_value: 'status_filter' must be one of pending, running, completed, cancelled, failed (got '%s')"),
                        *StatusFilter);
                    return nullptr;
                }
                bHasStatusFilter = true;
            }
        }

        int32 Limit = 100;
        if (Params.IsValid() && Params->HasField(TEXT("limit")))
        {
            double Raw;
            if (!Params->TryGetNumberField(TEXT("limit"), Raw))
            {
                OutError = TEXT("list_tasks: invalid_value_shape: 'limit' must be a number");
                return nullptr;
            }
            if (!FMath::IsFinite(Raw))
            {
                OutError = FString::Printf(
                    TEXT("list_tasks: invalid_value_shape: 'limit' must be a finite integer (got %g)"), Raw);
                return nullptr;
            }
            if (Raw != FMath::FloorToDouble(Raw))
            {
                OutError = TEXT("list_tasks: invalid_value_shape: 'limit' must be an integer");
                return nullptr;
            }
            Limit = static_cast<int32>(FMath::Clamp(Raw, 1.0, 500.0));
        }

        // Validate type_filter shape consistently with status_filter (PR #50
        // Codex P2 + Gemini medium review): silently ignoring non-string
        // values let clients with malformed filters get unexpectedly broad
        // result sets instead of an actionable error.
        FString TypeFilter;
        bool bHasTypeFilter = false;
        if (Params.IsValid())
        {
            const TSharedPtr<FJsonValue> TypeValue = Params->TryGetField(TEXT("type_filter"));
            if (TypeValue.IsValid() && TypeValue->Type != EJson::Null)
            {
                if (TypeValue->Type != EJson::String)
                {
                    OutError = FString::Printf(
                        TEXT("list_tasks: invalid_value_shape: 'type_filter' must be a string (got json type %d)"),
                        static_cast<int32>(TypeValue->Type));
                    return nullptr;
                }
                TypeFilter = TypeValue->AsString();
                bHasTypeFilter = true;
            }
        }

        TArray<FUCMCPTaskInfo> AllInfos;
        FUCMCPTaskRegistry::Get().Snapshot(AllInfos);
        const int32 Total = AllInfos.Num();

        // Single-pass filter + count + emit (PR #50 Gemini perf review).
        // Prior implementation copied AllInfos -> Matched -> Returned -> JSON;
        // this version filters, counts matches, and emits JSON in one pass.
        // Limit-respecting truncation stops emitting JSON beyond Limit while
        // continuing to count matches (so the 'matched' count is exact).
        int32 MatchedCount = 0;
        TArray<TSharedPtr<FJsonValue>> TasksJson;
        TasksJson.Reserve(FMath::Min(Total, Limit));

        for (const FUCMCPTaskInfo& Info : AllInfos)
        {
            if (bHasStatusFilter && Info.Status != StatusFilter)
            {
                continue;
            }
            if (bHasTypeFilter && Info.Type != TypeFilter)
            {
                continue;
            }

            if (MatchedCount < Limit)
            {
                TSharedRef<FJsonObject> Task = MakeShared<FJsonObject>();
                Task->SetStringField(TEXT("task_id"), Info.Id);
                Task->SetStringField(TEXT("type"), Info.Type);
                Task->SetStringField(TEXT("status"), Info.Status);
                Task->SetStringField(TEXT("start_time"), Info.StartTime);
                Task->SetStringField(TEXT("end_time"), Info.EndTime);
                Task->SetBoolField(TEXT("cancel_requested"), Info.bCancelRequested);

                if (Info.Result.IsValid())
                {
                    Task->SetObjectField(TEXT("result"), Info.Result);
                }
                if (!Info.Error.IsEmpty())
                {
                    Task->SetStringField(TEXT("error"), Info.Error);
                }

                TasksJson.Add(MakeShared<FJsonValueObject>(Task));
            }
            ++MatchedCount;
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("total"), Total);
        Out->SetNumberField(TEXT("matched"), MatchedCount);
        Out->SetNumberField(TEXT("returned"), TasksJson.Num());
        Out->SetArrayField(TEXT("tasks"), TasksJson);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ListTasks()
{
    return MakeShared<FHandler_ListTasks>();
}

