// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// render_sequence_mrq - render a ULevelSequence to an image sequence on disk
// via the Movie Render Queue (MRQ), ASYNCHRONOUSLY. Builds an MRQ queue + job
// programmatically, configures output + render pass, kicks off a PIE-based (or
// out-of-process) render on the game thread, returns a task_id IMMEDIATELY, and
// completes the FUCMCPTaskRegistry task from the executor's game-thread
// OnExecutorFinished delegate. Clients poll via poll_task / (best-effort) cancel
// via cancel_task. An optional map_path lets this same tool cover the
// "render a level" use case -- a UMoviePipelineExecutorJob carries both a
// Sequence and a Map FSoftObjectPath, so no separate render_level_mrq is needed.
//
// ============================ UE 5.7 API SURFACE ============================
// All verified against F:/UE_5.7 source (paths are MovieScene/MovieRenderPipeline):
//
//   UMoviePipelineQueueSubsystem (UEditorSubsystem)
//     MovieRenderPipelineEditor/Public/MoviePipelineQueueSubsystem.h
//       - GetQueue() const -> UMoviePipelineQueue*                          (:27)
//       - RenderQueueWithExecutorInstance(UMoviePipelineExecutorBase*)      (:81)
//           internally calls RenderQueueInstanceWithExecutorInstance(GetQueue(), exec)
//           which does ActiveExecutor = exec; exec->Execute(GetQueue());     (.cpp:118-145)
//           The subsystem KEEPS THE EXECUTOR ALIVE (ActiveExecutor) until it
//           finishes, then nulls it in its own OnExecutorFinished (.cpp:156-165).
//       - IsRendering() const -> bool  (delegates to ActiveExecutor)         (:93)
//       Acquire via GEditor->GetEditorSubsystem<UMoviePipelineQueueSubsystem>().
//
//   UMoviePipelineQueue (UObject) -- MovieRenderPipelineCore/Public/MoviePipelineQueue.h
//       - DeleteAllJobs() -> void                                           (:680)
//       - AllocateNewJob(TSubclassOf<UMoviePipelineExecutorJob>) -> job*    (:666)
//
//   UMoviePipelineExecutorJob (UObject) -- same header (:303)
//       - SetSequence(FSoftObjectPath) (BlueprintSetter)                    (:504)
//       - Map (public FSoftObjectPath UPROPERTY)                            (:582)
//       - GetConfiguration() const -> UMoviePipelinePrimaryConfig*          (:465)
//       - JobName (public FString UPROPERTY)                                (:574)
//
//   UMoviePipelinePrimaryConfig (UMoviePipelineConfigBase)
//       MovieRenderPipelineCore/Public/MoviePipelinePrimaryConfig.h         (:30)
//       CONFIRMED 5.7 name is *PrimaryConfig* (NOT MasterConfig -- that was pre-5.0).
//       - (via base UMoviePipelineConfigBase, MoviePipelineConfigBase.h:144)
//         FindOrAddSettingByClass(TSubclassOf<UMoviePipelineSetting>, bool, bool)
//           -> UMoviePipelineSetting*    -- adds OutputSetting + output container + pass.
//
//   UMoviePipelineOutputSetting (UMoviePipelineSetting)
//       MovieRenderPipelineCore/Public/MoviePipelineOutputSetting.h         (:12)
//       Members written here: OutputDirectory (FDirectoryPath, .Path), FileNameFormat,
//       OutputResolution (FIntPoint), bUseCustomFrameRate, OutputFrameRate (FFrameRate),
//       bOverrideExistingOutput, bUseCustomPlaybackRange, CustomStartFrame,
//       CustomEndFrame.
//
//   Output containers -- MovieRenderPipelineRenderPasses/Public/MoviePipelineImageSequenceOutput.h
//       UMoviePipelineImageSequenceOutput_PNG (:83) / _JPG (:106) / _BMP (:66)
//   EXR -- MovieRenderPipelineRenderPasses/Public/MoviePipelineEXROutput.h
//       UMoviePipelineImageSequenceOutput_EXR (:197)
//
//   Render passes -- MovieRenderPipelineRenderPasses/Public/MoviePipelineDeferredPasses.h
//       UMoviePipelineDeferredPassBase (LIT base, :55) / _Unlit (:235) /
//       _DetailLighting (:257) / _LightingOnly (:280) / _ReflectionsOnly (:303) /
//       _PathTracer (:326)
//
//   Executors -- MovieRenderPipelineEditor/Public/MoviePipelinePIEExecutor.h
//       UMoviePipelinePIEExecutor (UMoviePipelineLinearExecutorBase)         (:28)
//         - SetIsRenderingOffscreen(bool)                                    (:67)
//         - OnIndividualJobWorkFinished() -> FMoviePipelineWorkFinishedNative& (:45)
//       UMoviePipelineNewProcessExecutor (out-of-process)                    (:16)
//
//   Base executor -- MovieRenderPipelineCore/Public/MoviePipelineExecutor.h
//       UMoviePipelineExecutorBase (:45)
//         - OnExecutorFinished() -> FOnMoviePipelineExecutorFinishedNative&  (:187)
//             DECLARE_MULTICAST_DELEGATE_TwoParams(..., UMoviePipelineExecutorBase*, bool) (:21)
//         - OnExecutorErrored() -> FOnMoviePipelineExecutorErroredNative&    (:192)
//             FourParams(executor*, UMoviePipeline*, bool bIsFatal, FText)   (:24)
//         - CancelAllJobs()                                                  (:175)
//
//   FMoviePipelineWorkFinishedNative -- MoviePipelineBase.h:8
//       DECLARE_MULTICAST_DELEGATE_OneParam(..., FMoviePipelineOutputData)
//   FMoviePipelineOutputData -- MovieRenderPipelineDataTypes.h:1408
//       .bSuccess (bool), .ShotData (TArray<FMoviePipelineShotOutputData>)
//   FMoviePipelineShotOutputData -- :1360
//       .RenderPassData (TMap<FMoviePipelinePassIdentifier, FMoviePipelineRenderPassOutputData>)
//   FMoviePipelineRenderPassOutputData -- :1351
//       .FilePaths (TArray<FString>)   <-- the written-file list harvested below.
//
// ============================ ASYNC COMPLETION MODEL =========================
// UNLIKE start_sleep_task (EAsyncExecution::ThreadPool worker that polls a cancel
// flag), MRQ runs ON THE GAME THREAD: RenderQueueWithExecutorInstance starts a
// PIE session that is driven across many game-thread ticks, and the completion
// signal is the executor's OnExecutorFinished MULTICAST DELEGATE which fires on a
// LATER game-thread tick. There is no worker thread to poll. So the pattern is:
//
//   1. validate params
//   2. CreateTask(registry) -> task_id (also gives us a cancel flag we don't poll)
//   3. MarkRunning(task_id)
//   4. build queue + job + config (output setting, output container, render pass)
//   5. construct + configure the executor (offscreen, etc.)
//   6. bind OnExecutorFinished / OnExecutorErrored / OnIndividualJobWorkFinished
//      lambdas that capture ONLY the FString task_id (never the executor/job --
//      avoids lifetime tangles) and call MarkCompleted / MarkFailed
//   7. RenderQueueWithExecutorInstance(executor) -> returns immediately
//   8. return { task_id, status: pending, ... }
//
// The handler itself runs on the game thread (MCP TickClients is on the FTSTicker
// / game thread, MCPServer.cpp:208), so calling the editor subsystem + starting
// the render directly (no AsyncTask hop) is correct.
//
// FILE-LIST HARVEST: the PIE executor's OnIndividualJobWorkFinished fires (per
// job) just before OnExecutorFinished, carrying FMoviePipelineOutputData. Both
// delegates are bound in THIS .cpp, so we stash the harvested paths in a
// file-static map keyed by task_id (game-thread only, guarded for safety) and
// drain it inside the OnExecutorFinished lambda. No cross-file infrastructure.
//
// CANCEL (v1): cancel_task only sets the registry's atomic flag, which no MRQ
// path reads (there is no polling worker). Wiring real cancel would require a
// shared task_id -> executor map reachable from Handler_CancelTask.cpp. Per the
// spec that is out of scope for v1, so MRQ cancel is documented as UNSUPPORTED:
// poll_task reports the true terminal state when the render finishes regardless.
//
// ============================ ERROR FORMAT ===================================
// "render_sequence_mrq: <error_code>: <human-readable detail>"
// Stable codes: missing_params, missing_required_field, invalid_path,
// sequence_not_found, not_a_sequence, map_not_found, invalid_format,
// invalid_render_pass, no_editor, mrq_subsystem_unavailable, already_rendering,
// output_dir_invalid, queue_build_failed, executor_create_failed,
// render_start_failed.

