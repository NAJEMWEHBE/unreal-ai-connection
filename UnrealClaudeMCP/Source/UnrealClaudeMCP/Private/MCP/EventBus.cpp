// Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.

#include "MCP/EventBus.h"

#include "Misc/DateTime.h"
#include "Misc/Guid.h"
#include "Misc/ScopeLock.h"

// ---------------------------------------------------------------------------
// Singleton
// ---------------------------------------------------------------------------

FUCMCPEventBus& FUCMCPEventBus::Get()
{
    // Construct-on-first-use. Lives for process lifetime; UE delegates must
    // be unsubscribed in ShutdownModule before the module unloads, so this
    // singleton is never called into after its dependencies are gone.
    static FUCMCPEventBus Instance;
    return Instance;
}

FUCMCPEventBus::FUCMCPEventBus()
{
    // Pre-allocate so Push never allocates under the lock.
    Ring.SetNum(kRingSize);
}

// ---------------------------------------------------------------------------
// Push - called from delegate handlers (any thread)
// ---------------------------------------------------------------------------

void FUCMCPEventBus::Push(const FString& EventType, TSharedPtr<FJsonObject> Data)
{
    // Re-entrancy guard. UE delegates are usually not re-entrant in practice,
    // but if a delegate handler somehow ends up triggering another delegate
    // that lands here (e.g. logging inside a handler triggers an event that
    // logs again), drop the inner call rather than recursing into the lock.
    // Same discipline as LogCapture.
    static thread_local bool bInPush = false;
    if (bInPush) { return; }
    TGuardValue<bool> ReentrancyGuard(bInPush, true);

    // Build the entry outside the lock (string ops are the slow part).
    FUCMCPEvent Entry;
    Entry.EventType = EventType;
    Entry.Data = Data.IsValid() ? Data : MakeShared<FJsonObject>();

    const FDateTime Now = FDateTime::Now();
    Entry.Timestamp = FString::Printf(
        TEXT("%04d.%02d.%02d-%02d.%02d.%02d"),
        Now.GetYear(), Now.GetMonth(), Now.GetDay(),
        Now.GetHour(), Now.GetMinute(), Now.GetSecond());

    // Take the lock only for the seq assignment + ring write.
    FScopeLock Lock(&Mutex);

    Entry.Seq = NextSeq++;
    Ring[Head] = MoveTemp(Entry);
    Head = (Head + 1) % kRingSize;
    if (Count < kRingSize) { ++Count; }
}

// ---------------------------------------------------------------------------
// Snapshot - called from Handler_PollEvents (game thread)
// ---------------------------------------------------------------------------

TArray<FUCMCPEvent> FUCMCPEventBus::Snapshot(
    int64 SinceSeq,
    int32 MaxCount,
    const TArray<FString>& EventFilter,
    int64& OutNextSeq,
    int64& OutFirstSeqInBuffer,
    bool& OutDropped) const
{
    TArray<FUCMCPEvent> Out;
    // Don't early-return on non-positive MaxCount: the metadata (OutNextSeq,
    // OutFirstSeqInBuffer, OutDropped) must still be populated accurately so
    // callers asking "what's the bus state?" with MaxCount=0 don't get garbage
    // (the early return would have set OutNextSeq=0, which a client would
    // mistake for "buffer empty" and reset its cursor). The loop below
    // correctly handles MaxCount<=0 via its `Out.Num() < MaxCount` guard --
    // the loop body never runs and Out stays empty. Reserve clamps to
    // non-negative because TArray::Reserve(<negative>) is undefined.
    // (Caught by Gemini medium-priority review on PR #40.)
    Out.Reserve(FMath::Max(0, FMath::Min(MaxCount, kRingSize)));

    FScopeLock Lock(&Mutex);

    OutNextSeq = NextSeq;

    if (Count == 0)
    {
        OutFirstSeqInBuffer = -1;
        // No events ever pushed (or buffer was just constructed). SinceSeq=-1
        // is the "first poll" sentinel and is never considered dropped.
        OutDropped = false;
        return Out;
    }

    // Iterate the ring oldest-first. When the buffer is not full, the oldest
    // entry is at index 0 (entries are contiguous from 0 to Count-1). When
    // the buffer is full, the oldest is at Head (the position about to be
    // overwritten next), wrapping.
    const int32 StartIndex = (Count < kRingSize) ? 0 : Head;
    const FUCMCPEvent& OldestEntry = Ring[StartIndex];
    OutFirstSeqInBuffer = OldestEntry.Seq;

    // Drop detection (inclusive cursor semantics, see comment below): caller's
    // since_seq is below the oldest seq still buffered, meaning some events
    // the caller asked for (in the closed interval [since_seq, ...]) have
    // been evicted. SinceSeq=-1 (the initial-poll sentinel) is intentionally
    // treated as "no prior position" and is never reported as dropped.
    OutDropped = (SinceSeq >= 0 && SinceSeq < OutFirstSeqInBuffer);

    // Filter is INCLUSIVE on since_seq: returned events have seq >= since_seq.
    // The handler's documented client contract is "pass the previous response's
    // next_seq back as since_seq on the next poll" -- since next_seq is the id
    // about to be assigned (not yet pushed), the next event's seq will exactly
    // equal that value, and an exclusive filter would silently drop it.
    // (Caught by Codex P1 review on PR #40.)
    for (int32 i = 0; i < Count && Out.Num() < MaxCount; ++i)
    {
        const FUCMCPEvent& Entry = Ring[(StartIndex + i) % kRingSize];
        if (Entry.Seq < SinceSeq)
        {
            continue;  // already-seen
        }

        // Apply event-type filter (substring match on EventType).
        if (EventFilter.Num() > 0)
        {
            bool bMatched = false;
            for (const FString& F : EventFilter)
            {
                if (!F.IsEmpty() && Entry.EventType.Contains(F))
                {
                    bMatched = true;
                    break;
                }
            }
            if (!bMatched) { continue; }
        }

        Out.Add(Entry);
    }

    return Out;
}

