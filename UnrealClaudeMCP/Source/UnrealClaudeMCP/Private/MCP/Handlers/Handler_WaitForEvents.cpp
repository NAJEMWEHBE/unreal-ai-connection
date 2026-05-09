// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// wait_for_events - block briefly until matching editor events arrive, or
// the timeout expires. Pairs with poll_events: same buffer, same response
// shape, same cursor semantics; just adds a bounded wait when the buffer
// has nothing new yet.
//
// Architectural caveat (load-bearing - read this before extending):
//
// The MCP server's dispatcher (FUCMCPDispatcher::HandleMessage) is invoked
// synchronously from FUCMCPServer::TickClients, which runs as an FTSTicker
// callback on the GAME THREAD at 50ms intervals (MCPServer.cpp:205-208,
// :323). A handler that blocks for N ms therefore freezes the entire UE
// game thread for N ms -- visible to the editor user as a stall (cursor
// jitter, UI input lag, viewport stutter).
//
// Until the dispatcher is refactored to support truly async handlers (a
// future bundle), wait_for_events MUST keep its wait short enough that
// the editor stall is acceptable. We:
//   - Default timeout_ms to 500 (~half a frame at 30fps; perceptible but
//     brief; reasonable for "I want low-latency event delivery")
//   - Hard-cap at 5000 (5 seconds is firmly in editor-frozen territory;
//     above this we silently clamp and emit a 'note' so callers know)
//   - Sleep in 50ms slices via FPlatformProcess::Sleep (matches the
//     ticker cadence; the editor was going to be unresponsive for at
//     least 50ms anyway between ticks)
//
// Returns immediately if matching events are already buffered (no sleep
// at all). The wait only happens when the buffer has nothing new.
//
// Error format: "wait_for_events: <error_code>: <human-readable detail>"
// Stable error codes: invalid_value_shape.

#include "MCP/MCPHandler.h"
#include "MCP/EventBus.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/PlatformProcess.h"
#include "Misc/Timespan.h"
#include "Misc/DateTime.h"

namespace
{
    static constexpr int32 kDefaultMaxCount = 100;
    static constexpr int32 kHardMaxCount = FUCMCPEventBus::kRingSize;

    // Wait-loop tuning. The 50ms slice matches the FTSTicker cadence in
    // MCPServer.cpp -- we'd be waiting at least that long anyway between
    // ticks, so finer granularity wouldn't reduce real latency. The 5s
    // hard cap keeps the editor stall in "annoying" territory at worst,
    // not "completely frozen for 30s" territory.
    static constexpr int32 kDefaultTimeoutMs = 500;
    static constexpr int32 kMaxTimeoutMs = 5000;
    static constexpr int32 kSliceMs = 50;
}

