// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// dmx_stream_set / dmx_stream_stop / dmx_stream_status
//
// Continuous DMX output. A game-thread FTSTicker re-sends a held channel
// buffer to every active DMX output port every frame, so the Art-Net wire
// keeps carrying the latest values without a blocking Python loop (which
// would monopolise the game thread and freeze the wire on its first value).
//
// Threading note (important): FDMXOutputPort::SendDMX asserts IsInGameThread()
// (DMXProtocol/Private/IO/DMXOutputPort.cpp:807) — DMX output is game-thread
// only. A background FRunnable that calls SendDMX crashes. We therefore drive
// the re-send from FTSTicker::GetCoreTicker(), which fires on the game thread
// once per engine frame. Consequence: streaming flows whenever the editor is
// ticking — realtime viewport, PIE, or (the production case) Aximmetry's AX
// Engine link pulling frames. A fully idle, backgrounded editor stops ticking
// and the wire holds the last value, which is the correct hold-last-frame
// Art-Net behaviour.
//
// Params (JSON):
//   dmx_stream_set:
//     universe (int, default 1)        local universe id
//     channels ({ "ch": value, ... })  1-based channel -> 0..255
//     merge    (bool, default true)    merge into the universe's held buffer
//   dmx_stream_stop: stop the ticker, clear buffers
//   dmx_stream_status: running / frames / universes
//
// Stable error codes: missing_required_field, no_output_ports.

#include "MCP/MCPHandler.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Containers/Ticker.h"
#include "Misc/ScopeLock.h"

#include "IO/DMXPortManager.h"
#include "IO/DMXOutputPort.h"

// ---------------------------------------------------------------------------
// Game-thread streamer (FTSTicker-driven)
// ---------------------------------------------------------------------------

class FUCMCPDmxStreamer
{
public:
    /** universe id -> (1-based channel -> value). Guarded by Mutex. */
    TMap<int32, TMap<int32, uint8>> Buffers;
    FCriticalSection Mutex;

    FTSTicker::FDelegateHandle TickHandle;
    bool bRunning = false;
    int64 Frames = 0;

    bool Tick(float /*Delta*/)
    {
        // Runs on the game thread (FTSTicker) — SendDMX's IsInGameThread()
        // assertion is satisfied here.
        TMap<int32, TMap<int32, uint8>> Snapshot;
        {
            FScopeLock Lock(&Mutex);
            Snapshot = Buffers;
        }
        if (Snapshot.Num() > 0)
        {
            const TArray<FDMXOutputPortSharedRef>& Ports = FDMXPortManager::Get().GetOutputPorts();
            for (const FDMXOutputPortSharedRef& Port : Ports)
            {
                for (const TPair<int32, TMap<int32, uint8>>& U : Snapshot)
                {
                    if (U.Value.Num() > 0)
                    {
                        Port->SendDMX(U.Key, U.Value);
                    }
                }
            }
        }
        ++Frames;
        return true; // keep ticking
    }

    void EnsureRunning()
    {
        if (!bRunning)
        {
            Frames = 0;
            TickHandle = FTSTicker::GetCoreTicker().AddTicker(
                FTickerDelegate::CreateRaw(this, &FUCMCPDmxStreamer::Tick), 0.0f);
            bRunning = true;
        }
    }

    void StopAndClear()
    {
        if (bRunning)
        {
            FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
            bRunning = false;
        }
        FScopeLock Lock(&Mutex);
        Buffers.Empty();
    }
};

static FUCMCPDmxStreamer GUCMCPDmxStreamer;

// ---------------------------------------------------------------------------
// dmx_stream_set
// ---------------------------------------------------------------------------

