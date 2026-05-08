// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
//
// FUCMCPLogCapture - thread-safe in-process ring buffer for UE Output Log lines.
//
// Registered globally as an FOutputDevice in FUnrealClaudeMCPModule::StartupModule.
// Deregistered in ShutdownModule.  get_log_lines reads from this buffer via GetLines().

#pragma once

#include "CoreMinimal.h"
#include "Misc/OutputDevice.h"
#include "HAL/CriticalSection.h"

/** One captured log entry. */
struct FUCMCPLogEntry
{
    FString Timestamp;  // "YYYY.MM.DD-HH.MM.SS"
    FString Category;   // e.g. "LogTemp"
    FString Verbosity;  // e.g. "Warning"
    FString Message;    // raw message text (without prefix)
};

/**
 * Process-singleton FOutputDevice that keeps the last kLogCaptureRingSize
 * log lines in a fixed ring buffer.  All writes are mutex-protected so UE's
 * multi-threaded log system can't corrupt the buffer.
 *
 * Usage:
 *   // Registration (StartupModule):
 *   GLog->AddOutputDevice(&FUCMCPLogCapture::Get());
 *
 *   // De-registration (ShutdownModule):
 *   GLog->RemoveOutputDevice(&FUCMCPLogCapture::Get());
 *
 *   // Read a snapshot:
 *   TArray<FUCMCPLogEntry> Snapshot = FUCMCPLogCapture::Get().GetLines();
 */
class FUCMCPLogCapture : public FOutputDevice
{
public:
    static constexpr int32 kRingSize = 1000;

    /** Singleton access. */
    static FUCMCPLogCapture& Get();

    /**
     * FOutputDevice override — called by FOutputDeviceRedirector on every log
     * event from any thread.  Protected by Mutex.
     */
    virtual void Serialize(const TCHAR* V, ELogVerbosity::Type Verbosity,
                           const FName& Category) override;

    /**
     * Copy the ring buffer contents to a flat array (oldest-first).
     * Takes Mutex while copying, releases before returning.
     */
    TArray<FUCMCPLogEntry> GetLines() const;

private:
    FUCMCPLogCapture();

    mutable FCriticalSection Mutex;

    // Fixed-capacity ring. Elements at indices [0, kRingSize) are always
    // allocated; Count tracks how many have been filled.
    TArray<FUCMCPLogEntry> Ring;
    int32 Head  = 0;    // next write position (wraps modulo kRingSize)
    int32 Count = 0;    // entries filled (capped at kRingSize)
};
