// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/MCPServer.h"
#include "MCP/MCPDispatcher.h"

#include "Common/TcpListener.h"
#include "IPAddress.h"
#include "Sockets.h"
#include "SocketSubsystem.h"

DEFINE_LOG_CATEGORY_STATIC(LogUCMCP, Log, All);

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

        TArray<uint8> Buffer;
        Buffer.SetNumUninitialized(static_cast<int32>(Pending));
        int32 BytesRead = 0;
        if (!Sock->Recv(Buffer.GetData(), Buffer.Num(), BytesRead) || BytesRead <= 0)
        {
            Dropped.Add(Sock);
            continue;
        }

        const FUTF8ToTCHAR Conv(reinterpret_cast<const ANSICHAR*>(Buffer.GetData()), BytesRead);
        const FString Msg(Conv.Length(), Conv.Get());

        const FString Resp = FUCMCPDispatcher::HandleMessage(Msg);
        if (Resp.IsEmpty())
        {
            // Notification - per spec, no response
            continue;
        }

        const FTCHARToUTF8 RespConv(*Resp);
        const int32 RespLen = RespConv.Length();
        int32 BytesSent = 0;
        Sock->Send(reinterpret_cast<const uint8*>(RespConv.Get()), RespLen, BytesSent);
        if (BytesSent < RespLen)
        {
            UE_LOG(LogUCMCP, Warning,
                TEXT("Short write: sent %d of %d bytes (response truncated)"),
                BytesSent, RespLen);
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
