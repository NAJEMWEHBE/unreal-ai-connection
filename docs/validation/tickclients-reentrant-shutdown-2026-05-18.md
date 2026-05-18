# TickClients reentrant-shutdown crash — host-rebuilt + quit-path re-verified (2026-05-18)

## TL;DR

A handler dispatched from inside the `TickClients()` ranged-for (e.g.
`execute_unreal_python` running `quit_editor()`) reentrantly called
`FUCMCPServer::Stop()`, which emptied `ConnectedClients` **while the
ranged-for over it was still live** — UE's checked iterator aborted with
*"Array has changed during ranged-for iteration"* →
`StaticShutdownAfterError`. The fix iterates a snapshot of the client
list and bails out the moment the server is no longer running. The exact
crash path was **re-run against a freshly host-rebuilt UE 5.7 editor**
and it exited cleanly with **no assertion and no crash callstack**.
**Verdict: CRASH-FIXED = YES.** This run also validates the clean-exit
path end-to-end (`PackageRestoreData` empty, no dirty autosave).

## Root cause (verbatim call chain)

```
FUCMCPServer::TickClients()
  for (FSocket* Sock : ConnectedClients)          // L274 (pre-fix) ranged-for, live
    FUCMCPDispatcher::HandleMessage(Body)          // dispatches the request synchronously
      Handler_ExecutePython  ->  unreal.SystemLibrary.quit_editor()
        (or Handler_ExecuteConsoleCommand "Exit")
          ... reentrantly ...
          FUCMCPServer::Stop()
            ConnectedClients.Empty()               // mutates the array being iterated
            <sockets closed + destroyed>
  <control returns to the ranged-for>
  -> TCheckedPointerIterator detects the array changed
  -> "Array has changed during ranged-for iteration"
  -> StaticShutdownAfterError  (editor crash dump)
```

The handler runs **on the same thread, inside the loop body**, so the
reentrant `Stop()` mutates `ConnectedClients` before the ranged-for
finishes — the classic invalidated-iterator abort. The same path is also
how the editor is expected to shut down cleanly, so the crash masked the
clean-exit path.

## The fix (single file — `MCPServer.cpp`, `FUCMCPServer::TickClients()`)

`6ed22c3`, off `main` `4735be0`. Changes confined to `TickClients()`:

1. **Snapshot iteration.** `TArray<FSocket*> ClientsThisTick =
   ConnectedClients;` then `for (FSocket* Sock : ClientsThisTick)`. The
   ranged-for now walks a stable copy, so `Stop()`'s
   `ConnectedClients.Empty()` (and any `OnConnectionAccepted` `Add()`
   mid-tick) can no longer invalidate the iterator.
2. **Three `if (!bRunning) { return false; }` bail-outs** so the tick
   never touches sockets `Stop()` already destroyed:
   - **loop-top** (L282) — bail before processing the next snapshot entry;
   - **immediately after `FUCMCPDispatcher::HandleMessage`** (L336) — the
     dispatched handler may have torn the server down;
   - **before the `Dropped` cleanup loop** (L366) — `Stop()` already
     closed/destroyed all sockets and emptied the maps.

Normal (non-quit) request handling is unchanged: when no handler tears
the server down, `bRunning` stays true, the snapshot equals
`ConnectedClients`, and behaviour is identical to before. No header
change, no `Build.cs`/dependency change — one `.cpp`, leaf-only.

## The rebuild

Host plugin synced dev→host by `robocopy /MIR` (host plugin is a plain
copy); host `MCPServer.cpp` confirmed to contain the snapshot + 3 guards
before building. Cold rebuild:

- **`Result: Succeeded`** (`Total execution time: 19.12 seconds`).
- Toolchain: **`Using Visual Studio 2022 14.44.35227 toolchain
  (...VC\Tools\MSVC\14.44.35207...)`** — the pinned MSVC `14.44.35207`,
  which UE 5.7 builds cleanly with.
- The fixed translation unit was rebuilt and the plugin relinked:
  `[1/5] Compile [x64] MCPServer.cpp` → `[3/5] Link [x64]
  UnrealEditor-UnrealClaudeMCP.lib` → `[4/5] Link [x64]
  UnrealEditor-UnrealClaudeMCP.dll`.

## Reproduce result (the real verification — verbatim)

UE 5.7 relaunched against the host project; bound `127.0.0.1:18888`
(fresh editor session — log freshly written, not a Project-Browser
fallback). A framed JSON-RPC client (8-byte big-endian length prefix,
mirrored verbatim from `examples/smoke_test.py` /
`scripts/capture_demo_gif.py`) connected — so the socket was in
`ConnectedClients` and `TickClients` was iterating it — and sent a
single request that dispatches the reentrant teardown:

```
execute_unreal_python  {"code": "import unreal; unreal.SystemLibrary.quit_editor()"}
```

This is the precise scenario that previously aborted with *"Array has
changed during ranged-for iteration"* / `StaticShutdownAfterError`
inside `TickClients`.

**Handler response (the loop survived the reentrant dispatch and drained
the reply):**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": { "ok": true, "output": "None",
              "temp_script": ".../Intermediate/UnrealClaudeMCPPython/exec_*.py" }
}
```

**Process / log evidence:**

| Check | Result |
|------|--------|
| Editor process exited | **YES** — process gone within the poll window; **0** `UnrealEditor` processes remain |
| `Array has changed during ranged-for iteration` in log | **ABSENT** |
| `StaticShutdownAfterError` / `Assertion failed` / `LogWindows: Error` | **ABSENT** |
| Crash callstack mentioning `TickClients` / `FUCMCPServer` | **ABSENT** |
| New crash dir under `Saved\Crashes\` | **NONE** — count unchanged (43 → 43) across the run |
| Editor log tail | clean `LogExit` path |
| `Saved\Autosaves\PackageRestoreData.json` | `{ "RestoreEnabled": false, "Packages": [] }` — **`Packages` empty** |
| New dirty `Untitled*` autosave after launch | **NONE** (count = 0) |

Editor log tail (clean orderly shutdown — the relevant lines verbatim):

```
LogExit: Preparing to exit.
LogExit: Editor shut down
LogExit: Transaction tracking system shut down
LogUCMCP: Stopped
LogUnrealClaudeMCP: [UnrealClaudeMCP] Module shutdown
LogExit: Exiting.
Log file closed, 05/18/26 22:46:58
```

No `=== Critical error ===` block, no assertion, no `StaticShutdownAfterError`,
no ranged-for abort anywhere in the session log. `LogUCMCP: Stopped`
shows `FUCMCPServer::Stop()` ran to completion via the normal shutdown
path rather than crashing the iterator.

## Honest verdict

**CRASH-FIXED = YES.** The exact pre-fix crash path
(`execute_unreal_python` → `quit_editor()` reentrantly `Stop()`-ing the
server while `TickClients` iterates `ConnectedClients`) was re-run
against a freshly host-rebuilt UE 5.7 editor. The editor exited
**without** the `Array has changed during ranged-for iteration` /
`StaticShutdownAfterError` assertion and **without** any `TickClients` /
`FUCMCPServer` crash callstack; no new crash dump was written. The
snapshot iteration plus the three `bRunning` bail-outs hold under the
reentrant-teardown condition. The same run also validates the clean-exit
fix end-to-end: `PackageRestoreData` `Packages` is empty and no dirty
`Untitled*` autosave was created, i.e. the editor shut down cleanly. No
header or dependency change; the normal request path is unchanged.
