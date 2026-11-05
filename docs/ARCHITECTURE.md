# Architecture

## The picture

```mermaid
flowchart LR
    Client["MCP client<br/>(Claude Code, etc.)"]
    Bridge["bridge.py<br/>(Python, stdio)<br/>+ 37 synthetic tools"]

    subgraph UE["UE Editor process"]
        TCP["FTcpListener<br/>127.0.0.1:18888"]
        Disp["FUCMCPDispatcher<br/>JSON-RPC parse / route"]
        Reg["FUCMCPHandlerRegistry<br/>(71 C++ handlers)"]
        Native["UE native C++<br/>UnrealEd · UMG · UMGEditor<br/>PythonScriptPlugin · AssetRegistry"]
    end

    Client <-->|"MCP over stdio<br/>initialize · tools/list · tools/call"| Bridge
    Bridge <-->|"raw JSON-RPC 2.0<br/>over TCP"| TCP
    TCP --> Disp
    Disp --> Reg
    Reg --> Native
```

Sequence of one `tools/call` round trip:

```mermaid
sequenceDiagram
    autonumber
    participant C as Claude Code
    participant B as bridge.py
    participant S as UE TCP listener
    participant D as Dispatcher
    participant H as Handler
    C->>B: tools/call {name, arguments} (stdio)
    B->>S: {jsonrpc, id, method, params} (TCP)
    S->>D: drained on next FTSTicker tick (game thread)
    D->>H: Handle(Params, OutError)
    H->>H: call UE native C++ APIs
    H-->>D: TSharedPtr<FJsonObject> (or OutError)
    D-->>S: {jsonrpc, id, result|error}
    S-->>B: TCP response
    B-->>C: tools/call result wrapped as MCP content[]
```

## The four C++ files that matter

```
Source/UnrealAIConnection/
  Public/MCP/
    MCPServer.h          (TCP listener interface)
    MCPDispatcher.h      (one static function: HandleMessage)
    MCPHandler.h         (handler interface + registry)
  Private/
    UnrealAIConnectionModule.cpp   (registers all handlers + starts the server in StartupModule)
    MCP/
      MCPServer.cpp           (FTcpListener + per-tick recv/dispatch/send)
      MCPDispatcher.cpp       (parse JSON-RPC, look up handler, return JSON-RPC response)
      MCPHandler.cpp          (process-singleton FUCMCPHandlerRegistry)
      Handlers/
        Handler_*.cpp         (one per MCP method - pure leaves)
```

Each handler is a leaf with this shape:

```cpp
class FHandler_Foo : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("foo"); }

    virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override
    {
        // 1. Parse params
        // 2. Call UE native C++ APIs
        // 3. Return TSharedPtr<FJsonObject> on success
        //    OR set OutError and return nullptr on failure
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_Foo()
{
    return MakeShared<FHandler_Foo>();
}
```

Then in `UnrealAIConnectionModule.cpp::StartupModule`:

```cpp
extern TSharedRef<IUCMCPHandler> Make_Handler_Foo();
// ...
FUCMCPHandlerRegistry::Get().Register(Make_Handler_Foo());
```

## Shared modules (v0.3.0+)

Three helpers extract cross-cutting concerns from multiple handlers. Each lives in `Source/UnrealAIConnection/Private/MCP/` (alongside `MCPServer.cpp` etc., not under `Handlers/`):

- **`ActorIdentity`** — hybrid label-or-FName actor lookup. Used by `set_actor_transform`, `delete_actor`, `set_actor_property`, `add_component`. Returns `EResolveResult::Ambiguous` with the candidate FNames listed when a label matches multiple actors, so handlers can produce actionable `ambiguous_actor` errors.
- **`PropertyCoercion`** — JSON ↔ FProperty value bridge. Supports the v0.3.0 type list (primitives, strings, FName/FText, FVector/FVector2D/FRotator/FLinearColor/FColor, enums, TSoftObjectPtr). Used by `spawn_actor.properties`, `set_actor_property`, and `add_component.relative_transform`. Returns `ECoerceResult::Unsupported` with the FProperty class name for types deferred to v0.4.0 (USTRUCT, TArray, TMap, FObjectProperty, FInstancedStruct).
- **`LogCapture`** (v0.6.0) — `FUCMCPLogCapture : public FOutputDevice`, a thread-safe ring buffer that captures the last 1000 UE Output Log entries. Registered globally via `GLog->AddOutputDevice` in `StartupModule` (before handler registration, so early log lines are captured) and deregistered in `ShutdownModule`. `get_log_lines` reads a snapshot from this buffer. **Thread safety:** `FOutputDeviceRedirector` calls registered output devices from any thread; `FUCMCPLogCapture::Serialize` takes a `FCriticalSection` lock for each write. `GetLines()` copies the ring under the same lock, then releases before filtering — the lock is never held while iterating.

