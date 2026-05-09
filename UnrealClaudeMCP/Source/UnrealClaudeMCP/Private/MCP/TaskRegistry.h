// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// FUCMCPTaskRegistry - bookkeeping for long-running editor-side tasks.
//
// Tier 2 PR #44 framework: lets MCP clients kick off background work
// (sleep, eventually cooks/renders/bakes) and check on / cancel it later
// without holding an MCP request open. The pattern:
//
//   start_*_task(...)         -> task_id
//   poll_task(task_id)        -> { status, result | error }
//   cancel_task(task_id)      -> request cooperative cancellation
//
// The registry only knows about tasks; it doesn't know how they run. Each
// start_* handler kicks off its own worker (typically via Async on
// EAsyncExecution::ThreadPool) and updates the registry as state changes.
// Cancellation is COOPERATIVE: the worker checks the cancellation flag
// periodically and exits cleanly. The registry can't kill an unwilling
// worker -- and in UE 5.7 that's the right discipline anyway (forced
// thread termination corrupts game state).
//
// Lifecycle (PR #44): completed/cancelled/failed tasks live in the
// registry indefinitely. NO TTL in PR #44; if observable orphan-task
// accumulation becomes a problem, a follow-up PR will add cleanup.
// Same approach as PR #43 subscriptions.

#pragma once

#include "CoreMinimal.h"
#include "HAL/CriticalSection.h"
#include "Templates/Atomic.h"
#include "Dom/JsonObject.h"

namespace UCMCPTaskStatus
{
    // Status string constants. All transitions go: pending -> running ->
    // (completed | cancelled | failed). Once in a terminal state, the
    // task is immutable.
    inline const FString Pending   = TEXT("pending");
    inline const FString Running   = TEXT("running");
    inline const FString Completed = TEXT("completed");
    inline const FString Cancelled = TEXT("cancelled");
    inline const FString Failed    = TEXT("failed");
}

/** Snapshot of a task's state, returned by GetTask. */
struct FUCMCPTaskInfo
{
    FString Id;                              // FGuid::NewGuid().ToString()
    FString Type;                            // "sleep" / future: "cook" / "render" / etc.
    FString Status;                          // see UCMCPTaskStatus
    FString StartTime;                       // "YYYY.MM.DD-HH.MM.SS" (matches LogCapture)
    FString EndTime;                         // populated on terminal state; empty otherwise
    TSharedPtr<FJsonObject> Result;          // populated when Status == Completed
    FString Error;                           // populated when Status == Failed
    bool bCancelRequested = false;           // mirrors the atomic flag at snapshot time
};

class FUCMCPTaskRegistry
{
public:
    static FUCMCPTaskRegistry& Get();

    /**
     * Register a new task in 'pending' state. Returns the task id and an
     * atomic cancellation flag that the worker should poll periodically.
     * The flag is shared between the registry and the worker via TSharedPtr;
     * lifetime extends past task completion (until the entry is collected,
     * which doesn't happen in PR #44).
     */
    FString CreateTask(const FString& TaskType, TSharedPtr<TAtomic<bool>>& OutCancellationFlag);

    /** Move a task to 'running' state. Workers call this when they begin actual work. */
    void MarkRunning(const FString& TaskId);

    /** Terminal-state writers. Each is idempotent against a re-entered
     *  terminal state (re-calling SetCompleted on an already-completed task
     *  is a no-op rather than a contract violation). */
    void MarkCompleted(const FString& TaskId, TSharedPtr<FJsonObject> Result);
    void MarkCancelled(const FString& TaskId);
    void MarkFailed(const FString& TaskId, const FString& Error);

    /**
     * Request cancellation. Sets the atomic flag; the worker must check it
     * and self-terminate. Returns false if the task id is unknown OR if the
     * task is already in a terminal state (cancellation is meaningful only
     * for pending/running tasks).
     */
    bool RequestCancel(const FString& TaskId);

    /**
     * Snapshot a task. OutInfo is populated atomically under the lock so
     * callers see a consistent state (status / result / error / end_time
     * all match). Returns false if the task id is unknown.
     */
    bool GetTask(const FString& TaskId, FUCMCPTaskInfo& OutInfo) const;

private:
    FUCMCPTaskRegistry() = default;

    struct FRecord
    {
        FUCMCPTaskInfo Info;
        TSharedPtr<TAtomic<bool>> CancellationFlag;
    };

    mutable FCriticalSection Mutex;
    TMap<FString, FRecord> Tasks;

    /** Capture wall-clock timestamp in the canonical YYYY.MM.DD-HH.MM.SS form. */
    static FString NowString();

    /** Returns true iff Status is one of Completed / Cancelled / Failed. */
    static bool IsTerminal(const FString& Status);
};