#include "MCP/MCPHandler.h"

#if WITH_EDITOR

#include "MCP/TaskRegistry.h"
#include "MCP/Handlers/AssetPathUtil.h"

#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Editor.h"
#include "EditorAssetLibrary.h"
#include "Engine/World.h"
#include "Engine/Engine.h"
#include "UObject/Package.h"
#include "UObject/SoftObjectPath.h"
#include "Misc/Paths.h"
#include "Misc/FrameRate.h"
#include "Misc/ScopeLock.h"
#include "HAL/CriticalSection.h"
#include "Templates/SubclassOf.h"

// Movie Render Pipeline (engine plugin MovieRenderPipeline; modules
// MovieRenderPipelineCore / MovieRenderPipelineEditor / MovieRenderPipelineRenderPasses).
#include "MoviePipelineQueueSubsystem.h"
#include "MoviePipelineQueue.h"
#include "MoviePipelinePrimaryConfig.h"
#include "MoviePipelineOutputSetting.h"
#include "MoviePipelineImageSequenceOutput.h"
#include "MoviePipelineEXROutput.h"
#include "MoviePipelineDeferredPasses.h"
#include "MoviePipelinePIEExecutor.h"
#include "MoviePipelineNewProcessExecutor.h"
#include "MoviePipelineExecutor.h"
#include "MovieRenderPipelineDataTypes.h"

