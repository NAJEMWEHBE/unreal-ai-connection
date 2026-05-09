// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"
#include "Delegates/IDelegateInstance.h"

class FUnrealClaudeMCPModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    // Tier 2 (PR #40): editor-event delegate handles, retained so Shutdown
    // can detach cleanly before the engine subsystems we subscribed to are
    // torn down. Each maps 1:1 to a subscription wired in StartupModule.
    FDelegateHandle LevelActorAddedHandle;
    FDelegateHandle LevelActorDeletedHandle;
    FDelegateHandle AssetAddedHandle;

    // Tier 2 (PR #41): additional editor-event delegate handles.
    FDelegateHandle AssetRemovedHandle;
    FDelegateHandle AssetRenamedHandle;
    FDelegateHandle AssetPostImportHandle;
    FDelegateHandle PostSaveWorldHandle;
    FDelegateHandle MapChangeHandle;
};
