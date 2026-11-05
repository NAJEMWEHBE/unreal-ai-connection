// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/MCPServer.h"
#include "MCP/MCPDispatcher.h"

#include "Common/TcpListener.h"
#include "HAL/PlatformMisc.h"
#include "IPAddress.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
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

int32 FUCMCPServer::ParsePortString(const FString& Text)
{
    const FString Trimmed = Text.TrimStartAndEnd();
    // 1..5 ASCII digits only: rejects "", "-1", "+5", "1.5", "0x50", "18888abc".
    if (Trimmed.IsEmpty() || Trimmed.Len() > 5)
    {
        return 0;
    }
    for (int32 Index = 0; Index < Trimmed.Len(); ++Index)
    {
        const TCHAR Ch = Trimmed[Index];
        if (Ch < TEXT('0') || Ch > TEXT('9'))
        {
            return 0;
        }
    }
    const int32 Value = FCString::Atoi(*Trimmed);   // <= 99999, no overflow
    return (Value >= 1 && Value <= 65535) ? Value : 0;
}

int32 FUCMCPServer::ResolveConfiguredPort()
{
    // 1. -UCMCPPort=<n> on the command line.
    FString CmdLineValue;
    if (FParse::Value(FCommandLine::Get(), TEXT("UCMCPPort="), CmdLineValue))
    {
        const int32 Parsed = ParsePortString(CmdLineValue);
        if (Parsed != 0)
        {
            UE_LOG(LogUCMCP, Log, TEXT("Port %d from -UCMCPPort on the command line"), Parsed);
            return Parsed;
        }
        UE_LOG(LogUCMCP, Warning,
            TEXT("Ignoring -UCMCPPort=%s: not a port number in 1..65535"), *CmdLineValue);
    }

    // 2. UCMCP_PORT environment variable (shared with the Python bridge).
    const FString EnvValue = FPlatformMisc::GetEnvironmentVariable(TEXT("UCMCP_PORT"));
    if (!EnvValue.IsEmpty())
    {
        const int32 Parsed = ParsePortString(EnvValue);
        if (Parsed != 0)
        {
            UE_LOG(LogUCMCP, Log, TEXT("Port %d from the UCMCP_PORT environment variable"), Parsed);
            return Parsed;
        }
        UE_LOG(LogUCMCP, Warning,
            TEXT("Ignoring UCMCP_PORT=%s: not a port number in 1..65535"), *EnvValue);
    }

    // 3. Default.
    return kDefaultPort;
}

