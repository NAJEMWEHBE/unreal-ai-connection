// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

class FUnrealClaudeMCPModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;
};
