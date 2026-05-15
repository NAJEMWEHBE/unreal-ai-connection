# Handoff document

Single source of truth for resuming work on UnrealClaudeMCP in a fresh session of any MCP-compliant client. Read this first; it captures everything carried in the prior session's working memory.

> Earlier closing notes (1st through 22nd, sessions 2026-05-09 through 2026-05-15) are archived to [`docs/HANDOFF-archive.md`](HANDOFF-archive.md). This active file keeps the latest three consecutive notes (23rd-25th) for quick pickup.

---

## Project at a glance

**What this is:** An Unreal Engine 5.7 plugin + Python bridge that exposes editor automation to **any MCP-compliant client** (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, …) over a localhost TCP socket. The plugin adds a JSON-RPC server inside the editor; each "handler" is one MCP tool (~150 LoC of C++ in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`). The bridge translates between the client's stdio MCP protocol and the plugin's TCP wire format. **Vendor-neutral by design** — the wire protocol is open MCP (created by Anthropic, but any conforming client works); the project's repo/folder names retain "Claude" for legacy reasons but the capability is universal.

**Where it stands (post-PR #184 — scene v7 + marketplace tools hardened):** **102 tools total** (71 UE-side C++ handlers + 31 bridge-side synthetic tools — `marketplace_search` + `marketplace_import` are now fully reviewed/merged with SSRF guard, client-side filter, format-fallback parity, path-traversal sanitization, and license-distinction in their descriptions). Plugin version `0.9.1`, targets UE `5.7`. pytest baseline: **400** passing. (For the current HEAD commit, run `git log -1 origin/main`; the latest milestone PR is #184.)

Recent waves that landed in the current session lineage:
- **Wave A (PR #161)** — 6 quick-win tools: `get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `bulk_inspect_assets`
- **Wave A.5 (PR #162)** — 2 new tools: `pie_control`, `inspect_project_setting`
- **PR #164** — Wave A + A.5 bot-findings cleanup
- **PR #165** — codified standing rules #4 (delegation-by-default) and #5 (bot-review gate)
- **PR #166** — HANDOFF.md split into active + archive (~36K tokens / session-start saved)
- **Wave B (PR #167)** — 4 asset-hygiene synthetics: `find_unused_assets`, `get_reference_chain`, `bulk_compile_blueprints`, `audit_blueprint_compile_status` (88 → 92)
- **Wave C (PR #168)** — 4 actor-batch synthetics: `find_actors_by_class`, `bulk_focus_actors`, `bulk_screenshot_actors`, `bulk_set_actor_property` (92 → 96)
- **Wave D (PR #169)** — 4 utility synthetics: `compare_assets`, `bulk_set_console_variables`, `inspect_dependency_graph`, `bulk_fix_redirectors` (96 → 100 — **TARGET HIT**)

**What's NOT in main yet:** nothing in flight at session start. All bot-findings cleared; standing rules locked. Tool count is at the user's explicit 100-target.

---

## Open work + pending verification

**Open PRs:** none.

**Latest milestone on main:** PR #192 — convert_hdri_to_cubemap synthetic; merge commit on main is the next 25th-closing-note PR. For the current HEAD commit hash, run `git log -1 origin/main`.

**Pending verification on host machine (PRIMARY next-action item):**

The 7 new C++ handlers from Waves A + A.5 (`get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `pie_control`, `inspect_project_setting`) shipped with bridge-side schema + tests green, but **the host project still needs a cold rebuild** to register the new C++ handlers in the running editor. Until that happens:
- MCP `tools/list` already shows all 100 entries (bridge knows them from `TOOLS`)
- Calls to the 7 new C++ handler names will return JSON-RPC `-32601` (method not found) — the running plugin DLL doesn't have the new `Reg.Register(...)` lines compiled in yet
- The 1 new synthetic from Wave A (`bulk_inspect_assets`) IS reachable today (bridge-side composition; no UE rebuild needed)

**Live verification panel (run after host rebuild):**

- `list_tools` count → expect 71 C++ handlers registered (was 64 pre-Wave-A)
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

1. `cd F:\UnrealClaudeMCP && git pull origin main`
2. `taskkill /IM UnrealEditor.exe /F` (Live Coding holds the DLL otherwise; safe if UE isn't running). Or, with the module: `Import-Module .\scripts\UnrealClaudeMCP-Editor.psm1; Stop-UCMCPEditor`.
3. **Sync dev plugin → host plugin.** The host project's `Plugins/UnrealClaudeMCP/` may be a plain copy on this machine, in which case it drifts from the dev tree silently. Verify with `Get-Item "<host-project>\Plugins\UnrealClaudeMCP" | Select-Object LinkType` — a `Junction` or `SymbolicLink` value means it auto-tracks; empty means it's a plain copy and you must sync. To sync (always quote both paths — Windows project locations like `F:\ax plug in\…` contain spaces):
   ```
   robocopy "<repo>\UnrealClaudeMCP" "<host-project>\Plugins\UnrealClaudeMCP" /MIR /XD Binaries Intermediate .vs /NFL /NDL /NJH /NJS /NP
   ```
   Robocopy exit codes 0–7 mean success. The `/XD Binaries Intermediate` exclusion preserves the host's UBT cache so step 4 stays incremental.
4. `& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" <HostProjectName>Editor Win64 Development -project="<full path to host .uproject>"` — must end with `Result: Succeeded`. The target is `<HostProjectName>Editor`, NOT `<PluginName>Editor`. For the canonical host project, that's `HDMediaVirtualStudioEditor`.
5. Open the host `.uproject` in UE editor (use the path-quoting recipe in CLAUDE.md — pre-quote inside the `-ArgumentList` array element). Confirm **71 UE C++ handlers register** in the Output Log. Filter by `LogUCMCPHandler` and you should see exactly 71 lines `Registered handler '<name>'`. The 31 bridge-side synthetic tools never reach the UE process and so never appear in the Output Log; they're served by `SYNTHETIC_TOOLS` in `bridge/unreal_claude_mcp_bridge.py`. Total tools visible to MCP clients: 102. The TCP server then binds `127.0.0.1:18888` (~10s on warm DDC, 1–5 min cold). With the module: `$proc = Start-UCMCPEditor -ProjectPath "<full path>"; $ready = Wait-UCMCPReady; $check = Test-UCMCPHandlers -LogPath "<host-project>\Saved\Logs\<HostProjectName>.log" -ExpectedCount 71`.
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
| `FPythonCommandEx::ExecuteFile` mode tries to resolve `Cmd.Command` as a file path FIRST. Multi-line literal Python source can be misclassified as a path → `ExecPythonCommandEx` returns false silently. | All `ExecuteFile`-mode handlers MUST write the source to a real temp `.py` file (under `Intermediate/UnrealClaudeMCPPython/`) via `FFileHelper::SaveStringToFile` + `ON_SCOPE_EXIT` deletion, then pass the file path. |
| `static_cast<int32>(double)` for values > `INT_MAX` is **undefined behavior** — could overflow to negative, wrap, or worse. | Always **clamp on the wide type FIRST, narrow LAST**: `static_cast<int32>(FMath::Min(Raw, static_cast<double>(kMax)))`. |
| **`FPlatformProcess::Sleep` on the game thread freezes the editor.** UE's MCP dispatcher runs on the game thread. A blocking handler stalls every game-thread system AND the very delegates that fire the events you'd be waiting for. | Don't write blocking handlers in C++ for editor-event waits. The right home for "wait for X" logic is **bridge-side synthetic tools** (`SYNTHETIC_TOOLS` dict in `bridge/unreal_claude_mcp_bridge.py`). |
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
| **MCP server bridge code changes do NOT take effect mid-session.** The bridge MCP server process loads `bridge/unreal_claude_mcp_bridge.py` at session startup and caches the module. | Bridge-touching PRs are NOT live-verifiable until the MCP client restarts. First action on next session is the canonical live test panel. |
| **JSON-RPC transport strips embedded NUL bytes in path arguments.** | Defense-in-depth NUL-rejection in bulk-* validators is unreachable via the canonical MCP transport. Still worth keeping for direct-TCP probes. |
| **curl-on-18888 returns exit 56 (empty reply) even when plugin is bound** — the plugin's length-prefixed framing rejects HTTP with `framing_error: body length exceeds 1 GB cap`. | Confirm bind via `list_tools` through MCP, not curl through HTTP. |
| **`UInputSettings::GetActionMappings(NAME_None, ...)` does not exist as an overload.** Caught by chatgpt-codex-connector on PR #161. | Use the no-arg `GetActionMappings()` accessor + filter results manually if needed. |
| **`GEditor->IsPlayingSessionInEditor()` is the reliable PIE-state check** for UE 5.7; older `GEditor->PlayWorld != nullptr` is less reliable. | Prefer the accessor; flagged in Wave A.5 pre-flight review. |
| **`FindObject<UClass>(nullptr, *ClassPath)` is the canonical lookup**; `ANY_PACKAGE` is deprecated as of UE 5.1. | Don't use `ANY_PACKAGE` in new code. |
| **`GEditor->RequestPlaySession(FRequestPlaySessionParams)` is the canonical 5.7 PIE-launch API**; `EditorInvokeCommand` / `EditorPlaySimulate` are older fallbacks. | Use the params-struct API. |

### Vertical-slice task decomposition

When implementing a bundle, each task is one self-contained vertical slice that ends with a green commit:
1. Create `Handler_<Name>.cpp` + register in `UnrealClaudeMCPModule.cpp`
2. Add bridge `TOOLS` entry in `bridge/unreal_claude_mcp_bridge.py` (or a `SYNTHETIC_TOOLS` entry if it's bridge-side)
3. Add manifest entry in `UnrealClaudeMCP/Resources/mcp_manifest.json`
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
UnrealClaudeMCP/                               UE plugin (drops into <Project>/Plugins/)
  Source/UnrealClaudeMCP/
    Public/MCP/MCPServer.h                     TCP server header (per-client state structs)
    Public/UnrealClaudeMCPModule.h             Module class -- retains FDelegateHandle members
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
        Handler_*.cpp                          71 UE-side handlers (Tier 1 ergonomics, Tier 2
                                               event/task/REPL, Tier 3 inspect_* family + Wave A
                                               + Wave A.5).
                                               NOTE: wait_for_events / get_camera_transform /
                                               set_camera_transform / screenshot_actor / compile_mod_pak
                                               / compile_mod_pak_direct / bulk_* / inspect_data_asset /
                                               inspect_sound_class / inspect_sound_submix /
                                               inspect_audio_bus / inspect_material_function /
                                               inspect_metasound are SYNTHETIC (bridge-side) -- they
                                               do NOT have a Handler_*.cpp file. 31 synthetics total.
    UnrealClaudeMCP.Build.cs                   Module deps.
  Resources/mcp_manifest.json                  Tool catalog (mirrors bridge TOOLS, 102 entries)
  UnrealClaudeMCP.uplugin                      Plugin manifest (v0.9.1 / UE 5.7)

bridge/
  unreal_claude_mcp_bridge.py                  stdio↔TCP bridge.
                                               - SYNTHETIC_TOOLS dict (31 entries).
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
  UnrealClaudeMCP-Editor.psm1                  PowerShell module for editor lifecycle
                                               (Start/Stop/Wait/Test functions)
  seed_test_project.py                         Idempotent seeder for /Game/SmokeTest_*
                                               throwaway assets
  drift_sweep.py                               Doc-drift scanner (6 canonical signals × 8 files).
                                               Run before any PR that bumps counts.

.mcp.json (gitignored)                         Local MCP client config; points at
                                               bridge/unreal_claude_mcp_bridge.py.
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
  TOOLS.md                                     Per-tool params/results/examples (100 sections)
  ARCHITECTURE.md                              How pieces fit; UE 5.7 API gotchas
  INSTALLATION.md                              Step-by-step install
  HANDOFF.md                                   This file (latest 3 closing notes only)
  HANDOFF-archive.md                           Closing notes 1-21 (chronological, append-only)
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
5. The fresh session reads this doc, absorbs the directives, sees **102 tools shipped (71 C++ + 31 synthetic)**, and proceeds.

For specific resumption:
- *"Live-verify Waves A + A.5"* → host rebuild via the runbook above, then run the Wave A/A.5 verification panel
- *"Continue with Wave B"* → Blueprint graph mutation (per the community-roadmap research in the 19th closing-note); attended-Codex work, do not auto-dispatch
- *"Run the multi-agent workflow"* → directive #9 + standing rule #1 + `memory/feedback_multi_agent_workflow.md`

---

## Closing notes from prior sessions

> **Note:** Consecutive closing notes 1 through 22 (sessions 2026-05-09 through 2026-05-15) are archived in [`HANDOFF-archive.md`](HANDOFF-archive.md). Only the latest three (23rd-25th) are kept active here.

## Session 2026-05-15 (PR #189 — marketplace_import multi-map PBR mode)

Second bounded item picked from the 22nd-note parked list: complete the v2 promise of `marketplace_import` by adding opt-in `multi_map=true` so callers can pull a full PBR set in a single synthetic call instead of just the diffuse map. Default flow (diffuse-only) unchanged for back-compat.

**What landed (PR #189, merge commit `9ee5a7d`):**

- Two new bridge helpers: `_ambientcg_extract_pbr_maps(zip, dest_dir)` and `_polyhaven_pick_pbr_files(files, resolution, fmt)` — multi-map siblings of the existing single-map helpers. Same path-traversal safety (`os.path.basename` flatten) on the AmbientCG side; per-map format fallback (`png <-> jpg`) on the Polyhaven side so a mixed asset still resolves cleanly.
- New top-level orchestrator `_marketplace_import_multimap` handles the fan-out: resolve table → download/extract all maps → one `import_texture` call per canonical map. Color first so its failure surfaces before secondary maps.
- Canonical map set: `color`, `normal`, `roughness`, `ao`, `displacement`, `metalness`. Color is mandatory; other maps are best-effort and just absent from the response `maps` dict when the source doesn't ship them.
- Normal preference: `_NormalGL` / `nor_gl` (UE's OpenGL tangent-space convention) wins over `_NormalDX` / `nor_dx`. **Note**: PR #189 originally dropped DX entirely — bot-review (CodeRabbit Major) caught it and follow-up `33afae8` added DX fallback so DX-only assets resolve their normal map.
- New arg: `multi_map: bool = false`. Rejected for HDRIs and models (texture-only).
- New response fields (multi-map mode only): `maps` dict (`canonical → UE asset path`) and `import_results` dict (`canonical → native passthrough`). `ue_asset_path` pinned to `maps['color']` for back-compat.
- Naming in UE: Color stays at `<dest_name>` for back-compat; other maps land at `<dest_name>_<canonical>` (`<dest_name>_normal`, `<dest_name>_roughness`, etc.).
- Catalog kept in sync: bridge `TOOLS` list, `mcp_manifest.json`, `docs/TOOLS.md` all describe the new arg + response fields + multi-map example.

**Bot-review gate (rule #5 honored):**

- Greptile P1 inline: partial-import on mid-fan-out failure leaves orphaned UE assets — color imports, normal fails, retry double-fails on stale color. Applied: error response now includes structured `data` block with `failed_map`, `imported_so_far` (map → asset path), `remaining_maps`, and a recovery `hint`. Caller can delete the orphans or retry with `replace_existing=true`.
- Greptile P2: dead `_PBR_CANONICAL_MAPS` constant never referenced — iteration order is driven by the marker tables directly. Dropped.
- Greptile P2: unreachable `if canonical not in extracted_paths: continue` guard inside fan-out loop — `map_order` is constructed from `extracted_paths.keys()` by definition. Removed.
- CodeRabbit Major: DX-tangent normals dropped entirely from both marker tables. PR contract said GL *preferred over* DX, not GL-only. Added DX markers as fallback in both `_AMBIENTCG_MAP_MARKERS` (`_NormalDX` / `_normaldx`) and `_POLYHAVEN_MAP_KEYS` (`nor_dx` / `NormalDX`).
- CodeRabbit Minor: `docs/TOOLS.md` param wording said `multi_map` rejection was HDRI-only when the documented error contract correctly says texture-only. Tightened to "valid solely when `asset_type='texture'`; rejected for HDRIs and models".

Follow-up commit `33afae8` bundled all five bot-directed fixes plus three new regression tests (DX-only normal fallback on both backends + partial-failure `imported_so_far` shape). Mechanical-fix exception (CLAUDE.md rule #5) honored — same-branch surgical follow-up, no new logic, self-merge after CI green without second-pass bot review.

**Tool/test totals:**

- 102 tools (unchanged — PR #189 completes an existing tool's v2 promise, doesn't add a new one).
- pytest: 413 → **430** (+17: 9 helper-level unit + 4 e2e/validation + 1 partial-failure + 1 NormalGL-preference + 2 DX-normal fallback).
- Bridge coverage unchanged (~99%).
- 25 PRs in cumulative lineage (#161 → #189).

**Open follow-ups (carried forward from 22nd note, now reduced):**

- HDRI cubemap conversion (longlat → cubemap; no Python wrapper found in 5.7) — still parked.
- Sequencer keyframe authoring + Movie Render Queue — still attended-Codex C++ work.
- Host UE cold-rebuild for the 7 Wave A/A.5 C++ handlers — still pending; bridge-side schemas correct so MCP clients see all 102 entries, calls to the new C++ tools return `-32601` until rebuild.
- Local OSS LLM daemon empty-list bug — admin shell needed; pre-commit local-ensemble unavailable until fixed.
- `inspect_blueprint.blueprint_status` field — **closed** this session via grep: PR #183 already shipped it at `Handler_InspectBlueprint.cpp` line 79.
- v8 follow-ups list from 21st note: multi-map PBR — **closed by this PR**. AmbientCG zip-archive unpack — closed by PR #187. Two items remain (HDRI cubemap conversion, T1/T2/T3 reshoot under v7 textured lighting).

**Twenty-third consecutive closing-note.** Session 2026-05-15 still single-window — three PRs landed in sequence (#187 AmbientCG zip, #188 HANDOFF rotation, #189 multi-map PBR). Bot-review gate caught real bugs every time (orphan recovery, DX-normal coverage) — worth the latency. Tool count: 102. Standing rules: 5 (unchanged). Cadence intact.

---

## Session 2026-05-15 (live verification — PR #189 + closing the host-rebuild parked item)

Single-window verification pass. No new code shipped — all four feature PRs from earlier in the day (#187 / #188 / #189 / #190) already merged. This window drove a live test in UE 5.7 and confirmed the shape of what we shipped + closed a long-standing parked item.

**Live-test verified:**
- PR #189's `multi_map=true` path against four real CC0 assets through the full pipeline (catalog lookup → http download → extract → per-map `import_texture`):
  - AmbientCG `Marble012` (partial set: color/normal/roughness/displacement — no AO; partial-set handling worked)
  - AmbientCG `Travertine009` (full set, 5 maps)
  - AmbientCG `WoodFloor051` (full set, 5 maps)
  - Polyhaven `granite_tile` (full set via per-map URL fan-out, 5 maps)
  - Polyhaven HDRI `venice_sunset` (single-map / HDRI path)
- Total: 19 PBR textures + 1 HDRI landed in `/Game/Validation/Florence/`.
- Path-traversal safety, DX-normal fallback, partial-set handling, per-map format fallback all behaved as designed in the merged code.

**Parked item closed: 7 Wave A/A.5 C++ handlers carried since the 20th note as "needs host cold-rebuild" are now live.** Probed each over the bridge — `get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings` returned canonical result shapes; `pie_control` and `inspect_project_setting` returned `-32000 missing_required_field` (registered + reachable, missing args). The host plugin DLL was rebuilt at some point between the 20th note and now. **Tool count is 102/102 live**, not 95/102 as the 23rd note still claimed.

**Florence-plaza scene composed in UE:** 11 actors — granite plaza floor, marble dais, travertine walls (3) + columns (4), wood benches (2). 4 master materials wired live via `MaterialEditingLibrary` (Diffuse → BaseColor, Normal → MP_NORMAL with SAMPLERTYPE_NORMAL, Roughness → R into MP_ROUGHNESS, AO → R into MP_AMBIENT_OCCLUSION). SkyAtmosphere + atmospheric-sun-light directional + real-time-capture SkyLight + PostProcessVolume with histogram auto-exposure. Final hero screenshot saved at `docs/validation/florence-final-2026-05-15.png`. Composition scripts at `scripts/compose_florence_scene.py`, `scripts/rebuild_florence_clean.py`, `scripts/final_florence_lighting.py`, `scripts/polish_florence_shot.py`.

**UE 5.7 attribute gotchas pinned down (saved for next time):**
- `MaterialExpressionMultiply.const_b` / `MaterialExpressionClamp.min_default,max_default` — must use `set_editor_property`, NOT plain attribute assignment.
- `SkyLightComponent.cubemap` only accepts `UTextureCube`. A longlat-imported HDRI is `UTexture2D` and cannot drive the SpecifiedCubemap path. Workaround: `SLS_CAPTURED_SCENE` + `real_time_capture=True` against a `SkyAtmosphere` actor. The longlat → cubemap conversion is still the open follow-up from the 23rd note.
- `SkyLightComponent` has no `intensity_scale` attribute — use `set_intensity()` directly.
- `ExponentialHeightFogComponent.fog_inscattering_color` was renamed to `fog_inscattering_luminance` (and `directional_inscattering_color` → `directional_inscattering_luminance`).
- `unreal.Rotator` positional constructor order is `(roll, pitch, yaw)`. Always use keyword args.
- Polyhaven + AmbientCG AO maps ship as RGB JPGs; the texture sampler in a material must be `SAMPLERTYPE_COLOR`, not `SAMPLERTYPE_LINEAR_GRAYSCALE`, or the material silently falls back to default.
- `Material.expressions` (Python attribute) is protected. To enumerate nodes, use `MaterialEditingLibrary.get_material_expressions(mat)`.

**Tool/test totals (unchanged this window):**
- 102 tools, 102 live.
- pytest: 430 (no test changes this window).
- Bridge coverage unchanged.

**Remaining parked items after this window (now reduced):**
- HDRI longlat → cubemap conversion (still no UE 5.7 Python wrapper found — workaround via SkyAtmosphere + CapturedScene is good enough for now).
- Sequencer keyframe authoring + Movie Render Queue — still attended-Codex C++ work.
- Local OSS LLM daemon empty-list bug — admin shell needed.
- T1/T2/T3 reshoot under live textured scene — not done this window (time spent on multi-map validation + scene compose iterations); the Florence hero shot is the first artist-grade live capture of the post-v7 pipeline though.

**Twenty-fourth consecutive closing-note.** Session 2026-05-15 single window; verification-only, no merges. The bigger value of this window was that it cleared a parked item that had been load-bearing in three prior notes — the 7 C++ handlers are simply live now. Tool count: 102 live (corrected from 95). Standing rules: 5 (unchanged). Cadence intact.

---

## Session 2026-05-15 (PR #192 — convert_hdri_to_cubemap synthetic, closes 23rd-note longlat parked item)

User authorized "run until you finish all of that task" — single-window feature push closing the HDRI longlat→cubemap parked item carried since the 21st note.

**What landed (PR #192, merge commit `b682a53`):**

- New synthetic bridge tool `convert_hdri_to_cubemap` — wraps the canonical UE editor pipeline that has no direct Python converter in 5.7 vanilla: `SceneCaptureCube` against an inside-out unit sphere with the HDRI as an unlit emissive material, then `RenderingLibrary.render_target_create_static_texture_cube_editor_only` materializes the static `UTextureCube`.
- Tool count: 102 → **103** (71 C++ + 32 synthetic). Catalog mirrors in bridge `TOOLS`, `mcp_manifest.json`, `docs/TOOLS.md` all updated.
- Doc-count drift sweep across 11 files (102→103, 31→32 synthetic, 430→443 pytest, enumeration sentences extended).
- Args (validated): `hdri_path` (required, must start with `/Game/`), `dest_path` (optional, defaults to source folder), `dest_name` (optional, defaults to `<basename>_Cube`), `cube_size` (optional 16-8192, default 1024), `compression` (optional allowlist: `TC_HDR` / `TC_HDR_COMPRESSED` / `TC_HDR_F32` / `TC_DEFAULT`, default `TC_HDR`).
- Returns: `ok`, `source_hdri`, `cube_asset_path`, `dest_path`, `dest_name`, `cube_size`, `compression`.
- Live POC validated against Polyhaven `venice_sunset` — cube created at `/Game/Validation/Florence/HDRI_Venice_Sunset_Cube` before the PR opened.

**Bot-review gate (rule #5 honored; mechanical-fix exception applied):**

- CodeRabbit Major: capture source `SCS_FINAL_COLOR_LDR` → `SCS_SCENE_COLOR_HDR_NO_ALPHA`. LDR was tone-mapping + clamping HDR to 8-bit SDR — defeats the point of an HDR cubemap. **Critical fix** for fidelity.
- CodeRabbit Major: fixed temp asset names (`RT_HDRI_ToCube_Temp` / `M_HDRI_Sphere_ToCube_Temp`) → per-call `uuid4()[:12]` suffix. Concurrent calls no longer race; cleanup never touches pre-existing user content.
- CodeRabbit Major: wrapped RT/material/sphere/SCC/cube creation in `try/finally` with per-step guarded cleanup. One failure no longer strands the rest of the temp state.
- CodeRabbit Minor: `dest_path` validation tightened to exact `/Game` or prefix `/Game/`. Rejects `/GameFoo`, `/Gameplay/x`, `..`/`.` segments, backslashes.
- CodeRabbit Minor: TOOLS.md synthetic enumeration sentence — added `marketplace_search` + `marketplace_import` back to the list of 32 (count was correct but enumeration list was short).
- +5 regression tests for new validation + safety. Greptile: no findings.

Follow-up commit `1604cc7` bundled all five bot-directed fixes. Mechanical-fix exception (CLAUDE.md rule #5) honored — same-branch surgical follow-up, no new logic, self-merge after CI green.

**UE 5.7 API surface confirmed available (recorded for next time):**

- `unreal.SceneCaptureCube` (actor) + `unreal.SceneCaptureComponentCube` (component).
- `unreal.TextureRenderTargetCube` + `unreal.TextureRenderTargetCubeFactoryNew`.
- `unreal.RenderingLibrary.render_target_create_static_texture_cube_editor_only(rt, name, compression, mip_settings)` — 4-arg signature, NOT 5; the cube is created in the same package as the render target. **Compression enum must be passed as the enum member, not a string**.
- `SceneCaptureSource.SCS_SCENE_COLOR_HDR_NO_ALPHA` preserves HDR; the `SCS_FINAL_COLOR_*` variants tone-map and discard HDR range.
- `SceneCaptureComponentCube` in 5.7 dropped the `b_` prefix on Boolean properties — use `capture_every_frame` / `capture_on_movement`, not `b_capture_every_frame`.

**Tool/test totals:**

- 103 tools (71 C++ + 32 synthetic) — `+1` (`convert_hdri_to_cubemap`).
- pytest: 430 → **443** (+13: 8 initial coverage + 5 regression for bot-directed fixes).
- Bridge coverage unchanged (~99%).
- 26 PRs in cumulative lineage (#161 → #192).

**Remaining parked items after this window:**

- Sequencer keyframe authoring + Movie Render Queue — still attended-Codex C++ work; scoping touch happened this window but no code landed (risk of half-baked primitive > value).
- Local OSS LLM daemon empty-list bug — admin shell needed for Machine-scope env var or daemon upgrade.
- T1/T2/T3 reshoot under live textured scene — Florence hero shot from 24th-note window remains the first artist-grade live capture; expansion deferred.

**Twenty-fifth consecutive closing-note.** Session 2026-05-15 closing window. Three parked items cleared across this session's 5 merged PRs (#187 AmbientCG zip-unpack, #189 multi-map PBR, #192 cubemap converter, plus #188/#190/#191 handoff rotations; the host plugin DLL rebuild for the 7 Wave A/A.5 C++ handlers verified live in the 24th note). Tool count: 103 live. Standing rules: 5 (unchanged). Cadence intact.