void FUCMCPServer::Start(int32 InPort)
{
    if (bRunning)
    {
        UE_LOG(LogUCMCP, Verbose, TEXT("Start ignored: already running on port %d"), Port);
        return;
    }
    if (InPort < 1 || InPort > 65535)
    {
        UE_LOG(LogUCMCP, Warning, TEXT("Port %d is out of range; using %d"), InPort, kDefaultPort);
        InPort = kDefaultPort;
    }
    Port = InPort;

    ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    if (!Subsystem)
    {
        UE_LOG(LogUCMCP, Error, TEXT("NOT listening: no socket subsystem; the MCP server is off"));
        return;
    }

    // Build the listen socket here instead of letting FTcpListener do it.
    // FTcpListener(const FIPv4Endpoint&) creates and binds its socket inside
    // FRunnable::Init() on its own thread, so a failed bind is invisible to
    // this function, and the old code logged "Listening" either way. The
    // FTcpListener(FSocket&) ctor takes an already-listening socket, so we
    // know the bind result before claiming success.
    //
    // SO_REUSEADDR is deliberately OFF. FTcpListener's own default is
    // reusable = true, and on Windows SO_REUSEADDR lets a second socket bind
    // a port that is already listening: a second editor would "succeed" and
    // the two would split connections at random. UE's SetReuseAddr also sets
    // SO_REUSEPORT on Linux/macOS (SocketsBSD.cpp, SocketSubsystemMac.h),
    // which has the same effect there. Without it the second editor gets
    // SE_EADDRINUSE and says so below.
    const FIPv4Endpoint Endpoint(FIPv4Address::InternalLoopback, static_cast<uint16>(Port));
    const TSharedRef<FInternetAddr> BindAddr = Endpoint.ToInternetAddr();
    FSocket* NewSocket = Subsystem->CreateSocket(NAME_Stream, TEXT("UCMCP listener"), BindAddr->GetProtocolType());
    if (!NewSocket)
    {
        UE_LOG(LogUCMCP, Error, TEXT("NOT listening on 127.0.0.1:%d: could not create a socket (%s)"),
            Port, Subsystem->GetSocketError(Subsystem->GetLastErrorCode()));
        return;
    }

    const TCHAR* FailedStep = nullptr;
    if (!NewSocket->SetReuseAddr(false))
    {
        FailedStep = TEXT("SetReuseAddr");
    }
    else if (!NewSocket->Bind(*BindAddr))
    {
        FailedStep = TEXT("bind");
    }
    else if (!NewSocket->Listen(8))
    {
        FailedStep = TEXT("listen");
    }
    else if (!NewSocket->SetNonBlocking(true))
    {
        FailedStep = TEXT("SetNonBlocking");
    }

    if (FailedStep)
    {
        // Read the error before DestroySocket can overwrite it.
        const ESocketErrors Err = Subsystem->GetLastErrorCode();
        NewSocket->Close();
        Subsystem->DestroySocket(NewSocket);

        if (Err == ESocketErrors::SE_EADDRINUSE)
        {
            UE_LOG(LogUCMCP, Error,
                TEXT("NOT listening: port %d is already in use (another Unreal Editor with this plugin, ")
                TEXT("or another program, holds it). MCP clients pointed at %d are talking to that ")
                TEXT("process, not this editor. To run several editors, start each one with its own ")
                TEXT("port, e.g. -UCMCPPort=18889 on the editor command line (or set UCMCP_PORT), and ")
                TEXT("give the bridge the same UCMCP_PORT."),
                Port, Port);
        }
        else
        {
            UE_LOG(LogUCMCP, Error,
                TEXT("NOT listening on 127.0.0.1:%d: %s failed (%s). Pick another port with ")
                TEXT("-UCMCPPort=<n> or UCMCP_PORT."),
                Port, FailedStep, Subsystem->GetSocketError(Err));
        }
        return;
    }

    // Matches what FTcpListener::Init() sets on its own socket; accepted
    // sockets inherit it on the platforms we ship. Best effort.
    int32 ActualSendBufferSize = 0;
    NewSocket->SetSendBufferSize(2 * 1024 * 1024, ActualSendBufferSize);

    ListenSocket = NewSocket;
    // Default args only: the ctor's bInReusable flag is read solely when
    // FTcpListener builds its own socket (Init() skips that when given one),
    // and older engines' FSocket& ctor doesn't have the parameter at all.
    Listener = MakeUnique<FTcpListener>(*ListenSocket);
    Listener->OnConnectionAccepted().BindRaw(this, &FUCMCPServer::OnConnectionAccepted);

    // UNVERIFIED-COMPILE: FUCMCPTicker resolves to FTSTicker (>=5.0) or
    // FTicker (4.27). FTickerDelegate is the spelling on both. Shim defined
    // in UCMCPCompat.h (included via MCPServer.h). No host build this session.
    TickerHandle = FUCMCPTicker::GetCoreTicker().AddTicker(
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
        // UNVERIFIED-COMPILE: see AddTicker note above (FUCMCPTicker shim).
        FUCMCPTicker::GetCoreTicker().RemoveTicker(TickerHandle);
        TickerHandle.Reset();
    }

    // Reset the listener first: ~FTcpListener blocking-joins its accept thread,
    // so no further OnConnectionAccepted can race PendingAccepted after this.
    Listener.Reset();

    ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);

    // The FSocket& ctor left ownership with us; the accept thread is joined,
    // so nothing else can touch the listen socket now.
    if (ListenSocket)
    {
        ListenSocket->Close();
        if (Subsystem)
        {
            Subsystem->DestroySocket(ListenSocket);
        }
        ListenSocket = nullptr;
    }

    // D2: destroy sockets accepted on the listener thread but never adopted.
    {
        FScopeLock Lock(&PendingClientsCS);
        for (FSocket* Sock : PendingAccepted)
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
        PendingAccepted.Reset();
    }

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
    // D2: FTcpListener invokes this on its OWN thread. Park the socket under
    // the lock; the game thread adopts it at the top of TickClients. Never
    // touch ConnectedClients/ReadStates/WriteStates here — cross-thread
    // mutation of those is exactly the D2 race #225 left open.
    {
        FScopeLock Lock(&PendingClientsCS);
        PendingAccepted.Add(InSocket);
    }
    UE_LOG(LogUCMCP, Log, TEXT("Client connected from %s (pending adoption)"),
        *InEndpoint.ToString());
    return true;
}

bool FUCMCPServer::TickClients(float /*DeltaTime*/)
{
    ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);

    // D2: adopt sockets accepted on the FTcpListener thread BEFORE #225's
    // snapshot below, so every ConnectedClients/ReadStates/WriteStates
    // mutation is game-thread-only and the snapshot copy can't race the
    // listener thread.
    {
        TArray<FSocket*> NewlyAccepted;
        {
            FScopeLock Lock(&PendingClientsCS);
            NewlyAccepted = MoveTemp(PendingAccepted);
        }
        for (FSocket* Sock : NewlyAccepted)
        {
            ConnectedClients.Add(Sock);
            ReadStates.Add(Sock, FUCMCPClientReadState{});
            WriteStates.Add(Sock, FUCMCPClientWriteState{});
            UE_LOG(LogUCMCP, Log, TEXT("Client adopted (now %d clients)"),
                ConnectedClients.Num());
        }
    }

    TArray<FSocket*> Dropped;

    // A handler dispatched below (e.g. execute_unreal_python running quit_editor,
    // or execute_console_command "Exit") can reentrantly call FUCMCPServer::Stop()
    // -> ConnectedClients.Empty() + socket destruction, and OnConnectionAccepted can
    // Add() mid-tick. Iterate a snapshot so this ranged-for can't be invalidated;
    // the bRunning bail-outs below prevent touching sockets Stop() already destroyed.
    TArray<FSocket*> ClientsThisTick = ConnectedClients;
    for (FSocket* Sock : ClientsThisTick)
    {
        if (!bRunning) { return false; }

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
            if (!bRunning) { return false; }   // a handler tore the server down (quit/Exit); sockets already destroyed by Stop()
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

    if (!bRunning) { return false; }   // Stop() already closed/destroyed all sockets and emptied the maps
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
