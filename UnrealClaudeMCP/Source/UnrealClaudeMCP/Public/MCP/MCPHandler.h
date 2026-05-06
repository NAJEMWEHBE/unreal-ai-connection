// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// MCPHandler - interface + registry for MCP method handlers. Each handler
// exposes one JSON-RPC method (e.g. "execute_unreal_python"). Handlers are
// constructed once at module startup and registered into the process-singleton
// FUCMCPHandlerRegistry.

#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "Templates/SharedPointer.h"

class UNREALCLAUDEMCP_API IUCMCPHandler
{
public:
    virtual ~IUCMCPHandler() = default;

    /** JSON-RPC method name this handler responds to. Must be unique. */
    virtual FString GetMethodName() const = 0;

    /**
     * Run the handler. Called on the game thread by the dispatcher.
     * @param Params  Parsed "params" object from the JSON-RPC request (never null; empty if absent).
     * @param OutError  Set to a non-empty string on failure; leave empty on success.
     * @return Result object on success; nullptr on failure (with OutError populated).
     */
    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) = 0;
};

class UNREALCLAUDEMCP_API FUCMCPHandlerRegistry
{
public:
    static FUCMCPHandlerRegistry& Get();

    /** Add a handler. Last write wins on method-name collision. */
    void Register(TSharedRef<IUCMCPHandler> Handler);

    /** Find a handler by method name. Returns nullptr if absent. */
    IUCMCPHandler* Find(const FString& Method) const;

    /** Sorted list of all registered method names (for /list_tools, manifest, debugging). */
    TArray<FString> ListMethods() const;

private:
    FUCMCPHandlerRegistry() = default;
    TMap<FString, TSharedRef<IUCMCPHandler>> Handlers;
};
