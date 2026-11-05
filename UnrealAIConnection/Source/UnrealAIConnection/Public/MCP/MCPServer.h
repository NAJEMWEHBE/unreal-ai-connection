// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// MCPServer - lightweight TCP listener inside the UnrealAIConnection module.
// Accepts JSON-RPC 2.0 messages from MCP clients (e.g. Claude Code) and
// routes them to FUCMCPDispatcher. Auto-starts on editor launch via the
// module's StartupModule().
//
// Bind: 127.0.0.1 only (loopback). No remote exposure by design.

#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "HAL/CriticalSection.h"
#include "Misc/ScopeLock.h"
// UNVERIFIED-COMPILE: cross-engine ticker alias. FUCMCPTicker == FTSTicker
// (>=5.0) or FTicker (4.27). See UCMCPCompat.h. Source-authored only; no
// host build performed this session.
#include "UCMCPCompat.h"

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

class UNREALAICONNECTION_API FUCMCPServer
{
public:
    static FUCMCPServer& Get();

    /** Port used when neither -UCMCPPort= nor UCMCP_PORT names a valid one. */
    static constexpr int32 kDefaultPort = 18888;

    /**
     * Parse a decimal TCP port. Returns 1..65535, or 0 when Text is empty,
     * has anything but ASCII digits (surrounding whitespace is ignored), or
     * is out of range. Pure; covered by the UCMCP.Server automation tests.
     */
    static int32 ParsePortString(const FString& Text);

    /**
     * Port to listen on, in order of precedence:
     *   1. -UCMCPPort=<n> on the editor command line
     *   2. the UCMCP_PORT environment variable (the Python bridge reads the
     *      same variable, so one setting drives both sides)
     *   3. kDefaultPort
     * An invalid value logs a warning and falls through to the next source.
     */
    static int32 ResolveConfiguredPort();

    /**
     * Open the loopback listener. Idempotent: no-op if already running.
     * The listen socket is bound synchronously, so a failed bind (port taken
     * by another editor, for example) is logged as an error here and the
     * server stays stopped (IsRunning() == false).
     */
    void Start(int32 InPort = kDefaultPort);

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

    // The bound + listening socket handed to FTcpListener's FSocket& ctor.
    // That ctor does not take ownership (bDeleteSocket = false in
    // TcpListener.h), so Stop() destroys it AFTER Listener.Reset() has joined
    // the accept thread.
    FSocket* ListenSocket = nullptr;

    TArray<FSocket*> ConnectedClients;

    // D2 (PR #226 hardening layered on #225): FTcpListener runs its accept
    // loop on its OWN FRunnable thread, so OnConnectionAccepted fires off the
    // game thread. Accepted sockets are parked here under PendingClientsCS and
    // adopted on the game thread at the top of TickClients — keeping
    // ConnectedClients/ReadStates/WriteStates strictly game-thread so #225's
    // snapshot copy and the ranged-for can never race the listener thread.
    FCriticalSection PendingClientsCS;
    TArray<FSocket*> PendingAccepted;

    // v0.9.1: per-client partial-frame state. Keys are FSocket* — same lifetime
    // as ConnectedClients. Cleanup happens in three places: TickClients drop
    // path, Stop() iteration, destructor (via Stop).
    TMap<FSocket*, FUCMCPClientReadState> ReadStates;
    TMap<FSocket*, FUCMCPClientWriteState> WriteStates;

    FUCMCPTicker::FDelegateHandle TickerHandle;
    int32 Port = 0;
    bool bRunning = false;
};
