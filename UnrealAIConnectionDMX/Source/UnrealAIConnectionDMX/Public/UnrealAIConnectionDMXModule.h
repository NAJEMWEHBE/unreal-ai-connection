// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleInterface.h"

/**
 * Optional companion module. On load it registers the DMX MCP handlers into the
 * core UnrealAIConnection handler registry; on unload it removes them. The core
 * plugin has no DMX dependency, so it loads on hosts without DMXEngine/DMXProtocol;
 * enabling this companion is what brings the 4 DMX tools online.
 */
class FUnrealAIConnectionDMXModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;
};
