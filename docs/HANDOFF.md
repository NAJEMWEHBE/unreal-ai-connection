# Handoff document

Single source of truth for resuming work on Unreal AI Connection in a fresh session of any MCP-compliant client. Read this first; it captures everything carried in the prior session's working memory.

> Earlier closing notes (1st through 32nd, sessions 2026-05-09 through 2026-05-17) are archived to [`docs/HANDOFF-archive.md`](HANDOFF-archive.md). This active file keeps the latest three consecutive notes (33rd-35th) for quick pickup.

---

## Project at a glance

**What this is:** An Unreal Engine 5.7 plugin + Python bridge that exposes editor automation to **any MCP-compliant client** (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, …) over a localhost TCP socket. The plugin adds a JSON-RPC server inside the editor; each "handler" is one MCP tool (~150 LoC of C++ in `Source/UnrealAIConnection/Private/MCP/Handlers/`). The bridge translates between the client's stdio MCP protocol and the plugin's TCP wire format. **Vendor-neutral by design** — the wire protocol is open MCP (created by Anthropic, but any conforming client works); the repo, plugin folder, and module are all named "Unreal AI Connection".

**Where it stands (post-PR #218 — #216 README/HANDOFF honesty, #217 Phase H remaining clusters + `.uplugin` per-bucket generator, #218 ~17-handler UE 5.1 port; all merged):** **105 tools total** (72 UE-side C++ handlers + 33 bridge-side synthetic). Plugin version `0.9.1`. pytest baseline: **498** passing. **UE 5.1 (T2 bucket) is now compile- AND runtime-host-verified** — `RunUAT BuildPlugin` vs `F:\UE_5.1` (5.1.1) returned `ExitCode=0`, and a live-editor smoke proved `LogUCMCP: Listening on 127.0.0.1:18888` + all handlers registered + a real `get_viewport_screenshot` PNG (1014×582). UE 5.7 (T1) verified prior. UE 4.27 (T3) is untested — build-from-source at own risk, PR-welcome. *(**UE 5.7 is the officially supported & tested version; see `## Project scope` / ADR-0001.** Other UE versions are open / best-effort / community — the cross-engine scaffold is kept available, uncertified, not actively maintained; the 5.1/T2 host-verification is a useful data point for non-5.7 builds.)* **Host-build prerequisite (load-bearing):** UE ≤5.3 will NOT compile with VS2026/MSVC 14.51 or VS2022 14.44 (engine's own `__has_feature` header is a fatal C4668 under installed-build `-WarningsAsErrors`); host now has **MSVC 14.34.31933** — builds must pin it (UBT `BuildConfiguration.xml` `<WindowsPlatform><CompilerVersion>14.34.31933</CompilerVersion>`; `dist/build-tools/build51.sh` does this + restores the global config). All non-source artifacts live under gitignored `dist/` per the standing no-external-folders rule. (Current HEAD: `git log -1 origin/main`; latest milestone PR #218.)

Recent waves that landed in the current session lineage:
- **Wave A (PR #161)** — 6 quick-win tools: `get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `bulk_inspect_assets`
- **Wave A.5 (PR #162)** — 2 new tools: `pie_control`, `inspect_project_setting`
- **PR #164** — Wave A + A.5 bot-findings cleanup
- **PR #165** — codified standing rules #4 (delegation-by-default) and #5 (bot-review gate)
- **PR #166** — HANDOFF.md split into active + archive (~36K tokens / session-start saved)
- **Wave B (PR #167)** — 4 asset-hygiene synthetics: `find_unused_assets`, `get_reference_chain`, `bulk_compile_blueprints`, `audit_blueprint_compile_status` (88 → 92)
- **Wave C (PR #168)** — 4 actor-batch synthetics: `find_actors_by_class`, `bulk_focus_actors`, `bulk_screenshot_actors`, `bulk_set_actor_property` (92 → 96)
- **Wave D (PR #169)** — 4 utility synthetics: `compare_assets`, `bulk_set_console_variables`, `inspect_dependency_graph`, `bulk_fix_redirectors` (96 → 100 — **TARGET HIT**)

**What's NOT in main yet:** PR #226 — the TCP-server D2 cross-thread hardening (`FCriticalSection PendingClientsCS` + `PendingAccepted`), layered on top of merged #225's D1 fix. CI green; host UE 5.7 recompile + live D1+D2 re-verify DONE clean; CodeRabbit re-reviewed; awaiting maintainer merge (Claude never merges). Standing rules locked. Tool count is at the user's explicit 100-target.

---

## Open work + pending verification

**Open PRs:** none. (PR #226 — TCP-server D2 cross-thread hardening, layered on #225's D1 — merged 2026-05-18 as `95222bf`; both fixes now on `main`.)

**Latest milestone on main:** PR #225 (`cc5181b`) — TCP-server **D1** reentrant-`Stop()` fix (`TickClients()` iterates a `ClientsThisTick = ConnectedClients` snapshot + three `if(!bRunning){return false;}` bail-outs); host-verified at #225's own merge. Earlier on main: #220 (Wave A host UE 5.7 compile+runtime verification), #218 ~17-handler UE 5.1 port, #215 Phase H asset-registry compat shims. For the current HEAD commit hash, run `git log -1 origin/main`.

**TCP-server reentrant-shutdown crash — FIXED in two layered changes (D1 + D2):**

- **D1 (PR #225, merged `cc5181b`):** reentrant `Stop()` arriving during dispatch — `TickClients()` iterates a `ClientsThisTick = ConnectedClients` snapshot plus three `if(!bRunning){return false;}` bail-outs. #225's mechanism, host-verified at #225's own merge, kept verbatim.
- **D2 (PR #226, this branch):** `OnConnectionAccepted` runs on `FTcpListener`'s OWN listener thread (verified vs `F:\UE_5.7` source — `FTcpListener` is an `FRunnable`), so it raced `ConnectedClients`/`ReadStates`/`WriteStates`. Fix: `FCriticalSection PendingClientsCS` + `TArray<FSocket*> PendingAccepted`; the listener thread parks the accepted socket under `PendingClientsCS`; `TickClients` adopts pending sockets at the top BEFORE #225's snapshot so all `ConnectedClients`/`ReadStates`/`WriteStates` mutation is game-thread-only; `Stop()` drains `PendingAccepted` under the lock. #226's earlier `bTicking`/`bStopRequested` guard was removed as redundant with #225's bail-outs.
- **Status:** `origin/main` merged (HEAD `9c17bfb`); host UE 5.7 recompile DONE — `Result: Succeeded` (0 err / 0 warn, DLL linked, VS2022 14.44); live D2 re-verify DONE clean (smoke ×2, ~60+ connect/disconnect cycles, all crash signatures absent). PR #226 reframed, CI green (pytest 5/5), CodeRabbit re-reviewed, awaiting maintainer merge (Claude never merges).

**Host verification — DONE (UE 5.7 T1, 2026-05-17):**

The Wave A + Wave A.5 C++ handlers (`get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `pie_control`, `inspect_project_setting`) plus `render_camera_to_png` (PR #214) are now **host-COMPILE-AND-RUNTIME-verified on UE 5.7 (T1)**. The 2026-05-17 session ran the full host cold rebuild on the canonical host project: `Build.bat HDMediaVirtualStudioEditor Win64 Development` → `Result: Succeeded` (~46s, MSVC 14.44.35207); UE 5.7.4 launched and bound `127.0.0.1:18888`; **72** `Registered handler` lines in the Output Log (all 8 new C++ method names registered verbatim, zero handler warnings); `examples\smoke_test.py` exited 0 (all 11 sections green); `examples\verify_wave_a.py` exited 0 with **zero JSON-RPC `-32601`** on any of the 8 C++ methods — the inert-handler regression is CLEARED. `render_camera_to_png` wrote a 2,059,202-byte PNG, closing the 29th-note headless-capture root cause. Notes:
- MCP `tools/list` shows all 105 entries; the catalog reports 72 C++ handlers.
- `bulk_inspect_assets` is a bridge-side synthetic — reachable through the bridge, not on the raw plugin socket (unreachable there BY DESIGN per `MCPDispatcher.cpp` — confirmed not a regression).

**Live verification panel (re-runnable via `examples\verify_wave_a.py`; results host-PROVEN 2026-05-17):**

- `list_tools` count → 72 C++ handlers registered (was 64 pre-Wave-A; 71 pre-#214)
- `get_engine_version {}` → expect structured fields (`major`, `minor`, `patch`, `changelist`, `branch`, `minor_dotted`)
- `list_levels { path_under: "/Game", name_contains: "Map" }` → expect filtered UWorld asset registry result
- `save_dirty_assets {}` → expect `{ok: true, saved_count: <int>}`
- `get_selected_actors {}` → with one actor selected in the editor, expect per-actor name/label/class/transform
- `inspect_input_mappings {}` → expect action+axis mappings + `uses_enhanced_input` flag
- `pie_control { action: "query" }` → expect `{is_playing: false}` from idle editor
- `pie_control { action: "start", mode: "play" }` → PIE launches; subsequent `pie_control { action: "stop" }` ends it cleanly
- `inspect_project_setting { class_path: "/Script/Engine.RendererSettings" }` → expect bulk dump of editable UPROPERTYs
- `bulk_inspect_assets { paths: ["/Engine/BasicShapes/Cube", "/Engine/EngineMaterials/BaseFlattenMaterial"] }` → expect per-path inspect results

**Verification runbook** (6 steps, PowerShell, run on the user's host machine):

1. `cd <repo-root> && git pull origin main`  (`<repo-root>` = your local clone of `unreal-ai-connection`)
2. `taskkill /IM UnrealEditor.exe /F` (Live Coding holds the DLL otherwise; safe if UE isn't running). Or, with the module: `Import-Module .\scripts\UnrealAIConnection-Editor.psm1; Stop-UCMCPEditor`.
3. **Sync dev plugin → host plugin.** The host project's `Plugins/UnrealAIConnection/` may be a plain copy on this machine, in which case it drifts from the dev tree silently. Verify with `Get-Item "<host-project>\Plugins\UnrealAIConnection" | Select-Object LinkType` — a `Junction` or `SymbolicLink` value means it auto-tracks; empty means it's a plain copy and you must sync. To sync (always quote both paths — Windows project locations like `F:\ai\ax plug in\…` contain spaces):
   ```
   robocopy "<repo>\UnrealAIConnection" "<host-project>\Plugins\UnrealAIConnection" /MIR /XD Binaries Intermediate .vs /NFL /NDL /NJH /NJS /NP
   ```
   Robocopy exit codes 0–7 mean success. The `/XD Binaries Intermediate` exclusion preserves the host's UBT cache so step 4 stays incremental.
4. `& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" <HostProjectName>Editor Win64 Development -project="<full path to host .uproject>"` — must end with `Result: Succeeded`. The target is `<HostProjectName>Editor`, NOT `<PluginName>Editor`. For the canonical host project, that's `HDMediaVirtualStudioEditor`.
5. Open the host `.uproject` in UE editor (use the path-quoting recipe in CLAUDE.md — pre-quote inside the `-ArgumentList` array element). Confirm **72 UE C++ handlers register** in the Output Log. Filter by `LogUCMCPHandler` and you should see exactly 72 lines `Registered handler '<name>'`. The 33 bridge-side synthetic tools never reach the UE process and so never appear in the Output Log; they're served by `SYNTHETIC_TOOLS` in `bridge/unreal_ai_connection_bridge.py`. Total tools visible to MCP clients: 105. The TCP server then binds `127.0.0.1:18888` (~10s on warm DDC, 1–5 min cold). With the module: `$proc = Start-UCMCPEditor -ProjectPath "<full path>"; $ready = Wait-UCMCPReady; $check = Test-UCMCPHandlers -LogPath "<host-project>\Saved\Logs\<HostProjectName>.log" -ExpectedCount 72`.
6. **Smoke** — `py -3 examples\smoke_test.py --material-instance /Game/SmokeTest_MI --sequence /Game/SmokeTest_LS`. Then run the Wave A/A.5 live verification panel above.

**Pause/restart note (PR #174 scorecard follow-up #10):** Long verification runs that span an editor restart (manual or crash) lose every actor and edit that wasn't saved to the level. If you spawn validation actors or mutate the open map and intend to pause, run `save_dirty_assets {}` (or Ctrl+S in the editor) *before* the pause — unsaved actors and properties revert to the last on-disk state on relaunch. The PR #174 scorecard's `delete_actor` row hit this exact case (ValEnvPanelL/R spawned pre-pause, lost on restart, then returned `actor_not_found` on the post-resume delete — correct shape, not a tool defect, but easy to mistake for one).

---

## Operating directives the user has granted

These are explicit user instructions that override default Claude behavior. They have stayed in force across the entire prior session lineage.

1. **"Do everything"** — autonomous execution. Don't ask permission to proceed; pick a reasonable path and ship it. The user steps in only when they want to redirect.
2. **"Don't get hallucinated"** — every UE 5.7 API claim must be grounded in actual source (`F:/UE_5.7/Engine/Source/...` or `F:/UE_5.7/Engine/Plugins/...`). Cite line numbers in spec/commit messages. Past sessions caught real defects (`TC_BC4`, `TEXTUREGROUP_Bake`, `FStringOutputDevice`) by grounding before committing.
3. **"Use the right tool for the job"** — Python or C++ as fits. Don't dogmatically prefer one. The bridge is Python; the plugin is C++; bespoke per-asset operations route through `execute_unreal_python` rather than getting their own handler. **Refined by directive #7** for the synthetic-tool category.
4. **"After every PR, check codex and gemini comments, then merge yourself"** — both bots review automatically. Standard workflow: open PR → wait 1–3 minutes → triage findings → apply fixes as new commits → wait again for re-review → `gh pr merge <N> --merge`. **Refined by directive #7 + standing rule #5** — for mechanical PRs you can ship optimistically and read reviews post-merge.
5. **"Make them all"** — when the user authorizes a multi-bundle plan, push through all of them rather than splitting the commitment.
6. **"Close UE editor after every test unit"** — never leave UE editor running across test cycles or builds. UE's Live Coding holds the plugin DLL lock and blocks UBT (`Unable to build while Live Coding is active`). With the module: `Stop-UCMCPEditor` after every `Test-UCMCPHandlers` / smoke run. The `Start → Wait → Test → Stop` pattern is the canonical test cycle. **Now codified as standing rule #3.**
7. **"Ship optimistically for mechanical PRs; wait for bots on architectural ones."** Bot review wait is the largest dead-time bottleneck in this workflow (~5-10 min per PR × many PRs = significant wall-clock cost). For PRs that follow an established pattern, self-merge as soon as CI is green + `mergeStateStatus` is CLEAN, then read post-merge bot reviews and address findings in follow-up PRs. **Exception:** for PRs that introduce a new pattern, touch the dispatcher / threading model, change the wire protocol, or do anything architecturally novel, wait for bot eyes once before merging. **Reconciled with standing rule #5's mechanical-fix follow-up exception.**
8. **"Work with Codex as a co-developer, not just a reviewer."** When picking a multi-part task: **partition explicitly upfront** — name what Codex does and what Claude does in plain terms, before either starts. Three parallelism patterns (ranked by payoff): sub-PR concurrency, pipeline concurrency, fix-while-write. See `~/.claude/projects/<project>/memory/codex-collaboration-model.md` for the full pattern.
9. **"Multi-agent fleet, not just Codex+Claude."** Codex stays for C++ specialty; Sonnet code-explorer runs *one PR ahead* researching UE 5.7 APIs; Sonnet code-reviewer can pre-review staged Python work; Opus does the FINAL synthesis review of Codex's C++ + Python wiring read together as one coherent change before commit. **Critical:** the `general-purpose` Sonnet subagent's `Edit`/`Write` calls do NOT persist to the host working tree (sandbox isolation) — never delegate Python coding to it; Opus does Python directly when not delegated to Codex.
10. **"Vendor-neutral MCP — supports all clients, not just Claude Code."** The protocol is open MCP; Codex CLI, Cursor, Gemini CLI, Continue, etc. all work without code changes. Tool descriptions, manifest entries, and docs MUST use vendor-neutral language ("the LLM client", "the AI agent", or just describe what the tool does).
11. **"Opus does the review."** When the user says "review", that's Opus reviewing the AGGREGATE — Codex's C++ + Sonnet's contributions + the explorer brief — together as one coherent PR, against UE 5.7 source, sibling patterns, and the bot-finding catalog. Opus may also code (especially small fixes, or when Codex is unavailable). **Verify cross-language coherence:** every field declared in the manifest's `returns` block must be emitted by the C++ in the matching shape, and field NAMES must imply consistent SHAPES across sibling handlers.

---

## Standing rules (load-bearing across all sessions; do not relax without explicit user request)

These rules outlive any single session. Closing notes record the chronology of each rule's adoption; this section is the operative reference for resumption. Five rules, in order of adoption.

1. **Multi-agent ensemble review on every substantive change.** The maintainer has provisioned NVIDIA cloud reasoning, local OSS LLM tooling, Copilot CLI, CodeRabbit, the Gemini CI bot, and chatgpt-codex-connector specifically so Opus does not work solo. Use them. Pattern: dispatch 2-4 reviewers in parallel during ~30s waiting windows; integrate findings into the final diff before push. **Pre-COMMIT cadence preferred over post-PR-push** — Wave A (PR #161) retroactive review caught a real BLOCKER but added the cost of a fix-up commit; Wave A.5 (PR #162) pre-commit review caught comparable findings with zero rework. Per-provider configuration lives in the maintainer's private memory file (`feedback_multi_agent_workflow.md`).

2. **UE 5.7 editor launch is pre-authorized in every session.** The maintainer granted standing permission on 2026-05-12 morning and reiterated it on 2026-05-13 after the autopilot skipped live verification. Do not "skip live verification" as a shortcut; do not ask permission each session; do not wait for the next session. When live-reachable handlers add signal (canonical verification panel after a bridge-touching PR cycle, anything that exercises `127.0.0.1:18888`, smoke-test suite, Rotator round-trip lossless proofs, inspect_* synthetic logical-error envelope checks), **launch the editor immediately** using the path-quoting recipe at the top of this doc and in `CLAUDE.md`. PowerShell tool only — `Start-Process` is a PowerShell cmdlet, not a Bash command. UE typically binds in ~2 minutes; if CPU stays at ~7% one core and `Saved/Logs/HDMediaVirtualStudio.log` is stale, re-check the path-quoting.

3. **UE editor must be closed when verification work finishes.** UE 5.7 in Editor mode reserves ~4 GB RAM and pins multiple CPU threads; leaving it open between verification windows wastes resources the maintainer wants reclaimed. Cadence is "open, verify, close" — not "open and leave running for the session". Recipe: `Get-Process UnrealEditor -ErrorAction SilentlyContinue | Stop-Process -Force; Get-Process UnrealTraceServer -ErrorAction SilentlyContinue | Stop-Process -Force`. Re-launch via rule #2's recipe when the next live verification call is needed. The 2-minute warm-up is the cost; the cost of leaving it running idle for an hour is higher. The pairing of #2 and #3 is load-bearing — rule #2 alone could be read as "always have UE running"; #3 keeps the resource footprint bounded.

4. **(NEW 2026-05-13)** **Delegation-by-default (token conservation).** Every concrete work step is delegated to a sub-agent. The main Opus thread plays leader / integrator / decision-maker only — receives summaries, makes calls, ships. Concrete routing: file search / grep / repo exploration → Sonnet code-explorer or Explore agent; code review of in-flight diffs → local OSS LLM runtime (see private workflow config — coding-focused reasoning model + fast small instruction model in parallel) and GitHub PR bots (rule #5); both free, zero Claude tokens. Claude sub-agents (Sonnet code-reviewer, codex-rescue) reserved for true escalation when local capability is insufficient or for diff classes the GitHub bots historically miss; C++ implementation → Codex CLI (per multi-agent partitioning); UE 5.7 API verification → codex-rescue or NVIDIA cloud reasoning agent; multi-file mechanical edits → general-purpose agent or `caveman:cavecrew-builder` (≤2 files); bot-review readout → delegated agent reads + summarises; memory file writes → delegated agent. Main thread NEVER does work a sub-agent can do; reserves itself for orchestration, integration of sub-agent outputs, and final decisions. Reason: the maintainer's Claude session token budget is the constraining resource; sub-agent runs do not bill against it. Sub-agents are the fleet; Opus is the captain.

5. **(NEW 2026-05-13)** **Bot-review gate before any merge.** Never blind-merge a PR. After CI green + before `gh pr merge`, read every bot review on the PR. Current roster (2026-05-13): Gemini auto-review, CodeRabbit, chatgpt-codex-connector (Codex GitHub bot), greptile-apps, GitHub Copilot CLI. Any future bot the maintainer wires up joins the same gate. For each bot finding: **Apply** as a follow-up commit on the same branch (preferred for small fixes), OR **Dismiss** with explicit reason posted as a PR comment (e.g. "false positive: Build.cs:19 already has the dep" — verifiable claim). Bots regularly surface real defects the pre-commit ensemble missed. Worked examples: PR #161's P0 `UInputSettings::GetActionMappings(NAME_None, ...)` non-existent overload (caught by chatgpt-codex-connector post-merge); PR #162's vendor-neutral manifest regression (caught by CodeRabbit); PR #164's three P2 follow-ups on error-code reuse / cached-state inconsistency / cross-handler `class` shape drift (caught by greptile-apps + chatgpt-codex-connector). Reason: blind-merging discards this safety net; rule #5 makes the readout step mandatory. **Mechanical-fix follow-up exception (reconciles with directive #7):** when a follow-up commit on the same branch applies bot findings as direct surgical fixes (no new logic — e.g. add quote-around-identifier, split error-code, cache a state read, restore a field name for parity), self-merge is permitted without waiting for a second-pass bot review since the bots' first pass already directed the fix. New-logic commits still require a fresh bot pass before merge.

---

## Project scope

**UE 5.7 is the officially supported & tested version (ADR-0001).** Any other UE version is **open / best-effort / community** — the cross-engine compat scaffold is kept available (build-from-source, your choice, your risk, uncertified, not actively maintained, PR-welcome). Not frozen-dead, not dropped; reversible/expandable; newer-UE upgrades welcome on request.

---

## Established conventions (hard-won, do not relitigate)

### Error format

Every handler's `OutError` follows: `<tool>: <error_code>: <human-readable detail>`.

The `<error_code>` portion is a stable parseable token clients can branch on. Established codes (reusable across handlers): `missing_required_field`, `missing_params`, `asset_not_found`, `invalid_path`, `invalid_asset_name`, `dest_exists`, `create_failed`, `save_failed`, `actor_not_found`, `ambiguous_actor`, `not_a_sequence`, `not_a_material`, `not_a_material_instance`, `not_a_blueprint`, `not_a_static_mesh`, `parameter_not_applied`, `has_referencers`, `delete_failed`, `rename_failed`, `unknown_enum_value`, `invalid_value_shape`, `invalid_value_type`, `invalid_tag_value`, `cvar_not_found`, `read_only`, `python_unavailable`, `write_failed`, `reset_failed`, `compile_failed`, `command_execution_failed`, `subscription_not_found`, `task_not_found`.

### UE 5.7 traps already mapped

These are the bugs that bit prior sessions. Don't re-discover them. (Historical traps from earlier sessions are preserved here; see archive for the originating context.)

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
| `FPythonCommandEx::ExecuteFile` mode tries to resolve `Cmd.Command` as a file path FIRST. Multi-line literal Python source can be misclassified as a path → `ExecPythonCommandEx` returns false silently. | All `ExecuteFile`-mode handlers MUST write the source to a real temp `.py` file (under `Intermediate/UnrealAIConnectionPython/`) via `FFileHelper::SaveStringToFile` + `ON_SCOPE_EXIT` deletion, then pass the file path. |
| `static_cast<int32>(double)` for values > `INT_MAX` is **undefined behavior** — could overflow to negative, wrap, or worse. | Always **clamp on the wide type FIRST, narrow LAST**: `static_cast<int32>(FMath::Min(Raw, static_cast<double>(kMax)))`. |
| **`FPlatformProcess::Sleep` on the game thread freezes the editor.** UE's MCP dispatcher runs on the game thread. A blocking handler stalls every game-thread system AND the very delegates that fire the events you'd be waiting for. | Don't write blocking handlers in C++ for editor-event waits. The right home for "wait for X" logic is **bridge-side synthetic tools** (`SYNTHETIC_TOOLS` dict in `bridge/unreal_ai_connection_bridge.py`). |
| **Off-by-one cursor on poll-with-pass-next-seq-back contracts.** Exclusive `>` filter silently skips the very next event whose seq exactly equals the previous `next_seq`. | Use **inclusive** cursor semantics: filter `seq < since_seq` to skip (return `seq >= since_seq`). Drop detection: `since_seq < first_seq_in_buffer`. |
| **`set_*` handlers with optional fields default-to-zero is destructive.** Callers supplying only one side silently snap the other to origin/identity. | Either reject partial-update calls explicitly, OR read the current state first and preserve omitted sides. |
| **`UEditorAssetLibrary::LoadAsset` is the established pattern across all inspect/compile/move/rename/delete handlers.** | Follow the established pattern. Per directive #4, when source-grounded reasoning supports your judgment, your opinion overrides bot suggestions. |
| **`GetClass()->GetName()` returns the CLASS taxonomy, not the instance/asset identity.** | For asset references in result fields, use **`Asset->GetPathName()`** — the engine ground-truth asset path. |
| **Switch on a UE enum requires enumerating the COMPLETE value set.** `BlueprintStatusToString` was missing `BS_Error` AND `BS_BeingCreated`. | When mapping a UE enum to strings, **enumerate every value the enum can take**. |
| **Field-name-to-shape contract is cross-handler.** | `package_path` = suffix-free; `bounds` / `fixed_bounds` / `loaded_bounds` = `{min, max, size, center}` (NOT just `{min, max}`); `*_path` fields = `GetPathName()`. |
| **Bounds shape convention is `{min, max, size, center}` across all Inspect* handlers.** | Use `Bounds.GetSize()` and `Bounds.GetCenter()` (FBox) or `FBoxSphereBounds.GetBox()` first then derive — and emit all four fields. |
| **Synthetic tools must preserve upstream RPC error codes.** | When a synthetic tool's underlying `call_ue` returns an error, propagate `upstream_err.get("code", -32603)` rather than hardcoding `-32603`. |
| **TArray of TObjectPtr can have null entries** (deleted-but-unsaved morph targets, reimport scenarios). | Filter nulls when iterating; report count of valid entries only. |
| **Ambiguous lookup must error EVEN WITH a filter.** `TActorIterator` order is not stable. | Always error on `Matches.Num() > 1`, regardless of filter. Surface the filter values in the error message. |
| **`UEditorAssetLibrary` lives in `EditorScriptingUtilities` module. That dep is ALREADY in `Build.cs:19`.** | Don't "fix" missing-Build.cs-dep findings without verifying via grep first. |
| **Pre-merge pytest validates bridge schema + manifest drift only — never compiles C++.** Only host cold compile catches `error C2248: protected member`, `error C2027: undefined type`, `error C2039: not a member`, `error C1083: cannot open include file`, deprecation-warning-as-error (`C4996`). | Run the build BEFORE git push, not after merge. The `robocopy → Build.bat → editor → smoke` cycle is the canonical cold-compile validation. |
| **`USoundCue::SubtitlePriority` is protected; `USoundCue::MaxAudibleDistance` is private.** | Use `GetSubtitlePriority()` and `GetMaxDistance()`. |
| **`USoundWave::SampleRate` and `::ImportedSampleRate` are protected.** | Use `GetSampleRateForCurrentPlatform()` and `GetImportedSampleRate()`. |
| **`UAnimNotifyState` lives in `Animation/AnimNotifies/AnimNotifyState.h` (subdir).** Same for `AnimNotify.h`. | Forward declarations work for null-checks but `->member` access requires the full include from the correct subdir path. |
| **`FAnimNotifyEvent::NotifyStateClass` IS the `UClass*` (it's `TSubclassOf<UAnimNotifyState>`).** | Use `NotifyStateClass->GetName()` directly. Calling `->GetClass()->GetName()` returns the meta-class name `"Class"`. |
| **`UAnimMontage::GetParentAsset()` does NOT exist.** | Wrap in `#if WITH_EDITORONLY_DATA` + `HasParentAsset()` check + read `ParentAsset.Get()`. |
| **`UTexture::CompositeTexture` is C4996-deprecated as of UE 5.7.** | Use `GetCompositeTexture()` accessor. |
| **`USoundWave::GetNumFrames()` returns `int64`.** | Cast `int64` directly to `double` to preserve up-to-2^53 range; never narrow through `int32` first. |
| **`FRealCurve::GetNumKeys()` is the polymorphic accessor.** | Use this rather than `static_cast<FRichCurve*>` + `Keys.Num()` — survives any future `FRealCurve` subclass. |
| **UE 5.7 Python `unreal.Rotator(a, b, c)` takes `(roll, pitch, yaw)` positionally** — struct-memory order, NOT property-name order. Same with `unreal.Color(B, G, R, A)`. | Construct empty + assign by property name. Probe any new `unreal.*` struct before assuming positional args follow the docstring. |
| **MCP server bridge code changes do NOT take effect mid-session.** The bridge MCP server process loads `bridge/unreal_ai_connection_bridge.py` at session startup and caches the module. | Bridge-touching PRs are NOT live-verifiable until the MCP client restarts. First action on next session is the canonical live test panel. |
| **JSON-RPC transport strips embedded NUL bytes in path arguments.** | Defense-in-depth NUL-rejection in bulk-* validators is unreachable via the canonical MCP transport. Still worth keeping for direct-TCP probes. |
| **curl-on-18888 returns exit 56 (empty reply) even when plugin is bound** — the plugin's length-prefixed framing rejects HTTP with `framing_error: body length exceeds 1 GB cap`. | Confirm bind via `list_tools` through MCP, not curl through HTTP. |
| **`UInputSettings::GetActionMappings(NAME_None, ...)` does not exist as an overload.** Caught by chatgpt-codex-connector on PR #161. | Use the no-arg `GetActionMappings()` accessor + filter results manually if needed. |
| **`GEditor->IsPlayingSessionInEditor()` is the reliable PIE-state check** for UE 5.7; older `GEditor->PlayWorld != nullptr` is less reliable. | Prefer the accessor; flagged in Wave A.5 pre-flight review. |
| **`FindObject<UClass>(nullptr, *ClassPath)` is the canonical lookup**; `ANY_PACKAGE` is deprecated as of UE 5.1. | Don't use `ANY_PACKAGE` in new code. |
| **`GEditor->RequestPlaySession(FRequestPlaySessionParams)` is the canonical 5.7 PIE-launch API**; `EditorInvokeCommand` / `EditorPlaySimulate` are older fallbacks. | Use the params-struct API. |

### Vertical-slice task decomposition

When implementing a bundle, each task is one self-contained vertical slice that ends with a green commit:
1. Create `Handler_<Name>.cpp` + register in `UnrealAIConnectionModule.cpp`
2. Add bridge `TOOLS` entry in `bridge/unreal_ai_connection_bridge.py` (or a `SYNTHETIC_TOOLS` entry if it's bridge-side)
3. Add manifest entry in `UnrealAIConnection/Resources/mcp_manifest.json`
4. Add bridge schema test in `tests/test_bridge.py`
5. Bump `EXPECTED_TOOL_COUNT` (+ `EXPECTED_CPP_HANDLER_COUNT` or `EXPECTED_SYNTHETIC_TOOL_COUNT`) in `tests/conftest.py` and `tests/test_manifest_sync.py`. The parametrized `test_every_tool_routes_through_tools_call` automatically picks up new UE handlers; for synthetic tools it auto-skips.
6. Add `## <name>` section in `docs/TOOLS.md`
7. Run `py -3 scripts/drift_sweep.py` — flags every doc surface that needs the count bump (typically 8 files); apply
8. Run `py -3 -m pytest tests/ -q` — must be green
9. Commit

### Manifest sync trap

`test_manifest_sync.py` substring-searches for the word "required" in manifest param descriptions. Phrase optional fields without "required" appearing — use "must be supplied when X" or "needed when Y".

### Spec → plan → implementation flow

Every bundle follows this sequence:
1. Verify UE 5.7 APIs against source headers
2. Consider 2-3 approaches; pick one with rationale
3. Write spec to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
4. Spec self-review (placeholders / consistency / scope / ambiguity)
5. (For larger bundles) Write plan to `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`
6. Implement task by task, green pytest after each
7. Push, open PR, address bot findings per standing rule #5 (or ship optimistically per directive #7 + rule #5's mechanical-fix exception)

---

## Repository file map

```
UnrealAIConnection/                               UE plugin (drops into <Project>/Plugins/)
  Source/UnrealAIConnection/
    Public/MCP/MCPServer.h                     TCP server header (per-client state structs)
    Public/UnrealAIConnectionModule.h             Module class -- retains FDelegateHandle members
                                               for the event-bus subscriptions
    Private/MCP/
      MCPServer.cpp                            TCP server impl (state-machine framing as of v0.9.1).
                                               Game-thread FTSTicker dispatch -- see trap entry
                                               about blocking-on-game-thread.
      MCPDispatcher.cpp                        Method dispatcher
      MCPHandler.h                             IUCMCPHandler interface + registry
      LogCapture.{h,cpp}                       FOutputDevice ring buffer for get_log_lines (1000 entries)
      EventBus.{h,cpp}                         FUCMCPEventBus -- ring buffer of editor events +
                                               server-side subscription registry. Mirrors
                                               LogCapture's discipline (FCriticalSection + thread_local
                                               re-entrancy guard).
      TaskRegistry.{h,cpp}                     FUCMCPTaskRegistry -- registry of long-running
                                               background tasks. State machine: pending->running->
                                               (completed|cancelled|failed). Cooperative cancellation.
      PropertyCoercion.{h,cpp}                 JSON ↔ FProperty coercion (v0.4.0 advanced types)
      ActorIdentity.{h,cpp}                    Hybrid label-or-FName actor lookup
      Handlers/                                One file per handler, ~150 LoC each
        AssetPathUtil.h                        Shared path normalization helpers (v0.7.0)
        Handler_*.cpp                          72 UE-side handlers (Tier 1 ergonomics, Tier 2
                                               event/task/REPL, Tier 3 inspect_* family + Wave A
                                               + Wave A.5).
                                               NOTE: wait_for_events / get_camera_transform /
                                               set_camera_transform / screenshot_actor / compile_mod_pak
                                               / compile_mod_pak_direct / bulk_* / inspect_data_asset /
                                               inspect_sound_class / inspect_sound_submix /
                                               inspect_audio_bus / inspect_material_function /
                                               inspect_metasound are SYNTHETIC (bridge-side) -- they
                                               do NOT have a Handler_*.cpp file. 33 synthetics total.
    UnrealAIConnection.Build.cs                   Module deps.
  Resources/mcp_manifest.json                  Tool catalog (mirrors bridge TOOLS, 105 entries)
  UnrealAIConnection.uplugin                      Plugin manifest (v0.9.1 / UE 5.7)

bridge/
  unreal_ai_connection_bridge.py                  stdio↔TCP bridge.
                                               - SYNTHETIC_TOOLS dict (33 entries).
                                               - synthetic_* functions (one per synthetic tool).
                                               - Marker pattern for round-tripping results from
                                                 execute_unreal_python (UUID per call + log search,
                                                 refactored into _run_marker_pattern helper).
                                               - Defensive shape validation (NUL byte + .. segment
                                                 rejection) reusable across bulk_* validators.

examples/
  smoke_test.py                                Live integration smoke test. 15 default checks.
  .mcp.json.example                            Template MCP client config
  hello_run_python_file.py                     Test fixture for run_python_file

scripts/
  UnrealAIConnection-Editor.psm1                  PowerShell module for editor lifecycle
                                               (Start/Stop/Wait/Test functions)
  seed_test_project.py                         Idempotent seeder for /Game/SmokeTest_*
                                               throwaway assets
  drift_sweep.py                               Doc-drift scanner (6 canonical signals × 8 files).
                                               Run before any PR that bumps counts.

.mcp.json (gitignored)                         Local MCP client config; points at
                                               bridge/unreal_ai_connection_bridge.py.
AGENTS.md                                      Universal-agent project context (auto-loaded by
                                               Codex CLI, Copilot CLI, Cursor, Gemini CLI).
                                               Keep in sync with CLAUDE.md.
.github/copilot-instructions.md                Copilot reviewer guidance.

tests/
  conftest.py                                  EXPECTED_TOOL_COUNT (+ CPP / SYNTHETIC splits).
                                               Single source of truth for count assertions.
  test_bridge.py                               Bridge MCP protocol + schema tests.
  test_bridge_edge_cases.py                    Parametrized test_every_tool_routes_through_tools_call
                                               (excludes synthetic tools from round-trip assertion).
  test_manifest_sync.py                        Drift detection between bridge TOOLS and manifest.
  test_drift_sweep.py                          CI guard: runs scripts/drift_sweep.py + unit tests.
  test_no_personal_leaks.py                    CI guard: forbidden-pattern scan over tracked files.

docs/
  TOOLS.md                                     Per-tool params/results/examples (105 sections)
  ARCHITECTURE.md                              How pieces fit; UE 5.7 API gotchas
  INSTALLATION.md                              Step-by-step install
  HANDOFF.md                                   This file (latest closing notes; 33rd–36th active)
  HANDOFF-archive.md                           Closing notes 1-32 (chronological, append-only)
  RESTART-RECOVERY.md                          Post-format recovery procedure
  session-memory-archive/                      Snapshot of session memory files
  LANGUAGE-CHOICE-RETROSPECTIVE.md             Per-tool language verdict + decision flow
  superpowers/specs/                           Design specs per bundle, dated.
  superpowers/plans/                           Implementation plans per bundle, dated.

CHANGELOG.md                                   Keep-a-Changelog. [Unreleased] / [0.9.1] / earlier.
CONTRIBUTING.md                                Project conventions, 10-step new-tool playbook.
```

---

## How to resume in a fresh session

**If the development machine was just reformatted** (fresh OS install, all software gone): start with [`RESTART-RECOVERY.md`](RESTART-RECOVERY.md) instead — it walks through Git/Node/Python/VS-C++/Codex CLI install, then restoring session memory from [`session-memory-archive/`](session-memory-archive/) before the normal resume steps below apply.

1. Open a new session in the same repo (any MCP-compliant client).
2. Send: *"Read `docs/HANDOFF.md` and continue from there. The user is in autonomy mode — pick the next reasonable thing to do."*
3. **Verify Codex tooling** (per directive #8): `ToolSearch query="codex"` and/or `Bash codex --help`. If reachable, the multi-agent collaboration model is live.
4. **Verify the multi-agent fleet** (per directive #9 and standing rule #1): the explorer / reviewer subagents are usable in any session via the Agent tool. The `general-purpose` subagent works for research but **NOT for file writes** (sandbox isolation).
5. The fresh session reads this doc, absorbs the directives, sees **105 tools shipped (72 C++ + 33 synthetic)**, and proceeds.

For specific resumption:
- *"Live-verify Waves A + A.5"* → host rebuild via the runbook above, then run the Wave A/A.5 verification panel
- *"Continue with Wave B"* → Blueprint graph mutation (per the community-roadmap research in the 19th closing-note); attended-Codex work, do not auto-dispatch
- *"Run the multi-agent workflow"* → directive #9 + standing rule #1 + `memory/feedback_multi_agent_workflow.md`

---

## Closing notes from prior sessions

> **Note:** Consecutive closing notes 1 through 32 (sessions 2026-05-09 through 2026-05-17) are archived in [`HANDOFF-archive.md`](HANDOFF-archive.md). The latest closing notes (33rd–36th) are kept active here — a one-cycle 4-note set; the 33rd is due to rotate to the archive on the next pass.

## Session 2026-05-18 (UE 5.7 officially supported & tested; other versions open/best-effort/community — ADR-0001; scaffold kept available; docs-only)

The project scope was clarified by user decision: UE 5.7 is the officially supported & tested version, while the project stays **open** — anyone may build/run on **any** UE version, their own choice, at their own risk, via the **kept** cross-engine compat scaffold (best-effort, uncertified, not actively maintained, PR-welcome; newer-UE upgrades welcome on request). This session is docs/policy-only — no `.cpp/.h/.Build.cs` edits, no rebuild. The previously-host-proven artifacts from the 32nd note are the operative state going in.

**Prior task shipped (from the 32nd-note branch):** the `verify/wave-a-host-rebuild-ue57` work was squash-merged as **PR #220 → `main` (HEAD `5f4f596`)**; the feature branch was deleted; the host toolchain was restored to **MSVC 14.34.31933** (`BuildConfiguration.xml` line 4) *(this restore was later reverted by a concurrent session that re-flipped the pin to 14.44 to host-build PR #226 and left it; it was re-restored to 14.34.31933 on 2026-05-18 — verified, see the 35th note; the unqualified "restored" here was premature)*; the editor (PID 27988) was killed. No code or rebuild in that close-out beyond the merge — the 32nd note's host-PROVEN UE 5.7 (T1) compile+runtime verification stands as the baseline.

**Scope decision (ADR-0001, 2026-05-18):** the user clarified that **UE 5.7 is the officially supported & tested version**, while the project stays **open** for any UE version (best-effort/community, at the user's own risk). `docs/adr/ADR-0001-ue57-only-freeze-cross-engine-compat.md` was created (Accepted; the title supersedes the legacy slug). The Phase H / T1/T2/T3 cross-version path is **de-prioritised to best-effort/community, NOT frozen-dead or dropped**: T2 (UE 5.1) was historically host-verified in the 31st-note window — a useful data point for non-5.7 builds; T3 (UE 4.27) was never host-verified — untested, build-from-source at own risk, PR-welcome; the `UCMCPCompat.h` shims, per-handler version gates, and the per-bucket `.uplugin` generator are **kept available in the codebase, reversible/expandable**, uncertified and not actively maintained but PR-welcome — no `.cpp/.h/.Build.cs` touched (the compat code stays inert as-is by decision, described as *available*, not *frozen*).

**Docs reconciled this session (open best-effort framing — corrected pre-merge from an initial over-strict "5.7-only/FROZEN" draft):** ADR-0001 rewritten — title "UE 5.7 Officially Supported; Other Versions Best-Effort (Community)" (filename/slug kept to avoid churn; doc notes the title supersedes the slug); `docs/HANDOFF.md` — operative carry-forwards reframed to "official 5.7 + open best-effort/community" (cross-version items de-prioritised, scaffold kept available, uncertified, not actively maintained, PR-welcome), perpetual next-step annotation reframed likewise, `## Project scope` section reframed to the open policy, this 33rd note corrected in place (no 34th note), line-13 glance qualifier reframed; `README.md` — Phase-H roadmap paragraph reframed to "officially built/tested on 5.7; other versions community/best-effort via the kept scaffold", Mermaid client node made vendor-neutral (`Any MCP client`); `docs/PHASE-H-COMPAT.md` — banner reframed from FROZEN to "kept & available as a best-effort/community path" (technical content intact); `docs/COMPETITIVE-ANALYSIS.md` — UE-version-range rows + roadmap item reframed to official-vs-best-effort (tool-count 105 kept); `UnrealClaudeMCP.uplugin` — `Description` made vendor-neutral ("from any MCP-compliant client"; counts 72/33/105 and `EngineVersion` 5.7.0 already correct, untouched); `docs/TOOLS.md` — the 4 Phase-H engine-version-gate notes reframed from "inactive under UE 5.7-only" to "best-effort/community path; gate kept available (ADR-0001)". The compat code remains inert as-is (no `.cpp/.h/.Build.cs` touched) but is described as *available*, not *frozen*. Standing rules stay **5** (the scope is a separate `## Project scope` section, not a 6th rule).

**Carried forward / next actions (UE 5.7 official; other versions best-effort/community — ADR-0001):**
- Official feature work targets UE 5.7 APIs — one `.cpp` + one registration line per handler; no version gate / shim / per-bucket variant required for 5.7 (existing cross-version branches not torn out).
- README demo GIF — unblocked (`render_camera_to_png` host-built per the 32nd note); creative-recording window.
- Optional later: Phase H scaffold archival (move the cross-engine machinery to a less-prominent location) — low priority, reversible-by-design, stays available.
- Best-effort/community (scaffold kept available, uncertified, not actively maintained, PR-welcome): UE 4.27 (T3) and other non-5.7 versions — build-from-source at own risk; newer-UE upgrades welcome on request.
- **Maintainer-only (unchanged, still live):** merge this branch's PR after the bot-gate; ECC follow-up — PRs #207 and #204 are already CLOSED-unmerged (no ECC code reached `main`); the remaining action is **owner-only**: revoke the `ecc-tools` GitHub App at https://github.com/settings/installations and delete its 2 dangling origin branches (`ecc-tools/unreal-ai-connection-1778881813925`, `ecc-tools/UnrealClaudeMCP-1778879119146`); also add branch protection to `main` (currently none). Decide PR #205 A/B. Standing carry-forward (5.7-scoped): rename Stage 2 (C++ module identity — host rebuild); Phase J (BTSschool — separate dir); Movie Render Queue synthetic (attended-Codex C++); the local OSS LLM runtime has only its one default small model pulled — not a bug, not admin-gated (the local-llm MCP server reports installed models correctly; the runtime is a non-elevated tray app, no Windows service, models in a local model store); to add models pull the desired tag in a normal user shell (verify exact registry tags first).

**Thirty-third consecutive closing-note.** Session 2026-05-18: docs/policy-only — no code or rebuild. Prior task shipped (PR #220 squash-merged → `main` HEAD `5f4f596`; feature branch deleted; toolchain restored to MSVC 14.34.31933 — *later re-flipped to 14.44 by a concurrent session for PR #226 and re-restored to 14.34.31933 on 2026-05-18; see 35th note; "restored" here was premature*; editor PID 27988 killed). User clarified scope: **UE 5.7 is the officially supported & tested version, but the project stays OPEN — anyone may build/run on any UE version, their choice, at their own risk** — `docs/adr/ADR-0001-ue57-only-freeze-cross-engine-compat.md` created then corrected pre-merge (Accepted, 2026-05-18; title "UE 5.7 Officially Supported; Other Versions Best-Effort (Community)" supersedes the legacy slug). The Phase H / T1/T2/T3 cross-version path is **de-prioritised to best-effort/community, NOT frozen-dead or dropped**: T2 (UE 5.1) was historically host-verified (31st-note window) — a useful data point for non-5.7 builds; T3 (UE 4.27) was never host-verified — untested, build-from-source at own risk, PR-welcome; `UCMCPCompat.h` shims + per-handler version gates + the per-bucket `.uplugin` generator are **kept available, reversible/expandable**, uncertified and not actively maintained but PR-welcome (compat code stays inert as-is — no `.cpp/.h/.Build.cs` touched — described as *available*, not *frozen*). The 32nd-note host run compiled in **8 formerly-inert C++ handlers** (Wave A 5 + Wave A.5 2 + `render_camera_to_png` 1 = 8); the 9th item `bulk_inspect_assets` is a **bridge-side synthetic, never a C++ handler** (SKIP on the raw plugin socket by design). Docs reframed (open best-effort, corrected from an initial over-strict draft): ADR-0001 + HANDOFF (open-policy carry-forwards, perpetual-next-step + Project-scope + line-13 glance reframed, this note corrected in place — no 34th note) + README (Phase-H open best-effort + vendor-neutral Mermaid) + PHASE-H-COMPAT (best-effort banner) + COMPETITIVE-ANALYSIS (official-vs-best-effort rows) + `.uplugin` (vendor-neutral Description; counts 72/33/105 + `EngineVersion` 5.7.0 already correct, untouched) + TOOLS.md (4 Phase-H gate notes reframed best-effort, gate kept available). Prior pass had already rotated the 30th consecutive closing-note to `HANDOFF-archive.md` (active file keeps 31st–33rd; archive holds 1–30). Tool count: **105** (72 C++ + 33 synthetic). pytest: **498**. Standing rules: **5 (unchanged)**. Cadence intact.

---

## Session 2026-05-18 (PR #226 — TCP-server D2 cross-thread hardening, layered on merged #225; host re-verify pending)

This session reframed PR #226. #225 (`cc5181b`, already merged to `main`) fixed **D1** — a reentrant `Stop()` arriving during dispatch — via a snapshot `ClientsThisTick = ConnectedClients` plus three `if(!bRunning)return false;` bail-outs, and is host-verified. But #225's commit message misdiagnosed the secondary path as "game thread, no lock": verified against the on-disk `F:\UE_5.7` engine source that `FTcpListener` is an `FRunnable` and its `OnConnectionAccepted` fires on its **own listener thread**, so **D2** was still open on `main` — the listener thread races `ConnectedClients`/`ReadStates`/`WriteStates`, #225's snapshot copy is itself a racy read, and the `TMap` is mutated cross-thread.

PR `#226` is therefore layered **on top of** #225, not competing with it: #225's D1 fix is kept verbatim and #226 adds only the D2 fix — a new `FCriticalSection PendingClientsCS` + `TArray<FSocket*> PendingAccepted`; `OnConnectionAccepted` now parks the accepted socket under the lock; `TickClients` adopts pending sockets at the very top, BEFORE #225's snapshot, so every `ConnectedClients`/map mutation is game-thread-only and #225's snapshot/ranged-for can no longer race; `Stop()` drains `PendingAccepted` under the lock, reusing the single `ISocketSubsystem` fetch. #226's earlier standalone `bTicking`/`bStopRequested` reentrancy guard was DROPPED as redundant with #225's bail-outs. The `origin/main` merge was resolved by taking `main` as the base for `MCPServer.cpp`/`.h` + HANDOFF and re-applying the minimal D2 delta on top; the weave was verified (no conflict markers, #225's D1 intact, no duplicate D1 machinery). Bot triage: F2 (single `ISocketSubsystem`) + F3 (no redundant `PendingAccepted.Reset()` after `MoveTemp`) are baked into the layered form; F4 (ARCHITECTURE trap) reworded to the layered design; F5 (banner) reconciled by this rotation; F1 superseded (no deferred-`Stop()` tail exists anymore); F7 dismissed (repo-wide docstring advisory, not introduced here, not a CI gate). Host recompile + a live D1+D2 re-verify are still pending; PR #226 is to be reframed and the bot-gate re-run, then the user merges (never Claude). Env carry: the harness PowerShell tool is dead (Bash native only); Codex CLI was usage-limited until 2026-05-19 ~02:51, so the server-core merge was done captain-side.

**Thirty-fourth consecutive closing-note.** Session 2026-05-18: PR #226 reframed as a D2 cross-thread hardening layered on merged #225 (which fixed D1 only and misdiagnosed D2 as game-thread; FTcpListener is an FRunnable, OnConnectionAccepted runs on its own thread — verified vs F:\UE_5.7). Added FCriticalSection PendingClientsCS + PendingAccepted; listener-thread accept now parks under the lock, game thread adopts at top of TickClients before #225's snapshot; Stop() drains pending under the lock; #226's old bTicking/bStopRequested guard dropped as redundant. origin/main merge resolved (main as base for MCPServer.cpp/.h + HANDOFF, minimal D2 delta re-applied; weave verified). Bots F2/F3 baked in, F4/F5 done, F1 superseded, F7 dismissed. Host recompile + live D1+D2 re-verify pending; PR to be reframed + bot-gate re-run; user merges. Tool count: **105** (72 C++ + 33 synthetic). pytest: **498**. Standing rules: **5**. Cadence intact.

---

## Session 2026-05-18 (docs-only — cross-session toolchain-pin reconciliation; PR #226's Follow-up-C work untouched)

This session is docs-only — no `.cpp/.h/.Build.cs` edits, no rebuild. It reconciles a doc/reality drift discovered after the concurrent session shipped PR #226. **What the concurrent session did (NOT this session):** PR #226 — the D2 cross-thread `TickClients` hardening (`FCriticalSection PendingClientsCS` + `PendingAccepted` staging queue, layered on merged #225's D1) — was host-verified on MSVC 14.44 and **merged to `main` (`95222bf`)** by that session, closing "Follow-up C" (the `ConnectedClients` listener-thread race). This session did not touch any `.cpp`; the #226 server-core work stands as the concurrent session left it.

**The drift corrected:** the 32nd/33rd notes (and the line-335 "Host toolchain flip" body) asserted the MSVC pin **restore-to-14.34.31933 was completed 2026-05-18**. That was premature: the 14.34 pin was restored once on 2026-05-18, but the concurrent session then **re-flipped it to 14.44.35207 to host-build #226 and left it at 14.44**. It was **re-restored to 14.34.31933 on 2026-05-18** — verified: `BuildConfiguration.xml` line 4 is `<CompilerVersion>14.34.31933</CompilerVersion>`, so UE ≤5.3 (T2/T3) builds work again. The four "restored — done" assertions (line-335 body, 32nd note, 33rd-note narrative + 33rd note) were each annotated in place with a minimal reconciliation clause pointing here; append-only history preserved, unrelated history not rewritten. Net asserted end-state now matches reality: pin == 14.34.31933. **Follow-up (bot-directed, PR #227):** CodeRabbit/Gemini flagged two secondary navigation refs this rotation made stale — the archive file-map row (`HANDOFF.md` "Closing notes 1-31" → `1-32`) and the archive "Note:" cross-reference sentence (`archive holds 1-30, active holds 31-33` → `1-32 / 33-35`); both applied as a mechanical consistency follow-up on this branch. The CodeRabbit `342/352` "Claude never merges" vendor-neutral hit was **dismissed as not-introduced-here** — those occurrences are pre-existing concurrent-#226-session text (`HANDOFF.md:25,39`), outside this PR's diff and out of scope for a toolchain-drift reconciliation; `tests/test_no_personal_leaks.py` (the enforced vendor-neutral gate) passes 12/12.

**Still-open minor leftovers (carried, not actioned here):** (B) `import_blender_assets.py` flat-vs-Interchange-nested verifier cosmetic exit-1; the analogical `Handler_GetActorsInLevel.cpp:4` "Blender MCP" comment; stale merged local branches (`docs/demo-gif`, `claude/strange-banach-1a12d0`, `pr-204-review`). Maintainer-only carry-forwards from the 33rd/34th notes remain live (close PR #207 + revoke the `ecc-tools` GitHub App; decide PR #205 A/B; rename Stage 2; Phase J; Movie Render Queue synthetic; local OSS LLM daemon empty-list bug).

**Thirty-fifth consecutive closing-note.** Session 2026-05-18: docs-only — no code or rebuild. Cross-session reconciliation: the concurrent session merged **PR #226** (D2 cross-thread `TickClients` hardening — `FCriticalSection PendingClientsCS` + `PendingAccepted`, layered on merged #225's D1; host-verified on MSVC 14.44; merged to `main` `95222bf`), closing **Follow-up C** (`ConnectedClients` listener-thread race) — that was the concurrent session, NOT this one; no `.cpp` touched here. The prior "toolchain restored 2026-05-18" claim (32nd/33rd notes + line-335 body) was **premature** — the concurrent session re-flipped the MSVC pin to 14.44.35207 to host-build #226 and left it; the pin was **re-restored to 14.34.31933 on 2026-05-18 (verified — `BuildConfiguration.xml` line 4)** so UE ≤5.3 builds work again. Four restore assertions annotated in place with a minimal reconciliation clause (append-only history preserved). Still-open minor leftovers: (B) `import_blender_assets.py` flat-vs-Interchange-nested verifier cosmetic exit-1; the analogical `Handler_GetActorsInLevel.cpp:4` "Blender MCP" comment; stale merged local branches (`docs/demo-gif`, `claude/strange-banach-1a12d0`, `pr-204-review`). Rotated the 32nd consecutive closing-note to `HANDOFF-archive.md` (active file keeps 33rd–35th; archive holds 1–32). Tool count: **105** (72 C++ + 33 synthetic). pytest: **498**. Standing rules: **5**. Cadence intact.

---

## Session 2026-05-26 (UE 5.6 host-build + binary release; docs-accuracy sweep + drift-guard hardening)

**Thirty-sixth consecutive closing-note.** Sessions 2026-05-25 → 26. **Shipped to `main` since the 35th note** (all bot-gated, squash-merged): #228 UE 5.6 host-verified compile+smoke (Aximmetry); #229 `create_dmx_patch` + #231 `dmx_stream_*` (DMX authoring/streaming); #232–#236 docs/registry/social-card; #237 plugin install fix (relative `source:"./"` in `marketplace.json` — a same-repo GitHub self-pointer broke the Claude Code directory install); #238 `driving-unreal` bundled skill (`SKILL.md` + `reference.md` — know-how layer over the 105 tools, auto-discovered); #239 engine-compat badge; #240 Widget/UMG recipe + `tests/test_skill_tool_refs.py` tool-name guard; #241 tool-count 104→105 (`marketplace.json` + `plugin.json`); #242 surfaced the 5.6 release in README + PHASE-H-COMPAT.

**UE 5.6 build + release (this session):** root-caused the long-standing 5.6 build failure — a **user-level `BuildConfiguration.xml` `<CompilerVersion>` pinned to 14.34.31933**, while UE 5.6's UBT requires **≥14.38** (`Engine/Config/Windows/Windows_SDK.json`: `MinimumVisualCppVersion` 14.38.33130, preferred 14.38.x). **Cleared the pin** + installed **MSVC 14.38.33130** (VS2022 v17 BuildTools), built the **T1 `.uplugin` variant** (EngineVersion 5.4.0) against UE 5.6 → `BUILD SUCCESSFUL` (89 units) → installed into a throwaway 5.6 project (with `DMXEngine`+`DMXProtocol` enabled) → **live `smoke_test.py` EXIT 0 (all suites)**. Published **`v0.9.1-ue5.6`** GitHub release (Win64 binaries, 18.5 MB). **Load dep discovered:** the plugin links `DMXRuntime`/`DMXProtocol`, so a host project must enable `DMXEngine` + `DMXProtocol` or `UnrealClaudeMCP` fails to load (documented in the release notes + PHASE-H-COMPAT + README).

**⚠ TOOLCHAIN PIN CHANGED — carry forward:** `~/AppData/Roaming/Unreal Engine/UnrealBuildTool/BuildConfiguration.xml` `<CompilerVersion>` was **cleared (was 14.34.31933)** so the 5.6 build's ≥14.38 requirement is satisfiable (auto-select now picks 14.38/14.44/14.51). **5.6 + 5.7 build fine. If building T2/T3 (UE ≤5.3) again, re-pin to an engine-appropriate older toolchain** — those older engines may reject 14.38+. This **supersedes** the 35th note's "pin == 14.34.31933" end-state.

**Docs-accuracy sweep + drift-guard hardening (this session, docs-only):** a sub-agent audit found stale counts/dates living *outside* the drift-sweep scan set. Fixed: README (release date 2026-05-08→05-23 + 5.6-release line + skill in the "what's in the box" tree + 64→71 handler-log sample), CONTRIBUTING (80→105 / 280+→503 / "16 synthetics"→33 + skill in layout), 5 × `docs/setup/*` + INSTALLATION (104→105 / 71→72), RESTART-RECOVERY (104/71→105/72, 162→503, reworded to canonical forms). Rewrote the **placeholder `SECURITY.md`** (real localhost threat model + private-advisory disclosure path). Refreshed **CHANGELOG** ([Unreleased] post-#185 highlights + Internal tally 102/71/31/400→105/72/33/503; [0.9.1] date →2026-05-23). **Hardened `scripts/drift_sweep.py`:** added `CONTRIBUTING.md` to `SCAN_FILES` and reworded CONTRIBUTING/RESTART counts into the canonical phrasings the existing patterns enforce → those files can no longer silently drift. Deleted untracked `HDMediaUnrealMCP/` junk; `git worktree prune` (1 stale removed; 4 locked old-path worktrees left as harmless gitignored). Shipped on branch `docs/accuracy-sweep-and-guard` (PR pending bot-gate + merge).

**Rolling-three:** active file now holds **33rd–36th** (a one-cycle 4-note set; **the 33rd is due to rotate to `HANDOFF-archive.md` next pass**). Tool count: **105** (72 C++ + 33 synthetic). pytest: **503**. Standing rules: **5**. Cadence intact.
