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

/**
 * Per-client read state. Buffers a partial frame across TickClients calls so
 * a TCP-fragmented message doesn't get mistaken for a closed connection.
 *
 * v0.9.1 wire-framing fix (Codex review on PR #10): the v0.5.0 helpers used a
 * tight loop on Recv that treated `BytesRead == 0` as fatal disconnect, but
 * non-blocking sockets return `BytesRead == 0` for "no data right now too."
 * Now each client has dedicated buffers + accumulators that survive across
 * tick boundaries.
 */
struct FUCMCPClientReadState
{
    // Phase 1: 8-byte big-endian length prefix
    uint8 PrefixBuf[8] = {};
    int32 PrefixAccum = 0;

    // Phase 2: body (size known once PrefixAccum reaches 8)
    uint64 BodyLength = 0;
    TArray<uint8> BodyBuf;
    int32 BodyAccum = 0;

    bool IsHeaderComplete() const { return PrefixAccum >= 8; }
    bool IsBodyComplete() const
    {
        return IsHeaderComplete() && BodyAccum >= static_cast<int32>(BodyLength);
    }
    void Reset()
    {
        PrefixAccum = 0;
        BodyLength = 0;
        BodyBuf.Reset();
        BodyAccum = 0;
        FMemory::Memzero(PrefixBuf, sizeof(PrefixBuf));
    }
};

/** Per-client write state. Drains pending bytes across ticks. */
struct FUCMCPClientWriteState
{
    TArray<uint8> PendingBytes;     // 8-byte prefix + UTF-8 body, concatenated
    int32 BytesSent = 0;

    bool IsDrained() const { return BytesSent >= PendingBytes.Num(); }
    void Reset()
    {
        PendingBytes.Reset();
        BytesSent = 0;
    }
};

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

    // v0.9.1: per-client partial-frame state. Keys are FSocket* — same lifetime
    // as ConnectedClients. Cleanup happens in three places: TickClients drop
    // path, Stop() iteration, destructor (via Stop).
    TMap<FSocket*, FUCMCPClientReadState> ReadStates;
    TMap<FSocket*, FUCMCPClientWriteState> WriteStates;

    FTSTicker::FDelegateHandle TickerHandle;
    int32 Port = 0;
    bool bRunning = false;
};