class FHandler_DmxStreamSet : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("dmx_stream_set"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("dmx_stream_set: missing_required_field: 'channels' is required");
            return nullptr;
        }

        int32 Universe = 1;
        Params->TryGetNumberField(TEXT("universe"), Universe);

        bool bMerge = true;
        Params->TryGetBoolField(TEXT("merge"), bMerge);

        const TSharedPtr<FJsonObject>* ChannelsObj = nullptr;
        if (!Params->TryGetObjectField(TEXT("channels"), ChannelsObj) || !ChannelsObj || !ChannelsObj->IsValid())
        {
            OutError = TEXT("dmx_stream_set: missing_required_field: 'channels' object {\"ch\": value} is required");
            return nullptr;
        }

        TMap<int32, uint8> NewMap;
        for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : (*ChannelsObj)->Values)
        {
            const int32 Ch = FCString::Atoi(*Pair.Key);
            double Val = 0.0;
            Pair.Value->TryGetNumber(Val);
            if (Ch >= 1 && Ch <= 512)
            {
                NewMap.Add(Ch, static_cast<uint8>(FMath::Clamp(static_cast<int32>(Val), 0, 255)));
            }
        }

        {
            FScopeLock Lock(&GUCMCPDmxStreamer.Mutex);
            TMap<int32, uint8>& UniverseMap = GUCMCPDmxStreamer.Buffers.FindOrAdd(Universe);
            if (!bMerge)
            {
                UniverseMap.Empty();
            }
            for (const TPair<int32, uint8>& KV : NewMap)
            {
                UniverseMap.Add(KV.Key, KV.Value);
            }
        }

        const int32 NumPorts = FDMXPortManager::Get().GetOutputPorts().Num();
        if (NumPorts == 0)
        {
            OutError = TEXT("dmx_stream_set: no_output_ports: no DMX output ports configured");
            return nullptr;
        }

        GUCMCPDmxStreamer.EnsureRunning();

        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetBoolField(TEXT("running"), GUCMCPDmxStreamer.bRunning);
        Out->SetNumberField(TEXT("frames"), static_cast<double>(GUCMCPDmxStreamer.Frames));
        Out->SetNumberField(TEXT("output_ports"), NumPorts);
        Out->SetNumberField(TEXT("universe"), Universe);
        Out->SetNumberField(TEXT("channels_set"), NewMap.Num());
        return Out;
    }
};

// ---------------------------------------------------------------------------
// dmx_stream_stop
// ---------------------------------------------------------------------------

class FHandler_DmxStreamStop : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("dmx_stream_stop"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        GUCMCPDmxStreamer.StopAndClear();
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetBoolField(TEXT("running"), false);
        return Out;
    }
};

// ---------------------------------------------------------------------------
// dmx_stream_status
// ---------------------------------------------------------------------------

class FHandler_DmxStreamStatus : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("dmx_stream_status"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetBoolField(TEXT("running"), GUCMCPDmxStreamer.bRunning);
        Out->SetNumberField(TEXT("frames"), static_cast<double>(GUCMCPDmxStreamer.Frames));
        Out->SetNumberField(TEXT("output_ports"), FDMXPortManager::Get().GetOutputPorts().Num());

        TArray<TSharedPtr<FJsonValue>> Universes;
        {
            FScopeLock Lock(&GUCMCPDmxStreamer.Mutex);
            for (const TPair<int32, TMap<int32, uint8>>& UPair : GUCMCPDmxStreamer.Buffers)
            {
                TSharedPtr<FJsonObject> U = MakeShared<FJsonObject>();
                U->SetNumberField(TEXT("universe"), UPair.Key);
                U->SetNumberField(TEXT("channels"), UPair.Value.Num());
                Universes.Add(MakeShared<FJsonValueObject>(U));
            }
        }
        Out->SetArrayField(TEXT("universes"), Universes);
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_DmxStreamSet()    { return MakeShared<FHandler_DmxStreamSet>(); }
TSharedRef<IUCMCPHandler> Make_Handler_DmxStreamStop()   { return MakeShared<FHandler_DmxStreamStop>(); }
TSharedRef<IUCMCPHandler> Make_Handler_DmxStreamStatus() { return MakeShared<FHandler_DmxStreamStatus>(); }
