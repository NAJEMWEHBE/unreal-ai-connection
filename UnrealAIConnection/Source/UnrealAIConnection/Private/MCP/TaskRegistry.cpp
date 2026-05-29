// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/TaskRegistry.h"

#include "Misc/DateTime.h"
#include "Misc/Guid.h"
#include "Misc/ScopeLock.h"

FUCMCPTaskRegistry& FUCMCPTaskRegistry::Get()
{
    static FUCMCPTaskRegistry Instance;
    return Instance;
}

FString FUCMCPTaskRegistry::NowString()
{
    // FDateTime::ToString() defaults to "%Y.%m.%d-%H.%M.%S" -- exactly the
    // canonical YYYY.MM.DD-HH.MM.SS form LogCapture and EventBus use, so
    // no explicit format string needed. (Gemini medium-priority on PR #44
    // suggested this cleanup -- LogCapture.cpp + EventBus.cpp still use
    // the hand-rolled Printf form; potential follow-up cleanup PR.)
    return FDateTime::Now().ToString();
}

bool FUCMCPTaskRegistry::IsTerminal(const FString& Status)
{
    return Status == UCMCPTaskStatus::Completed
        || Status == UCMCPTaskStatus::Cancelled
        || Status == UCMCPTaskStatus::Failed;
}

FString FUCMCPTaskRegistry::CreateTask(const FString& TaskType, TSharedPtr<TAtomic<bool>>& OutCancellationFlag)
{
    const FString Id = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);
    const FString StartTs = NowString();

    FRecord Record;
    Record.Info.Id = Id;
    Record.Info.Type = TaskType;
    Record.Info.Status = UCMCPTaskStatus::Pending;
    Record.Info.StartTime = StartTs;
    // TAtomic<bool>'s default ctor zero-initializes -- explicit init for clarity.
    Record.CancellationFlag = MakeShared<TAtomic<bool>>(false);
    OutCancellationFlag = Record.CancellationFlag;

    FScopeLock Lock(&Mutex);
    Tasks.Add(Id, MoveTemp(Record));
    return Id;
}

void FUCMCPTaskRegistry::MarkRunning(const FString& TaskId)
{
    FScopeLock Lock(&Mutex);
    if (FRecord* Record = Tasks.Find(TaskId))
    {
        // Only valid transition is pending -> running. Workers occasionally
        // race against cancellation; if cancel landed first, leave the
        // status alone (it's already in a terminal state).
        if (Record->Info.Status == UCMCPTaskStatus::Pending)
        {
            Record->Info.Status = UCMCPTaskStatus::Running;
        }
    }
}

void FUCMCPTaskRegistry::MarkCompleted(const FString& TaskId, TSharedPtr<FJsonObject> Result)
{
    FScopeLock Lock(&Mutex);
    if (FRecord* Record = Tasks.Find(TaskId))
    {
        if (!IsTerminal(Record->Info.Status))
        {
            Record->Info.Status = UCMCPTaskStatus::Completed;
            Record->Info.Result = Result;
            Record->Info.EndTime = NowString();
        }
    }
}

void FUCMCPTaskRegistry::MarkCancelled(const FString& TaskId)
{
    FScopeLock Lock(&Mutex);
    if (FRecord* Record = Tasks.Find(TaskId))
    {
        if (!IsTerminal(Record->Info.Status))
        {
            Record->Info.Status = UCMCPTaskStatus::Cancelled;
            Record->Info.EndTime = NowString();
        }
    }
}

void FUCMCPTaskRegistry::MarkFailed(const FString& TaskId, const FString& Error)
{
    FScopeLock Lock(&Mutex);
    if (FRecord* Record = Tasks.Find(TaskId))
    {
        if (!IsTerminal(Record->Info.Status))
        {
            Record->Info.Status = UCMCPTaskStatus::Failed;
            Record->Info.Error = Error;
            Record->Info.EndTime = NowString();
        }
    }
}

bool FUCMCPTaskRegistry::RequestCancel(const FString& TaskId)
{
    TSharedPtr<TAtomic<bool>> Flag;

    {
        FScopeLock Lock(&Mutex);
        FRecord* Record = Tasks.Find(TaskId);
        if (!Record)
        {
            return false;
        }
        if (IsTerminal(Record->Info.Status))
        {
            return false;
        }
        Record->Info.bCancelRequested = true;
        Flag = Record->CancellationFlag;
    }

    // Set the atomic flag OUTSIDE the lock. The flag is shared with the
    // worker; the worker reads it lock-free, so this Store is the cheapest
    // possible cross-thread signal. No need to hold the registry mutex.
    if (Flag.IsValid())
    {
        Flag->Store(true);
    }
    return true;
}

bool FUCMCPTaskRegistry::GetTask(const FString& TaskId, FUCMCPTaskInfo& OutInfo) const
{
    FScopeLock Lock(&Mutex);
    if (const FRecord* Record = Tasks.Find(TaskId))
    {
        OutInfo = Record->Info;
        // Mirror the atomic flag's current value at snapshot time. The
        // flag may be set by the time the caller reads OutInfo, but that's
        // racy regardless -- the snapshot semantics are "as of the moment
        // we held the lock".
        if (Record->CancellationFlag.IsValid())
        {
            OutInfo.bCancelRequested = Record->CancellationFlag->Load();
        }
        return true;
    }
    return false;
}

void FUCMCPTaskRegistry::Snapshot(TArray<FUCMCPTaskInfo>& OutInfos) const
{
    FScopeLock Lock(&Mutex);
    OutInfos.Reset();
    OutInfos.Reserve(Tasks.Num());
    for (const TPair<FString, FRecord>& Pair : Tasks)
    {
        FUCMCPTaskInfo Info = Pair.Value.Info;
        if (Pair.Value.CancellationFlag.IsValid())
        {
            Info.bCancelRequested = Pair.Value.CancellationFlag->Load();
        }
        OutInfos.Add(MoveTemp(Info));
    }
}
