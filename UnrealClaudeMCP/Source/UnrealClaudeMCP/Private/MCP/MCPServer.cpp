// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/MCPServer.h"
#include "MCP/MCPDispatcher.h"

#include "Common/TcpListener.h"
#include "IPAddress.h"
#include "Sockets.h"
#include "SocketSubsystem.h"
#include "SocketTypes.h"

DEFINE_LOG_CATEGORY_STATIC(LogUCMCP, Log, All);

// ---------------------------------------------------------------------------
// v0.9.1 wire-framing state machine
//
// v0.5.0 added length-prefixed framing (8-byte big-endian length + body) but
// the helpers used a tight all-or-nothing loop on Recv/Send, treating any
// `BytesRead == 0` as a fatal disconnect. Codex review on PR #10 (P1 ×2)
// caught that this is wrong on non-blocking sockets — `BytesRead == 0` with
// `bOk == true` means "no data right now," which UE itself treats as
// success-with-retry on streaming sockets (verified at SocketsBSD.cpp).
//
// This rewrite adds per-client read/write state buffered across TickClients
// invocations. AdvanceRead / AdvanceWrite return a tri-state:
//   - Complete:   a whole frame is now ready (read) or fully sent (write)
//   - InProgress: would-block; resume next tick
//   - Disconnect: real error; drop the client
//
// IsWouldBlock disambiguates via ISocketSubsystem::GetLastErrorCode() ==
// SE_EWOULDBLOCK — same pattern UE 5.7's BSD socket implementation uses.
// ---------------------------------------------------------------------------

enum class EUCMCPFrameResult : uint8
{
    Complete,
    InProgress,
    Disconnect,
};

/** True if the most recent socket call's last error is SE_EWOULDBLOCK. */
static bool IsWouldBlock()
{
    ISocketSubsystem* Subsys = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    if (!Subsys)
    {
        return false;
    }
    const ESocketErrors LastError = Subsys->GetLastErrorCode();
    return LastError == ESocketErrors::SE_EWOULDBLOCK;
}

/**
 * Advance the per-client read state by whatever bytes are available right now.
 * Returns Complete only when a full frame has assembled in State.BodyBuf.
 */
static EUCMCPFrameResult AdvanceRead(FSocket* Socket, FUCMCPClientReadState& State)
{
    // --- Phase 1: read the 8-byte big-endian length prefix ---
    while (State.PrefixAccum < 8)
    {
        int32 BytesRead = 0;
        const bool bOk = Socket->Recv(
            State.PrefixBuf + State.PrefixAccum,
            8 - State.PrefixAccum,
            BytesRead,
            ESocketReceiveFlags::None);
        if (!bOk || BytesRead == 0)
        {
            return IsWouldBlock() ? EUCMCPFrameResult::InProgress
                                  : EUCMCPFrameResult::Disconnect;
        }
        State.PrefixAccum += BytesRead;
    }

    // --- Phase 1 → 2 transition: decode prefix, allocate body buffer ---
    if (State.BodyLength == 0)
    {
        uint64 BodyLen = 0;
        for (int32 i = 0; i < 8; ++i)
        {
            BodyLen = (BodyLen << 8) | static_cast<uint64>(State.PrefixBuf[i]);
        }
        if (BodyLen == 0)
        {
            UE_LOG(LogUCMCP, Warning, TEXT("framing_error: zero-length body"));
            return EUCMCPFrameResult::Disconnect;
        }
        constexpr uint64 MaxBodyBytes = 1024ULL * 1024ULL * 1024ULL; // 1 GB
        if (BodyLen > MaxBodyBytes)
        {
            UE_LOG(LogUCMCP, Warning,
                TEXT("framing_error: body length %llu exceeds 1 GB cap"), BodyLen);
            return EUCMCPFrameResult::Disconnect;
        }
        State.BodyLength = BodyLen;
        State.BodyBuf.SetNumUninitialized(static_cast<int32>(BodyLen));
        State.BodyAccum = 0;
    }

    // --- Phase 2: read BodyLength bytes ---
    while (State.BodyAccum < static_cast<int32>(State.BodyLength))
    {
        int32 BytesRead = 0;
        const bool bOk = Socket->Recv(
            State.BodyBuf.GetData() + State.BodyAccum,
            static_cast<int32>(State.BodyLength) - State.BodyAccum,
            BytesRead,
            ESocketReceiveFlags::None);
        if (!bOk || BytesRead == 0)
        {
            return IsWouldBlock() ? EUCMCPFrameResult::InProgress
                                  : EUCMCPFrameResult::Disconnect;
        }
        State.BodyAccum += BytesRead;
    }

    return EUCMCPFrameResult::Complete;
}

