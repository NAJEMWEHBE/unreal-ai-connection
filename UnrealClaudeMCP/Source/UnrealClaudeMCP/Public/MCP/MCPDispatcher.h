// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// MCPDispatcher - stateless static utility that parses one JSON-RPC 2.0
// request, locates the matching handler in FUCMCPHandlerRegistry, runs it,
// and serializes the response back to a JSON string.
//
// Threading: callers MUST invoke HandleMessage on the game thread. The TCP
// server's tick callback satisfies this.

#pragma once

#include "CoreMinimal.h"

class UNREALCLAUDEMCP_API FUCMCPDispatcher
{
public:
    /**
     * Parse one JSON-RPC 2.0 request and return the response as a JSON string.
     * Errors (parse failures, unknown methods, handler errors) are returned as
     * proper JSON-RPC error objects.
     *
     * Notifications (requests with no "id") get an empty string back, per spec.
     */
    static FString HandleMessage(const FString& InMessage);
};
