// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "UnrealClaudeMCPModule.h"
#include "MCP/MCPServer.h"
#include "MCP/MCPHandler.h"

DEFINE_LOG_CATEGORY_STATIC(LogUnrealClaudeMCP, Log, All);

// Forward-declared handler factories (one per Handler_*.cpp file in MCP/Handlers/)
extern TSharedRef<IUCMCPHandler> Make_Handler_ExecutePython();
extern TSharedRef<IUCMCPHandler> Make_Handler_GetProjectSummary();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectBlueprint();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectWidgetTree();
extern TSharedRef<IUCMCPHandler> Make_Handler_EditWidgetTree();
extern TSharedRef<IUCMCPHandler> Make_Handler_GetViewportScreenshot();
extern TSharedRef<IUCMCPHandler> Make_Handler_ListTools();
extern TSharedRef<IUCMCPHandler> Make_Handler_GetActorsInLevel();
extern TSharedRef<IUCMCPHandler> Make_Handler_FocusActor();
extern TSharedRef<IUCMCPHandler> Make_Handler_LoadLevel();
extern TSharedRef<IUCMCPHandler> Make_Handler_TakeHighResScreenshot();
extern TSharedRef<IUCMCPHandler> Make_Handler_ImportTexture();
extern TSharedRef<IUCMCPHandler> Make_Handler_ConfigureTexture();
extern TSharedRef<IUCMCPHandler> Make_Handler_FindAssets();
extern TSharedRef<IUCMCPHandler> Make_Handler_SpawnActor();
extern TSharedRef<IUCMCPHandler> Make_Handler_SetActorTransform();

static constexpr int32 kMCPDefaultPort = 18888;

void FUnrealClaudeMCPModule::StartupModule()
{
    UE_LOG(LogUnrealClaudeMCP, Log, TEXT("[UnrealClaudeMCP] Module started"));

    FUCMCPHandlerRegistry& Reg = FUCMCPHandlerRegistry::Get();
    Reg.Register(Make_Handler_ExecutePython());
    Reg.Register(Make_Handler_GetProjectSummary());
    Reg.Register(Make_Handler_InspectBlueprint());
    Reg.Register(Make_Handler_InspectWidgetTree());
    Reg.Register(Make_Handler_EditWidgetTree());
    Reg.Register(Make_Handler_GetViewportScreenshot());
    Reg.Register(Make_Handler_ListTools());
    Reg.Register(Make_Handler_GetActorsInLevel());
    Reg.Register(Make_Handler_FocusActor());
    Reg.Register(Make_Handler_LoadLevel());
    Reg.Register(Make_Handler_TakeHighResScreenshot());
    Reg.Register(Make_Handler_ImportTexture());
    Reg.Register(Make_Handler_ConfigureTexture());
    Reg.Register(Make_Handler_FindAssets());
    Reg.Register(Make_Handler_SpawnActor());
    Reg.Register(Make_Handler_SetActorTransform());

    FUCMCPServer::Get().Start(kMCPDefaultPort);
}

void FUnrealClaudeMCPModule::ShutdownModule()
{
    FUCMCPServer::Get().Stop();
    UE_LOG(LogUnrealClaudeMCP, Log, TEXT("[UnrealClaudeMCP] Module shutdown"));
}

IMPLEMENT_MODULE(FUnrealClaudeMCPModule, UnrealClaudeMCP)
