# Handoff document

Single source of truth for resuming work on UnrealClaudeMCP in a fresh Claude Code session. Read this first; it captures everything carried in the prior session's working memory.

---

## Project at a glance

**What this is:** An Unreal Engine 5.7 plugin + Python bridge that exposes editor automation to MCP clients (Claude Code, etc.) over a localhost TCP socket. The plugin adds a JSON-RPC server inside the editor; each "handler" is one MCP tool (~150 LoC of C++ in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`). The bridge translates between Claude Code's stdio MCP protocol and the plugin's TCP wire format.

**Where it stands:** v0.12.0 — **52 tools total** (49 UE-side handlers + 3 bridge-side synthetic tools). Tier 1 (ergonomic wins) and **Tier 2 (the autonomy multipliers — editor event push, task tracking, persistent Python REPL)** are fully shipped. The language-shim experiment proposed in PR #37 has run; the conclusions are codified in `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md` and as new directive #7 below.

**What's NOT in main yet:** nothing in flight. All Tier 2 PRs (#40-#45) and the language-shim experiment (PR #46) are merged. Live verification on the host machine is still pending — see "Verification status" below.

---

## Open work + pending verification

**Open PRs:** none. As of end of 2026-05-09 session, every PR opened in this session has merged (PR #38 through PR #46 — 9 PRs, ~5500 LoC total).

**Latest commit on main:** `e8d8bfb` (merge of PR #46).

**This session's PRs (chronological, all 2026-05-09):**

Tier 1 closeout:
- PR #39 — `get_console_variable` + `set_console_variable` (paired CVar handlers; closes Tier 1 fully). Codex P2 caught precision loss in `%g` truncation; fixed via `%.17g` + integer-detection.

Tier 2 entrypoint (the autonomy multiplier — UE → Claude callbacks):
- PR #40 — **`FUCMCPEventBus` ring buffer + `poll_events` handler + 3 starter delegates** (`actor_spawned`, `actor_deleted`, `asset_added`). Architecture: type-agnostic ring (mirrors `LogCapture`), `FCriticalSection` + `thread_local` re-entrancy guard, monotonic int64 seq with drop detection. Codex P1 caught off-by-one cursor (was exclusive `<=`, must be inclusive `<` to match the documented "pass `next_seq` back" contract); Gemini caught early-return-with-garbage-metadata when `MaxCount<=0`. Both addressed; design simpler than the C++/transport alternatives ruled out in the spec.
- PR #41 — 5 more delegate subscriptions (`asset_removed`, `asset_renamed`, `asset_post_import`, `level_post_save`, `map_changed`). Pure additive; **8 event types total**. Gemini caught missing `class_path` in two payloads + literal bit-shift in `MapChange` flags; both fixed.
- PR #42 — **`wait_for_events` long-poll**, redesigned as a **bridge-side synthetic tool** after Codex's P1 finding that a UE-side blocking handler would freeze the game thread (which is also where most editor delegates fire — making the wait useless for game-thread events). Set the precedent for `SYNTHETIC_TOOLS` in the bridge.
- PR #43 — Server-side subscriptions (`register_subscription` / `unsubscribe` / `poll_subscription`). Both bots independently caught the same cursor-advance bug (filter-rejected events were re-scanned on every poll); fixed.
- PR #44 — **`FUCMCPTaskRegistry` framework + `start_sleep_task` tracer + `poll_task` + `cancel_task`**. Cooperative-cancellation discipline (no safe forced thread termination in UE 5.7). Both bots independently caught cast-before-clamp on `duration_ms`; fixed.
- PR #45 — **Persistent Python REPL** (`exec_python_persistent` + `reset_python_state`). Implementation collapsed to a one-line change: UE's `FPythonCommandEx` already has `FileExecutionScope::Public` for shared globals. Both bots caught missing temp-file pattern in `reset_python_state`; fixed.

Language-shim experiment + workflow strategy:
- PR #46 — **2 C++ handlers (`find_console_variables`, `inspect_static_mesh`) + 2 bridge-side synthetic shims (`get_camera_transform`, `set_camera_transform`)**. Comparison codified in `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md` addendum. Findings: shims win for write-only setters wrapping Python-reachable APIs (~3× shorter LoC); C++ wins for struct access and registry iteration; getter shims pay a "marker-pattern tax". Codex P1 + 4 Gemini findings addressed in the fix commit; one Gemini finding dismissed with rationale (consistency with all other inspect/compile/etc. handlers using `UEditorAssetLibrary::LoadAsset`).

**Verification status:** live verification on the host machine is **still pending**. The runbook below is unchanged in shape from the prior session, but the assertions need bumping (now 49 UE handlers register at startup, not 36; smoke test could exercise the new event/task/REPL surface). When you next have access to the host machine, start there.

**Verification runbook** (6 steps, PowerShell, run on the user's host machine):

1. `cd C:\Users\NINOH\Desktop\UnrealClaudeMCP && git pull origin main`
2. `taskkill /IM UnrealEditor.exe /F` (Live Coding holds the DLL otherwise; safe if UE isn't running). Or, with the module: `Import-Module .\scripts\UnrealClaudeMCP-Editor.psm1; Stop-UCMCPEditor`.
3. **Sync dev plugin → host plugin.** The host project's `Plugins/UnrealClaudeMCP/` may be a plain copy on this machine, in which case it drifts from the dev tree silently. Verify with `Get-Item "<host-project>\Plugins\UnrealClaudeMCP" | Select-Object LinkType` — a `Junction` or `SymbolicLink` value means it auto-tracks; empty means it's a plain copy and you must sync. To sync (always quote both paths — Windows project locations like `C:\Users\<you>\Documents\Unreal Projects\…` contain spaces):
   ```
   robocopy "<repo>\UnrealClaudeMCP" "<host-project>\Plugins\UnrealClaudeMCP" /MIR /XD Binaries Intermediate .vs /NFL /NDL /NJH /NJS /NP
   ```
   Robocopy exit codes 0–7 mean success. The `/XD Binaries Intermediate` exclusion preserves the host's UBT cache so step 4 stays incremental.
4. `& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" <HostProjectName>Editor Win64 Development -project="<full path to host .uproject>"` — must end with `Result: Succeeded`. The target is `<HostProjectName>Editor`, NOT `<PluginName>Editor`. For the canonical `UnrealClaudeMCPTest` host project, that's `UnrealClaudeMCPTestEditor`.
5. Open the host `.uproject` in UE editor; confirm **49 UE handlers register** in the Output Log. Filter by `LogUCMCPHandler` and you should see exactly 49 lines `Registered handler '<name>'`. The TCP server then binds `127.0.0.1:18888` (~10s on warm DDC, 1–5 min cold). With the module: `$proc = Start-UCMCPEditor -ProjectPath "<full path>"; $ready = Wait-UCMCPReady; $check = Test-UCMCPHandlers -LogPath "<host-project>\Saved\Logs\<HostProjectName>.log" -ExpectedCount 49`.
6. **Smoke** — `py -3 examples\smoke_test.py --material-instance /Game/SmokeTest_MI --sequence /Game/SmokeTest_LS`. Note: smoke_test was last updated for 36 handlers; it still works against the registered set but doesn't exercise the new event/task/REPL/camera surface. Adding sections for those is a reasonable post-verification follow-up.

**New live-verification scenarios to spot-check (Tier 2 + experiment surface):**
- `poll_events {}` once after editor startup → returns startup-flood events; subsequent polls with `next_seq` show only newly-fired
- `spawn_actor Cube` → `poll_events` returns `actor_spawned` with the cube's payload
- `register_subscription { event_filter: ["actor_spawned"] }` → `spawn_actor` → `poll_subscription` returns the spawn; cursor advances; subsequent poll returns nothing new
- `start_sleep_task { duration_ms: 5000 }` → returns task_id; `poll_task` shows running; after 5s shows completed with `slept_ms: 5000`. `cancel_task` mid-sleep → 50ms later poll shows cancelled
- `exec_python_persistent { code: "x = 5" }` then `exec_python_persistent { code: "unreal.log(f'__X__{x}__END__')" }` → second call sees `x` from first
- `find_console_variables { prefix: "r.Lumen." }` → returns Lumen CVars
- `inspect_static_mesh { path: "/Engine/BasicShapes/Cube" }` → returns LOD 0 stats + bounds
- `get_camera_transform {}` → location + rotation; `set_camera_transform { location: { x:0, y:0, z:1000 }, rotation: { pitch:-90 } }` → camera snaps top-down (rotation.yaw/roll preserved per Codex P1 fix); follow-up `get_camera_transform` confirms

---

## Operating directives the user has granted

These are explicit user instructions that override default Claude behavior. They have stayed in force across the entire prior session. **Directives #1-#6 carried over from the prior HANDOFF; #7-#8 were added during the 2026-05-09 continuation session.**

1. **"Do everything"** — autonomous execution. Don't ask permission to proceed; pick a reasonable path and ship it. The user steps in only when they want to redirect.
2. **"Don't get hallucinated"** — every UE 5.7 API claim must be grounded in actual source (`F:/UE_5.7/Engine/Source/...` or `F:/UE_5.7/Engine/Plugins/...`). Cite line numbers in spec/commit messages. Past sessions caught real defects (`TC_BC4`, `TEXTUREGROUP_Bake`, `FStringOutputDevice`) by grounding before committing.
3. **"Use the right tool for the job"** — Python or C++ as fits. Don't dogmatically prefer one. The bridge is Python; the plugin is C++; bespoke per-asset operations route through `execute_unreal_python` rather than getting their own handler. **Refined by directive #7** for the synthetic-tool category.
4. **"After every PR, check codex and gemini comments, then merge yourself"** — both bots review automatically. Standard workflow: open PR → wait 1–3 minutes → triage findings → apply fixes as new commits → wait again for re-review → `gh pr merge <N> --merge`. **Refined by directive #7** — for mechanical PRs you can ship optimistically and read reviews post-merge.
5. **"Make them all"** — when the user authorizes a multi-bundle plan, push through all of them rather than splitting the commitment.
6. **"Close UE editor after every test unit"** — never leave UE editor running across test cycles or builds. UE's Live Coding holds the plugin DLL lock and blocks UBT (`Unable to build while Live Coding is active`). With the module: `Stop-UCMCPEditor` after every `Test-UCMCPHandlers` / smoke run. The `Start → Wait → Test → Stop` pattern is the canonical test cycle.
7. **(NEW 2026-05-09)** **"Ship optimistically for mechanical PRs; wait for bots on architectural ones."** Bot review wait is the largest dead-time bottleneck in this workflow (~5-10 min per PR × many PRs = significant wall-clock cost). For PRs that follow an established pattern (new handler mirroring a prior one, additive event source, count bumps), self-merge as soon as CI is green + `mergeStateStatus` is CLEAN, then read post-merge bot reviews and address findings in follow-up PRs. **Exception:** for PRs that introduce a new pattern, touch the dispatcher / threading model, change the wire protocol, or do anything architecturally novel, wait for bot eyes once before merging — Codex's P1 on PR #42 (`wait_for_events` redesign) is the canonical example of a finding that would have been very expensive to address post-merge.
   - **Pre-empt the bug classes the bots have already taught us:** cast-before-clamp on numeric inputs, off-by-one cursor semantics with documented "pass next_seq back" contract, marker-pattern fragility in shims, missing temp-file pattern for `ExecuteFile` mode, partial-update destruction on setters with optional fields, missing input validation (NaN/Infinity, fractional integers). Pre-empting these during integration captures ~80% of what bots catch.
8. **(NEW 2026-05-09)** **"Work with Codex as a co-developer, not just a reviewer."** The user installed a Codex plugin in Claude Code so the bridge can drive Codex from this environment. Goal: speed (parallelism), not quality (already strong). When picking a multi-part task: **partition explicitly upfront** — name what Codex does and what Claude does in plain terms, before either starts. Three parallelism patterns (ranked by payoff):
   - **Sub-PR concurrency**: Codex implements C++ handler, Claude implements bridge/manifest/tests/docs in parallel; converge on one branch.
   - **Pipeline concurrency**: the moment PR N is pushed, start PR N+1 on a fresh branch (filling the bot-review wait window with productive work). Mind branch conflicts on common files (module.cpp, bridge.py, tests/test_bridge.py, manifest.json, TOOLS.md).
   - **Fix-while-write**: Codex addresses bot findings on PR N while Claude is implementing PR N+1; saves the context-switch cost. Requires Codex to know the established patterns.
   See `~/.claude/projects/<project>/memory/codex-collaboration-model.md` for the full pattern. **Verify the Codex tooling is reachable on session start** (`ToolSearch query="codex"` or `Bash codex --help`); if not, ask the user how to invoke it before guessing.

---

## Established conventions (hard-won, do not relitigate)

### Error format

Every handler's `OutError` follows: `<tool>: <error_code>: <human-readable detail>`.

The `<error_code>` portion is a stable parseable token clients can branch on. Established codes (reusable across handlers): `missing_required_field`, `missing_params`, `asset_not_found`, `invalid_path`, `invalid_asset_name`, `dest_exists`, `create_failed`, `save_failed`, `actor_not_found`, `ambiguous_actor`, `not_a_sequence`, `not_a_material`, `not_a_material_instance`, `not_a_blueprint`, `not_a_static_mesh`, `parameter_not_applied`, `has_referencers`, `delete_failed`, `rename_failed`, `unknown_enum_value`, `invalid_value_shape`, `invalid_value_type`, `invalid_tag_value`, `cvar_not_found`, `read_only`, `python_unavailable`, `write_failed`, `reset_failed`, `compile_failed`, `command_execution_failed`, `subscription_not_found`, `task_not_found`.

### UE 5.7 traps already mapped

These are the bugs that bit prior sessions. Don't re-discover them.

| Trap | What to do |
|---|---|
| `FOutputDevice` subclasses default to `CanBeUsedOnAnyThread() = false`, which routes log dispatch through GLog's serializing queue and stalls the game thread under load | Always override to `return true`. See `LogCapture.h`. |
| `FOutputDevice::Serialize` has both 3-arg and 4-arg variants; UE 5.7's pure virtual is 3-arg | Implement the 3-arg signature. |
| `ELogVerbosity::Type` packs flag bits (`SetColor`, `BreakOnLog`) in the upper byte | Mask with `ELogVerbosity::VerbosityMask` (= `0xf`) before switching. |
| `FPackageName::GetAssetPackageExtension()` returns `.uasset` only — wrong for `UWorld` levels (`.umap`) | Use `FPackageName::DoesPackageExist(PackagePath, &OutFilename)` which auto-resolves. |
| `UEditorAssetLibrary::DeleteAsset` is documented as a force-delete; no built-in referencer check | Run `IAssetRegistry::GetReferencers` first. |
| `UMaterialEditingLibrary::SetMaterialInstance*ParameterValue`'s bool return is unreliable across UE versions | Combine pre-verify (`Get<Type>ParameterNames`) + post-verify (scan `MIC->{Scalar,Vector,Texture}ParameterValues` array). Ignore the bool. |
| `GEngine->Exec` returns false on unrecognized commands | Capture and propagate as `command_execution_failed`. |
| `UEditorAssetLibrary::SaveAsset` returns false on SCC checkout failure or read-only file | Capture and propagate as `save_failed` with explicit "created in memory but not persisted" wording. |
| Non-blocking sockets return `BytesRead == 0` for "no data right now," NOT for "disconnect" | Disambiguate via `ISocketSubsystem::Get()->GetLastErrorCode() == SE_EWOULDBLOCK`. See v0.9.1's `MCPServer.cpp`. |
| `Helper.AddDefaultValue_Invalid_NeedsRehash` for TSet/TMap leaves the container in invalid state on early return | Always `EmptyElements()` + `Rehash()` on error paths. |
| `EmptyAndAddUninitializedValues` for TArray leaves slots uninitialized on mid-loop early return → UB | Pre-initialize every slot via `Inner->InitializeValue` before the coercion loop. |
| `UMaterialInstanceConstantFactoryNew::InitialParent` is declared as a bare `UPROPERTY()` without `EditAnywhere`/`BlueprintReadWrite`, so it is **not** reachable via Python's `set_editor_property`. | Skip the factory's `InitialParent`; create the MI without a parent, then set `UMaterialInstance::Parent` (`MaterialInstance.h:647`) post-creation. See `scripts/seed_test_project.py`. |
| `FPythonCommandEx::ExecuteFile` mode does not capture script stdout / eval-result back through `CommandResult`; `EvaluateStatement` mode captures only the last expression's value. | For Python-script results that need to round-trip back to the bridge, emit a marker via `unreal.log("__MARKER__<json>__END__")` and retrieve through `get_log_lines{category_filter:"LogPython"}`. Use a per-call UUID in the marker to disambiguate from stale entries. |
| **(NEW 2026-05-09)** `FPythonCommandEx::ExecuteFile` mode tries to resolve `Cmd.Command` as a file path FIRST. Multi-line literal Python source can be misclassified as a path → `ExecPythonCommandEx` returns false silently. | All `ExecuteFile`-mode handlers MUST write the source to a real temp `.py` file (under `Intermediate/UnrealClaudeMCPPython/`) via `FFileHelper::SaveStringToFile` + `ON_SCOPE_EXIT` deletion, then pass the file path. See `Handler_ExecutePython.cpp`, `Handler_RunPythonFile.cpp`, `Handler_ApplyPythonToSelection.cpp`, `Handler_ExecPythonPersistent.cpp`, `Handler_ResetPythonState.cpp` — they all follow this pattern. PR #45 shipped a violation that both bots caught; the canonical pattern was applied in the fix. |
| **(NEW 2026-05-09)** `static_cast<int32>(double)` for values > `INT_MAX` is **undefined behavior** — could overflow to negative, wrap, or worse. Subsequent `FMath::Min` against the (garbage) int32 produces silently wrong results. | Always **clamp on the wide type FIRST, narrow LAST**: `static_cast<int32>(FMath::Min(Raw, static_cast<double>(kMax)))`. Caught by both Codex + Gemini independently on PRs #44 and #45 — same family of bug as PR #39's `%g`-truncates-precision and PR #43's cursor-doesn't-advance-past-inspected-events. |
| **(NEW 2026-05-09)** **`FPlatformProcess::Sleep` on the game thread freezes the editor.** UE's MCP dispatcher runs on the game thread (`MCPServer.cpp:205-208`, `:323`). A blocking handler stalls every game-thread system (UI, ticker, viewport). It also stalls **the very delegates that fire the events you'd be waiting for** (`OnLevelActorAdded`, `OnLevelActorDeleted`, `OnAssetPostImport`, `MapChange`, `PostSaveWorldWithContext` are all game-thread). | Don't write blocking handlers in C++ for editor-event waits. The right home for "wait for X" logic is **bridge-side synthetic tools** (`SYNTHETIC_TOOLS` dict in `bridge/unreal_claude_mcp_bridge.py`) — Python sleeps in a separate process, UE's game thread keeps running, events fire normally. PR #42's `wait_for_events` is the worked example after Codex's P1 redesigned it from a broken C++ handler. |
| **(NEW 2026-05-09)** **Off-by-one cursor on poll-with-pass-next-seq-back contracts.** If your handler documents "pass `next_seq` back as `since_seq` for the next poll" AND the filter is exclusive (`>`), the very next event (whose seq exactly equals the previous `next_seq`) is silently skipped on every poll — deterministic event loss with `dropped=false`. | Use **inclusive** cursor semantics: filter `seq < since_seq` to skip (return `seq >= since_seq`). Drop detection: `since_seq < first_seq_in_buffer`. See `EventBus.cpp::Snapshot`. |
| **(NEW 2026-05-09)** **`set_*` handlers with optional fields default-to-zero is destructive.** If a setter has optional `location`/`rotation`/etc. fields and defaults missing fields to `0`, callers supplying only one side silently snap the other to origin/identity. | Either reject partial-update calls explicitly, OR read the current state first and preserve omitted sides. PR #46's `set_camera_transform` does the latter (extra round-trip cost on partial updates, full updates skip the read). |
| **(NEW 2026-05-09)** **`UEditorAssetLibrary::LoadAsset` is the established pattern across all inspect/compile/move/rename/delete handlers.** Even if `LoadObject<UObject>` would technically avoid the `EditorScriptingUtilities` dependency, that dep is already declared in `Build.cs` for many other handlers — switching one handler creates a precedent inconsistency. | Follow the established pattern. Per directive #4, when source-grounded reasoning supports your judgment, your opinion overrides bot suggestions. PR #46 dismissed Gemini's `LoadObject` suggestion for `Handler_InspectStaticMesh` on this basis. |

### Vertical-slice task decomposition

When implementing a bundle, each task is one self-contained vertical slice that ends with a green commit:
1. Create `Handler_<Name>.cpp` + register in `UnrealClaudeMCPModule.cpp`
2. Add bridge `TOOLS` entry in `bridge/unreal_claude_mcp_bridge.py` (or a `SYNTHETIC_TOOLS` entry if it's bridge-side)
3. Add manifest entry in `UnrealClaudeMCP/Resources/mcp_manifest.json`
4. Add bridge schema test in `tests/test_bridge.py`
5. Bump count assertion (twice — `test_tools_list_has_*` and `test_handle_tools_list_returns_all_tools`) and `test_manifest_sync.py`. The parametrized `test_every_tool_routes_through_tools_call` automatically picks up new UE handlers; for synthetic tools it auto-skips.
6. Add `## <name>` section in `docs/TOOLS.md`
7. Run `py -3 -m pytest tests/ -q` — must be green
8. Commit

### Manifest sync trap

`test_manifest_sync.py` substring-searches for the word "required" in manifest param descriptions. Phrase optional fields without "required" appearing.

### Spec → plan → implementation flow

Every bundle follows this sequence:
1. Verify UE 5.7 APIs against source headers
2. Consider 2-3 approaches; pick one with rationale
3. Write spec to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
4. Spec self-review (placeholders / consistency / scope / ambiguity)
5. (For larger bundles) Write plan to `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`
6. Implement task by task, green pytest after each
7. Push, open PR, address codex + Gemini findings (or ship optimistically per directive #7)

---

## Repository file map

```
UnrealClaudeMCP/                               UE plugin (drops into <Project>/Plugins/)
  Source/UnrealClaudeMCP/
    Public/MCP/MCPServer.h                     TCP server header (per-client state structs)
    Public/UnrealClaudeMCPModule.h             Module class -- now retains FDelegateHandle members
                                               for the 8 event-bus subscriptions (PR #40 + PR #41)
    Private/MCP/
      MCPServer.cpp                            TCP server impl (state-machine framing as of v0.9.1).
                                               Game-thread FTSTicker dispatch -- see new trap entry
                                               about blocking-on-game-thread.
      MCPDispatcher.cpp                        Method dispatcher
      MCPHandler.h                             IUCMCPHandler interface + registry
      LogCapture.{h,cpp}                       FOutputDevice ring buffer for get_log_lines (1000 entries)
      EventBus.{h,cpp}                         (NEW PR #40+#43) FUCMCPEventBus -- ring buffer of editor
                                               events + server-side subscription registry. Mirrors
                                               LogCapture's discipline (FCriticalSection + thread_local
                                               re-entrancy guard). Subscriptions stored in TMap<FGuid, ...>;
                                               cursor advances past INSPECTED events (not just delivered)
                                               since filters are immutable.
      TaskRegistry.{h,cpp}                     (NEW PR #44) FUCMCPTaskRegistry -- registry of long-running
                                               background tasks. State machine: pending->running->
                                               (completed|cancelled|failed). Cooperative cancellation via
                                               TSharedPtr<TAtomic<bool>> shared between registry + worker.
                                               No TTL (deferred).
      PropertyCoercion.{h,cpp}                 JSON ↔ FProperty coercion (v0.4.0 advanced types)
      ActorIdentity.{h,cpp}                    Hybrid label-or-FName actor lookup
      Handlers/                                One file per handler, ~150 LoC each
        AssetPathUtil.h                        Shared path normalization helpers (v0.7.0)
        Handler_*.cpp                          49 UE-side handlers (Tier 1: 36 baseline + 5 v0.10.0
                                               ergonomics; Tier 2: poll_events / register_subscription /
                                               unsubscribe / poll_subscription / start_sleep_task /
                                               poll_task / cancel_task / exec_python_persistent /
                                               reset_python_state; Experiment: find_console_variables /
                                               inspect_static_mesh).
                                               NOTE: wait_for_events / get_camera_transform /
                                               set_camera_transform are SYNTHETIC (bridge-side) -- they
                                               do NOT have a Handler_*.cpp file.
    UnrealClaudeMCP.Build.cs                   Module deps (added MaterialEditor v0.9.0; LevelSequence +
                                               MovieScene + MovieSceneTracks + LevelSequenceEditor v0.8.0).
                                               EventBus + TaskRegistry use only Core / CoreUObject /
                                               JsonUtilities -- no new deps for Tier 2.
  Resources/mcp_manifest.json                  Tool catalog (mirrors bridge TOOLS, 52 entries)
  UnrealClaudeMCP.uplugin                      Plugin manifest

bridge/
  unreal_claude_mcp_bridge.py                  stdio↔TCP bridge. NEW since prior HANDOFF:
                                               - SYNTHETIC_TOOLS dict (PR #42+#46): bridge-side
                                                 implementations of tools that compose UE handlers.
                                                 Currently: wait_for_events, get_camera_transform,
                                                 set_camera_transform.
                                               - synthetic_* functions (one per synthetic tool).
                                               - Marker pattern for round-tripping results from
                                                 execute_unreal_python (UUID per call + log search).
                                               - import math (PR #46 -- math.isfinite() validation)
                                               - import time, uuid (PR #42, PR #46)

examples/
  smoke_test.py                                Live integration smoke test. Last updated for 36-tool
                                               assertion; doesn't yet exercise Tier 2 / experiment
                                               surface. Reasonable post-verification follow-up.
  .mcp.json.example                            Template Claude Code MCP config
  hello_run_python_file.py                     Test fixture for run_python_file (PR #31)

scripts/                                       Orchestration scripts (introduced 2026-05-09 prior session)
  UnrealClaudeMCP-Editor.psm1                  PowerShell module for editor lifecycle
                                               (Start/Stop/Wait/Test functions; PR #25)
  seed_test_project.py                         Idempotent seeder for /Game/SmokeTest_*
                                               throwaway assets (PR #26)

.mcp.json (gitignored)                         Local Claude Code MCP config; points at
                                               bridge/unreal_claude_mcp_bridge.py.

tests/
  test_bridge.py                               Bridge MCP protocol + schema tests. NEW since prior:
                                               - Schema test per Tier 2 + experiment handler
                                               - Behavioral tests for synthetic_wait_for_events
                                                 with mocked call_ue (the polling loop, deadline,
                                                 dropped short-circuit, integer validation, error
                                                 propagation)
                                               - test_*_is_synthetic checks for SYNTHETIC_TOOLS
                                                 registration
  test_bridge_edge_cases.py                    Parametrized test_every_tool_routes_through_tools_call
                                               EXCLUDES synthetic tools from the round-trip assertion
                                               (they intentionally don't 1:1 forward). 143 tests pass.
  test_manifest_sync.py                        Drift detection between bridge TOOLS and manifest
                                               (count assertion: 52)

docs/
  TOOLS.md                                     Per-tool params/results/examples (52 sections)
  ARCHITECTURE.md                              How pieces fit; UE 5.7 API gotchas
  INSTALLATION.md                              Step-by-step install
  HANDOFF.md                                   This file
  LANGUAGE-CHOICE-RETROSPECTIVE.md             Per-tool language verdict + 5-step decision flow.
                                               PR #46 added the language-shim experiment addendum
                                               with quantitative + qualitative comparison and a
                                               6th step in the decision flow for synthetic shims.
  superpowers/specs/                           Design specs per bundle, dated.
                                               NEW: 2026-05-09-cvar-handlers-design.md (PR #39),
                                                    2026-05-09-tier2-event-push-design.md (PR #40+#41+#42+#43).
  superpowers/plans/                           Implementation plans per bundle, dated.
```

---

## Deferred work (queued for future bundles)

These are real items either explicitly deferred or obvious follow-ups. **None are committed to** — the user picks the priority.

### Tier 2 follow-ups (now possible because the framework is shipped)

- **Subscription TTL** — PR #43 ships subscriptions without inactivity TTL. If orphan accumulation becomes observable, add an `FTSTicker`-driven cleanup that expires subs after N minutes of no `poll_subscription`.
- **Task TTL** — same shape: PR #44 ships tasks without TTL; completed/cancelled/failed tasks accumulate. Add cleanup after N minutes terminal.
- **`list_tasks(status_filter, limit)`** — readily implemented on top of `FUCMCPTaskRegistry`. Useful for "what's running?" workflows.
- **More event types** — `blueprint_compiled` (no global UE delegate; needs per-BP subscription bookkeeping), `mi_parameter_changed` (no UE delegate; needs `Handler_SetMIParameter` to push into the bus on success), more granular asset-registry events.
- **More task types** — the framework is generic; concrete starts: `start_cook_task(target, platform)`, `start_render_sequence_task(sequence_path, output_path)`, `start_lightmap_bake_task(quality)`. All need cooperative cancellation polling at sub-second cadence.
- **Async dispatcher refactor** (large) — would let C++ handlers be truly long-running without blocking the game thread. Currently the bridge-side synthetic-tool pattern is the workaround for the "wait for X" use case. If a real workflow demands a C++ handler that takes >5s, this refactor becomes worth doing.

### Sequencer follow-ups (deferred from prior HANDOFF; still relevant)

- **Keyframe authoring** — `set_transform_keyframe(sequence_path, binding_guid, frame, location, rotation)`. ~600-800 LoC. UE channel APIs are heavily templated.
- **Spawnable bindings** — `bind_spawnable_to_sequence` with template object.
- **Movie Render Queue** — `render_sequence_to_disk(sequence_path, output_path, preset_name)` via `UMoviePipelineQueueEngineSubsystem::RenderQueueWithExecutor`. Pairs naturally with the new task-tracking framework.

### Material follow-ups

- **Material-graph editing** — let the LLM author new materials from `UMaterialExpression` nodes. UE has 200+ subclasses. Decompose into 3-4 sub-bundles.
- **Static-switch parameter mutation** — `SetMaterialInstanceStaticSwitchParameterValue` triggers shader recompiles; out of v0.9.0 scope.
- **Material function instances + layer/blend parameters** — `EMaterialParameterAssociation::LayerParameter` / `BlendParameter`.

### Asset operations

- **Bulk operations** — `bulk_delete_assets`, `bulk_move_assets`. Partial-success error handling is non-trivial.
- **`duplicate_asset(path, dest)`** — clean wrap of `UEditorAssetLibrary::DuplicateAsset`. Well-defined; good Codex-first-task candidate.
- **Project-wide refactoring** — `rename_class_references(old, new)` cascades across BPs / Widget BPs / Materials.

### Net-new ideas worth considering

- **`screenshot_actor(actor_name)`** — frame the viewport on an actor and capture a focused thumbnail. High leverage for asset-pipeline doc generation.
- **Multi-editor coordination** — one Claude session driving multiple UE editors at once (different ports). Useful for cross-project asset migrations.
- **Asset diff tool** — `diff_asset(path_a, path_b)` returns a structural delta.
- **Niagara / Animation / Landscape openers** — `inspect_niagara_system`, `inspect_anim_blueprint`, `inspect_landscape`.
- **Build farm integration** — `cook_project(target)` / `package_project(platform, config)`. Long-running, pairs with `FUCMCPTaskRegistry`.
- **Testing framework hooks** — `run_automation_tests(filter)` triggers a subset, returns pass/fail.

---

## Autonomy roadmap

Surfaces beyond the current 52 tools that would meaningfully expand "Claude → Unreal" autonomy.

**Tier 1 (ergonomic wins):** ✅ FULLY SHIPPED
- All 5 originally-proposed Tier 1 handlers landed across PRs #31-#39.

**Tier 2 (autonomy multipliers):** ✅ FULLY SHIPPED
- ✅ Editor event push — `FUCMCPEventBus` + `poll_events` + 8 event types + `wait_for_events` + subscriptions (PRs #40-#43)
- ✅ Long-running task tracking — `FUCMCPTaskRegistry` + `start_sleep_task` / `poll_task` / `cancel_task` (PR #44)
- ✅ Persistent Python REPL — `exec_python_persistent` + `reset_python_state` via `FileExecutionScope::Public` (PR #45)

**Tier 3 (coverage expansion — none started):**
- ⏳ Asset diff tool, multi-editor coordination, Niagara/Animation/Landscape openers, workspace state save/restore, watch_log, build farm integration via task tracking, automation test hooks, screenshot_actor, duplicate_asset, bulk asset operations
- ⏳ Sequencer keyframe authoring, Movie Render Queue (via task tracking)
- ⏳ Material graph editing (multi-PR)

**The natural NEXT move** (per the autonomy ladder): **a multi-PR Tier 3 sprint using the Codex collaboration model from directive #8.** This is the first time we have a co-developer; pick a target with several similar-shaped handlers (e.g. the openers — `inspect_niagara_system` / `inspect_anim_blueprint` / `inspect_landscape`) so the parallelism advantage is obvious from the first PR. Realistic estimate per the speed analysis: prior Tier 2's 6 PRs took ~3 hours sequentially; the same surface with pipeline parallelism could be ~1.5 hours.

---

## How to resume in a fresh session

1. Open a new Claude Code session in the same repo.
2. Send: *"Read `docs/HANDOFF.md` and continue from there. The user is in autonomy mode — pick the next reasonable thing to do."*
3. **Verify Codex tooling** (per directive #8): `ToolSearch query="codex"` and/or `Bash codex --help`. If reachable, the new collaboration model is live; if not, fall back to solo work and ask the user how to invoke the plugin.
4. The fresh session reads this doc, absorbs the directives, sees 52 tools shipped, and proceeds.

For specific resumption:
- *"Live-verify everything that landed in 2026-05-09's continuation"* → run the runbook at the top of this doc with the bumped 49-handler assertion + spot-check the new event/task/REPL/camera surface
- *"Start Tier 3 with a co-developer task"* → pick `duplicate_asset` or `screenshot_actor` as the first delegated piece
- *"Continue from a specific deferred item"* → pick from the deferred-work section

---

## Closing notes from prior sessions

**Session 2026-05-09 (initial):** Built and merged the v0.10.0 ergonomics bundle (PRs #25-#37). Encoded merge-authority directive (#4); shipped LANGUAGE-CHOICE-RETROSPECTIVE.md. Prior HANDOFF version captured this state.

**Session 2026-05-09 (continuation — Tier 2 sprint + language-shim experiment):**
- 8 PRs merged in ~3 hours: #39 (Tier 1 closeout), #40-#45 (Tier 2 — event push, more events, wait_for_events, subscriptions, task tracking, persistent REPL), #46 (language-shim experiment).
- **52 tools total** (49 UE-side handlers + 3 bridge-side synthetic tools). Handler count went from 36 → 49; total tool count 36 → 52.
- 8 real bot-caught bugs across the session: precision-loss `%g`, off-by-one inclusive cursor, fractional `max_count` corrupting cursor, blocking-on-game-thread (forced wait_for_events redesign), filter-rejected events re-scanned forever, payload-class-path inconsistency, cast-before-clamp UB, missing temp-file pattern in reset_python_state, partial-update destruction in set_camera_transform, marker-search log window too small. **All addressed; pre-emption discipline updated in directive #7.**
- Two new subsystems that future PRs reuse: `FUCMCPEventBus` (event ring + subscription registry) and `FUCMCPTaskRegistry` (long-running background work). Both are type-agnostic — adding new event sources or new task types is additive in the module file.
- New synthetic-tool pattern (`SYNTHETIC_TOOLS` dict in `bridge/unreal_claude_mcp_bridge.py`) — bridge-side compositions of UE handlers, used for `wait_for_events` / `get_camera_transform` / `set_camera_transform`. The decision flow in `LANGUAGE-CHOICE-RETROSPECTIVE.md` now has a 6th step routing the right cases to this pattern.
- Two new directives (#7 ship-mechanical-fast, #8 Codex co-developer) reflect the user's speed orientation. The Codex plugin was installed during this session but wasn't yet visible from the running Claude Code; verify on session start.

**The user's working style** (carried over): fast merge cycles. Direct preferences. Doesn't fault deferrals if explicit. Values honesty about what's verified vs just-shipped. Recently became deeply speed-oriented: the 8-PR sprint felt productive but the bot-review wait was the largest dead-time. Directive #7 (optimistic merge for mechanical PRs) and #8 (Codex parallelism) are both responses to that observation.

**What worked this session:** vertical slices. Source-grounded UE 5.7 API verification. Bot reviews on every PR (8 real bugs caught). Pre-empting known bug classes during integration (the trap table now has 6 new entries from this session's findings). Honest dismissal of one bot suggestion with rationale (LoadObject vs LoadAsset on PR #46). Redesign-don't-patch when the architectural critique is right (PR #42 wait_for_events moved from C++ to bridge).

**What to watch:** my own propensity to repeat the same bug class across PRs (cast-before-clamp + missing temp-file pattern were both flagged twice in different PRs before I internalized the discipline). The trap table is the long-term mitigation. **Live verification is still pending** — host machine has not exercised the Tier 2 surface yet. Build-correctness risk is real for new C++ subsystems (EventBus, TaskRegistry); spec-level grounding helps but only live build proves it.
