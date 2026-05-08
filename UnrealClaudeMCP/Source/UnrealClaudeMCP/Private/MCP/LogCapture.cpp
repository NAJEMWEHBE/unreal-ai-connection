// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/LogCapture.h"

#include "HAL/PlatformTime.h"
#include "Misc/DateTime.h"

// ---------------------------------------------------------------------------
// Singleton
// ---------------------------------------------------------------------------

FUCMCPLogCapture& FUCMCPLogCapture::Get()
{
    // Construct-on-first-use.  Static local is safe for singletons that are
    // registered/deregistered by the module lifecycle (StartupModule /
    // ShutdownModule).  The object lives for the process lifetime — UE's
    // GLog must have RemoveOutputDevice called before it shuts down.
    static FUCMCPLogCapture Instance;
    return Instance;
}

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

FUCMCPLogCapture::FUCMCPLogCapture()
{
    // Pre-allocate the ring so Serialize never allocates under the lock.
    Ring.SetNum(kRingSize);
}

// ---------------------------------------------------------------------------
// FOutputDevice::Serialize — called from any thread
// ---------------------------------------------------------------------------

void FUCMCPLogCapture::Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity,
                                 const FName& Category)
{
    // Re-entrancy guard: GLog dispatches every log line to every registered
    // FOutputDevice. If anything inside this Serialize ends up emitting a log
    // line itself (FString::Printf allocation under heavy debug builds, the
    // Mutex's debug telemetry, etc.), we'd recurse infinitely / deadlock on
    // the FCriticalSection. Drop re-entrant calls silently — the deepest call
    // is the one we want to record, not the synthetic noise on top of it.
    static thread_local bool bInSerialize = false;
    if (bInSerialize) { return; }
    TGuardValue<bool> ReentrancyGuard(bInSerialize, true);

    // Build entry outside the lock (string operations are the slow part).
    FUCMCPLogEntry Entry;

    // Timestamp: use wall-clock time formatted to match UE's output log style.
    const FDateTime Now = FDateTime::Now();
    Entry.Timestamp = FString::Printf(
        TEXT("%04d.%02d.%02d-%02d.%02d.%02d"),
        Now.GetYear(), Now.GetMonth(), Now.GetDay(),
        Now.GetHour(), Now.GetMinute(), Now.GetSecond());

    Entry.Category  = Category.ToString();
    Entry.Message   = FString(V);

    // Convert ELogVerbosity to the canonical string names that match the
    // min_verbosity parameter accepted by get_log_lines.
    switch (Verbosity)
    {
        case ELogVerbosity::Fatal:       Entry.Verbosity = TEXT("Fatal");       break;
        case ELogVerbosity::Error:       Entry.Verbosity = TEXT("Error");       break;
        case ELogVerbosity::Warning:     Entry.Verbosity = TEXT("Warning");     break;
        case ELogVerbosity::Display:     Entry.Verbosity = TEXT("Display");     break;
        case ELogVerbosity::Log:         Entry.Verbosity = TEXT("Log");         break;
        case ELogVerbosity::Verbose:     Entry.Verbosity = TEXT("Verbose");     break;
        case ELogVerbosity::VeryVerbose: Entry.Verbosity = TEXT("VeryVerbose"); break;
        default:                         Entry.Verbosity = TEXT("Log");         break;
    }

    // Write into ring under the lock.
    FScopeLock Lock(&Mutex);
    Ring[Head] = MoveTemp(Entry);
    Head = (Head + 1) % kRingSize;
    if (Count < kRingSize) { ++Count; }
}

// ---------------------------------------------------------------------------
// GetLines — snapshot, oldest-first
// ---------------------------------------------------------------------------

TArray<FUCMCPLogEntry> FUCMCPLogCapture::GetLines() const
{
    TArray<FUCMCPLogEntry> Snapshot;

    {
        FScopeLock Lock(&Mutex);
        Snapshot.Reserve(Count);

        if (Count < kRingSize)
        {
            // Buffer not yet full: entries are contiguous from index 0 to Count-1.
            for (int32 i = 0; i < Count; ++i)
            {
                Snapshot.Add(Ring[i]);
            }
        }
        else
        {
            // Buffer is full: oldest entry is at Head, wrapping around.
            for (int32 i = 0; i < kRingSize; ++i)
            {
                Snapshot.Add(Ring[(Head + i) % kRingSize]);
            }
        }
    }

    return Snapshot;
}