/** Build the [8-byte prefix | UTF-8 body] byte array for sending. */
static void EncodeFrame(const FString& Body, TArray<uint8>& OutBytes)
{
    const FTCHARToUTF8 Conv(*Body);
    const int32 BodyLen = Conv.Length();
    const uint8* BodyData = reinterpret_cast<const uint8*>(Conv.Get());

    OutBytes.SetNumUninitialized(8 + BodyLen);
    const uint64 LengthVal = static_cast<uint64>(BodyLen);
    for (int32 i = 0; i < 8; ++i)
    {
        OutBytes[i] = static_cast<uint8>((LengthVal >> (8 * (7 - i))) & 0xFF);
    }
    if (BodyLen > 0)
    {
        FMemory::Memcpy(OutBytes.GetData() + 8, BodyData, BodyLen);
    }
}

/**
 * Advance the per-client write state by whatever bytes the OS will accept now.
 * Returns Complete only when State.PendingBytes has fully drained.
 */
static EUCMCPFrameResult AdvanceWrite(FSocket* Socket, FUCMCPClientWriteState& State)
{
    while (!State.IsDrained())
    {
        const int32 Remaining = State.PendingBytes.Num() - State.BytesSent;
        int32 BytesSent = 0;
        const bool bOk = Socket->Send(
            State.PendingBytes.GetData() + State.BytesSent,
            Remaining,
            BytesSent);
        if (!bOk || BytesSent <= 0)
        {
            return IsWouldBlock() ? EUCMCPFrameResult::InProgress
                                  : EUCMCPFrameResult::Disconnect;
        }
        State.BytesSent += BytesSent;
    }
    return EUCMCPFrameResult::Complete;
}

/** Convert a complete read state's body bytes to FString. */
static FString DecodeBody(const FUCMCPClientReadState& State)
{
    const FUTF8ToTCHAR Conv(
        reinterpret_cast<const ANSICHAR*>(State.BodyBuf.GetData()),
        static_cast<int32>(State.BodyLength));
    return FString(Conv.Length(), Conv.Get());
}

// ---------------------------------------------------------------------------
// FUCMCPServer
// ---------------------------------------------------------------------------

FUCMCPServer& FUCMCPServer::Get()
{
    static FUCMCPServer Instance;
    return Instance;
}

// Defined here (not =default in header) so TUniquePtr<FTcpListener> sees
// the complete FTcpListener type when generating the destructor.
FUCMCPServer::FUCMCPServer() = default;

FUCMCPServer::~FUCMCPServer()
{
    Stop();
}

void FUCMCPServer::Start(int32 InPort)
{
    if (bRunning)
    {
        UE_LOG(LogUCMCP, Verbose, TEXT("Start ignored: already running on port %d"), Port);
        return;
    }
    Port = InPort;

    const FIPv4Endpoint Endpoint(FIPv4Address::InternalLoopback, static_cast<uint16>(Port));
    Listener = MakeUnique<FTcpListener>(Endpoint);
    Listener->OnConnectionAccepted().BindRaw(this, &FUCMCPServer::OnConnectionAccepted);

    TickerHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateRaw(this, &FUCMCPServer::TickClients),
        0.05f
    );

    bRunning = true;
    UE_LOG(LogUCMCP, Log, TEXT("Listening on 127.0.0.1:%d"), Port);
}