Each handler stays a leaf — these modules just lift cross-cutting concerns to where they can be tested once. `PropertyCoercion` and `ActorIdentity` (plus the server's port parsing and frame-length rules) have UE Automation tests under `Private/Tests/`, named `UCMCP.*`, using the reflected fixture types in `Private/Tests/UCMCPTestTypes.h`. Run them headless with `UnrealEditor-Cmd.exe "<project>.uproject" -nullrhi -unattended -ExecCmds="Automation RunTests UCMCP;Quit" -log` (host-build runbook step 7 in `docs/HANDOFF.md`). The "leaf with bounded responsibility" pattern still holds.

## Threading

`FTSTicker` callbacks run on the game thread. The TCP listener registers a per-tick callback that drains pending bytes, dispatches synchronously, and sends responses. Handlers therefore run on the game thread, where they can safely call any UE editor API.

Tradeoff: a slow handler will stall the editor's tick. This is acceptable for the current handler set — each call returns quickly. Genuinely long-running work (sleeps, multi-step pipelines) is handled by the task pattern (`start_sleep_task` / `poll_task` / `cancel_task` / `list_tasks`): the starter handler registers a task and returns immediately, and the work runs off the dispatch thread. Future handlers that block on disk I/O or the network should follow the same pattern instead of stalling the tick.

### Latency and the background-throttle

The socket drain runs on the core ticker at a 50 ms cadence (`AddTicker(..., 0.05f)` in `MCPServer.cpp`), so a warm round-trip is ~50 ms and a request backlog drains quickly (`TickClients` processes up to 32 frames per client per tick — a larger backlog clears over the next few ticks). **But the ticker is frame-bound** — it cannot fire faster than the editor's game thread ticks. A *backgrounded* editor is throttled by UE's "Use Less CPU when in Background" performance setting (default on) to a few FPS, which caps the drain to the frame rate and inflates every command to ~one frame (~300 ms) — the usual MCP case, where the user drives the editor from another window. To keep commands responsive, `StartupModule` disables `UEditorPerformanceSettings::bThrottleCPUWhenNotForeground` for the session (in-memory via `GetMutableDefault`; **not** `SaveConfig`'d, so the user's saved preference is untouched and a launch without this plugin is unchanged). Opt out with the env var `UCMCP_KEEP_BACKGROUND_THROTTLE=1`. Because handlers must run on the game thread (UE API safety), this — not a faster timer — is the lever that matters for unfocused-editor latency.

## Undo / transactions

Handlers that mutate level/actor state are made undoable at the dispatcher. `IUCMCPHandler::IsMutating()` (default `false`) marks such handlers; when true, `FUCMCPDispatcher::HandleMessage` wraps the `Handle()` call in an `FScopedTransaction`, so the whole operation collapses to a single editor undo step (Ctrl+Z). For the transaction to record a change, the handler must call `UObject::Modify()` on each object it edits **before** mutating it (and use editor-undo-aware paths — e.g. `UWorld::EditorDestroyActor` rather than the runtime `DestroyActor`). An empty transaction (handler bailed during validation before any `Modify()`) is discarded automatically, so marking a handler mutating is safe even on its error paths. The first wave covers the level/actor mutators (`spawn_actor`, `delete_actor`, `set_actor_transform`, `set_actor_property`, `add_component`); asset-content authoring (material instances, textures, widget blueprints) uses the package/save model rather than the level undo stack and is intentionally left un-transacted for now.

## Port and bind

The listener binds `127.0.0.1` only. The port comes from `-UCMCPPort=<n>` on the editor command line, else the `UCMCP_PORT` environment variable (the bridge reads the same variable), else `18888`. A value outside 1..65535 logs a warning and falls through to the next source (`FUCMCPServer::ResolveConfiguredPort`).

`FUCMCPServer::Start` creates, binds and listens on the socket itself, then hands it to `FTcpListener`'s `FSocket&` constructor. `FTcpListener`'s endpoint constructor would bind inside `FRunnable::Init()` on its own thread, where a failure can't be seen by the caller. So `Listening on 127.0.0.1:<port>` is only logged after a real bind; a failed bind logs `NOT listening ...` at Error and the server stays off. The socket is **not** reusable (no `SO_REUSEADDR`): on Windows that flag lets a second editor bind a port that is already listening, and UE's `SetReuseAddr` also sets `SO_REUSEPORT` on Linux/macOS. With it off, a second editor on the same port gets `SE_EADDRINUSE` and says so. To run several editors, give each its own `-UCMCPPort=`.

## JSON-RPC framing (v0.5.0+)

Every TCP message uses explicit length-prefixed framing. Each frame on the wire is:

```
+--------+--------+--------+--------+--------+--------+--------+--------+--------+--------+
|     8-byte big-endian uint64 body length     |       N bytes of UTF-8 JSON body         |
+--------+--------+--------+--------+--------+--------+--------+--------+--------+--------+
```

- The 8-byte prefix encodes the body byte count as an unsigned 64-bit integer in network (big-endian) byte order.
- The body is the raw UTF-8 JSON-RPC 2.0 object.
- Length of 0 is invalid. On the plugin side a request body over **64 MB** is invalid (`UCMCP::Framing::kMaxBodyBytes` in `MCPServer.h`). Both are rejected with `framing_error` and the client is dropped. (The bridge still accepts response frames up to 1 GB; its own cap is a separate change.)
- The plugin never allocates the declared length up front: the body buffer grows as bytes actually arrive, so a bare 8-byte prefix can't pin memory.
- At most **8 clients** are connected at once; a 9th connection is closed straight away by the listener thread (Warning `Refused client ...`). The bridge opens one short connection per call, so this only bites if something holds sockets open.
- A client with **no bytes moving in either direction for 300 s** (mid-frame or between frames) is dropped. Handlers run synchronously on the game thread and the client's clock is stamped after its handler returns, so a request that legitimately runs for minutes does not trip it.
- An HTTP request sent to the port by mistake (or by a browser) reads `GET / HT` as the length, about 5.1e18 bytes, and is dropped by the cap.

Both sides (`MCPServer.cpp` via `ReadFramedMessage` / `WriteFramedMessage`, and the bridge via `recv_framed` / `send_framed`) loop on `Recv` / `Send` until the exact byte count is transferred, eliminating the old "one `recv()` = one whole message" assumption.

**Stable error code: `framing_error`** — returned when the length prefix is unreadable, when the declared length exceeds the cap (64 MB for requests into the plugin, 1 GB for responses into the bridge), when the declared length is zero, or when body bytes stop arriving before the declared length is met (socket timeout or premature close).

Mixed-version connections (v0.4.0 bridge + v0.5.0 plugin, or vice versa) fail immediately with a framing error on the receiving side — loud, not silent. The bridge and plugin must be upgraded together.

## Why a Python bridge

Claude Code's MCP client speaks the **MCP protocol** (`initialize`, `tools/list`, `tools/call`) over stdio. The plugin's server speaks **raw JSON-RPC 2.0** over TCP, with custom method names like `execute_unreal_python`.

The bridge does two things:
1. Translates the MCP `initialize` / `tools/list` / `tools/call` envelope into raw method calls
2. Provides the static tool catalog with JSON Schema parameter descriptions (the catalog is duplicated from `Resources/mcp_manifest.json` because Claude Code expects it during `tools/list`)

If you don't use Claude Code, you don't need the bridge — connect to the TCP server directly.

## Build dependencies

`Source/UnrealAIConnection/UnrealAIConnection.Build.cs`:

```csharp
// PublicDependencyModuleNames
"Core",
"CoreUObject",
"Engine",
"UnrealEd",                       // UAssetImportTask, UTextureFactory, UFactory
"Slate",
"SlateCore",
"EditorScriptingUtilities",       // UEditorAssetLibrary in LoadLevel / SaveLoadedAsset / etc.
"EditorSubsystem",                // ULevelEditorSubsystem
"AssetRegistry",                  // GetProjectSummary asset count
"AssetTools",                     // IAssetTools::ImportAssetTasks
"Sockets", "Networking",          // TCP listener
"Json", "JsonUtilities",          // JSON-RPC framing
"PythonScriptPlugin",             // execute_unreal_python handler
"GraphEditor",                    // UEdGraph iteration in InspectBlueprint
"Kismet",                         // FBlueprintEditorUtils in EditWidgetTree
"EngineSettings",                 // UGeneralProjectSettings in GetProjectSummary
"UMG", "UMGEditor",               // widget classes + WidgetTree

// PrivateDependencyModuleNames
"InputCore",
"Projects",
"PropertyEditor",
"LevelEditor",
```

If a handler references a UE class whose owning module isn't in this list, the link step fails with `LNK2019: unresolved external symbol`. The fix is always to add the right module name. The UE source on disk is the ground truth — find the class declaration, look at the `_API` macro prefix, that names the module.

## Adding a new handler — full recipe

1. Decide the method name and the JSON shape of params + result.
2. Create `Source/UnrealAIConnection/Private/MCP/Handlers/Handler_NewThing.cpp`:
   ```cpp
   #include "MCP/MCPHandler.h"
   // ... include UE headers you need
   class FHandler_NewThing : public IUCMCPHandler { ... };
   TSharedRef<IUCMCPHandler> Make_Handler_NewThing() {
       return MakeShared<FHandler_NewThing>();
   }
   ```
3. In `UnrealAIConnectionModule.cpp`, near the other `extern` declarations:
   ```cpp
   extern TSharedRef<IUCMCPHandler> Make_Handler_NewThing();
   ```
4. In `StartupModule`, near the other `Reg.Register` calls:
   ```cpp
   Reg.Register(Make_Handler_NewThing());
   ```
5. If the handler needs a new UE module, add it to `Build.cs`.
6. Update `Resources/mcp_manifest.json` with the new tool's schema.
7. If the bridge is used, add the same tool to the `TOOLS` list in `bridge/unreal_ai_connection_bridge.py`.
8. Rebuild (`Build.bat UnrealEditor ...` or VS Build Solution).
9. Restart UE — the new tool registers automatically on module load.

## UE 5.7 API gotchas the original implementation hit (so you don't repeat them)

These are real bugs / surprises that cost hours to find. Documented here as a defensive scar collection:

- `FImageUtils::PNGCompressImageArray` takes `TArrayView64<const FColor>` source and `TArray64<uint8>` output. `CompressImageArray` is deprecated in 5.1 and writes a thumbnail-sized JPEG, not a PNG. (A reviewer subagent specifically told us the opposite — the source is the ground truth, not the model.)
- `EPythonCommandExecutionMode::ExecuteFile` accepts EITHER a file path OR literal source text — but its file-vs-source heuristic fails on multi-line scripts with comments. The handler always writes the source to a temp `.py` file under `Intermediate/UnrealAIConnectionPython/` and passes the file path. Bulletproof.
- `FPluginDescriptor::EnabledByDefault` is `EPluginEnabledByDefault` (enum: `Unspecified` / `Enabled` / `Disabled`), NOT a bool. Cast to a string before serializing.
- `OnClicked.AddDynamic(this, &Class::Method)` is a preprocessor macro that captures the function name as a string at the call site. Wrapping it in a C++ template breaks the capture and crashes at runtime. Each binding is its own inline call.
- `BlueprintEditorLibrary.reparent_blueprint` crashes UE for `EditorUtilityWidgetBlueprint`. Workaround: delete the asset and recreate with `EditorUtilityWidgetBlueprintFactory.parent_class = <CustomClass>` from inception.
- `edit_widget_tree`-style mutations require `WT->Modify()` + `WT->MarkPackageDirty()` + `FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(WBP)` + `SaveLoadedAsset(WBP)` to persist. Compile is a separate concern; don't compile per-edit (it crashes when many edits arrive in quick succession). Compile once at the end of a batch via the explicit `compile: true` flag on the last call.
- `TUniquePtr<T>` defaulted destructors require the type to be complete. If you `=default` the destructor of a class that owns a `TUniquePtr<FTcpListener>` in its header, MSVC errors with "incomplete type". Move the destructor implementation to the `.cpp` so the include for the held type lives there.
- `UTexture` mutations require the full `PreEditChange(nullptr)` + `Modify()` + set property + `PostEditChangeProperty(emptyEvent)` + (optional `UpdateResource()` for GPU rebuild) + `UEditorAssetLibrary::SaveLoadedAsset(...)` dance. Skipping `UpdateResource()` lets the in-editor preview keep showing the **old** texture even after the new settings are saved to disk; reopening the asset doesn't refresh it because the cached resource is still the pre-edit one. Reference: `Engine/Source/Runtime/Engine/Classes/Engine/Texture.h:1883`.
- `TextureCompressionSettings` enum names drift across UE versions. UE 5.7 source at `Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h` is the only authoritative list. The plan we shipped originally listed `TC_BC4`, `TC_BC5`, and `TEXTUREGROUP_Bake` as valid; none of those exist in UE 5.7. Always verify enum names against the version's source rather than copy-pasting from older docs.
- **UE 5.7 Python wrapper constructors do not always take args in property-name order.** Some take args in struct-memory order, which silently scrambles values if you assume the docstring property order matches positionally. Probed live on 2026-05-12 from the running editor:

  | Constructor | Positional order is... | Safe? |
  |---|---|---|
  | `unreal.Vector(1, 2, 3)` | `x=1, y=2, z=3` | ✓ matches property order |
  | `unreal.Vector2D(10, 20)` | `x=10, y=20` | ✓ matches property order |
  | `unreal.LinearColor(0.1, 0.2, 0.3, 0.4)` | `r=0.1, g=0.2, b=0.3, a=0.4` | ✓ matches property order |
  | `unreal.Quat(1, 2, 3, 4)` | `x=1, y=2, z=3, w=4` | ✓ matches property order |
  | `unreal.Rotator(1, 2, 3)` | **`roll=1, pitch=2, yaw=3`** | ✗ struct-memory order (Roll, Pitch, Yaw); fixed in PR #127 |
  | `unreal.Color(11, 22, 33, 44)` | **`b=11, g=22, r=33, a=44`** | ✗ BGRA struct-memory order (DirectX legacy); no current usage but the trap is real |

  **Rule:** for any `unreal.*` struct construction in bridge-emitted Python, use empty constructor + named property assignment instead of positional args:

  ```python
  _r = unreal.Rotator()
  _r.pitch = pitch_value
  _r.yaw = yaw_value
  _r.roll = roll_value
  ```

  Property assignment is invariant to UE's struct memory layout. Positional construction is fine only for `Vector`, `Vector2D`, `LinearColor`, `Quat` per the probe; treat any future struct as suspect until probed. A one-liner probe can validate any new struct in seconds:

  ```python
  s = unreal.SomeStruct(1, 2, 3)
  unreal.log(f"__PROBE__ unreal.SomeStruct(1,2,3) -> {s.field_a} {s.field_b} {s.field_c} __END__")
  ```

- **Never structurally mutate `ConnectedClients` (Add/Empty) while `FUCMCPServer::TickClients()` is iterating clients.** Two distinct hazards, fixed in two layered changes — a removal-only fix addresses neither:

  - **D1 — reentrant `Stop()` during mid-tick dispatch (PR #225, on `main`).** A handler dispatched inside the tick (e.g. `execute_unreal_python` → `quit_editor` → `FUCMCPServer::Stop()`) can tear the server down while `TickClients()` is still in its per-client loop. Fixed by iterating a snapshot — `TArray<FSocket*> ClientsThisTick = ConnectedClients;` — plus three `if (!bRunning) { return false; }` bail-outs: at the loop top, immediately after `FUCMCPDispatcher::HandleMessage`, and before the `Dropped` cleanup. The reentrant `Stop()` thus can't invalidate the iterator and can't touch sockets it already destroyed.
  - **D2 — cross-thread accept race (PR #226, layered on #225).** `FTcpListener` is an `FRunnable` and invokes `OnConnectionAccepted` on its OWN thread. That callback therefore only parks the new socket into a lock-guarded `PendingAccepted`, guarded by `FCriticalSection PendingClientsCS`, and never touches `ConnectedClients`/`ReadStates`/`WriteStates`. The game thread adopts pending sockets at the very TOP of `TickClients` — before #225's snapshot copy — so every `ConnectedClients`/`ReadStates`/`WriteStates` mutation is game-thread-only and #225's snapshot copy and ranged-for can never race the listener thread. `Stop()` drains `PendingAccepted` under the same lock.
  - **Removal was already correctly deferred** via the local `Dropped` array — the real `ConnectedClients.Remove` runs in a second loop after the ranged-for. There is NO `bTicking`/`bStopRequested` reentrancy guard: an earlier #226 draft used one, but it was dropped as redundant with #225's `bRunning` bail-outs.
  - **Failure modes these prevent:** any structural mutation of `ConnectedClients` (Add/Empty) during the live ranged-for trips UE's `TARRAY_RANGED_FOR_CHECKS` ensure "Array has changed during ranged-for iteration!" (`Engine/Source/Runtime/Core/Public/Containers/Array.h`, Development/Editor builds) → editor crash on shutdown. Separately, the cross-thread `TMap` mutation in D2 is heap-corruption-grade and independent of the iterator check.

## License

MIT. © 2026 HD Media (Kuwait).
