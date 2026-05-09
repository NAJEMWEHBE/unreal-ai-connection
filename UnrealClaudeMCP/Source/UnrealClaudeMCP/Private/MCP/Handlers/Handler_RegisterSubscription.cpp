// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// register_subscription - create a server-side cursor + filter on the
// FUCMCPEventBus. Returns a subscription_id that pairs with poll_subscription
// (to drain matched events) and unsubscribe (to release).
//
// Why subscriptions vs poll_events:
//   - poll_events makes the client manage since_seq across calls; if the
//     client loses cursor state (restart, crash), it has to re-sync.
//   - register_subscription + poll_subscription puts the cursor on the
//     server; the client just calls poll_subscription with the id and
//     gets only events it hasn't seen.
//   - The filter is also server-side -- no need to re-send it on every
//     poll. Modest wire-savings for filter-heavy workflows.
//
// PR #43 ships subscriptions WITHOUT TTL: they live until explicit
// unsubscribe. If orphan accumulation becomes observable in real
// workflows, a follow-up PR will add inactivity-based cleanup.
//
// Error format: "register_subscription: <error_code>: <human-readable detail>"
// Stable error codes: invalid_value_shape.

#include "MCP/MCPHandler.h"
#include "MCP/EventBus.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

class FHandler_RegisterSubscription : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("register_subscription"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        TArray<FString> EventFilter;
        if (Params.IsValid())
        {
            const TSharedPtr<FJsonValue> EventFilterVal = Params->TryGetField(TEXT("event_filter"));
            if (EventFilterVal.IsValid())
            {
                if (EventFilterVal->Type != EJson::Array)
                {
                    OutError = TEXT("register_subscription: invalid_value_shape: 'event_filter' must be an array of strings");
                    return nullptr;
                }
                for (const TSharedPtr<FJsonValue>& Elem : EventFilterVal->AsArray())
                {
                    if (!Elem.IsValid() || Elem->Type != EJson::String)
                    {
                        OutError = TEXT("register_subscription: invalid_value_shape: 'event_filter' elements must be strings");
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

        int64 InitialNextSeq = 0;
        const FString SubscriptionId = FUCMCPEventBus::Get().RegisterSubscription(EventFilter, InitialNextSeq);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("subscription_id"), SubscriptionId);
        Out->SetNumberField(TEXT("initial_next_seq"), static_cast<double>(InitialNextSeq));

        TArray<TSharedPtr<FJsonValue>> FilterEcho;
        FilterEcho.Reserve(EventFilter.Num());
        for (const FString& F : EventFilter)
        {
            FilterEcho.Add(MakeShared<FJsonValueString>(F));
        }
        Out->SetArrayField(TEXT("event_filter"), FilterEcho);

        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_RegisterSubscription()
{
    return MakeShared<FHandler_RegisterSubscription>();
}