// ---------------------------------------------------------------------------
// Subscription API (PR #43)
// ---------------------------------------------------------------------------
//
// Subscriptions are bus-side state holding a cursor + filter so clients can
// poll without tracking since_seq. The implementation reuses the same lock
// and the same ring for the actual event read; the only new state is the
// per-sub cursor, which advances atomically with each PollSubscription call.

FString FUCMCPEventBus::RegisterSubscription(const TArray<FString>& EventFilter, int64& OutInitialNextSeq)
{
    // FGuid is cryptographically random; collision probability between
    // session restarts is effectively zero. ToString() produces the
    // canonical hyphenated 36-char form (e.g. "5C2D...-...").
    const FString Id = FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens);

    FUCMCPSubscription Sub;
    Sub.Id = Id;
    Sub.EventFilter = EventFilter;

    FScopeLock Lock(&Mutex);
    // The cursor starts at the bus's current NextSeq -- subscribers see
    // events fired AFTER subscription, not historical ones. This avoids
    // the asset-registry initial-scan flood being delivered to every
    // newly-created subscription.
    Sub.NextSeq = NextSeq;
    OutInitialNextSeq = NextSeq;
    Subscriptions.Add(Id, MoveTemp(Sub));
    return Id;
}

bool FUCMCPEventBus::Unsubscribe(const FString& SubscriptionId)
{
    FScopeLock Lock(&Mutex);
    return Subscriptions.Remove(SubscriptionId) > 0;
}

TArray<FUCMCPEvent> FUCMCPEventBus::PollSubscription(
    const FString& SubscriptionId,
    int32 MaxCount,
    int64& OutNextSeq,
    int64& OutFirstSeqInBuffer,
    bool& OutDropped,
    bool& OutFound)
{
    TArray<FUCMCPEvent> Out;
    OutFound = false;

    FScopeLock Lock(&Mutex);

    FUCMCPSubscription* Sub = Subscriptions.Find(SubscriptionId);
    if (!Sub)
    {
        // Unknown id: return empty + OutFound=false. Caller turns this
        // into a not_found error. Other out-params left as-is so the
        // caller can choose whether to surface them.
        return Out;
    }
    OutFound = true;

    // Reuse the snapshot logic by inlining a filtered scan. Drop detection
    // mirrors Snapshot: if the sub's cursor is below the oldest buffered
    // seq, some events the sub asked for were evicted.
    OutNextSeq = NextSeq;

    if (Count == 0)
    {
        OutFirstSeqInBuffer = -1;
        OutDropped = false;
        return Out;
    }

    Out.Reserve(FMath::Max(0, FMath::Min(MaxCount, kRingSize)));

    const int32 StartIndex = (Count < kRingSize) ? 0 : Head;
    OutFirstSeqInBuffer = Ring[StartIndex].Seq;
    OutDropped = (Sub->NextSeq < OutFirstSeqInBuffer);

    int64 HighestDeliveredSeq = Sub->NextSeq - 1;
    for (int32 i = 0; i < Count && Out.Num() < MaxCount; ++i)
    {
        const FUCMCPEvent& Entry = Ring[(StartIndex + i) % kRingSize];
        if (Entry.Seq < Sub->NextSeq)
        {
            continue;  // already-delivered to this subscriber
        }

        // Apply per-sub event-type filter (substring match on EventType).
        if (Sub->EventFilter.Num() > 0)
        {
            bool bMatched = false;
            for (const FString& F : Sub->EventFilter)
            {
                if (!F.IsEmpty() && Entry.EventType.Contains(F))
                {
                    bMatched = true;
                    break;
                }
            }
            if (!bMatched) { continue; }
        }

        Out.Add(Entry);
        HighestDeliveredSeq = Entry.Seq;
    }

    // Advance the per-sub cursor past the highest delivered seq. Note:
    // even if filter rejected events between Sub->NextSeq and the highest
    // matched event, we don't advance past unmatched ones -- a later
    // call with a different filter could legitimately want them. The
    // server-side cursor strictly tracks "what THIS subscription has
    // received under its own filter".
    Sub->NextSeq = HighestDeliveredSeq + 1;

    return Out;
}
