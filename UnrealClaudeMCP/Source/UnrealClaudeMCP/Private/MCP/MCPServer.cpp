// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/MCPServer.h"
#include "MCP/MCPDispatcher.h"

#include "Common/TcpListener.h"
#include "IPAddress.h"
#include "Sockets.h"
#include "SocketSubsystem.h"

DEFINE_LOG_CATEGORY_STATIC(LogUCMCP, Log, All);

// ---------------------------------------------------------------------------
// Wire-framing helpers  (v0.5.0)
//
// Every message on the TCP wire is:
//   <8-byte big-endian uint64 body length> <N bytes of UTF-8 JSON body>
//
// ReadFramedMessage: accumulates the 8-byte prefix then the body.
// WriteFramedMessage: prepends the 8-byte prefix before sending the body.
// ---------------------------------------------------------------------------

static bool ReadFramedMessage(FSocket* Socket, FString& OutBody, FString& OutError)
{
    // --- Step 1: read exactly 8 bytes for the length prefix ---
    uint8 PrefixBuf[8];
    int32 PrefixAccum = 0;
    while (PrefixAccum < 8)
    {
        int32 BytesRead = 0;
        const bool bOk = Socket->Recv(
            PrefixBuf + PrefixAccum,
            8 - PrefixAccum,
            BytesRead,
            ESocketReceiveFlags::None);
        if (!bOk || BytesRead <= 0)
        {
            OutError = TEXT("framing_error: socket closed while reading length prefix");
            return false;
        }
        PrefixAccum += BytesRead;
    }

    // Decode big-endian uint64
    uint64 BodyLength = 0;
    for (int32 i = 0; i < 8; ++i)
    {
        BodyLength = (BodyLength << 8) | static_cast<uint64>(PrefixBuf[i]);
    }

    if (BodyLength == 0)
    {
        OutError = TEXT("framing_error: zero-length body");
        return false;
    }
    constexpr uint64 MaxBodyBytes = 1024ULL * 1024ULL * 1024ULL; // 1 GB cap
    if (BodyLength > MaxBodyBytes)
    {
        OutError = FString::Printf(
            TEXT("framing_error: length %llu exceeds 1 GB cap"), BodyLength);
        return false;
    }

    // --- Step 2: read exactly BodyLength bytes ---
    TArray<uint8> BodyBuf;
    BodyBuf.SetNumUninitialized(static_cast<int32>(BodyLength));
    int32 BodyAccum = 0;
    while (BodyAccum < static_cast<int32>(BodyLength))
    {
        int32 BytesRead = 0;
        const bool bOk = Socket->Recv(
            BodyBuf.GetData() + BodyAccum,
            static_cast<int32>(BodyLength) - BodyAccum,
            BytesRead,
            ESocketReceiveFlags::None);
        if (!bOk || BytesRead <= 0)
        {
            OutError = TEXT("framing_error: socket closed while reading body");
            return false;
        }
        BodyAccum += BytesRead;
    }

    // Convert UTF-8 body bytes to FString
    const FUTF8ToTCHAR Conv(
        reinterpret_cast<const ANSICHAR*>(BodyBuf.GetData()),
        static_cast<int32>(BodyLength));
    OutBody = FString(Conv.Length(), Conv.Get());
    return true;
}

static bool WriteFramedMessage(FSocket* Socket, const FString& Body, FString& OutError)
{
    // Encode body as UTF-8
    const FTCHARToUTF8 Conv(*Body);
    const int32 BodyLen = Conv.Length();
    const uint8* BodyData = reinterpret_cast<const uint8*>(Conv.Get());

    // Build 8-byte big-endian length prefix (PrefixBuf[0] is the MSB)
    const uint64 LengthVal = static_cast<uint64>(BodyLen);
    uint8 PrefixBuf[8];
    for (int32 i = 0; i < 8; ++i)
    {
        PrefixBuf[i] = static_cast<uint8>((LengthVal >> (8 * (7 - i))) & 0xFF);
    }

    // --- Send the 8-byte prefix ---
    int32 PrefixSent = 0;
    while (PrefixSent < 8)
    {
        int32 BytesSent = 0;
        const bool bOk = Socket->Send(PrefixBuf + PrefixSent, 8 - PrefixSent, BytesSent);
        if (!bOk || BytesSent <= 0)
        {
            OutError = TEXT("framing_error: socket closed while sending length prefix");
            return false;
        }
        PrefixSent += BytesSent;
    }

    // --- Send the body ---
    int32 BodySent = 0;
    while (BodySent < BodyLen)
    {
        int32 BytesSent = 0;
        const bool bOk = Socket->Send(BodyData + BodySent, BodyLen - BodySent, BytesSent);
        if (!bOk || BytesSent <= 0)
        {
            OutError = TEXT("framing_error: socket closed while sending body");
            return false;
        }
        BodySent += BytesSent;
    }

    return true;
}

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
        if (!Sock)
        {
            Dropped.Add(Sock);
            continue;
        }

        const ESocketConnectionState State = Sock->GetConnectionState();
        if (State != SCS_Connected)
        {
            Dropped.Add(Sock);
            continue;
        }

        uint32 Pending = 0;
        if (!Sock->HasPendingData(Pending) || Pending == 0)
        {
            continue;
        }

        // Read the length-prefixed framed message (v0.5.0 wire format)
        FString Msg;
        FString ReadError;
        if (!ReadFramedMessage(Sock, Msg, ReadError))
        {
            UE_LOG(LogUCMCP, Warning, TEXT("ReadFramedMessage failed: %s"), *ReadError);
            Dropped.Add(Sock);
            continue;
        }

        const FString Resp = FUCMCPDispatcher::HandleMessage(Msg);
        if (Resp.IsEmpty())
        {
            // Notification - per spec, no response
            continue;
        }

        // Write the length-prefixed framed response (v0.5.0 wire format)
        FString WriteError;
        if (!WriteFramedMessage(Sock, Resp, WriteError))
        {
            UE_LOG(LogUCMCP, Warning, TEXT("WriteFramedMessage failed: %s"), *WriteError);
            Dropped.Add(Sock);
        }
    }

    for (FSocket* Sock : Dropped)
    {
        ConnectedClients.Remove(Sock);
        if (Sock && Subsystem)
        {
            Sock->Close();
            Subsystem->DestroySocket(Sock);
        }
    }

    return true;
}
