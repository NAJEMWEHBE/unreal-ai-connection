// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// MCPServer - lightweight TCP listener inside the UnrealClaudeMCP module.
// Accepts JSON-RPC 2.0 messages from MCP clients (e.g. Claude Code) and
// routes them to FUCMCPDispatcher. Auto-starts on editor launch via the
// module's StartupModule().
//
// Bind: 127.0.0.1 only (loopback). No remote exposure by design.

#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"

class FSocket;
class FTcpListener;

class UNREALCLAUDEMCP_API FUCMCPServer
{
public:
    static FUCMCPServer& Get();

    /** Open the loopback listener. Idempotent: no-op if already running. */
    void Start(int32 InPort = 18888);

    /** Close the listener and any open client sockets. Idempotent. */
    void Stop();

    bool IsRunning() const { return bRunning; }
    int32 GetPort() const { return Port; }

private:
    FUCMCPServer();
    ~FUCMCPServer();

    FUCMCPServer(const FUCMCPServer&) = delete;
    FUCMCPServer& operator=(const FUCMCPServer&) = delete;

    /** Called by FTcpListener when a client connects. */
    bool OnConnectionAccepted(FSocket* InSocket, const FIPv4Endpoint& InEndpoint);

    /** Drains pending bytes from each connected socket and dispatches them. */
    bool TickClients(float DeltaTime);

    TUniquePtr<FTcpListener> Listener;
    TArray<FSocket*> ConnectedClients;
    FTSTicker::FDelegateHandle TickerHandle;
    int32 Port = 0;
    bool bRunning = false;
};
