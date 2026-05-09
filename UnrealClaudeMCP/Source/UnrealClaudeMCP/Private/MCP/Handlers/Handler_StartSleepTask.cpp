// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// start_sleep_task - Tier 2 PR #44 framework tracer. Background-sleeps
// for `duration_ms` then completes the task. Useful by itself for
// "wait N ms and then do something" workflows; primary purpose is to
// exercise the FUCMCPTaskRegistry threading + cancellation paths
// without UE-specific complications.
//
// Future task types (not in PR #44) would follow this same shape:
//   1. validate params
//   2. CreateTask in the registry
//   3. spawn a background worker via Async (or FRunnableThread for
//      operations that need their own thread lifecycle)
//   4. worker periodically checks the cancellation flag and updates
//      registry status
//   5. worker calls MarkCompleted / MarkFailed when done
//   6. handler returns task_id immediately (no blocking)
//
// Cancellation is cooperative: the worker polls the flag in 50ms slices.
// Cancel latency is therefore bounded at 50ms, which is fine for
// human-perceived "stop" semantics. UE 5.7's TAtomic<bool>::Load is
// lock-free so the polling cost is negligible.
//
// Error format: "start_sleep_task: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, invalid_value_shape.

#include "MCP/MCPHandler.h"
#include "MCP/TaskRegistry.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Async/Async.h"
#include "HAL/PlatformProcess.h"

namespace
{
    // Cancellation polling cadence inside the sleep loop. 50ms = the same
    // slice MCPServer's ticker uses + the same slice wait_for_events used
    // before its bridge-side redesign. Trade-off: lower = faster cancel
    // response; higher = less CPU. 50ms is sub-perceptible for cancel UX.
    static constexpr int32 kSliceMs = 50;

    // Hard cap on duration_ms. Long-running tasks are exactly the use case,
    // but uncapped also means a misconfigured client can pin a thread-pool
    // worker for hours. 1 hour is a reasonable upper bound for sleep --
    // actual cooks/renders/bakes (future task types) won't use this handler.
    static constexpr int32 kMaxDurationMs = 60 * 60 * 1000;
}

class FHandler_StartSleepTask : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("start_sleep_task"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("start_sleep_task: missing_required_field: 'duration_ms' is required");
            return nullptr;
        }

        const TSharedPtr<FJsonValue> DurationVal = Params->TryGetField(TEXT("duration_ms"));
        if (!DurationVal.IsValid())
        {
            OutError = TEXT("start_sleep_task: missing_required_field: 'duration_ms' is required");
            return nullptr;
        }
        if (DurationVal->Type != EJson::Number)
        {
            OutError = TEXT("start_sleep_task: invalid_value_shape: 'duration_ms' must be a positive integer");
            return nullptr;
        }
        const double Raw = DurationVal->AsNumber();
        if (!FMath::IsFinite(Raw) || FMath::TruncToDouble(Raw) != Raw)
        {
            OutError = FString::Printf(
                TEXT("start_sleep_task: invalid_value_shape: 'duration_ms' must be a finite integer (got %g)"), Raw);
            return nullptr;
        }
        if (Raw <= 0)
        {
            OutError = FString::Printf(
                TEXT("start_sleep_task: invalid_value_shape: 'duration_ms' must be > 0 (got %g)"), Raw);
            return nullptr;
        }
        const int32 DurationMs = FMath::Min(static_cast<int32>(Raw), kMaxDurationMs);

        // --- register task and spawn worker --------------------------------

        TSharedPtr<TAtomic<bool>> CancelFlag;
        const FString TaskId = FUCMCPTaskRegistry::Get().CreateTask(TEXT("sleep"), CancelFlag);

        // Spawn worker on the engine's thread pool. Async returns a TFuture;
        // we don't keep it -- the worker writes back to the registry on
        // completion. Lambda captures by value, including the SharedPtr to
        // the cancel flag (lifetime extends to worker).
        Async(EAsyncExecution::ThreadPool,
            [TaskId, DurationMs, CancelFlag]()
            {
                FUCMCPTaskRegistry::Get().MarkRunning(TaskId);

                const double DeadlineSeconds = FPlatformTime::Seconds()
                    + (static_cast<double>(DurationMs) / 1000.0);

                // Sleep loop: wake every kSliceMs to check for cancellation.
                while (FPlatformTime::Seconds() < DeadlineSeconds)
                {
                    if (CancelFlag.IsValid() && CancelFlag->Load())
                    {
                        FUCMCPTaskRegistry::Get().MarkCancelled(TaskId);
                        return;
                    }
                    FPlatformProcess::Sleep(static_cast<float>(kSliceMs) / 1000.0f);
                }

                TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
                Result->SetNumberField(TEXT("slept_ms"), static_cast<double>(DurationMs));
                FUCMCPTaskRegistry::Get().MarkCompleted(TaskId, Result);
            });

        // --- response ------------------------------------------------------

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("task_id"), TaskId);
        Out->SetStringField(TEXT("type"), TEXT("sleep"));
        Out->SetStringField(TEXT("status"), UCMCPTaskStatus::Pending);
        Out->SetNumberField(TEXT("duration_ms"), static_cast<double>(DurationMs));
        Out->SetStringField(TEXT("note"),
            TEXT("Worker spawned on EAsyncExecution::ThreadPool. Poll via poll_task with the returned task_id; "
                 "cancel via cancel_task. Cancel latency is ~50ms (cooperative polling)."));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_StartSleepTask()
{
    return MakeShared<FHandler_StartSleepTask>();
}