namespace
{
    // Resolve the output-container UMoviePipelineSetting subclass for a format
    // string. Returns nullptr for an unrecognized format (caller surfaces
    // invalid_format).
    UClass* ResolveOutputContainerClass(const FString& Format)
    {
        const FString F = Format.ToLower();
        if (F == TEXT("png")) { return UMoviePipelineImageSequenceOutput_PNG::StaticClass(); }
        if (F == TEXT("jpg") || F == TEXT("jpeg")) { return UMoviePipelineImageSequenceOutput_JPG::StaticClass(); }
        if (F == TEXT("bmp")) { return UMoviePipelineImageSequenceOutput_BMP::StaticClass(); }
        if (F == TEXT("exr")) { return UMoviePipelineImageSequenceOutput_EXR::StaticClass(); }
        return nullptr;
    }

    // Resolve the deferred-pass UMoviePipelineSetting subclass for a render_pass
    // string. Returns nullptr for an unrecognized pass (caller surfaces
    // invalid_render_pass).
    UClass* ResolveRenderPassClass(const FString& Pass)
    {
        const FString P = Pass.ToLower();
        if (P == TEXT("lit")) { return UMoviePipelineDeferredPassBase::StaticClass(); }
        if (P == TEXT("unlit")) { return UMoviePipelineDeferredPass_Unlit::StaticClass(); }
        if (P == TEXT("detail_lighting")) { return UMoviePipelineDeferredPass_DetailLighting::StaticClass(); }
        if (P == TEXT("lighting_only")) { return UMoviePipelineDeferredPass_LightingOnly::StaticClass(); }
        if (P == TEXT("reflections_only")) { return UMoviePipelineDeferredPass_ReflectionsOnly::StaticClass(); }
        if (P == TEXT("path_tracer")) { return UMoviePipelineDeferredPass_PathTracer::StaticClass(); }
        return nullptr;
    }

    // Harvest the flat list of written file paths out of a finished job's
    // FMoviePipelineOutputData (ShotData -> RenderPassData -> FilePaths). Empty
    // for the out-of-process executor (which does not deliver this callback).
    TArray<FString> HarvestFilePaths(const FMoviePipelineOutputData& Data)
    {
        TArray<FString> Files;
        for (const FMoviePipelineShotOutputData& Shot : Data.ShotData)
        {
            for (const TPair<FMoviePipelinePassIdentifier, FMoviePipelineRenderPassOutputData>& Pair : Shot.RenderPassData)
            {
                Files.Append(Pair.Value.FilePaths);
            }
        }
        return Files;
    }

