# audio_analyze_and_place — design spec (Wave 3, the async tool)

Status: **WIP, banked 2026-05-31.** Branch `feat/wave3-audio-analyze`. Resume here next session.
This is the last Wave-3 backlog tool. It is materially harder than decal_scatter / the inspect_*
tools: async analysis + a dynamic delegate + a small core API change. Every UE 5.7 API below was
read from engine source this session (file:line cited).

## What it does
Analyze a `USoundWave` for onsets/beats and return beat timestamps (phase 1 = beats only;
placement/keyframing is a deliberate follow-up tool). Uses AudioSynesthesia's `UOnsetNRT`.

## Why it's async (deadlock note)
`UAudioAnalyzerNRT::AnalyzeAudio()` (WITH_EDITOR, AudioAnalyzerNRT.h:168) spawns a background
`FAutoDeleteAsyncTask` and marshals the result back via `AsyncTask(GameThread, ...)` →
`OnAnalysisComplete.Broadcast()`. MCP handlers run on the **game thread**. A synchronous in-handler
busy-wait would DEADLOCK (the game thread can't pump the completion task while blocked). So the
handler is **non-blocking**: it kicks off analysis, registers a task, and returns a `task_id`
immediately; the caller polls `poll_task`.

## Core prerequisite (DONE on the branch)
The companion needs the task subsystem. `FUCMCPTaskRegistry` (TaskRegistry.h) was:
1. moved `Private/MCP/` → `Public/MCP/` (include string `"MCP/TaskRegistry.h"` is module-relative,
   unchanged for the 5 core callers), and
2. given `UNREALAICONNECTION_API` on the class (mirrors `FUCMCPHandlerRegistry`) so a companion DLL
   can link `Get()/CreateTask/MarkRunning/MarkCompleted/MarkFailed`.
   **TODO next session: build the core to confirm the export compiles, before the companion.**

## Completion signal — REQUIRES a helper UObject
`OnAnalysisComplete` is a `DECLARE_DYNAMIC_MULTICAST_DELEGATE` (no params), `WITH_EDITORONLY_DATA`
(AudioAnalyzerNRT.h:52,174). Dynamic delegates can only bind a **UFUNCTION on a UObject** — NOT a
lambda/raw. So we need a small helper UClass in the companion:

```cpp
// UAudioAnalyzeTaskListener.h (companion Public, + .generated.h)
UCLASS()
class UAudioAnalyzeTaskListener : public UObject {
    GENERATED_BODY()
public:
    UPROPERTY() TObjectPtr<UOnsetNRT> Analyzer;   // UPROPERTY ref keeps the analyzer alive
    FString TaskId;
    int32   Channel = 0;
    bool    bNormalized = false;
    float   MinStrength = 0.f;
    UFUNCTION() void OnComplete();                // bound to Analyzer->OnAnalysisComplete
};
```

## Handler flow (Handler_AudioAnalyzeAndPlace.cpp, IsMutating=false, WITH_EDITOR-gated)
1. Load `USoundWave` (LoadAsset + Cast; not_a_sound_wave). Reject (specific error codes):
   `Sound->bProcedural` → `procedural_unsupported`; >2 channels → `multichannel_unsupported`;
   `GetImportedSoundWaveData(...)` fails → `no_imported_data`. (MetaSoundSource/SoundSourceBus are
   already excluded by the `Sound` property's DisallowedClasses.)
2. `NewObject<UOnsetNRT>` + `NewObject<UOnsetNRTSettings>`; set `Sensitivity` (0..1),
   `GranularityInSeconds` (0.005..0.25), `MinimumFrequency`, `MaximumFrequency`, `bDownmixToMono`
   from params. `Analyzer->Settings = settings; Analyzer->SetSound(wave);`
3. `NewObject<UAudioAnalyzeTaskListener>`; `AddToRoot()` it (keeps listener + analyzer alive across
   the async window); set its fields; `Analyzer->OnAnalysisComplete.AddDynamic(Listener, &UAudioAnalyzeTaskListener::OnComplete);`
4. `FUCMCPTaskRegistry::Get().CreateTask("audio_analyze", OutCancelFlag)` → taskId; store on listener;
   `MarkRunning(taskId)`; `Analyzer->AnalyzeAudio();`
5. Return `{ ok:true, task_id, status:"running", sound_wave }` (DurationInSeconds is 0 until done).
6. `OnComplete()` (fires on game thread, later tick): `Analyzer->GetChannelOnsetsBetweenTimes(0,
   Analyzer->DurationInSeconds, Channel, OutTimes, OutStrengths)` (or `GetNormalized...`); filter by
   MinStrength; build `beats:[{t,strength}]`; `MarkCompleted(TaskId, {ok, duration, channel,
   beat_count, beats})`; `RemoveFromRoot(this)` → listener + analyzer become GC-eligible.

Pre-checks (step 1) gate failures BEFORE AnalyzeAudio so OnAnalysisComplete reliably fires. A
pathological never-fires leaves a "running" task — tolerated per TaskRegistry's no-TTL design.

## Companion
`UnrealAIConnectionAudio` (EnabledByDefault:false). `.uplugin` Plugins: UnrealAIConnection +
AudioSynesthesia. Build.cs deps: Core, CoreUObject, Engine, Json, EditorScriptingUtilities,
UnrealAIConnection, AudioSynesthesia, AudioAnalyzer. Same StartupModule/ShutdownModule register/
unregister pattern as the other companions.

## Params / response
Params: `sound_wave`(req), `sensitivity`(0.5), `granularity_sec`(0.01), `min_frequency`(20),
`max_frequency`(20000), `downmix_to_mono`(true), `channel`(0), `normalized_strength`(false),
`min_strength`(0). Kick response: `{ok, task_id, status:"running", sound_wave}`. poll_task result:
`{ok, status:"completed", duration, channel, beat_count, beats:[{t,strength}]}`.

## Catalog + verify
Catalog 147 → 148; cpp_handlers 110 → 111; pytest +1. Live-verify needs AudioSynesthesia enabled in
the host + a `USoundWave` asset (import a short .wav, or find one). If no asset: prove registers +
reachable + kick returns a task_id + poll_task transitions running→completed. Full beat detection
needs a real wav.

## Engine source refs (UE 5.7, verified)
- UOnsetNRT / UOnsetNRTSettings / GetChannelOnsetsBetweenTimes — `Engine/Plugins/Runtime/AudioSynesthesia/Source/AudioSynesthesia/Classes/OnsetNRT.h:15,59,69,73,77`
- UAudioAnalyzerNRT: Sound:84, DurationInSeconds:88, SetSound:92 (WITH_EDITOR), AnalyzeAudio:168
  (WITH_EDITOR), OnAnalysisComplete:174 (WITH_EDITORONLY_DATA), delegate decl:52 — `Engine/Source/Runtime/AudioAnalyzer/Classes/AudioAnalyzerNRT.h`
- FUCMCPTaskRegistry — `MCP/TaskRegistry.h` (now Public): Get/CreateTask/MarkRunning/MarkCompleted/MarkFailed.

## Fallback (if AudioSynesthesia is judged too heavy/optional to depend on)
Read raw PCM via `USoundWave::GetImportedSoundWaveData(...)` and run a simple energy/spectral-flux
onset detector in C++ on the game thread — synchronous, no plugin, no helper UObject, ~150 LOC.
Lower quality than the CQT-based UOnsetNRT. Documented as an option, not preferred.

## Review feedback folded in (PR #287, closed — corrected to real API)
External review (#287, now closed) raised three points worth keeping. Its code samples used
fabricated signatures — `FUCMCPTaskRegistry::MarkCompleted(FGuid,bool)` / `(FGuid,bool,TArray<float>)`,
`CreateTask(FGuid,FGraphEventRef)`, `USoundWave::GetNumChannels()`, single-arg
`GetImportedSoundWaveData(TArrayView)` — none of which exist (verified against UE 5.8 source +
`TaskRegistry.h/.cpp`); do not copy them. Corrected takeaways:

1. **Listener lifetime — `UEditorSubsystem` is an alternative to the `AddToRoot`/`RemoveFromRoot` in
   steps 3–6.** Instead of root-setting each listener, an editor subsystem can hold them in a
   `UPROPERTY() TMap<FString, TObjectPtr<UAudioAnalyzeTaskListener>>` — GC tracks them, no leak if a
   task never fires. Key on the registry's `FString` task id (not `FGuid`). The current AddToRoot path
   is fine for one-shot tasks; the subsystem is cleaner if several analyses run concurrently. Decide at
   implementation.
2. **Validate the `channel` param (real gap).** Step 1 rejects >2-channel sounds, but nothing bounds
   the `channel` param (0..N-1) before `GetChannelOnsetsBetweenTimes(..., Channel, ...)` in step 6.
   `USoundWave` has no `GetNumChannels()` — use the `NumChannels` property:
   `if (Channel < 0 || Channel >= Sound->NumChannels)` → `invalid_arguments` with a bounds message.
3. **Optional async fallback.** The Fallback section above is synchronous on the game thread. If it
   proves heavy, move the detector off-thread with `FFunctionGraphTask::CreateAndDispatchWhenReady(fn,
   TStatId(), nullptr, ENamedThreads::AnyNormalThreadNormalTask)`, then hop back via
   `ENamedThreads::GameThread` to call `FUCMCPTaskRegistry::Get().MarkCompleted(TaskId, Result)`
   (`MarkFailed(TaskId, Error)` on failure). Sync stays acceptable as the documented not-preferred path.
