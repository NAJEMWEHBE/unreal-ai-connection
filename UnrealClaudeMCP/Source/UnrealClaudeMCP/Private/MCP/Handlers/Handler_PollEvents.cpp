// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// poll_events - drain editor events from FUCMCPEventBus.
//
// Tier 2 entrypoint (PR #40 / v0.11.0). Today the bridge is request-response;
// this handler turns that into pubsub by snapshotting events that UE delegates
// have pushed into a ring buffer since the caller's last poll.
//
// Typical client flow:
//   1. First call: poll_events {} (since_seq defaults to -1; "from oldest").
//      Read next_seq from the response.
//   2. Subsequent calls: poll_events { since_seq: <last next_seq> }.
//      Receive only newly-fired events.
//   3. If response.dropped == true, the caller missed events between polls
//      (ring buffer overwrote them). Recover by re-syncing whatever editor
//      state matters from the explicit query handlers (find_assets,
//      get_actors_in_level, etc.).
//
// Subscriptions are wired in UnrealClaudeMCPModule::StartupModule. The bus
// itself is type-agnostic; adding new event sources is additive (no changes
// to this handler needed). See docs/superpowers/specs/2026-05-09-tier2-event-push-design.md
// for the full Tier 2 multi-PR roadmap.
//
// Error format: "poll_events: <error_code>: <human-readable detail>"
// Stable error codes: invalid_value_shape.

#include "MCP/MCPHandler.h"
#include "MCP/EventBus.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
    static constexpr int32 kDefaultMaxCount = 100;
    static constexpr int32 kHardMaxCount = FUCMCPEventBus::kRingSize;
}

class FHandler_PollEvents : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("poll_events"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- read optional params -------------------------------------------
        //
        // All params are optional. Defaults: since_seq=-1 ("from oldest"),
        // max_count=100, event_filter=[] (no filter).

        int64 SinceSeq = -1;
        int32 MaxCount = kDefaultMaxCount;
        TArray<FString> EventFilter;

        if (Params.IsValid())
        {
            // since_seq: numeric. Use AsNumber() through TryGetField rather
            // than TryGetNumberField -- the latter fails silently on int64
            // values that don't round-trip through double cleanly. (Doubles
            // can represent int64 exactly only up to 2^53; in practice our
            // seqs won't exceed that within any plausible session, but use
            // the explicit path anyway for clarity.)
            const TSharedPtr<FJsonValue> SinceSeqVal = Params->TryGetField(TEXT("since_seq"));
            if (SinceSeqVal.IsValid())
            {
                if (SinceSeqVal->Type != EJson::Number)
                {
                    OutError = TEXT("poll_events: invalid_value_shape: 'since_seq' must be a number (int64); got non-number JSON type");
                    return nullptr;
                }
                SinceSeq = static_cast<int64>(SinceSeqVal->AsNumber());
            }

            const TSharedPtr<FJsonValue> MaxCountVal = Params->TryGetField(TEXT("max_count"));
            if (MaxCountVal.IsValid())
            {
                if (MaxCountVal->Type != EJson::Number)
                {
                    OutError = TEXT("poll_events: invalid_value_shape: 'max_count' must be a positive integer; got non-number JSON type");
                    return nullptr;
                }
                const double Raw = MaxCountVal->AsNumber();
                if (Raw <= 0)
                {
                    OutError = FString::Printf(
                        TEXT("poll_events: invalid_value_shape: 'max_count' must be > 0 (got %g)"), Raw);
                    return nullptr;
                }
                MaxCount = FMath::Min(static_cast<int32>(Raw), kHardMaxCount);
            }

            const TSharedPtr<FJsonValue> EventFilterVal = Params->TryGetField(TEXT("event_filter"));
            if (EventFilterVal.IsValid())
            {
                if (EventFilterVal->Type != EJson::Array)
                {
                    OutError = TEXT("poll_events: invalid_value_shape: 'event_filter' must be an array of strings");
                    return nullptr;
                }
                for (const TSharedPtr<FJsonValue>& Elem : EventFilterVal->AsArray())
                {
                    if (!Elem.IsValid() || Elem->Type != EJson::String)
                    {
                        OutError = TEXT("poll_events: invalid_value_shape: 'event_filter' elements must be strings");
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

        // --- snapshot the bus -----------------------------------------------

        int64 NextSeq = 0;
        int64 FirstSeqInBuffer = -1;
        bool bDropped = false;

        const TArray<FUCMCPEvent> Events = FUCMCPEventBus::Get().Snapshot(
            SinceSeq, MaxCount, EventFilter, NextSeq, FirstSeqInBuffer, bDropped);

        // --- build response -------------------------------------------------

        TArray<TSharedPtr<FJsonValue>> EventsArray;
        EventsArray.Reserve(Events.Num());
        for (const FUCMCPEvent& E : Events)
        {
            TSharedPtr<FJsonObject> EventObj = MakeShared<FJsonObject>();
            EventObj->SetNumberField(TEXT("seq"), static_cast<double>(E.Seq));
            EventObj->SetStringField(TEXT("event"), E.EventType);
            EventObj->SetStringField(TEXT("ts"), E.Timestamp);
            // Data is shared by reference; consumers should not mutate.
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
        Out->SetArrayField(TEXT("events"), EventsArray);

        if (bDropped)
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Caller's since_seq is older than the oldest event in the ring buffer (capacity 1000). "
                     "Some events fired between polls were not buffered. Re-sync any editor state you depend "
                     "on via the explicit query handlers (get_actors_in_level, find_assets, etc.) and resume "
                     "polling with next_seq from this response."));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_PollEvents()
{
    return MakeShared<FHandler_PollEvents>();
}
