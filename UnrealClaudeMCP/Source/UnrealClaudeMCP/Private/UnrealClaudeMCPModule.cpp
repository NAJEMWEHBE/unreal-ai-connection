// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "UnrealClaudeMCPModule.h"
#include "MCP/MCPServer.h"
#include "MCP/MCPHandler.h"
#include "MCP/LogCapture.h"

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
extern TSharedRef<IUCMCPHandler> Make_Handler_DeleteActor();
extern TSharedRef<IUCMCPHandler> Make_Handler_SetActorProperty();
extern TSharedRef<IUCMCPHandler> Make_Handler_AddComponent();
extern TSharedRef<IUCMCPHandler> Make_Handler_GetLogLines();
extern TSharedRef<IUCMCPHandler> Make_Handler_ExecuteConsoleCommand();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectAsset();
extern TSharedRef<IUCMCPHandler> Make_Handler_MoveAsset();
extern TSharedRef<IUCMCPHandler> Make_Handler_RenameAsset();
extern TSharedRef<IUCMCPHandler> Make_Handler_DeleteAsset();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectSequence();
extern TSharedRef<IUCMCPHandler> Make_Handler_CreateSequence();
extern TSharedRef<IUCMCPHandler> Make_Handler_BindActorToSequence();
extern TSharedRef<IUCMCPHandler> Make_Handler_CreateMaterialInstance();
extern TSharedRef<IUCMCPHandler> Make_Handler_SetMIParameter();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectMaterial();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectMaterialInstance();
extern TSharedRef<IUCMCPHandler> Make_Handler_RunPythonFile();
extern TSharedRef<IUCMCPHandler> Make_Handler_FixUpRedirectors();

static constexpr int32 kMCPDefaultPort = 18888;

void FUnrealClaudeMCPModule::StartupModule()
{
    UE_LOG(LogUnrealClaudeMCP, Log, TEXT("[UnrealClaudeMCP] Module started"));

    // Register the log capture device before anything else so early log lines
    // (including handler registration messages) are buffered from the start.
    // CanBeUsedOnAnyThread() = true on the device opts us out of GLog's
    // serializing queue, which would otherwise stall the game thread under
    // heavy startup logging and break the FTSTicker dispatch loop.
    GLog->AddOutputDevice(&FUCMCPLogCapture::Get());

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
    Reg.Register(Make_Handler_DeleteActor());
    Reg.Register(Make_Handler_SetActorProperty());
    Reg.Register(Make_Handler_AddComponent());
    Reg.Register(Make_Handler_GetLogLines());
    Reg.Register(Make_Handler_ExecuteConsoleCommand());
    Reg.Register(Make_Handler_InspectAsset());
    Reg.Register(Make_Handler_MoveAsset());
    Reg.Register(Make_Handler_RenameAsset());
    Reg.Register(Make_Handler_DeleteAsset());
    Reg.Register(Make_Handler_InspectSequence());
    Reg.Register(Make_Handler_CreateSequence());
    Reg.Register(Make_Handler_BindActorToSequence());
    Reg.Register(Make_Handler_CreateMaterialInstance());
    Reg.Register(Make_Handler_SetMIParameter());
    Reg.Register(Make_Handler_InspectMaterial());
    Reg.Register(Make_Handler_InspectMaterialInstance());
    Reg.Register(Make_Handler_RunPythonFile());
    Reg.Register(Make_Handler_FixUpRedirectors());

    FUCMCPServer::Get().Start(kMCPDefaultPort);
}

void FUnrealClaudeMCPModule::ShutdownModule()
{
    FUCMCPServer::Get().Stop();

    // Deregister the log capture device before the module unloads so GLog
    // doesn't call into a dangling pointer.
    GLog->RemoveOutputDevice(&FUCMCPLogCapture::Get());

    UE_LOG(LogUnrealClaudeMCP, Log, TEXT("[UnrealClaudeMCP] Module shutdown"));
}

IMPLEMENT_MODULE(FUnrealClaudeMCPModule, UnrealClaudeMCP)
