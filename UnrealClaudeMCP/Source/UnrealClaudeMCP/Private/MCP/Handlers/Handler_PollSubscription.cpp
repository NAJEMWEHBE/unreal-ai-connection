// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// poll_subscription - drain events for a server-side subscription. The
// per-sub cursor advances atomically with the read, so a successful poll
// will not return the same events twice.
//
// Differences vs poll_events:
//   - No since_seq param: the cursor is server-side, advancing on each call.
//   - No event_filter param: the filter was set at register_subscription
//     time and cannot be changed for an existing sub (re-register if you
//     need a different filter).
//   - max_count works the same way (default 100, hard max 1000).
//   - Same dropped/note semantics: dropped=true means events the sub asked
//     for were evicted from the ring before this poll could see them.
//
// Error format: "poll_subscription: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field, invalid_value_shape, subscription_not_found.

#include "MCP/MCPHandler.h"
#include "MCP/EventBus.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
    static constexpr int32 kDefaultMaxCount = 100;
    static constexpr int32 kHardMaxCount = FUCMCPEventBus::kRingSize;
}

class FHandler_PollSubscription : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("poll_subscription"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("poll_subscription: missing_required_field: 'subscription_id' is required");
            return nullptr;
        }

        FString SubscriptionId;
        if (!Params->TryGetStringField(TEXT("subscription_id"), SubscriptionId) || SubscriptionId.IsEmpty())
        {
            OutError = TEXT("poll_subscription: missing_required_field: 'subscription_id' is required and must not be empty");
            return nullptr;
        }

        int32 MaxCount = kDefaultMaxCount;
        const TSharedPtr<FJsonValue> MaxCountVal = Params->TryGetField(TEXT("max_count"));
        if (MaxCountVal.IsValid())
        {
            if (MaxCountVal->Type != EJson::Number)
            {
                OutError = TEXT("poll_subscription: invalid_value_shape: 'max_count' must be a positive integer");
                return nullptr;
            }
            const double Raw = MaxCountVal->AsNumber();
            if (!FMath::IsFinite(Raw) || FMath::TruncToDouble(Raw) != Raw)
            {
                OutError = FString::Printf(
                    TEXT("poll_subscription: invalid_value_shape: 'max_count' must be a finite integer (got %g)"), Raw);
                return nullptr;
            }
            if (Raw <= 0)
            {
                OutError = FString::Printf(
                    TEXT("poll_subscription: invalid_value_shape: 'max_count' must be > 0 (got %g)"), Raw);
                return nullptr;
            }
            MaxCount = FMath::Min(static_cast<int32>(Raw), kHardMaxCount);
        }

        int64 NextSeq = 0;
        int64 FirstSeqInBuffer = -1;
        bool bDropped = false;
        bool bFound = false;

        const TArray<FUCMCPEvent> Events = FUCMCPEventBus::Get().PollSubscription(
            SubscriptionId, MaxCount, NextSeq, FirstSeqInBuffer, bDropped, bFound);

        if (!bFound)
        {
            OutError = FString::Printf(
                TEXT("poll_subscription: subscription_not_found: '%s' is not a registered subscription "
                     "(was it created via register_subscription? did unsubscribe drop it? did the editor restart?)"),
                *SubscriptionId);
            return nullptr;
        }

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
        Out->SetStringField(TEXT("subscription_id"), SubscriptionId);
        Out->SetNumberField(TEXT("next_seq"), static_cast<double>(NextSeq));
        Out->SetNumberField(TEXT("first_seq_in_buffer"), static_cast<double>(FirstSeqInBuffer));
        Out->SetNumberField(TEXT("returned"), static_cast<double>(Events.Num()));
        Out->SetBoolField(TEXT("dropped"), bDropped);
        Out->SetArrayField(TEXT("events"), EventsArray);

        if (bDropped)
        {
            Out->SetStringField(TEXT("note"),
                TEXT("Subscription cursor fell below the oldest event in the ring buffer (capacity 1000) -- "
                     "events fired between polls were not buffered. Re-sync any editor state your workflow "
                     "depends on via the explicit query handlers (get_actors_in_level, find_assets, etc.). "
                     "The subscription itself is still valid and the cursor has advanced to the latest seq."));
        }
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_PollSubscription()
{
    return MakeShared<FHandler_PollSubscription>();
}
