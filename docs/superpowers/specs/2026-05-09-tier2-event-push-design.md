# UnrealClaudeMCP Tier 2 — Editor event push design

**Date:** 2026-05-09
**Status:** Approved (autonomy mode — spec stands as the design contract)
**Author:** Claude
**Builds on:** v0.10.1 (38 handlers; Tier 1 ergonomics complete)
**Target releases:** v0.11.0 (PR #40 tracer bullet) + future v0.11.x PRs

---

## Why this is "the autonomy multiplier"

Tier 1 made Claude's existing tooling cheaper (less escaping pain, fewer round-trips). Tier 2 changes the **kind** of automation possible: today the bridge is request-response — Claude only knows about the editor at the moment it asks. With editor event push, UE notifies Claude when state changes, enabling reactive flows:

- *user dropped a chair into the level → reposition camera, suggest material*
- *texture import finished → kick off the texture-config pipeline*
- *blueprint compile failed → fetch logs, propose fix*
- *level saved → snapshot version metadata*

The current 38 handlers cover all the *outbound* automation surface. Adding event push opens the *inbound* surface, which is the larger leverage point.

---

## The MCP-shape constraint (load-bearing)

**MCP itself is request-response.** The protocol does have server-initiated `notifications/*`, but real MCP clients (Claude Code, Claude Desktop, etc.) don't surface arbitrary custom notifications as user-actionable signals. They're for protocol metadata (`tools/list_changed`, etc.).

Consequence: the user-facing surface for "subscribe to UE events" must be a **poll-style MCP tool**. UE pushes into a server-side buffer; the MCP client drains the buffer with a tool call.

This is not a limitation specific to this design — it's the structural nature of how MCP composes with editor pubsub. Other MCP servers facing the same problem (sentry, datadog, etc.) reach the same shape.

---

## Architectural choice: UE-side ring buffer + `poll_events` handler

We considered four shapes:

1. **UE-side ring buffer + poll handler** — UE delegates write structured events into a process-singleton ring buffer (`FUCMCPEventBus`). MCP clients call `poll_events(since_seq, max_count, event_filter)` to drain.
2. **Bidirectional TCP** — extend the wire protocol to support server-initiated frames; bridge runs a second thread to read pushed events; MCP client uses `poll_events` to drain bridge-side buffer.
3. **Separate WebSocket channel** — second listener on a different port; long-lived connection; bridge demux's events from tool-call responses.
4. **Out-of-band file/IPC** — UE writes events to a JSON-lines file; user runs a separate watcher process. Bypass MCP entirely.

**Chosen Option 1.** Reasons:

| | Option 1 | Option 2 | Option 3 | Option 4 |
|---|---|---|---|---|
| Protocol changes | None — reuses existing TCP request/response | Wire format change (server-push frames) | New transport | None — bypass MCP |
| Bridge complexity | None — bridge is unchanged | New reader thread + buffer | Second listener + demux | Watcher process |
| MCP client integration | Just another tool call | Just another tool call | Just another tool call | Off-platform |
| Latency | Polling interval (e.g. 1-2s) | Sub-second | Sub-second | File-watch lag |
| First-PR risk | LOW (mirrors LogCapture) | MEDIUM (transport-layer change) | HIGH (new listener) | Off-architecture |

Option 1 is the smallest possible architectural change that delivers the value, and it directly mirrors the established `LogCapture.h/.cpp` ring-buffer pattern. The polling-latency cost (1-2s typical) is acceptable for the use cases listed above (none are sub-second-critical). A future PR can layer a `wait_for_events(timeout, filter)` long-poll handler on top of the same buffer if and when sub-second latency becomes the load-bearing requirement.

Option 2 is the fallback if Option 1's polling latency is later observed to bottleneck a real workflow. Option 3 is rejected (high complexity, no current benefit). Option 4 is rejected (off-architecture).

---

## Architecture (Option 1)

```
UE Editor                                          Bridge (unchanged)        MCP Client
─────────                                          ───────────────────       ──────────
GEngine->OnLevelActorAdded()      ──┐
GEngine->OnLevelActorDeleted()    ──┼─→ FUCMCPEventBus::Push(Type, Payload)
IAssetRegistry::OnAssetAdded()    ──┘     │
... (more in future PRs)                  ↓
                                      Ring buffer
                                      (1000 entries)
                                      monotonic seq
                                          │
                                          ↓ Snapshot()
                                      Handler_PollEvents
                                          │
                                          ↓ TCP response (existing wire format)
                                                                    ────→  bridge passes through  ────→  poll every N seconds
```

Key properties:
- **Thread-safe**: `FCriticalSection` + `thread_local` re-entrancy guard, like LogCapture. Required because `IAssetRegistry::OnAssetAdded` is `DECLARE_TS_MULTICAST_DELEGATE` (fires from any thread, including background asset-registry scans).
- **Fixed-size ring** (default 1000): bounded memory, no allocation under the lock. Old events evicted when buffer is full.
- **Monotonic seq numbers**: every event gets a strictly-increasing `int64` seq. Clients pass `since_seq` to get only newer events.
- **Drop detection**: response includes `next_seq` (= the seq the next-written event would receive) and `dropped` (true iff `since_seq` is older than the oldest seq currently in the buffer). Clients see `dropped=true` and know they missed events between polls.

---

## Event schema

Every event is a JSON object with a stable shape:

```json
{
  "seq": 1234,
  "event": "actor_spawned",
  "ts": "2026.05.09-16.42.13",
  "data": { /* event-specific payload */ }
}
```

| Field | Type | Notes |
|---|---|---|
| `seq` | int64 | Monotonic, never reused. Ranges from 0 (first event ever) upward. |
| `event` | string | Snake_case event type name (`actor_spawned`, `actor_deleted`, `asset_added`, …). |
| `ts` | string | Wall-clock timestamp `YYYY.MM.DD-HH.MM.SS`, matching `LogCapture`'s format for visual consistency. |
| `data` | object | Event-specific payload (see per-event docs). May be empty for events with no associated state. |

The `data` payload for the 3 starter events:

### `actor_spawned`

Fired from `UEngine::OnLevelActorAdded(AActor*)`.

```json
{ "actor_label": "StaticMeshActor_0", "actor_name": "StaticMeshActor_0", "class": "StaticMeshActor", "level": "/Game/Maps/MyMap" }
```

- `actor_label` — Outliner display name (`AActor::GetActorLabel()`)
- `actor_name` — FName-derived unique name (`AActor::GetName()`)
- `class` — leaf class name (e.g. `StaticMeshActor`)
- `level` — package path of the level the actor belongs to, or `""` if the actor has no outer world

### `actor_deleted`

Fired from `UEngine::OnLevelActorDeleted(AActor*)`. Same shape as `actor_spawned` — captures pre-deletion state since the actor pointer is still valid at delegate time.

### `asset_added`

Fired from `IAssetRegistry::OnAssetAdded(const FAssetData&)`. Includes registry-loaded assets at startup (high-volume during initial scan), in-memory created assets, and post-import assets.

```json
{ "package_path": "/Game/Textures/T_Stone", "asset_path": "/Game/Textures/T_Stone.T_Stone", "name": "T_Stone", "class": "Texture2D", "class_path": "/Script/Engine.Texture2D" }
```

Note: the *initial registry scan* on editor startup will flood `asset_added` for every asset in the project. The 1000-entry ring will overflow during startup — that's expected and signaled via the `dropped` field in `poll_events` responses. Clients are expected to ignore the startup flood (poll once after startup with a recent `since_seq`, or use future per-event filtering to skip `asset_added` until later in the editor lifecycle).

---

## Handler: `poll_events`

### Params

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `since_seq` | int | No | `-1` | Return events with `seq >= since_seq` (**inclusive cursor**). `-1` means "from the oldest event currently buffered". On the first poll, callers typically pass `-1` to discover the current `next_seq`, then keep polling with the seq from the last response. Inclusive semantics: `next_seq` is the id about to be assigned (not yet pushed), so the next event will arrive at exactly that seq and an exclusive filter would silently drop it. |
| `max_count` | int | No | `100` | Cap returned events. Hard max `1000` (= ring size). |
| `event_filter` | array of string | No | `[]` (empty = all) | Substring match against `event` type names. Multiple filters are OR-combined. |

### Result

```json
{
  "ok": true,
  "next_seq": 4523,
  "first_seq_in_buffer": 3523,
  "returned": 12,
  "dropped": false,
  "events": [
    {"seq": 4511, "event": "actor_spawned", "ts": "...", "data": {...}},
    ...
  ]
}
```

- `next_seq` — the seq the next event would be assigned. Pass this back as `since_seq` on the next poll for "events since I last looked".
- `first_seq_in_buffer` — the smallest seq currently in the ring. If `since_seq < first_seq_in_buffer`, the response sets `dropped=true`.
- `returned` — count of events in the response (≤ `max_count`).
- `dropped` — true iff caller-supplied `since_seq` was older than the oldest buffered event. Tells the caller they missed events between polls.

### Errors

| Code | Trigger |
|---|---|
| `invalid_value_shape` | `event_filter` is not an array of strings, or `max_count` is non-positive / out of bounds. |

---

## Multi-PR roadmap

The full Tier 2 surface is too big for one PR. Decomposing into vertical slices:

| PR | Scope | Risk | Est. LoC |
|---|---|---|---|
| **#40 (this PR)** | `FUCMCPEventBus` + `poll_events` + 3 starter delegates (`actor_spawned`, `actor_deleted`, `asset_added`). End-to-end validation of the architecture. | LOW — mirrors LogCapture | ~350 |
| #41 | Additional delegate hooks: `asset_removed`, `asset_renamed`, `asset_post_import`, `level_loaded`, `level_saved`, `blueprint_compiled`, `mi_parameter_changed`. Same buffer; just more event sources. | LOW — additive | ~250 |
| #42 | `wait_for_events(timeout_ms, since_seq, event_filter)` long-poll handler — block until match or timeout. Reduces polling-cost / latency tradeoff. | MEDIUM — async on game thread | ~200 |
| #43 | Persistent subscription state (a session id; client subscribes once and gets server-side filter caching). Cleans up the wire weight when polling many events. | MEDIUM — adds session lifecycle | ~250 |
| Future | Other Tier 2 items: long-running task tracking (`start_task` / `poll_task` / `cancel_task`), persistent Python REPL. | Each its own bundle. | TBD |

**This PR (#40) targets only the tracer bullet.** Each subsequent PR can ship independently, gates on review of the previous, and stays within the user's "small focused PRs that self-merge cleanly" preference.

---

## UE 5.7 surface used (source-grounded)

Cited against engine headers in `F:/UE_5.7/Engine/Source/`:

| Symbol | Location | Notes |
|---|---|---|
| `UEngine::OnLevelActorAdded()` | `Runtime/Engine/Classes/Engine/Engine.h:2200` | Returns `FLevelActorAddedEvent&`. Declared at line 2199 as `DECLARE_EVENT_OneParam(UEngine, FLevelActorAddedEvent, AActor*)`. Game-thread only. |
| `UEngine::OnLevelActorDeleted()` | `Runtime/Engine/Classes/Engine/Engine.h:2207` | Returns `FLevelActorDeletedEvent&`. Declared at line 2206 as `DECLARE_EVENT_OneParam(UEngine, FLevelActorDeletedEvent, AActor*)`. Game-thread only. |
| `IAssetRegistry::OnAssetAdded()` | `Runtime/AssetRegistry/Public/AssetRegistry/IAssetRegistry.h:923` | Returns `FAssetAddedEvent&`. Declared at line 922 as `DECLARE_TS_MULTICAST_DELEGATE_OneParam(FAssetAddedEvent, const FAssetData&)`. **`TS_` prefix → thread-safe / fires from arbitrary threads**, including background registry scans. The event bus's locking discipline must accommodate this. |
| `FAssetData` | `Runtime/CoreUObject/Public/AssetRegistry/AssetData.h` | Standard registry record: `PackageName`, `PackagePath`, `AssetName`, `AssetClassPath`. |
| `AActor::GetActorLabel()` | `Runtime/Engine/Classes/GameFramework/Actor.h` | Editor-only Outliner display name. |
| `FCriticalSection` + `FScopeLock` | `Runtime/Core/Public/HAL/CriticalSection.h` | Same lock primitive as LogCapture. |

---

## Implementation shape (PR #40)

```
UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/
  EventBus.h         # ~70 LoC — singleton declaration + Push/Snapshot API + per-event Push helpers
  EventBus.cpp       # ~150 LoC — ring buffer impl + 3 delegate handler methods that build payloads
  Handlers/
    Handler_PollEvents.cpp   # ~110 LoC — validate args, snapshot, filter, build response

UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp
  # +30 LoC: subscribe in StartupModule, unsubscribe in ShutdownModule
  # +1 line: Reg.Register(Make_Handler_PollEvents())
```

Bridge / manifest / tests / docs follow the established vertical-slice pattern (count bumps 38 → 39, new schema test, new TOOLS.md section).

### Threading discipline

The bus's `Push()` method:
- Takes `Mutex` for the ring write only
- Builds the JSON payload **outside** the lock (string ops are the slow part — exact same discipline as LogCapture)
- Re-entrancy guard via `thread_local bool`: if a delegate handler somehow invokes another delegate (unlikely but possible — e.g. if logging inside a delegate triggers an event), drop the inner call rather than recursing

The handler's `Snapshot()` method:
- Takes `Mutex`, copies the matching slice into a flat array oldest-first, releases the lock
- Builds the JSON response from the snapshot — no engine state access during JSON encoding

### Subscription lifecycle

In `StartupModule`:
- Acquire delegate handles via `AddRaw(&FUCMCPEventBus::Get(), &FUCMCPEventBus::OnActorAdded)` etc.
- Store `FDelegateHandle` per subscription in a per-bus member array

In `ShutdownModule`:
- Iterate the handles and call `OnLevelActorAdded().Remove(Handle)` to detach safely before the module unloads

This mirrors how LogCapture's `AddOutputDevice` / `RemoveOutputDevice` registration is paired across the module lifecycle.

---

## Test plan

### Unit (pytest, no editor)

- `test_poll_events_in_tools_catalog` — schema shape (no required params; optional `since_seq` int / `max_count` int / `event_filter` array).
- Bump count assertions 38 → 39 in three places (`test_tools_list_has_thirtyeight_entries` → `_thirtynine_`, `test_handle_tools_list_returns_all_tools`, `test_manifest_tool_count_matches_bridge`).
- Add `poll_events` to the expected set in `test_tool_names_are_unique_and_match_handlers`.

### Smoke (live, manual — runbook step 5/6)

- Editor registers 39 handlers (was 38).
- After UE startup completes (registry scan flood is over), call `poll_events` with `since_seq: -1`. Returns events present in the buffer; `next_seq` reflects the count of events fired since editor start.
- Spawn a Cube via `spawn_actor`. Then `poll_events since_seq=<previous next_seq>`. Returns at least one `actor_spawned` event for the new actor.
- Delete the Cube via `delete_actor`. Then `poll_events`. Returns at least one `actor_deleted` event.
- Import a texture via `import_texture`. Then `poll_events with event_filter:["asset_added"]`. Returns at least one `asset_added` for the new texture.
- Test the drop-detection path: poll with a `since_seq` deliberately older than the oldest buffered event (e.g. `-2`). Response sets `dropped=true`.

---

## Risk

LOW for PR #40. The architecture is a direct mirror of an existing in-codebase pattern (LogCapture). The three delegates have well-known signatures cited against UE 5.7 source. The only nontrivial design decisions (poll-not-push, drop detection, threading discipline) are documented above with rationale.

The single live-only verification gap is the asset-registry startup flood — we expect it but haven't measured how big it gets in practice. If the 1000-entry ring overflows in <100ms during startup (plausible for big projects), the `dropped=true` detection is the safety net, not a correctness bug. PR #41 can revisit the ring size or add a "skip events during startup" mode if the data warrants.
