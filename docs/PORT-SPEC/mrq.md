# PORT-SPEC — Movie Render Queue Family (`UMrqToolset`)

Read [00-overview.md](00-overview.md) first. Designed in [ticket #301](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/301).

**Moat status:** strong — 5.8 source scan ([#300 re-grill](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/300)) found **no MRQ toolset anywhere** under `Engine\Plugins\Experimental\Toolsets` or `ToolsetRegistry`. Epic's ~230-tool sequencer surface stops at rendering.

**Workflow served (locked):** an agent renders a Level Sequence (or the current level) to frames or a movie on disk, asynchronously — call once, get progress streamed, receive the file list when done; guard/cancel a running render; checkpoint level + sequences before risky work. Sequence *authoring* is Epic's ground (SequencerTools, [#300](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/300) out-of-scope) — Smoke setups here drive Epic's own tools to author the test sequence.

**Async model (locked):** async-native. `render_sequence` returns a `UToolCallAsyncResult` subclass — the framework holds the MCP call open, streams progress notifications (`ModelContextProtocol.ProgressIntervalSeconds`, default 1.0), and delivers the typed result when the executor finishes. No task registry, no poll tool — the donor's `task_id`/`poll_task` plumbing dies here. `cancel_render` is **new capability** (donor documented cancel as unsupported): with the Toolset port there is no task-id map to build — MRQ has at most one active render, and cancel is just `CancelAllJobs()` on the subsystem's active executor.

**Output scope (locked):** image sequences `png | jpg | bmp | exr` + **ProRes** movie container (pro-viz delivery staple; needs the `AppleProResMedia` plugin). Six deferred passes: `lit | unlit | detail_lighting | lighting_only | reflections_only | path_tracer`. WAV audio export and AVI ruled out.

**Donor code** (mine for internals, NOT tool shape):

- `UnrealAIConnection/Source/UnrealAIConnection/Private/MCP/Handlers/Handler_RenderSequenceMrq.cpp` — the crown jewel. Its header comment carries a **5.7-source-verified MRQ API table** (subsystem/queue/job/config/output-setting/container/pass/executor classes with header:line refs) — re-verify against 5.8, then trust it. Key hard-won facts, all still the design here:
  - Queue path: `GEditor->GetEditorSubsystem<UMoviePipelineQueueSubsystem>()` → `GetQueue()` → `DeleteAllJobs()` → `AllocateNewJob()` → `SetSequence(FSoftObjectPath)` + `Job->Map` → `GetConfiguration()` → `FindOrAddSettingByClass` for **three separate settings**: `UMoviePipelineOutputSetting` (dir/name/res/rate/range), the output container class, and the render-pass class — forgetting the pass yields *no image*.
  - The subsystem keeps the executor alive (`ActiveExecutor`) until finish; delegates fire on later game-thread ticks. Bind `OnExecutorFinished` / `OnExecutorErrored` / (PIE only) `OnIndividualJobWorkFinished` **before** `RenderQueueWithExecutorInstance`; capture only value types + a `TSharedRef` file-sink into the lambdas — never the executor/job (lifetime tangles).
  - File-list harvest: `OnIndividualJobWorkFinished(FMoviePipelineOutputData)` → `ShotData[].RenderPassData{}.FilePaths` — **PIE executor only**; the out-of-process executor delivers no per-file callback (result then carries a note, inspect dir on disk).
  - After `RenderQueueWithExecutorInstance`, check `GetActiveExecutor() != nullptr` — the subsystem can silently reject the start (race); that's the fail-fast.
  - `FFrameRate(round(fps*1000), 1000)` for non-integer rates (23.976). Custom range requires both ends. Reject relative output dirs. `IsA<ULevelSequence>()` (accepts subclasses) not class-name compare. Map defaults to the current editor world's package; a job without a valid Map renders garbage.
  - A PIE session is launched — result note must tell the agent to save dirty assets first; the editor's MRQ panel queue gets overwritten.
- `.../Handlers/Handler_SequenceSnapshot.cpp` — checkpoint dupe via `UEditorAssetSubsystem::DuplicateAsset(Source, Dest)`; label sanitization (alnum/`_`/`-`); timestamped `/Game/_Snapshots/<label>_<YYYYmmdd_HHMMSS>` folder; per-item ok flags so partial failure isn't masked; note about saving dirty packages.

---

## Shared spec structs

Declared in `MrqSpecTypes.h`. All reflected — these ARE the JSON schemas the agent sees.

```cpp
UENUM(BlueprintType)
enum class EMrqOutputFormat : uint8 { PNG, JPG, BMP, EXR, ProRes };

UENUM(BlueprintType)
enum class EMrqRenderPass : uint8 { Lit, Unlit, DetailLighting, LightingOnly, ReflectionsOnly, PathTracer };

/** Optional render knobs. Defaults render the sequence's own range at its own
 *  display rate, 1920x1080 PNG Lit, offscreen PIE, overwriting existing files. */
USTRUCT(BlueprintType)
struct FMrqRenderSettings
{
	GENERATED_BODY()

	/** Level to render in. Null = the currently loaded editor level. */
	UPROPERTY() TSoftObjectPtr<UWorld> Map;
	UPROPERTY() EMrqOutputFormat Format = EMrqOutputFormat::PNG;
	UPROPERTY() EMrqRenderPass RenderPass = EMrqRenderPass::Lit;
	UPROPERTY(meta=(ClampMin=1, ClampMax=16384)) int32 Width = 1920;
	UPROPERTY(meta=(ClampMin=1, ClampMax=16384)) int32 Height = 1080;
	/** MRQ filename format tokens allowed, e.g. "{sequence_name}.{frame_number}". */
	UPROPERTY() FString FileNameFormat = TEXT("{sequence_name}.{frame_number}");
	/** 0 = use the sequence's own display rate. Non-integer rates (23.976) supported. */
	UPROPERTY(meta=(ClampMin=0, ClampMax=240)) float OutputFrameRate = 0.f;
	UPROPERTY() bool bOverwriteExisting = true;
	/** Offscreen PIE render (no visible PIE viewport). */
	UPROPERTY() bool bRenderOffscreen = true;
	/** Out-of-process render. Per-file result list is unavailable in this mode. */
	UPROPERTY() bool bUseNewProcess = false;
	/** Custom range (display-rate frames). Both ends meaningful only when enabled. */
	UPROPERTY() bool bUseCustomPlaybackRange = false;
	UPROPERTY() int32 CustomStartFrame = 0;
	UPROPERTY() int32 CustomEndFrame = 0;
};

USTRUCT(BlueprintType)
struct FMrqRenderResult
{
	GENERATED_BODY()

	UPROPERTY() bool bSuccess = false;
	UPROPERTY() FString OutputDirectory;
	UPROPERTY() TArray<FString> FilesWritten;   // empty for bUseNewProcess (see Note)
	UPROPERTY() int32 FrameCount = 0;
	UPROPERTY() FString Note;
};

USTRUCT(BlueprintType)
struct FMrqStatus
{
	GENERATED_BODY()

	UPROPERTY() bool bRendering = false;
	UPROPERTY() FString JobName;   // empty when idle
};

USTRUCT(BlueprintType)
struct FMrqSnapshotEntry
{
	GENERATED_BODY()

	UPROPERTY() FString Source;
	UPROPERTY() FString Snapshot;
	UPROPERTY() bool bOk = false;
	UPROPERTY() FString Error;   // empty on success
};

USTRUCT(BlueprintType)
struct FMrqSnapshotResult
{
	GENERATED_BODY()

	UPROPERTY() bool bAllOk = false;
	UPROPERTY() FString SnapshotFolder;
	UPROPERTY() FString LevelSnapshot;
	UPROPERTY() TArray<FMrqSnapshotEntry> Sequences;
	UPROPERTY() int32 Count = 0;
	UPROPERTY() FString Note;
};
```

---

## Tools

All `static UFUNCTION(meta = (AICallable))` on `UMrqToolset : public UToolsetDefinition` (`UCLASS(BlueprintType, Hidden)`). Errors raised via `RaiseScriptError`, never returned. Tools 2–4 sync; tool 1 async.

### 1. `render_sequence`

```cpp
/**
 * Renders a Level Sequence to disk via the Movie Render Queue and returns when
 * the render finishes. Output is an image sequence (PNG/JPG/BMP/EXR) or a
 * ProRes movie. Renders in the given level, or the currently loaded editor
 * level when none is given. Launches a Play-In-Editor session — save dirty
 * assets before calling. The editor's Movie Render Queue panel contents are
 * replaced by this render. Progress is reported while rendering.
 *
 * @param Sequence        The Level Sequence to render.
 * @param OutputDirectory Absolute filesystem directory for the output files.
 * @param Settings        Format, pass, resolution, frame rate, range, executor knobs.
 * @return Render outcome with the list of files written.
 */
static UMrqRenderAsyncResult* RenderSequence(ULevelSequence* Sequence,
	const FString& OutputDirectory, const FMrqRenderSettings& Settings);
```

`UMrqRenderAsyncResult : public UToolCallAsyncResult` carries an `FMrqRenderResult`. **Exact contract (5.8-source-verified, `ToolCallAsyncResult.h`):** `SetError(const FString&)` is inherited from the base (h:84); `SetValue()` is a per-subclass convention, not a base method — author `SetValue(FMrqRenderResult)` that calls the protected template `MaybeBroadcastSuccessfulCompletion(...)` (h:116) and override `GetValueAsJson()` (h:75); never write `bIsComplete`/`Error` directly (shipped siblings `UToolCallAsyncResultString`/`...Image`/`...Void` show the pattern). Donor's whole queue-build + delegate-binding + harvest design ports intact — only the completion sink changes (async result instead of task registry). Raise before going async on: relative `OutputDirectory`, non-sequence asset, missing map, subsystem null, `IsRendering()` already true, rejected start (`GetActiveExecutor()` null after kick).

ProRes: `UMoviePipelineAppleProResOutput` in the `AppleProResMedia` plugin (5.8-confirmed, `MoviePipelineAppleProResOutput.h:13`) — but the header is **Private**, so it cannot be `#include`d: resolve the `UClass` at runtime by path (`/Script/AppleProResMedia.MoviePipelineAppleProResOutput`); raise `format_unavailable` when unresolved (plugin disabled). Soft lookup is *required*, not just optional-dependency hygiene.

**Smoke:** setup: author the test scene **with Epic's own tools** — `SequencerTools.create_level_sequence` + `create_camera` + `set_camera_cut_binding`, playback range ~24 frames, in McpSmoke. Call `render_sequence` (PNG, 320×180, offscreen, custom range 0–23) to a scratch dir. Assert: call blocks with progress notifications, result `bSuccess=true`, `FrameCount==24`, all `FilesWritten` exist on disk. Re-run with `Format=ProRes` **only if** AppleProResMedia enabled — else assert `format_unavailable` raised. Teardown: delete scratch dir + `/Game/Smoke`.

### 2. `get_render_status`

```cpp
/**
 * Reports whether a Movie Render Queue render is currently in progress.
 *
 * @return Render status (rendering flag and active job name).
 */
static FMrqStatus GetRenderStatus();
```

`QueueSubsystem->IsRendering()`; job name from `GetQueue()` first job when rendering.

**Smoke:** idle → `bRendering=false`. During tool 1's render (second client session if serial dispatch blocks — see concurrency caveat below) → `true`.

### 3. `cancel_render`

```cpp
/**
 * Cancels the in-progress Movie Render Queue render, if any. The pending
 * render_sequence call then completes with bSuccess=false. No-op when idle.
 *
 * @return Render status after the cancel request (bRendering may lag a tick).
 */
static FMrqStatus CancelRender();
```

`GetActiveExecutor()->CancelAllJobs()` when rendering; idle = clean no-op (returns status, no raise). New capability over donor.

**Smoke:** start a long render (600 frames), cancel. Assert: pending `render_sequence` resolves with `bSuccess=false` promptly; status returns to idle; partial frames on disk are fine.

### 4. `snapshot_before_render`

```cpp
/**
 * Crash-safety checkpoint: duplicates the currently loaded level and any given
 * Level Sequence assets into a timestamped folder under /Game/_Snapshots/, so
 * risky work can be rolled back. Assets are duplicated in memory — save dirty
 * packages afterwards or the snapshot is lost on editor restart.
 *
 * @param Label     Folder label; sanitized to alphanumerics, '_' and '-'.
 * @param Sequences Level Sequence assets to include in the snapshot.
 * @return Snapshot folder, per-asset outcomes, and total duplicated count.
 */
static FMrqSnapshotResult SnapshotBeforeRender(const FString& Label,
	const TArray<ULevelSequence*>& Sequences);
```

Donor: `Handler_SequenceSnapshot.cpp` — direct port (label sanitization, `/Game/`-only guard for the current level, `UEditorAssetSubsystem::DuplicateAsset`, per-item ok, partial failure ≠ success). Typed `ULevelSequence*` array replaces the donor's path strings at the schema — but `DuplicateAsset` takes **FString package paths** (5.8-confirmed, `EditorAssetSubsystem.h:185`), so resolve each asset back to its package path (`GetOutermost()->GetName()`) before the call.

**Smoke:** with McpSmoke level open + tool-1's sequence: call with label `pre-render`. Assert: `/Game/_Snapshots/pre-render_<timestamp>/` holds level + sequence duplicates, `bAllOk=true`, `Count==2`. Teardown: delete `/Game/_Snapshots`.

---

## Family-level acceptance

Run Smoke blocks 4 → 1 → 2 → 3 in one sitting against a live 5.8 editor via `call_tool` (project: `F:\ai\claude-work\McpSmoke\`; sequence authored via Epic's SequencerTools). End state: checkpoint taken, a 24-frame PNG render on disk with the file list returned through one async call, status observed both ways, and a long render cancelled cleanly. Then delete `/Game/Smoke`, `/Game/_Snapshots`, scratch dirs.

## Build-session notes

- **Concurrency caveat (settle during first Smoke):** overview primer says tool calls run serially on the game thread. `UToolCallAsyncResult` exists to free the loop while a call is pending — but **verify** that the server actually serves `get_render_status`/`cancel_render` while `render_sequence` is pending. If it doesn't, cancel/status still work from a second MCP session (server supports multiple `Mcp-Session-Id`s); record whichever behaviour is real in the tool doc comments.
- **Every UE API named above was verified against 5.8 source at design time** (`F:\UE_5.8`, ue-api-verifier pass on [#301](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/301), 11/11 confirmed with file:line evidence — donor's 5.7 table holds on 5.8). Notables: `Lit` pass = `UMoviePipelineDeferredPassBase` itself (no `_Lit` suffix class exists); `RenderQueueWithExecutorInstance` returns void (post-kick `GetActiveExecutor()` check is the only failure signal); async-result contract as stated in tool 1.
- Keep the donor's "already_rendering" guard **and** the post-kick `GetActiveExecutor()` null check — the race is real.
- `Map` in settings is a soft pointer: resolve + `IsA<UWorld>` guard before building the job; null map + no loaded editor world = raise, never render garbage.
- Snapshot tool uses `FDateTime::Now()` for the folder timestamp — editor-side, fine (no determinism constraint there).
