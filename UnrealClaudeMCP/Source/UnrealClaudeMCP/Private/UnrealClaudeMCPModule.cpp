// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "UnrealClaudeMCPModule.h"
#include "MCP/MCPServer.h"
#include "MCP/MCPHandler.h"
#include "MCP/LogCapture.h"
#include "MCP/EventBus.h"

// Tier 2 event-push delegate sources (PR #40)
#include "Engine/Engine.h"           // UEngine::OnLevelActorAdded/Deleted (Engine.h:2200/2207)
#include "Engine/World.h"            // UWorld::GetPathName for actor.level
#include "GameFramework/Actor.h"     // AActor::GetActorLabel/GetName/GetClass
#include "AssetRegistry/IAssetRegistry.h"        // OnAssetAdded (IAssetRegistry.h:923, TS_ delegate)
#include "AssetRegistry/AssetRegistryModule.h"   // FAssetRegistryModule loader
#include "AssetRegistry/AssetData.h" // FAssetData fields read by the asset_added payload builder
#include "Dom/JsonObject.h"

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
extern TSharedRef<IUCMCPHandler> Make_Handler_ApplyPythonToSelection();
extern TSharedRef<IUCMCPHandler> Make_Handler_CompileBlueprint();
extern TSharedRef<IUCMCPHandler> Make_Handler_GetConsoleVariable();
extern TSharedRef<IUCMCPHandler> Make_Handler_SetConsoleVariable();
extern TSharedRef<IUCMCPHandler> Make_Handler_PollEvents();

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
    Reg.Register(Make_Handler_ApplyPythonToSelection());
    Reg.Register(Make_Handler_CompileBlueprint());
    Reg.Register(Make_Handler_GetConsoleVariable());
    Reg.Register(Make_Handler_SetConsoleVariable());
    Reg.Register(Make_Handler_PollEvents());

    // -----------------------------------------------------------------
    // Tier 2 (PR #40): wire 3 starter delegates into the FUCMCPEventBus.
    //
    // Each subscription is a lambda that builds the event-specific JSON
    // payload and calls Bus.Push. The bus is type-agnostic — adding
    // new event sources later means adding more lambdas here, no changes
    // to EventBus.{h,cpp} required.
    //
    // Handles are retained on the module so ShutdownModule can detach
    // before the engine/registry subsystems tear down.
    // -----------------------------------------------------------------

    if (GEngine)
    {
        LevelActorAddedHandle = GEngine->OnLevelActorAdded().AddLambda(
            [](AActor* Actor)
            {
                if (!Actor) { return; }
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetStringField(TEXT("actor_label"), Actor->GetActorLabel());
                Data->SetStringField(TEXT("actor_name"), Actor->GetName());
                Data->SetStringField(TEXT("class"),
                    Actor->GetClass() ? Actor->GetClass()->GetName() : TEXT(""));
                UWorld* World = Actor->GetWorld();
                Data->SetStringField(TEXT("level"),
                    World ? World->GetPathName() : TEXT(""));
                FUCMCPEventBus::Get().Push(TEXT("actor_spawned"), Data);
            });

        LevelActorDeletedHandle = GEngine->OnLevelActorDeleted().AddLambda(
            [](AActor* Actor)
            {
                if (!Actor) { return; }
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetStringField(TEXT("actor_label"), Actor->GetActorLabel());
                Data->SetStringField(TEXT("actor_name"), Actor->GetName());
                Data->SetStringField(TEXT("class"),
                    Actor->GetClass() ? Actor->GetClass()->GetName() : TEXT(""));
                UWorld* World = Actor->GetWorld();
                Data->SetStringField(TEXT("level"),
                    World ? World->GetPathName() : TEXT(""));
                FUCMCPEventBus::Get().Push(TEXT("actor_deleted"), Data);
            });
    }

    {
        // OnAssetAdded is a TS_ multicast (IAssetRegistry.h:922) — fires from
        // background asset-registry scan threads. The bus's locking handles
        // this; nothing special needed at the call site.
        IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
            TEXT("AssetRegistry")).Get();
        AssetAddedHandle = AR.OnAssetAdded().AddLambda(
            [](const FAssetData& AssetData)
            {
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetStringField(TEXT("package_path"), AssetData.PackageName.ToString());
                Data->SetStringField(TEXT("asset_path"), AssetData.GetObjectPathString());
                Data->SetStringField(TEXT("name"), AssetData.AssetName.ToString());
                Data->SetStringField(TEXT("class"),
                    AssetData.AssetClassPath.GetAssetName().ToString());
                Data->SetStringField(TEXT("class_path"),
                    AssetData.AssetClassPath.ToString());
                FUCMCPEventBus::Get().Push(TEXT("asset_added"), Data);
            });
    }

    FUCMCPServer::Get().Start(kMCPDefaultPort);
}

void FUnrealClaudeMCPModule::ShutdownModule()
{
    FUCMCPServer::Get().Stop();

    // Detach Tier 2 event-bus subscriptions before the engine subsystems
    // we subscribed to tear down. Guard each removal -- in some shutdown
    // orderings GEngine may already be null even though we're still running.
    if (GEngine)
    {
        if (LevelActorAddedHandle.IsValid())
        {
            GEngine->OnLevelActorAdded().Remove(LevelActorAddedHandle);
            LevelActorAddedHandle.Reset();
        }
        if (LevelActorDeletedHandle.IsValid())
        {
            GEngine->OnLevelActorDeleted().Remove(LevelActorDeletedHandle);
            LevelActorDeletedHandle.Reset();
        }
    }

    if (AssetAddedHandle.IsValid())
    {
        // Use GetModuleChecked here (not LoadModuleChecked) -- shutdown is
        // not the time to load modules. If AssetRegistry is already gone,
        // we leak the handle and that's fine (the broadcaster is gone too).
        if (FAssetRegistryModule* ARModule = FModuleManager::GetModulePtr<FAssetRegistryModule>(
                TEXT("AssetRegistry")))
        {
            ARModule->Get().OnAssetAdded().Remove(AssetAddedHandle);
        }
        AssetAddedHandle.Reset();
    }

    // Deregister the log capture device before the module unloads so GLog
    // doesn't call into a dangling pointer.
    GLog->RemoveOutputDevice(&FUCMCPLogCapture::Get());

    UE_LOG(LogUnrealClaudeMCP, Log, TEXT("[UnrealClaudeMCP] Module shutdown"));
}

IMPLEMENT_MODULE(FUnrealClaudeMCPModule, UnrealClaudeMCP)
