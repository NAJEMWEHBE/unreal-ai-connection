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
#include "AssetRegistry/IAssetRegistry.h"        // OnAssetAdded/Removed/Renamed (IAssetRegistry.h:923/930/936, all TS_)
#include "AssetRegistry/AssetRegistryModule.h"   // FAssetRegistryModule loader
#include "AssetRegistry/AssetData.h" // FAssetData fields read by the asset_* payload builders
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

// Tier 2 event-push delegate sources (PR #41)
#include "Editor.h"                  // FEditorDelegates (OnAssetPostImport / PostSaveWorldWithContext / MapChange)
#include "Factories/Factory.h"       // UFactory* param of OnAssetPostImport (Editor.h:108/295)
#include "UObject/ObjectSaveContext.h"  // FObjectPostSaveContext value param of PostSaveWorldWithContext

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
extern TSharedRef<IUCMCPHandler> Make_Handler_DuplicateAsset();
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
extern TSharedRef<IUCMCPHandler> Make_Handler_RegisterSubscription();
extern TSharedRef<IUCMCPHandler> Make_Handler_Unsubscribe();
extern TSharedRef<IUCMCPHandler> Make_Handler_PollSubscription();
extern TSharedRef<IUCMCPHandler> Make_Handler_StartSleepTask();
extern TSharedRef<IUCMCPHandler> Make_Handler_PollTask();
extern TSharedRef<IUCMCPHandler> Make_Handler_CancelTask();
extern TSharedRef<IUCMCPHandler> Make_Handler_ListTasks();
extern TSharedRef<IUCMCPHandler> Make_Handler_ExecPythonPersistent();
extern TSharedRef<IUCMCPHandler> Make_Handler_ResetPythonState();
extern TSharedRef<IUCMCPHandler> Make_Handler_FindConsoleVariables();
extern TSharedRef<IUCMCPHandler> Make_Handler_InspectStaticMesh();

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
    Reg.Register(Make_Handler_DuplicateAsset());
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
    Reg.Register(Make_Handler_RegisterSubscription());
    Reg.Register(Make_Handler_Unsubscribe());
    Reg.Register(Make_Handler_PollSubscription());
    Reg.Register(Make_Handler_StartSleepTask());
    Reg.Register(Make_Handler_PollTask());
    Reg.Register(Make_Handler_CancelTask());
    Reg.Register(Make_Handler_ListTasks());
    Reg.Register(Make_Handler_ExecPythonPersistent());
    Reg.Register(Make_Handler_ResetPythonState());
    Reg.Register(Make_Handler_FindConsoleVariables());
    Reg.Register(Make_Handler_InspectStaticMesh());

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
        // Asset-registry events are TS_ multicasts -- fire from background
        // registry scan threads. Bus locking handles this; nothing special
        // at the call site.
        IAssetRegistry& AR = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(
            TEXT("AssetRegistry")).Get();

        // PR #40: asset_added (initial scan + post-import + in-memory creation).
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

        // PR #41: asset_removed -- IAssetRegistry.h:930
        AssetRemovedHandle = AR.OnAssetRemoved().AddLambda(
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
                FUCMCPEventBus::Get().Push(TEXT("asset_removed"), Data);
            });

        // PR #41: asset_renamed -- IAssetRegistry.h:936 (two params: new
        // FAssetData + old object path string)
        AssetRenamedHandle = AR.OnAssetRenamed().AddLambda(
            [](const FAssetData& AssetData, const FString& OldObjectPath)
            {
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetStringField(TEXT("new_asset_path"), AssetData.GetObjectPathString());
                Data->SetStringField(TEXT("old_asset_path"), OldObjectPath);
                Data->SetStringField(TEXT("new_package_path"), AssetData.PackageName.ToString());
                Data->SetStringField(TEXT("name"), AssetData.AssetName.ToString());
                Data->SetStringField(TEXT("class"),
                    AssetData.AssetClassPath.GetAssetName().ToString());
                Data->SetStringField(TEXT("class_path"),
                    AssetData.AssetClassPath.ToString());
                FUCMCPEventBus::Get().Push(TEXT("asset_renamed"), Data);
            });
    }

    {
        // PR #41: editor delegates (FEditorDelegates is a static-member
        // namespace at Editor.h:184+; subscriptions don't go through any
        // module accessor). These fire on the game thread.

        // asset_post_import -- Editor.h:295/108. Fires after UFactory finishes
        // importing an asset (whether single import_texture, batch reimport,
        // or drag-and-drop into Content Browser). Distinct from asset_added:
        // asset_added fires for ANY new registry entry (including the initial
        // scan flood); asset_post_import fires only on actual import.
        AssetPostImportHandle = FEditorDelegates::OnAssetPostImport.AddLambda(
            [](UFactory* Factory, UObject* Asset)
            {
                if (!Asset) { return; }
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetStringField(TEXT("asset_path"), Asset->GetPathName());
                Data->SetStringField(TEXT("name"), Asset->GetName());
                Data->SetStringField(TEXT("class"),
                    Asset->GetClass() ? Asset->GetClass()->GetName() : TEXT(""));
                Data->SetStringField(TEXT("factory"),
                    (Factory && Factory->GetClass()) ? Factory->GetClass()->GetName() : TEXT(""));
                FUCMCPEventBus::Get().Push(TEXT("asset_post_import"), Data);
            });

        // level_post_save -- Editor.h:273/92. Fires after a UWorld is saved.
        // FObjectPostSaveContext carries cook/save flags; we expose just the
        // world path for now (clients that need cook context can fall back to
        // explicit checks via execute_unreal_python).
        PostSaveWorldHandle = FEditorDelegates::PostSaveWorldWithContext.AddLambda(
            [](UWorld* World, FObjectPostSaveContext /*Context*/)
            {
                if (!World) { return; }
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetStringField(TEXT("level"), World->GetPathName());
                FUCMCPEventBus::Get().Push(TEXT("level_post_save"), Data);
            });

        // map_changed -- Editor.h:196/82. Single uint32 flag-bitmap param.
        // Test against the named MapChangeEventFlags constants (Editor.h:435+)
        // rather than literal bit values: stays correct if UE ever renumbers
        // the flags (unlikely but defensive). Emits both the raw int and a
        // humanized flag-name array so callers can filter on either axis
        // without binding to UE's bit values.
        MapChangeHandle = FEditorDelegates::MapChange.AddLambda(
            [](uint32 Flags)
            {
                TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
                Data->SetNumberField(TEXT("flags"), static_cast<double>(Flags));
                TArray<TSharedPtr<FJsonValue>> FlagNames;
                if (Flags & MapChangeEventFlags::NewMap)        { FlagNames.Add(MakeShared<FJsonValueString>(TEXT("new_map"))); }
                if (Flags & MapChangeEventFlags::MapRebuild)    { FlagNames.Add(MakeShared<FJsonValueString>(TEXT("map_rebuild"))); }
                if (Flags & MapChangeEventFlags::WorldTornDown) { FlagNames.Add(MakeShared<FJsonValueString>(TEXT("world_torn_down"))); }
                Data->SetArrayField(TEXT("flag_names"), FlagNames);
                FUCMCPEventBus::Get().Push(TEXT("map_changed"), Data);
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

    // Detach asset-registry subscriptions (PR #40 + PR #41). Use GetModulePtr
    // here -- shutdown is not the time to load modules. If AssetRegistry is
    // already gone we leak the handles, which is harmless (the broadcaster
    // is also gone).
    if (AssetAddedHandle.IsValid()
        || AssetRemovedHandle.IsValid()
        || AssetRenamedHandle.IsValid())
    {
        if (FAssetRegistryModule* ARModule = FModuleManager::GetModulePtr<FAssetRegistryModule>(
                TEXT("AssetRegistry")))
        {
            IAssetRegistry& AR = ARModule->Get();
            if (AssetAddedHandle.IsValid())   { AR.OnAssetAdded().Remove(AssetAddedHandle); }
            if (AssetRemovedHandle.IsValid()) { AR.OnAssetRemoved().Remove(AssetRemovedHandle); }
            if (AssetRenamedHandle.IsValid()) { AR.OnAssetRenamed().Remove(AssetRenamedHandle); }
        }
        AssetAddedHandle.Reset();
        AssetRemovedHandle.Reset();
        AssetRenamedHandle.Reset();
    }

    // Detach editor delegates (PR #41). FEditorDelegates::* are static
    // multicasts; no module-load step required to remove. Safe to call
    // unconditionally once we've checked the handle is valid.
    if (AssetPostImportHandle.IsValid())
    {
        FEditorDelegates::OnAssetPostImport.Remove(AssetPostImportHandle);
        AssetPostImportHandle.Reset();
    }
    if (PostSaveWorldHandle.IsValid())
    {
        FEditorDelegates::PostSaveWorldWithContext.Remove(PostSaveWorldHandle);
        PostSaveWorldHandle.Reset();
    }
    if (MapChangeHandle.IsValid())
    {
        FEditorDelegates::MapChange.Remove(MapChangeHandle);
        MapChangeHandle.Reset();
    }

    // Deregister the log capture device before the module unloads so GLog
    // doesn't call into a dangling pointer.
    GLog->RemoveOutputDevice(&FUCMCPLogCapture::Get());

    UE_LOG(LogUnrealClaudeMCP, Log, TEXT("[UnrealClaudeMCP] Module shutdown"));
}

IMPLEMENT_MODULE(FUnrealClaudeMCPModule, UnrealClaudeMCP)
