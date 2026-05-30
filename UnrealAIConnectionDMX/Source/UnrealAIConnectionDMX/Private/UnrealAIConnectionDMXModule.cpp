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

    // Register each handler and keep its TSharedRef so ShutdownModule can
    // unregister by GetMethodName() (no hardcoded method strings to drift).
    auto RegisterHandler = [this, &Reg](TSharedRef<IUCMCPHandler> Handler)
    {
        Reg.Register(Handler);
        RegisteredHandlers.Add(Handler);
    };

    RegisterHandler(Make_Handler_CreateDmxPatch());
    RegisterHandler(Make_Handler_DmxStreamSet());
    RegisterHandler(Make_Handler_DmxStreamStop());
    RegisterHandler(Make_Handler_DmxStreamStatus());
}

void FUnrealAIConnectionDMXModule::ShutdownModule()
{
    // The core module owns the registry singleton: FUCMCPHandlerRegistry::Get()
    // is a function-local static compiled into UnrealEditor-UnrealAIConnection.
    // This companion depends on the core, so UE unloads it BEFORE the core and
    // Get() is normally safe here. Guard anyway: under a forced or out-of-order
    // unload, calling into an already-unloaded core DLL would crash. Unregister
    // by each handler's own GetMethodName() so the names can never drift from
    // what StartupModule registered.
    if (FModuleManager::Get().IsModuleLoaded(TEXT("UnrealAIConnection")))
    {
        FUCMCPHandlerRegistry& Reg = FUCMCPHandlerRegistry::Get();
        for (const TSharedRef<IUCMCPHandler>& Handler : RegisteredHandlers)
        {
            Reg.Unregister(Handler->GetMethodName());
        }
    }
    RegisteredHandlers.Empty();
}

IMPLEMENT_MODULE(FUnrealAIConnectionDMXModule, UnrealAIConnectionDMX)
