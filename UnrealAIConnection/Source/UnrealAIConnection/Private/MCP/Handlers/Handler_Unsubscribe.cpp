// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// unsubscribe - remove a subscription previously created via
// register_subscription. Idempotent in spirit -- calling on an unknown id
// returns ok=true with was_present=false rather than an error, so callers
// can blanket-unsubscribe on shutdown without worrying about partial state.
//
// Error format: "unsubscribe: <error_code>: <human-readable detail>"
// Stable error codes: missing_required_field.

#include "MCP/MCPHandler.h"
#include "MCP/EventBus.h"
#include "Dom/JsonObject.h"

class FHandler_Unsubscribe : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("unsubscribe"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("unsubscribe: missing_required_field: 'subscription_id' is required");
            return nullptr;
        }

        FString SubscriptionId;
        if (!Params->TryGetStringField(TEXT("subscription_id"), SubscriptionId) || SubscriptionId.IsEmpty())
        {
            OutError = TEXT("unsubscribe: missing_required_field: 'subscription_id' is required and must not be empty");
            return nullptr;
        }

        const bool bWasPresent = FUCMCPEventBus::Get().Unsubscribe(SubscriptionId);

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("subscription_id"), SubscriptionId);
        Out->SetBoolField(TEXT("was_present"), bWasPresent);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_Unsubscribe()
{
    return MakeShared<FHandler_Unsubscribe>();
}