class FHandler_WaitForEvents : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("wait_for_events"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- read optional params -------------------------------------------

        int64 SinceSeq = -1;
        int32 MaxCount = kDefaultMaxCount;
        int32 TimeoutMs = kDefaultTimeoutMs;
        bool bTimeoutClamped = false;
        TArray<FString> EventFilter;

        if (Params.IsValid())
        {
            // since_seq -- same shape + validation as poll_events
            const TSharedPtr<FJsonValue> SinceSeqVal = Params->TryGetField(TEXT("since_seq"));
            if (SinceSeqVal.IsValid())
            {
                if (SinceSeqVal->Type != EJson::Number)
                {
                    OutError = TEXT("wait_for_events: invalid_value_shape: 'since_seq' must be an integer");
                    return nullptr;
                }
                const double Raw = SinceSeqVal->AsNumber();
                if (!FMath::IsFinite(Raw) || FMath::TruncToDouble(Raw) != Raw)
                {
                    OutError = FString::Printf(
                        TEXT("wait_for_events: invalid_value_shape: 'since_seq' must be a finite integer (got %g)"), Raw);
                    return nullptr;
                }
                SinceSeq = static_cast<int64>(Raw);
            }

            // max_count -- same shape + validation as poll_events
            const TSharedPtr<FJsonValue> MaxCountVal = Params->TryGetField(TEXT("max_count"));
            if (MaxCountVal.IsValid())
            {
                if (MaxCountVal->Type != EJson::Number)
                {
                    OutError = TEXT("wait_for_events: invalid_value_shape: 'max_count' must be a positive integer");
                    return nullptr;
                }
                const double Raw = MaxCountVal->AsNumber();
                if (!FMath::IsFinite(Raw) || FMath::TruncToDouble(Raw) != Raw)
                {
                    OutError = FString::Printf(
                        TEXT("wait_for_events: invalid_value_shape: 'max_count' must be a finite integer (got %g)"), Raw);
                    return nullptr;
                }
                if (Raw <= 0)
                {
                    OutError = FString::Printf(
                        TEXT("wait_for_events: invalid_value_shape: 'max_count' must be > 0 (got %g)"), Raw);
                    return nullptr;
                }
                MaxCount = FMath::Min(static_cast<int32>(Raw), kHardMaxCount);
            }

            // timeout_ms -- new vs poll_events. Cap at kMaxTimeoutMs and
            // record the clamp so the response can surface it.
            const TSharedPtr<FJsonValue> TimeoutVal = Params->TryGetField(TEXT("timeout_ms"));
            if (TimeoutVal.IsValid())
            {
                if (TimeoutVal->Type != EJson::Number)
                {
                    OutError = TEXT("wait_for_events: invalid_value_shape: 'timeout_ms' must be a non-negative integer");
                    return nullptr;
                }
                const double Raw = TimeoutVal->AsNumber();
                if (!FMath::IsFinite(Raw) || FMath::TruncToDouble(Raw) != Raw || Raw < 0)
                {
                    OutError = FString::Printf(
                        TEXT("wait_for_events: invalid_value_shape: 'timeout_ms' must be a finite non-negative integer (got %g)"), Raw);
                    return nullptr;
                }
                const int32 Requested = static_cast<int32>(FMath::Min(Raw, static_cast<double>(INT32_MAX)));
                if (Requested > kMaxTimeoutMs)
                {
                    bTimeoutClamped = true;
                    TimeoutMs = kMaxTimeoutMs;
                }
                else
                {
                    TimeoutMs = Requested;
                }
            }

            // event_filter -- same shape + validation as poll_events
            const TSharedPtr<FJsonValue> EventFilterVal = Params->TryGetField(TEXT("event_filter"));
            if (EventFilterVal.IsValid())
            {
                if (EventFilterVal->Type != EJson::Array)
                {
                    OutError = TEXT("wait_for_events: invalid_value_shape: 'event_filter' must be an array of strings");
                    return nullptr;
                }
                for (const TSharedPtr<FJsonValue>& Elem : EventFilterVal->AsArray())
                {
                    if (!Elem.IsValid() || Elem->Type != EJson::String)
                    {
                        OutError = TEXT("wait_for_events: invalid_value_shape: 'event_filter' elements must be strings");
                        return nullptr;
                    }
                    FString S;
                    Elem->TryGetString(S);
                    if (!S.IsEmpty())
                    {
                        EventFilter.Add(MoveTemp(S));
                    }
                }
            }
        }

        // --- wait loop ------------------------------------------------------
        //
        // First check is non-blocking (deadline check happens after the
        // FIRST snapshot, not before, so we always get a "free" check even
        // if timeout_ms == 0). If matching events are buffered, return
        // immediately -- no sleep at all.
        //
        // Otherwise, sleep kSliceMs and re-check, until either match or
        // timeout. We use FDateTime::UtcNow rather than FPlatformTime
        // because we want wall-clock relative to user expectation, not
        // monotonic relative to engine startup (the difference is invisible
        // for sub-5s waits but the wall-clock semantics match what users
        // mean by "wait 500ms").

        FUCMCPEventBus& Bus = FUCMCPEventBus::Get();

        const FDateTime Deadline = FDateTime::UtcNow() + FTimespan::FromMilliseconds(TimeoutMs);

        int64 NextSeq = 0;
        int64 FirstSeqInBuffer = -1;
        bool bDropped = false;
        TArray<FUCMCPEvent> Events;

        for (;;)
        {
            Events = Bus.Snapshot(SinceSeq, MaxCount, EventFilter,
                                  NextSeq, FirstSeqInBuffer, bDropped);
            if (Events.Num() > 0 || bDropped)
            {
                break;  // matched events OR caller missed events between polls
            }
            if (FDateTime::UtcNow() >= Deadline)
            {
                break;  // timed out with no match -- return empty
            }
            FPlatformProcess::Sleep(static_cast<float>(kSliceMs) / 1000.0f);
        }

        // --- build response (same shape as poll_events) --------------------

        TArray<TSharedPtr<FJsonValue>> EventsArray;
        EventsArray.Reserve(Events.Num());
        for (const FUCMCPEvent& E : Events)
        {
            TSharedPtr<FJsonObject> EventObj = MakeShared<FJsonObject>();
            EventObj->SetNumberField(TEXT("seq"), static_cast<double>(E.Seq));
            EventObj->SetStringField(TEXT("event"), E.EventType);
            EventObj->SetStringField(TEXT("ts"), E.Timestamp);
            EventObj->SetObjectField(TEXT("data"),
                E.Data.IsValid() ? E.Data : MakeShared<FJsonObject>());
            EventsArray.Add(MakeShared<FJsonValueObject>(EventObj));
        }

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetNumberField(TEXT("next_seq"), static_cast<double>(NextSeq));
        Out->SetNumberField(TEXT("first_seq_in_buffer"), static_cast<double>(FirstSeqInBuffer));
        Out->SetNumberField(TEXT("returned"), static_cast<double>(Events.Num()));
        Out->SetBoolField(TEXT("dropped"), bDropped);
        Out->SetBoolField(TEXT("timed_out"), Events.Num() == 0 && !bDropped);
        Out->SetArrayField(TEXT("events"), EventsArray);

        if (bTimeoutClamped)
        {
            Out->SetStringField(TEXT("note"),
                FString::Printf(
                    TEXT("Requested timeout_ms exceeded the hard cap of %d ms (single-threaded "
                         "dispatcher freezes the editor game thread during the wait); clamped."),
                    kMaxTimeoutMs));
        }
        else if (bDropped)
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Caller's since_seq is older than the oldest event in the ring buffer (capacity 1000). "
                     "Some events fired between polls were not buffered. Re-sync editor state via the "
                     "explicit query handlers and resume polling with next_seq from this response."));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_WaitForEvents()
{
    return MakeShared<FHandler_WaitForEvents>();
}
