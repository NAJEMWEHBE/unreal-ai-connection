# Code Review: PR #278 - audio_analyze_and_place Design Spec

**PR**: [WIP] audio_analyze_and_place (Wave 3, banked) — core prep + design spec  
**Reviewer**: BruceZeng (曾炜峻)  
**Date**: 2026-06-17  
**Repo**: [NAJEMWEHBE/unreal-ai-connection](https://github.com/NAJEMWEHBE/unreal-ai-connection)  

---

## 📋 Overview

PR #278 contains safe prep changes (FUCMCPTaskRegistry public export) and a design spec for the upcoming `audio_analyze_and_place` MCP tool. The tool will analyze audio files in UE5 and automatically place sound-related objects in the scene.

**Gemini Code Assist** has already provided 3 review comments. This review builds upon those findings with additional C++/UE5 specific insights.

---

## ✅ What Looks Good

1. **TaskRegistry Export Strategy** - Promoting `FUCMCPTaskRegistry` to public API is the right approach for cross-module async coordination
2. **Poll-based Architecture** - Using `poll_task` for async status checking follows the existing MCP patterns
3. **Helper UObject Design** - `UAudioAnalyzeTaskListener` for delegate binding is a standard UE5 pattern

---

## 🔍 Detailed Review Comments

### Comment #1: Memory Management via UEditorSubsystem

**Location**: `docs/superpowers/specs/2026-05-31-audio-analyze-design.md` lines 56-57

**Gemini's Concern**: `AddToRoot()` / `RemoveFromRoot()` risk memory leaks.

**Additional Analysis**:

```cpp
// Current approach (problematic):
UAudioAnalyzeTaskListener* Listener = NewObject<UAudioAnalyzeTaskListener>();
Listener->AddToRoot(); // Risk: never removed if analysis fails
```

**Recommended Solution** - Use `UEditorSubsystem`:

```cpp
// UEditorSubsystem approach (safer):
UCLASS()
class UNREALAICONNECTIONAUDIO_API UAudioAnalyzeTaskSubsystem : public UEditorSubsystem
{
    GENERATED_BODY()
    
public:
    void RegisterTask(UAudioAnalyzeTaskListener* Listener, FGuid TaskId);
    void UnregisterTask(FGuid TaskId);
    
private:
    // Using TObjectPtr for proper UE garbage collection
    UPROPERTY()
    TMap<FGuid, TObjectPtr<UAudioAnalyzeTaskListener>> ActiveTasks;
    
    // Cleanup on subsystem shutdown
    virtual void Deinitialize() override;
};

// Usage:
void UAudioAnalyzeTaskSubsystem::RegisterTask(UAudioAnalyzeTaskListener* Listener, FGuid TaskId)
{
    ActiveTasks.Add(TaskId, Listener); // UPROPERTY handles GC automatically
}
```

**Benefits**:
- No manual `AddToRoot()` / `RemoveFromRoot()` needed
- `UPROPERTY()` with `TObjectPtr` ensures proper garbage collection
- Centralized lifecycle management
- Automatic cleanup on editor shutdown via `Deinitialize()`

---

### Comment #2: Channel Index Boundary Validation

**Location**: `docs/superpowers/specs/2026-05-31-audio-analyze-design.md` lines 49-51

**Gemini's Concern**: Missing validation for `channel` parameter bounds.

**Recommended Addition**:

```cpp
// Add to validation step after channel count check:
bool UAudioAnalyzeHandler::ValidateChannelIndex(USoundWave* Sound, int32 Channel)
{
    if (Channel < 0)
    {
        OutError = TEXT("audio_analyze_and_place: invalid_arguments: channel must be non-negative");
        return false;
    }
    
    // GetNumChannels() returns 1 for mono, 2 for stereo
    const int32 ActualChannels = Sound->GetNumChannels();
    if (Channel >= ActualChannels)
    {
        OutError = FString::Printf(
            TEXT("audio_analyze_and_place: invalid_arguments: channel index %d out of bounds (sound has %d channels)"),
            Channel, ActualChannels);
        return false;
    }
    
    return true;
}
```

**Also consider**: Document the error behavior clearly in the tool's error codes table.

---

### Comment #3: Async Fallback for Onset Detector

**Location**: `docs/superpowers/specs/2026-05-31-audio-analyze-design.md` lines 94-96

**Gemini's Concern**: Synchronous onset detector blocks game thread.

**Recommended Implementation**:

```cpp
// Async fallback using UE's Task Graph system:
void UAudioAnalyzeHandler::RunFallbackOnsetDetectorAsync(
    USoundWave* Sound, 
    int32 Channel,
    FGuid TaskId)
{
    // Capture necessary data for background task
    TWeakObjectPtr<USoundWave> SoundWeak = Sound;
    const int32 CaptureChannel = Channel;
    
    // Use UE's Task Graph for safe async execution
    FGraphEventRef Task = FFunctionGraphTask::CreateAndDispatchWhenReady(
        [SoundWeak, CaptureChannel, TaskId]()
        {
            if (!SoundWeak.IsValid()) return;
            
            USoundWave* LocalSound = SoundWeak.Get();
            
            // Read PCM data
            TArrayView<const uint8> RawData;
            if (!LocalSound->GetImportedSoundWaveData(RawData))
            {
                // Report failure via TaskRegistry
                FUCMCPTaskRegistry::MarkCompleted(TaskId, false);
                return;
            }
            
            // Simple energy-based onset detection (non-blocking)
            TArray<float> Onsets = ComputeEnergyOnsets(RawData, CaptureChannel);
            
            // Schedule completion on game thread
            FGraphEventRef CompletionTask = FFunctionGraphTask::CreateAndDispatchWhenReady(
                [TaskId, Onsets]()
                {
                    FUCMCPTaskRegistry::MarkCompleted(TaskId, true, Onsets);
                },
                TStatId(), nullptr, ENamedThreads::GameThread);
        },
        TStatId(), nullptr, ENamedThreads::AnyNormalThreadNormalTask);
    
    // Register task for polling
    FUCMCPTaskRegistry::CreateTask(TaskId, Task);
}
```

---

## 📝 Additional Suggestions

### 1. Error Code Standardization

Consider adding these error codes to the spec:

| Error Code | Trigger | Description |
|------------|---------|-------------|
| `invalid_arguments` | Channel < 0 | Negative channel index |
| `channel_out_of_bounds` | Channel >= NumChannels | Channel exceeds sound's channel count |
| `analysis_cancelled` | Task cancelled | User or system cancelled analysis |
| `memory_allocation_failed` | Large audio file | OOM during PCM read |

### 2. Consider Cancellation Token Pattern

For better async task management:

```cpp
// In audio_analyze_and_place params:
struct FAudioAnalyzeParams
{
    FString AssetPath;
    int32 Channel;
    float StartTime;
    float EndTime;
    // New: cancellation support
    FString TaskId; // For poll_task correlation
    bool bCancellationRequested;
};
```

### 3. Documentation Improvements

Add a section explaining the difference between:
- **Real-time analysis**: Using `UOnsetNRT` for high-quality analysis
- **Fallback analysis**: Simple energy-based for quick previews

---

## 🎯 Summary

| Category | Status | Notes |
|----------|--------|-------|
| Memory Management | ⚠️ Needs Work | Use UEditorSubsystem instead of AddToRoot |
| Input Validation | ⚠️ Needs Work | Add channel index bounds check |
| Async Design | ⚠️ Needs Work | Fallback must be async |
| Error Handling | ✅ Good | Follows existing patterns |
| Documentation | ✅ Good | Clear spec structure |

**Overall**: The design spec is solid. Main improvements needed are around memory safety and async correctness.

---

## 💡 Willing to Contribute

If the maintainers agree with these suggestions, I'm happy to:
1. Implement the `UEditorSubsystem` approach
2. Add the channel validation code
3. Implement the async fallback pattern

Please let me know how I can help move this forward! 🙏