    // File-local bridge between the per-job work-finished callback (which has the
    // file list) and the executor-finished callback (which builds the result).
    // Keyed by task_id. Both callbacks fire on the game thread, but we guard with
    // a critical section anyway -- the cost is negligible and it documents the
    // shared-state intent. Entries are written by RecordHarvest and drained
    // (removed) by TakeHarvest inside the finished lambda.
    static FCriticalSection GHarvestMutex;
    static TMap<FString, TArray<FString>> GHarvestByTask;

    void RecordHarvest(const FString& TaskId, TArray<FString>&& Files)
    {
        FScopeLock Lock(&GHarvestMutex);
        TArray<FString>& Slot = GHarvestByTask.FindOrAdd(TaskId);
        Slot.Append(MoveTemp(Files));
    }

    TArray<FString> TakeHarvest(const FString& TaskId)
    {
        FScopeLock Lock(&GHarvestMutex);
        TArray<FString> Files;
        GHarvestByTask.RemoveAndCopyValue(TaskId, Files);
        return Files;
    }
}

class FHandler_RenderSequenceMrq : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("render_sequence_mrq"); }

    // Read-only w.r.t. the editor undo stack: MRQ output is files on disk +
    // a transient PIE session, none of which belongs on Ctrl+Z. (The dispatcher
    // only wraps mutating handlers in an FScopedTransaction.)
    virtual bool IsMutating() const override { return false; }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // --- editor / subsystem guards --------------------------------------
        if (!GEditor)
        {
            OutError = TEXT("render_sequence_mrq: no_editor: GEditor is null (editor build only)");
            return nullptr;
        }
        if (!Params.IsValid())
        {
            OutError = TEXT("render_sequence_mrq: missing_params: 'sequence_path' and 'output_dir' are required");
            return nullptr;
        }

        // --- required: sequence_path ----------------------------------------
        FString SequencePath;
        if (!Params->TryGetStringField(TEXT("sequence_path"), SequencePath) || SequencePath.IsEmpty())
        {
            OutError = TEXT("render_sequence_mrq: missing_required_field: 'sequence_path' is required and must not be empty");
            return nullptr;
        }

        // --- required: output_dir -------------------------------------------
        FString OutputDir;
        if (!Params->TryGetStringField(TEXT("output_dir"), OutputDir) || OutputDir.IsEmpty())
        {
            OutError = TEXT("render_sequence_mrq: missing_required_field: 'output_dir' is required and must not be empty");
            return nullptr;
        }
        // Written verbatim into UMoviePipelineOutputSetting::OutputDirectory.Path.
        // MRQ accepts {format-token} substitution and absolute paths; we require
        // a non-relative path so the render does not land somewhere surprising
        // (relative-to-cwd) under headless automation.
        if (FPaths::IsRelative(OutputDir))
        {
            OutError = TEXT("render_sequence_mrq: output_dir_invalid: 'output_dir' must be an absolute filesystem path");
            return nullptr;
        }

        // --- optional: format (default png) ---------------------------------
        FString Format = TEXT("png");
        Params->TryGetStringField(TEXT("format"), Format);
        UClass* OutputContainerClass = ResolveOutputContainerClass(Format);
        if (!OutputContainerClass)
        {
            OutError = FString::Printf(
                TEXT("render_sequence_mrq: invalid_format: '%s' is not a supported format (use png|jpg|bmp|exr)"),
                *Format);
            return nullptr;
        }

        // --- optional: render_pass (default lit) ----------------------------
        FString RenderPass = TEXT("lit");
        Params->TryGetStringField(TEXT("render_pass"), RenderPass);
        UClass* RenderPassClass = ResolveRenderPassClass(RenderPass);
        if (!RenderPassClass)
        {
            OutError = FString::Printf(
                TEXT("render_sequence_mrq: invalid_render_pass: '%s' is not a supported pass "
                     "(use lit|unlit|detail_lighting|lighting_only|reflections_only|path_tracer)"),
                *RenderPass);
            return nullptr;
        }

        // --- resolve the sequence asset (mirrors set_sequence_playback_range) -
        const FString SequenceObjectPath = UCMCPAssetPath::ToObjectPath(SequencePath);
        if (!UEditorAssetLibrary::DoesAssetExist(SequenceObjectPath))
        {
            OutError = FString::Printf(
                TEXT("render_sequence_mrq: sequence_not_found: '%s' is not in the asset registry"),
                *SequencePath);
            return nullptr;
        }
        // We don't strictly need the loaded UObject (the job takes an
        // FSoftObjectPath), but loading + Cast is the only reliable not_a_sequence
        // guard short of a registry class query, and matches the rest of the
        // sequencer lane. ULevelSequence lives in the LevelSequence module
        // (already a Build.cs dep). We avoid a hard #include of LevelSequence.h
        // here by checking the class name via the loaded object's UClass.
        UObject* LoadedSeq = UEditorAssetLibrary::LoadAsset(SequenceObjectPath);
        if (!LoadedSeq)
        {
            OutError = FString::Printf(
                TEXT("render_sequence_mrq: not_a_sequence: '%s' could not be loaded"),
                *SequencePath);
            return nullptr;
        }
        if (!LoadedSeq->GetClass()->GetName().Equals(TEXT("LevelSequence")))
        {
            OutError = FString::Printf(
                TEXT("render_sequence_mrq: not_a_sequence: '%s' is a %s, not a LevelSequence"),
                *SequencePath, *LoadedSeq->GetClass()->GetName());
            return nullptr;
        }

        // --- resolve the map -------------------------------------------------
        // Optional. If omitted, default to the currently-loaded editor world's
        // package path (the render_level_mrq merge). A job without a valid Map
        // renders garbage, so this MUST resolve to something.
        FString MapObjectPath;
        FString MapPathIn;
        if (Params->TryGetStringField(TEXT("map_path"), MapPathIn) && !MapPathIn.IsEmpty())
        {
            const FString MapObj = UCMCPAssetPath::ToObjectPath(MapPathIn);
            if (!UEditorAssetLibrary::DoesAssetExist(MapObj))
            {
                OutError = FString::Printf(
                    TEXT("render_sequence_mrq: map_not_found: '%s' is not in the asset registry"),
                    *MapPathIn);
                return nullptr;
            }
            MapObjectPath = MapObj;
        }
        else
        {
            UWorld* EditorWorld = GEditor->GetEditorWorldContext().World();
            UPackage* WorldPackage = EditorWorld ? EditorWorld->GetOutermost() : nullptr;
            if (!WorldPackage)
            {
                OutError = TEXT("render_sequence_mrq: map_not_found: no map_path given and no editor world is loaded to default from");
                return nullptr;
            }
            // /Game/Maps/MyMap  ->  /Game/Maps/MyMap.MyMap
            MapObjectPath = UCMCPAssetPath::ToObjectPath(WorldPackage->GetName());
        }

        // --- optional rendering knobs ---------------------------------------
        FString FileNameFormat = TEXT("{sequence_name}.{frame_number}");
        Params->TryGetStringField(TEXT("file_name_format"), FileNameFormat);

        int32 ResWidth = 1920;
        int32 ResHeight = 1080;
        const TSharedPtr<FJsonObject>* ResObj = nullptr;
        if (Params->TryGetObjectField(TEXT("resolution"), ResObj) && ResObj && (*ResObj).IsValid())
        {
            double W = 0.0, H = 0.0;
            if ((*ResObj)->TryGetNumberField(TEXT("width"), W) && W > 0.0) { ResWidth = FMath::Clamp(static_cast<int32>(W), 1, 16384); }
            if ((*ResObj)->TryGetNumberField(TEXT("height"), H) && H > 0.0) { ResHeight = FMath::Clamp(static_cast<int32>(H), 1, 16384); }
        }

        double OutputFrameRate = 0.0;  // 0 => use the sequence's own display rate
        Params->TryGetNumberField(TEXT("output_frame_rate"), OutputFrameRate);

        bool bOverwriteExisting = true;
        Params->TryGetBoolField(TEXT("overwrite_existing"), bOverwriteExisting);

        bool bUseNewProcess = false;
        Params->TryGetBoolField(TEXT("use_new_process"), bUseNewProcess);

        bool bRenderOffscreen = true;
        Params->TryGetBoolField(TEXT("render_offscreen"), bRenderOffscreen);

        // Optional custom playback range (display-rate frames). Both ends required
        // together; if absent the render covers the sequence's own range.
        bool bUseCustomRange = false;
        int32 CustomStartFrame = 0;
        int32 CustomEndFrame = 0;
        const TSharedPtr<FJsonObject>* RangeObj = nullptr;
        if (Params->TryGetObjectField(TEXT("use_custom_playback_range"), RangeObj) && RangeObj && (*RangeObj).IsValid())
        {
            double S = 0.0, E = 0.0;
            const bool bHasStart = (*RangeObj)->TryGetNumberField(TEXT("start_frame"), S);
            const bool bHasEnd = (*RangeObj)->TryGetNumberField(TEXT("end_frame"), E);
            if (bHasStart && bHasEnd)
            {
                bUseCustomRange = true;
                CustomStartFrame = static_cast<int32>(S);
                CustomEndFrame = static_cast<int32>(E);
            }
        }

        // --- MRQ subsystem + already-rendering guard ------------------------
        UMoviePipelineQueueSubsystem* QueueSubsystem =
            GEditor->GetEditorSubsystem<UMoviePipelineQueueSubsystem>();
        if (!QueueSubsystem)
        {
            OutError = TEXT("render_sequence_mrq: mrq_subsystem_unavailable: GetEditorSubsystem<UMoviePipelineQueueSubsystem>() returned null "
                            "(is the MovieRenderPipeline plugin enabled for this project?)");
            return nullptr;
        }
        if (QueueSubsystem->IsRendering())
        {
            OutError = TEXT("render_sequence_mrq: already_rendering: a Movie Render Queue render is already in progress; "
                            "wait for it to finish (poll_task) or cancel it before starting another");
            return nullptr;
        }

        // --- build the queue + job ------------------------------------------
        // Per RenderQueueWithExecutorInstance (MoviePipelineQueueSubsystem.cpp:143-145)
        // the executor renders GetQueue() -- the editor's single transient queue,
        // also shown in the MRQ UI panel. We populate it (clean slate via
        // DeleteAllJobs, then one job). This is the spec-documented, GC-safe path
        // (the subsystem owns its queue for the editor's lifetime). The job is
        // owned by the queue, the config by the job, so nothing here needs an
        // explicit GC anchor.
        UMoviePipelineQueue* Queue = QueueSubsystem->GetQueue();
        if (!Queue)
        {
            OutError = TEXT("render_sequence_mrq: queue_build_failed: GetQueue() returned null");
            return nullptr;
        }
        Queue->DeleteAllJobs();

        UMoviePipelineExecutorJob* Job = Queue->AllocateNewJob(UMoviePipelineExecutorJob::StaticClass());
        if (!Job)
        {
            OutError = TEXT("render_sequence_mrq: queue_build_failed: AllocateNewJob returned null");
            return nullptr;
        }
        Job->JobName = TEXT("render_sequence_mrq");
        Job->SetSequence(FSoftObjectPath(SequenceObjectPath));
        Job->Map = FSoftObjectPath(MapObjectPath);

        UMoviePipelinePrimaryConfig* Config = Job->GetConfiguration();
        if (!Config)
        {
            OutError = TEXT("render_sequence_mrq: queue_build_failed: job has no primary configuration");
            return nullptr;
        }

        // Output setting (where + how to name the files).
        UMoviePipelineOutputSetting* OutputSetting = Cast<UMoviePipelineOutputSetting>(
            Config->FindOrAddSettingByClass(UMoviePipelineOutputSetting::StaticClass()));
        if (!OutputSetting)
        {
            OutError = TEXT("render_sequence_mrq: queue_build_failed: could not add UMoviePipelineOutputSetting to the config");
            return nullptr;
        }
        OutputSetting->OutputDirectory.Path = OutputDir;
        OutputSetting->FileNameFormat = FileNameFormat;
        OutputSetting->OutputResolution = FIntPoint(ResWidth, ResHeight);
        OutputSetting->bOverrideExistingOutput = bOverwriteExisting;
        if (OutputFrameRate > 0.0)
        {
            OutputSetting->bUseCustomFrameRate = true;
            // FFrameRate stores a rational; express the requested decimal fps as
            // (round(fps*1000) / 1000) so non-integer rates (e.g. 23.976) survive.
            OutputSetting->OutputFrameRate = FFrameRate(FMath::RoundToInt(OutputFrameRate * 1000.0), 1000);
        }
        if (bUseCustomRange)
        {
            OutputSetting->bUseCustomPlaybackRange = true;
            OutputSetting->CustomStartFrame = CustomStartFrame;
            OutputSetting->CustomEndFrame = CustomEndFrame;
        }

        // Output container (image format) -- a SEPARATE setting from the pass.
        if (!Config->FindOrAddSettingByClass(OutputContainerClass))
        {
            OutError = TEXT("render_sequence_mrq: queue_build_failed: could not add the image-output container setting");
            return nullptr;
        }

        // Render pass (what to render) -- the other half; forgetting this yields no image.
        if (!Config->FindOrAddSettingByClass(RenderPassClass))
        {
            OutError = TEXT("render_sequence_mrq: queue_build_failed: could not add the render-pass setting");
            return nullptr;
        }

        // --- register the task BEFORE we start (so the delegate can complete it) -
        TSharedPtr<TAtomic<bool>> CancelFlag;  // not polled (no worker); cancel goes via CancelAllJobs
        const FString TaskId = FUCMCPTaskRegistry::Get().CreateTask(TEXT("mrq_render"), CancelFlag);
        FUCMCPTaskRegistry::Get().MarkRunning(TaskId);

        // --- construct + configure the executor ------------------------------
        UMoviePipelineExecutorBase* Executor = nullptr;
        if (bUseNewProcess)
        {
            // Out-of-process. Per-file harvesting is NOT available here; we will
            // complete with output_dir + success only.
            Executor = NewObject<UMoviePipelineNewProcessExecutor>(QueueSubsystem);
        }
        else
        {
            UMoviePipelinePIEExecutor* PIEExecutor = NewObject<UMoviePipelinePIEExecutor>(QueueSubsystem);
            if (PIEExecutor)
            {
                PIEExecutor->SetIsRenderingOffscreen(bRenderOffscreen);
                // Per-job file list (PIE only). Bind BEFORE Render*; capture only
                // the task_id (FString) -- never the executor/job -- to avoid
                // lifetime tangles. This delegate fires just before
                // OnExecutorFinished on the same job.
                PIEExecutor->OnIndividualJobWorkFinished().AddLambda(
                    [TaskId](FMoviePipelineOutputData Data)
                    {
                        RecordHarvest(TaskId, HarvestFilePaths(Data));
                    });
            }
            Executor = PIEExecutor;
        }

        if (!Executor)
        {
            FUCMCPTaskRegistry::Get().MarkFailed(TaskId,
                TEXT("render_sequence_mrq: executor_create_failed: NewObject for the MRQ executor returned null"));
            OutError = TEXT("render_sequence_mrq: executor_create_failed: NewObject for the MRQ executor returned null");
            return nullptr;
        }

        // Completion (game-thread). bSuccess comes straight from the executor.
        // We merge in any harvested file list recorded by the work-finished
        // callback above. Capture only FString task_id + output_dir.
        const FString OutputDirCopy = OutputDir;
        const bool bNewProc = bUseNewProcess;
        Executor->OnExecutorFinished().AddLambda(
            [TaskId, OutputDirCopy, bNewProc](UMoviePipelineExecutorBase* /*InExec*/, bool bSuccess)
            {
                TArray<FString> Files = TakeHarvest(TaskId);

                TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
                Result->SetBoolField(TEXT("ok"), true);
                Result->SetStringField(TEXT("output_dir"), OutputDirCopy);
                Result->SetBoolField(TEXT("success"), bSuccess);

                TArray<TSharedPtr<FJsonValue>> FileVals;
                for (const FString& F : Files)
                {
                    FileVals.Add(MakeShared<FJsonValueString>(F));
                }
                Result->SetArrayField(TEXT("files_written"), FileVals);
                Result->SetNumberField(TEXT("frame_count"), static_cast<double>(Files.Num()));

                if (Files.Num() == 0)
                {
                    Result->SetStringField(TEXT("note"),
                        bNewProc
                            ? TEXT("Out-of-process render: per-file enumeration is not available for use_new_process; "
                                   "inspect output_dir on disk for the written frames.")
                            : TEXT("No per-file list was captured (the render may have produced no files, or the "
                                   "work-finished callback did not fire); inspect output_dir on disk."));
                }

                FUCMCPTaskRegistry::Get().MarkCompleted(TaskId, Result);
            });

        // Error (game-thread). Capture the human-readable failure text.
        Executor->OnExecutorErrored().AddLambda(
            [TaskId](UMoviePipelineExecutorBase* /*InExec*/, UMoviePipeline* /*Pipeline*/, bool bIsFatal, FText ErrorText)
            {
                // Non-fatal errors do not end the render; only record on fatal.
                // The terminal transition still happens via OnExecutorFinished
                // (with bSuccess=false). To avoid masking a later "completed",
                // we record fatal errors as failures eagerly; OnExecutorFinished's
                // MarkCompleted is then a no-op (registry terminal-state guard).
                // Also drain any partial harvest so the entry does not leak.
                if (bIsFatal)
                {
                    TakeHarvest(TaskId);
                    FUCMCPTaskRegistry::Get().MarkFailed(TaskId,
                        FString::Printf(TEXT("render_sequence_mrq: render failed: %s"), *ErrorText.ToString()));
                }
            });

        // --- kick it off (returns immediately) ------------------------------
        // RenderQueueWithExecutorInstance stores ActiveExecutor = Executor (so the
        // subsystem keeps it alive across the render) and calls Execute(GetQueue()).
        QueueSubsystem->RenderQueueWithExecutorInstance(Executor);

        // Guard: if the subsystem rejected the start (e.g. a race put it in a
        // rendering state), it logs + returns without setting ActiveExecutor.
        if (QueueSubsystem->GetActiveExecutor() == nullptr)
        {
            TakeHarvest(TaskId);
            FUCMCPTaskRegistry::Get().MarkFailed(TaskId,
                TEXT("render_sequence_mrq: render_start_failed: the queue subsystem did not begin rendering "
                     "(another render may have started, or the job/queue was rejected)"));
            OutError = TEXT("render_sequence_mrq: render_start_failed: the queue subsystem did not begin rendering");
            return nullptr;
        }

        // --- synchronous response -------------------------------------------
        TSharedPtr<FJsonObject> Out = MakeShared<FJsonObject>();
        Out->SetBoolField(TEXT("ok"), true);
        Out->SetStringField(TEXT("task_id"), TaskId);
        Out->SetStringField(TEXT("type"), TEXT("mrq_render"));
        Out->SetStringField(TEXT("status"), UCMCPTaskStatus::Pending);
        Out->SetStringField(TEXT("sequence_path"), SequenceObjectPath);
        Out->SetStringField(TEXT("map_path"), MapObjectPath);
        Out->SetStringField(TEXT("output_dir"), OutputDir);
        Out->SetStringField(TEXT("format"), Format.ToLower());
        Out->SetStringField(TEXT("note"),
            TEXT("Movie Render Queue render started on the game thread. Poll via poll_task with the returned "
                 "task_id; result on completion carries { success, output_dir, files_written, frame_count }. "
                 "cancel_task is NOT wired for mrq_render in v1 (the render has no polling worker to observe the "
                 "flag). NOTE: a PIE session is launched -- save dirty assets first; the editor's MRQ panel "
                 "queue is overwritten by this render."));
        return Out;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_RenderSequenceMrq()
{
    return MakeShared<FHandler_RenderSequenceMrq>();
}

#else  // !WITH_EDITOR

// Non-editor builds: MRQ is editor-only (UMoviePipelineQueueSubsystem is a
// UEditorSubsystem). Provide a stub handler that always reports no_editor so the
// registration in UnrealAIConnectionModule.cpp links in every configuration.
#include "Dom/JsonObject.h"

class FHandler_RenderSequenceMrq : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("render_sequence_mrq"); }
    virtual bool IsMutating() const override { return false; }
    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& /*Params*/, FString& OutError) override
    {
        OutError = TEXT("render_sequence_mrq: no_editor: Movie Render Queue is editor-only (non-editor build)");
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_RenderSequenceMrq()
{
    return MakeShared<FHandler_RenderSequenceMrq>();
}

#endif // WITH_EDITOR
