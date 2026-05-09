// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// FUCMCPEventBus - thread-safe in-process ring buffer of editor events.
// Tier 2 entrypoint: turns the request-response bridge into a pubsub channel
// (UE delegates fire -> Push() into the ring -> Handler_PollEvents drains).
//
// Mirrors the FUCMCPLogCapture pattern (see LogCapture.h): pre-allocated
// fixed-size ring, FCriticalSection for thread safety, snapshot-on-read.
// Differences:
//   - Each entry carries a strictly-monotonic int64 seq so callers can
//     paginate ("events since seq N") and detect drops.
//   - The payload is a TSharedPtr<FJsonObject> rather than flat strings;
//     callers attach event-specific structured data per Push.
//
// The bus is type-agnostic: it knows nothing about specific UE delegates.
// Per-event subscriptions are wired as lambdas in UnrealClaudeMCPModule's
// StartupModule, which build the JSON payload and call Push(). Adding new
// event sources later requires no change to this file.

#pragma once

#include "CoreMinimal.h"
#include "HAL/CriticalSection.h"
#include "Dom/JsonObject.h"

/** One captured editor event. */
struct FUCMCPEvent
{
    int64 Seq = 0;                          // monotonic; never reused
    FString EventType;                      // snake_case (e.g. "actor_spawned")
    FString Timestamp;                      // "YYYY.MM.DD-HH.MM.SS" (matches LogCapture)
    TSharedPtr<FJsonObject> Data;           // event-specific payload; non-null after Push
};

/**
 * Process-singleton ring buffer of editor events. Thread-safe: Push may be
 * called from any thread (IAssetRegistry::OnAssetAdded fires from background
 * scan threads), and Snapshot may be called concurrently from the game thread
 * (the dispatcher).
 *
 * Usage:
 *
 *   // In StartupModule -- subscribe a delegate that pushes into the bus:
 *   GEngine->OnLevelActorAdded().AddLambda([](AActor* A) {
 *       auto Data = MakeShared<FJsonObject>();
 *       Data->SetStringField("actor_label", A ? A->GetActorLabel() : TEXT(""));
 *       FUCMCPEventBus::Get().Push(TEXT("actor_spawned"), Data);
 *   });
 *
 *   // In Handler_PollEvents -- snapshot, filter, build response:
 *   TArray<FUCMCPEvent> Snap = FUCMCPEventBus::Get().Snapshot(SinceSeq, MaxCount, EventFilter);
 */
class FUCMCPEventBus
{
public:
    static constexpr int32 kRingSize = 1000;

    /** Singleton access. Construct-on-first-use; lives for process lifetime. */
    static FUCMCPEventBus& Get();

    /**
     * Append an event to the ring. Assigns the next seq, captures the current
     * timestamp, and stores the supplied payload by shared reference. Must be
     * called from delegate handlers; payload should not be mutated after.
     *
     * Thread-safe (FCriticalSection + thread_local re-entrancy guard).
     */
    void Push(const FString& EventType, TSharedPtr<FJsonObject> Data);

    /**
     * Copy events with seq > SinceSeq into a flat array (oldest-first).
     * If EventFilter is non-empty, only events whose EventType matches at
     * least one filter substring are returned. Capped at MaxCount.
     *
     * Out params (filled atomically with the snapshot to give callers a
     * consistent view of the bus's state):
     *   - OutNextSeq           the seq the next-pushed event would receive
     *   - OutFirstSeqInBuffer  the smallest seq currently buffered (or -1 if empty)
     *   - OutDropped           true iff SinceSeq is older than OutFirstSeqInBuffer-1
     *                          (caller missed events between polls)
     */
    TArray<FUCMCPEvent> Snapshot(
        int64 SinceSeq,
        int32 MaxCount,
        const TArray<FString>& EventFilter,
        int64& OutNextSeq,
        int64& OutFirstSeqInBuffer,
        bool& OutDropped) const;

private:
    FUCMCPEventBus();

    mutable FCriticalSection Mutex;

    // Pre-allocated fixed-capacity ring; never reallocates after construction.
    TArray<FUCMCPEvent> Ring;
    int32 Head  = 0;       // next write position (wraps modulo kRingSize)
    int32 Count = 0;       // entries filled (capped at kRingSize)
    int64 NextSeq = 0;     // monotonically increasing; assigned on each Push
};
