// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// UnrealAIConnectionDMX - optional companion plugin for Unreal AI Connection.
//
// It exists purely to keep the DMX dependency (DMXRuntime / DMXProtocol) OUT of the
// core plugin. The core plugin links neither, so UnrealEditor-UnrealAIConnection.dll
// loads on any project regardless of whether the DMX plugins are enabled. This
// companion (disabled by default) brings the 4 DMX MCP tools online when, and only
// when, a project opts in by enabling it.
//
// Mechanism: the core exposes the exported singleton FUCMCPHandlerRegistry (MCP/
// MCPHandler.h is Public). The dispatcher resolves handlers per request via
// FUCMCPHandlerRegistry::Get().Find(method), so handlers registered here at load time
// (PostEngineInit, after the core module on which we depend) are immediately visible
// to the running TCP server. ShutdownModule removes them so the registry never holds a
// TSharedRef into a module that has unloaded (Live-Coding reload safety).

#include "UnrealAIConnectionDMXModule.h"

#include "Modules/ModuleManager.h"
#include "MCP/MCPHandler.h"

// Handler factories, defined in this module's Handler_*.cpp (moved here from the core).
extern TSharedRef<IUCMCPHandler> Make_Handler_CreateDmxPatch();
extern TSharedRef<IUCMCPHandler> Make_Handler_DmxStreamSet();
extern TSharedRef<IUCMCPHandler> Make_Handler_DmxStreamStop();
extern TSharedRef<IUCMCPHandler> Make_Handler_DmxStreamStatus();

void FUnrealAIConnectionDMXModule::StartupModule()
{
    FUCMCPHandlerRegistry& Reg = FUCMCPHandlerRegistry::Get();
    Reg.Register(Make_Handler_CreateDmxPatch());
    Reg.Register(Make_Handler_DmxStreamSet());
    Reg.Register(Make_Handler_DmxStreamStop());
    Reg.Register(Make_Handler_DmxStreamStatus());
}

void FUnrealAIConnectionDMXModule::ShutdownModule()
{
    // Guard: the core module (which owns the registry singleton) may have already torn
    // down on editor exit. FUCMCPHandlerRegistry::Get() returns a function-local static
    // in the core module; if the core is still loaded this cleanly removes our entries.
    FUCMCPHandlerRegistry& Reg = FUCMCPHandlerRegistry::Get();
    Reg.Unregister(TEXT("create_dmx_patch"));
    Reg.Unregister(TEXT("dmx_stream_set"));
    Reg.Unregister(TEXT("dmx_stream_stop"));
    Reg.Unregister(TEXT("dmx_stream_status"));
}

IMPLEMENT_MODULE(FUnrealAIConnectionDMXModule, UnrealAIConnectionDMX)