void FUCMCPServer::Stop()
{
    if (!bRunning)
    {
        return;
    }

    if (TickerHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickerHandle);
        TickerHandle.Reset();
    }

    Listener.Reset();

    ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    for (FSocket* Sock : ConnectedClients)
    {
        if (Sock)
        {
            Sock->Close();
            if (Subsystem)
            {
                Subsystem->DestroySocket(Sock);
            }
        }
    }
    ConnectedClients.Empty();
    ReadStates.Empty();
    WriteStates.Empty();
    bRunning = false;
    UE_LOG(LogUCMCP, Log, TEXT("Stopped"));
}

bool FUCMCPServer::OnConnectionAccepted(FSocket* InSocket, const FIPv4Endpoint& InEndpoint)
{
    if (!InSocket)
    {
        return false;
    }
    InSocket->SetNonBlocking(true);
    ConnectedClients.Add(InSocket);
    // Explicit insertions so the per-client state lifetime is visible at the
    // accept site rather than implicit via FindOrAdd later.
    ReadStates.Add(InSocket, FUCMCPClientReadState{});
    WriteStates.Add(InSocket, FUCMCPClientWriteState{});
    UE_LOG(LogUCMCP, Log, TEXT("Client connected from %s (now %d clients)"),
        *InEndpoint.ToString(), ConnectedClients.Num());
    return true;
}

bool FUCMCPServer::TickClients(float /*DeltaTime*/)
{
    ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    TArray<FSocket*> Dropped;

    for (FSocket* Sock : ConnectedClients)
    {
        if (!Sock || Sock->GetConnectionState() != SCS_Connected)
        {
            Dropped.Add(Sock);
            continue;
        }

        // Step 1: drain any pending write state first. If a previous tick left
        // a partial response in flight, push more bytes before reading new
        // requests — keeps responses ordered ahead of the next read.
        FUCMCPClientWriteState& W = WriteStates.FindOrAdd(Sock);
        if (!W.IsDrained())
        {
            const EUCMCPFrameResult WR = AdvanceWrite(Sock, W);
            if (WR == EUCMCPFrameResult::Disconnect)
            {
                Dropped.Add(Sock);
                continue;
            }
            if (WR == EUCMCPFrameResult::InProgress)
            {
                // Still flushing; reads can wait until the response goes out.
                continue;
            }
            // Complete — fall through to reads.
            W.Reset();
        }

        // Step 2: drain as many complete read frames as are available this
        // tick. The 32-frame safety bound prevents one busy client from
        // monopolizing the tick; fairness across clients comes from the
        // outer ConnectedClients loop.
        FUCMCPClientReadState& R = ReadStates.FindOrAdd(Sock);
        bool bDropAfter = false;
        for (int32 SafetyBound = 0; SafetyBound < 32; ++SafetyBound)
        {
            const EUCMCPFrameResult RR = AdvanceRead(Sock, R);
            if (RR == EUCMCPFrameResult::Disconnect)
            {
                bDropAfter = true;
                break;
            }
            if (RR == EUCMCPFrameResult::InProgress)
            {
                // No more bytes ready right now; resume next tick.
                break;
            }

            // RR == Complete: extract the body, dispatch, queue the response.
            const FString Body = DecodeBody(R);
            R.Reset();

            const FString Resp = FUCMCPDispatcher::HandleMessage(Body);
            if (Resp.IsEmpty())
            {
                // Notification — per JSON-RPC spec, no response. Try next frame.
                continue;
            }

            EncodeFrame(Resp, W.PendingBytes);
            W.BytesSent = 0;
            const EUCMCPFrameResult WR2 = AdvanceWrite(Sock, W);
            if (WR2 == EUCMCPFrameResult::Disconnect)
            {
                bDropAfter = true;
                break;
            }
            if (WR2 == EUCMCPFrameResult::InProgress)
            {
                // Response queued but not fully drained; resume next tick.
                break;
            }
            // Send fully drained — clear and keep reading more frames.
            W.Reset();
        }

        if (bDropAfter)
        {
            Dropped.Add(Sock);
        }
    }

    for (FSocket* Sock : Dropped)
    {
        ConnectedClients.Remove(Sock);
        ReadStates.Remove(Sock);
        WriteStates.Remove(Sock);
        if (Sock && Subsystem)
        {
            Sock->Close();
            Subsystem->DestroySocket(Sock);
        }
    }

    return true;
}
