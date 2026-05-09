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
    const FDateTime Now = FDateTime::Now();
    return FString::Printf(
        TEXT("%04d.%02d.%02d-%02d.%02d.%02d"),
        Now.GetYear(), Now.GetMonth(), Now.GetDay(),
        Now.GetHour(), Now.GetMinute(), Now.GetSecond());
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
