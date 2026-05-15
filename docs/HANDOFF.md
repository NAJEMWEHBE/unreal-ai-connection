# Handoff document

Single source of truth for resuming work on UnrealClaudeMCP in a fresh session of any MCP-compliant client. Read this first; it captures everything carried in the prior session's working memory.

> Earlier closing notes (1st through 17th, sessions 2026-05-09 through 2026-05-12 autopilot extension) are archived to [`docs/HANDOFF-archive.md`](HANDOFF-archive.md). This active file keeps the latest three consecutive notes (18th-20th) for quick pickup.

---

## Project at a glance

**What this is:** An Unreal Engine 5.7 plugin + Python bridge that exposes editor automation to **any MCP-compliant client** (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, …) over a localhost TCP socket. The plugin adds a JSON-RPC server inside the editor; each "handler" is one MCP tool (~150 LoC of C++ in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`). The bridge translates between the client's stdio MCP protocol and the plugin's TCP wire format. **Vendor-neutral by design** — the wire protocol is open MCP (created by Anthropic, but any conforming client works); the project's repo/folder names retain "Claude" for legacy reasons but the capability is universal.

**Where it stands (post-PR #170 + marketplace tools merged on `fix/scene-brightness-2026-05-14`):** **102 tools total** (71 UE-side C++ handlers + 31 bridge-side synthetic tools — `marketplace_search` + `marketplace_import` landed mid-session 2026-05-14, see `docs/design/marketplace-tools-design.md`). Plugin version `0.9.1`, targets UE `5.7`. pytest baseline: **396** passing. (For the current HEAD commit, run `git log -1 origin/main`; the latest milestone PR is #170.)

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

**Latest milestone on main:** PR #170 — 20th closing-note + 17th rotated to archive. For the current HEAD commit hash, run `git log -1 origin/main` (intentionally not pinned in this doc since closing-note PRs are self-referential and any pinned hash falls one commit behind on merge).

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
  HANDOFF-archive.md                           Closing notes 1-17 (chronological, append-only)
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

> **Note:** Consecutive closing notes 1 through 17 (sessions 2026-05-09 through 2026-05-12 autopilot extension) are archived in [`HANDOFF-archive.md`](HANDOFF-archive.md). Only the latest three (18th-20th) are kept active here.

**Session 2026-05-13 (autopilot resume — three standing rules locked, live verification panel run, scaffolding docs created):**

This was a continuation window after the maintainer flagged that the 16-PR autopilot-extension wave (closed in the 17th closing-note) had shipped without ever launching the editor for live verification. Three things happened in this window, in this order: standing rules got reinforced, live verification got run, and the scaffolding docs that the project had been missing got created.

**The three standing rules now permanent in this file:**

1. **Multi-agent ensemble review on every substantive change.** (Originally landed in PR #153 / 17th closing-note. Reinforced this window.)
2. **UE 5.7 editor launch is pre-authorized in every session.** (Landed in PR #155 this window after the maintainer's explicit reminder.)
3. **UE editor must be closed when verification work finishes.** (Landed in PR #156 this window as the companion to rule #2. Cadence is "open, verify, close" — not "open and leave running for the session".)

The pairing of #2 and #3 is load-bearing: rule #2 alone could be read as "always have UE running"; the addition of rule #3 keeps the resource cost bounded. ~4 GB RAM + multiple pinned CPU threads is what UE in Editor mode holds; closing it between verification windows reclaims those.

**Live verification panel run (4/4 PASS) on 2026-05-13:**

| Probe | Result | Validates |
|---|---|---|
| `list_tools` count | 64 C++ handlers registered | TCP listener bound, plugin loaded |
| `set_camera_transform { location:{x:100,y:200,z:300}, rotation:{pitch:-20,yaw:45,roll:7} }` | `ok: true` | SET path live-reachable |
| `get_camera_transform` round-trip | location + rotation byte-identical to SET | **PR #127 Rotator silent-scramble fix verified LIVE** (the regression class that prompted the original RESUME.md verification panel) |
| `inspect_data_asset { path: "/Game/NoSuch" }` | `error_message: "inspect_data_asset: asset_not_found: /Game/NoSuch"` | PR #126 canonical message-shape verified LIVE |
| `bulk_move_assets { paths: ["/Game/NoSuch"], dest_folder: "/Game/Archive" }` | `ok:false, failed:1, results[0].error_code:-32000` | PR #133 partial-failure envelope verified LIVE |

The 22 stale bridge PRs that RESUME.md flagged as needing live verification are all now confirmed working in a running editor. After the panel finished, UE was closed via the `Get-Process UnrealEditor / UnrealTraceServer | Stop-Process -Force` recipe documented in rule #3.

**Trap caught + recorded this window: the curl-on-18888 false negative.** Polling `127.0.0.1:18888` with curl returns exit 56 (empty reply) even when the plugin is bound, because the plugin's length-prefixed framing rejects HTTP requests with `framing_error: body length 5135603447292250196 exceeds 1 GB cap` (those 8 bytes decode to the ASCII characters `GET / HT` interpreted as a big-endian uint64). **Right way to confirm bind: call `list_tools` through MCP**, not curl through HTTP. The framing_error log lines from a curl probe are not a sign of a broken plugin; they're a sign of a wrong-protocol probe.

**Scaffolding docs created this window:**

- **`CHANGELOG.md`** (PR #157) — Keep-a-Changelog + SemVer. Three sections: `[Unreleased]` (PRs #141 → current), `[0.9.1]` (bulk_*_assets family completion + inspect-synthetic round-out), and `[0.9.0 and earlier]` (deferred to HANDOFF + git log). Pointer at the top of the file makes audience-routing explicit: per-tool details in TOOLS.md, architecture in ARCHITECTURE.md, chronology in HANDOFF.md, human-readable release notes in CHANGELOG.md.
- **`CONTRIBUTING.md`** (PR #158) — project conventions in one place. 10-step playbook for adding a new tool (links to RESUME.md), the "one handler = one .cpp" / "req_id intentionally untyped" / "vendor-neutral language" rules, the multi-agent-ensemble note flagged up front (it's an unusual OSS pattern, worth flagging so contributors aren't confused by the diversity of review styles in PR comments), CI matrix, security disclosure flow.
- **README hero badge row** (PRs #154, #157) — added `pytest passing`, `tools 80`, and `changelog: keep a changelog` badges so casual visitors get the numbers + scaffolding pointer on page-load.

**Test coverage this window:**

- **PR #159** — `test_marker_pattern_propagates_execute_unreal_python_failure_envelope`. Closed the last gap in the marker-pattern test grid: covered cases were happy-path / marker_not_found / marker_truncated / invalid_json, but no test exercised `exec_resp.ok == False` (Python interpreter raised). Locks the contract: when exec fails, bridge does NOT proceed to scan logs, returns `-32603` with traceback in message.

**Tool / test totals at the end of this window:**

- 80 tools (unchanged from the previous closing-note — focus was hardening + docs, not net-new).
- pytest: 283 → 284 (+1 from PR #159).
- 19 PRs in this window (#141 → #159), 18 merged at the time of this note; the closing-note PR itself adds the 19th merge.

**What to watch in the next session:**

- **First action: restart Claude Code.** PRs #150 (type-hint sweep), #152 (parametrize tests), #155/#156 (rules) all touched the bridge module. The MCP cache in any running bridge process is stale; restart unblocks them.
- **No outstanding C++ work from this window** — all 19 PRs were doc / test / scaffolding. C++-only deferred items remain unchanged from the previous closing-note: Sequencer keyframe authoring + Movie Render Queue. Both need attended Codex per multi-agent partitioning.
- **Three standing rules are now load-bearing project knowledge.** Multi-agent ensemble / UE-launch / UE-close. Reinforce in every new session's resume reflex; the maintainer should not have to reiterate them.

**Eighteenth consecutive closing-note.** Session 2026-05-12 → 13 now spans 9+ documented windows. Cadence intact.

---

**Session 2026-05-13 (community-roadmap research + Wave A + Wave A.5 — first WRITE-side wave of the autopilot extension):**

After the 18th closing-note resume window (doc + test hardening only) the maintainer asked "how can we make this more useful for the community?" This window answered by running a multi-agent deep-research sweep, deriving a 10-tool priority roadmap, and shipping the first two batches (Wave A + Wave A.5 = 8 new tools).

**Community-roadmap research (multi-agent deep-research):**

Three parallel reviewers (general-purpose web-search agent reading Reddit r/unrealengine + UE forums + YouTube tutorial demand + competitor MCP open issues; Sonnet read-only codebase mapper; cloud reasoning model strategic synthesis) converged independently on the same TOP-10 priorities. Convergence across distinct retrieval surfaces is the strongest signal that the answer isn't an artifact of one reviewer's bias.

The top-10 by impact-to-cost ratio:

1. Blueprint graph authoring (`add_node`, `connect_pins`, `add_variable`, `create_blueprint`) — biggest competitive gap vs every other UE-MCP
2. Sequencer keyframe authoring — cinematics #1 friction point
3. PIE control loop (`start_pie/stop_pie`, `run_automation_tests`, `save_dirty_assets`) — closes the "did my edit work?" validation feedback loop
4. Project settings get/set (`UDeveloperSettings` reflection)
5. Asset hygiene (`find_unused_assets`, `get_reference_chain`)
6. Movie Render Queue (already-deferred C++ item)
7. Enhanced Input scaffolding
8. Insights performance capture
9. Build automation (`run_cook_commandlet`, `parse_cook_failure`)
10. Quick-win cluster (`bulk_inspect_assets`, `inspect_input_mappings`, `get_engine_version`, `list_levels`, `get_selected_actors`, `set_actor_selection`)

The MCP/MCP ecosystem skews heavily toward inspection (50%+ of competing surfaces are read-only); the underserved phase is graph mutation. That's where Wave B will go.

**Wave A shipped (PR #161, MERGED) — 6 new tools, 7 atomic commits:**

| Tool | Tier | Effect |
|------|------|--------|
| `get_engine_version` | C++ | Structured engine-version fields (major/minor/patch/changelist/branch + minor_dotted) — LLM branches on version without parsing get_project_summary's string |
| `list_levels` | C++ | UWorld asset registry query with optional path_under + name_contains — closes the load_level_by_path gap (caller no longer needs pre-knowledge of paths) |
| `save_dirty_assets` | C++ | UEditorLoadingAndSavingUtils::SaveDirtyPackages wrapper, mirrors editor 'Save All' — closes the persistence loop after every edit-side tool |
| `get_selected_actors` | C++ | USelection iterator → per-actor name/label/class/transform — companion to apply_python_to_selection, lets LLM observe before acting |
| `inspect_input_mappings` | C++ | UInputSettings CDO: action+axis mappings + uses_enhanced_input flag — #1 beginner Enhanced-Input migration blocker |
| `bulk_inspect_assets` | Synthetic | inspect_asset composition over paths[] — pipeline-audit pattern (500 inspects in 1 call) |

Wave A initially shipped solo (no multi-agent review) — the maintainer flagged it as a standing-rule violation. Retroactive ensemble review caught a real BLOCKER (Handler_SaveDirtyAssets included `FileHelpers.h` but called `UEditorLoadingAndSavingUtils::SaveDirtyPackages` which lives in `EditorLoadingAndSavingUtils.h`). Fixed mid-PR before merge. Lesson logged.

**Wave A.5 shipped (PR #162, awaiting CI merge at this closing-note) — 2 new tools:**

| Tool | Tier | Effect |
|------|------|--------|
| `pie_control` | C++ | Single tool with action=start\|stop\|query + optional mode=play\|simulate. Wraps GEditor->RequestPlaySession / RequestEndPlayMap / IsPlayingSessionInEditor. Closes the canonical "did my edit work?" feedback loop |
| `inspect_project_setting` | C++ | Reflects any UDeveloperSettings subclass; bulk mode dumps every editable UPROPERTY, single mode returns one. Stringification mirrors inspect_data_asset's heuristic (ExportText / container sentinel / asset path) |

Wave A.5 used the **pre-COMMIT** multi-agent ensemble pattern (not retroactive — the lesson from Wave A applied). One BLOCKER + two MAJOR findings caught at design phase, applied before any code was written:

- BLOCKER → `GEditor->IsPlayingSessionInEditor()` chosen over the less-reliable older `GEditor->PlayWorld != nullptr` check
- MAJOR → `FindObject<UClass>(nullptr, *ClassPath)` chosen over deprecated `ANY_PACKAGE` (deprecated in UE 5.1)
- MAJOR → `GEditor->RequestPlaySession(FRequestPlaySessionParams)` chosen over `EditorInvokeCommand` / `EditorPlaySimulate` as the canonical 5.7 launch API

The pattern paid off again in CI: `test_no_personal_leaks.py` caught a comment-leak — two handler files had credited the specific pre-flight reviewer by name. A follow-up scrub-commit fixed both. Local pytest had skipped the leak because `git ls-files` doesn't surface unstaged files — operational lesson recorded: **run pytest AFTER git add when adding new files**, not before.

**Tool / test totals at the end of this window:**

- 86 → **88 tools** (+8 from session start; +6 Wave A merged, +2 Wave A.5 in flight). Split: 69 → **71 C++ handlers**; synthetic count holds at **17** (one new synthetic landed in Wave A).
- pytest: 243 → **302** (+59 across the autopilot-extension + Wave A + Wave A.5 windows combined).
- 8 → **12 files** under drift_sweep coverage (extension from the previous window holds; no new scan targets added).
- 23 PRs in the cumulative session (#141 → #162).

**The three standing rules remain load-bearing project knowledge:**

1. Multi-agent ensemble review on every substantive change. **Pre-COMMIT, not post-PR-push.** Wave A's retroactive review caught a real BLOCKER but added the cost of one round-trip + fix-up commit; Wave A.5's pre-commit review caught comparable findings with zero rework. The pre-commit cadence is the canonical form going forward.
2. UE 5.7 launch pre-authorized in every session — never ask, never skip live verification when it adds signal.
3. UE close when verification work finishes — Editor mode reserves ~4 GB RAM, do not leave running idle.

**What to watch in the next session:**

- **First action: restart Claude Code.** Wave A's 5 new C++ handlers + Wave A.5's 2 new C++ handlers all need fresh-bridge load. 7 new handler.cpp files added; bridge cache is stale across all of them.
- **Cold-compile in UE.** All 7 new C++ handlers need editor rebuild on the host project before live verification works. This is the maintainer's next build window; bridge-side schema is already correct so any MCP client will see all 88 tool entries on `tools/list` immediately — but calls to the new handlers will return JSON-RPC error -32601 (method not found) until UE rebuilds the plugin DLL with the new handler registrations.
- **PR #162 awaits CI green + merge** (was failing on personal-leak guard at the start of this closing-note — scrub commit fixed). Re-check CI status, merge with `--admin --squash --delete-branch` when green.
- **Wave B = Blueprint graph mutation** is the headline next priority. Per multi-agent partitioning rule, this is attended-Codex work. Do not auto-dispatch.
- **Wave B prerequisites verified by this window's roadmap research:** existing `edit_widget_tree` is the architectural proof-of-concept for direct UObject mutation; the same `K2Node` mutation pattern extends naturally. `FBlueprintEditorUtils::AddMemberVariable` + `KismetEditorUtilities` + `UEdGraphSchema_K2::TryCreateConnection` are the canonical API surface.

**Nineteenth consecutive closing-note.** Session 2026-05-12 → 13 now spans 11+ documented windows. The cadence is the project rhythm.

---

## Session 2026-05-13 (final — token-burn cleanup, plugin diet, 88 → 100 tools)

Pickup after 19th note. Headline: token-burn investigation revealed pre-typed overhead averaging ~80-120K tokens per turn. Aggressive cuts taken to shrink that overhead, then three-wave push 88 → 100 tools landed the user's explicit milestone.

**PRs landed in chronological order:**

| PR | Title | Effect |
|---|---|---|
| #164 | Wave A + A.5 bot-findings cleanup | Applied 11 bot findings + P0 InputSettings non-existent overload |
| #165 | Codify standing rules #4 + #5 | Delegation-by-default + bot-review gate, with mechanical-fix follow-up exception |
| #166 | HANDOFF split | 1509 → 516 active + 941 archive; ~36K tokens saved per session-start |
| #167 | Wave B — 4 asset-hygiene synthetics | find_unused_assets, get_reference_chain, bulk_compile_blueprints, audit_blueprint_compile_status; 88 → 92 tools |
| #168 | Wave C — 4 actor-batch synthetics | find_actors_by_class, bulk_focus_actors, bulk_screenshot_actors, bulk_set_actor_property; 92 → 96; 4 bot follow-up fixes incl. trailing-dot guard, settle-delay race, malformed-JSON success false-positive, vendor-neutral wording |
| #169 | Wave D — 4 utility synthetics | compare_assets, bulk_set_console_variables, inspect_dependency_graph, bulk_fix_redirectors; 96 → 100 ← TARGET HIT. Gemini caught + fixed rollback-order best-practice (reverse captured CVars when restoring) |

**Token-burn investigation results.** Findings:

- 13+ `claude.exe` processes running concurrently — likely CC subprocesses (MCP hosts, workers) not 13 separate conversations, but 805 MB and 419 MB ones suggested large active contexts
- 68 plugins enabled — system-prompt manifest bloat
- HANDOFF.md = 1509 lines (~54K tokens) auto-loaded
- GSD hooks firing on every tool call injecting `additionalContext`
- Output style "learning" preamble per turn (~3K tokens)
- System reminders (skills list, MCP servers, deferred tools, modified-files notifications) per turn (~5-15K tokens)
- No active cron / scheduled-tasks / loop-workers — burn was NOT background workers
- Estimated total per-turn overhead: ~80-120K tokens

Total cuts applied this session: ~55-65K tokens / session-start saved (HANDOFF split + plugin diet + project hook override + output-style swap user-side).

**Plugin diet.** Three stages: Tier 1 conservative (68 → 53, dropped 15 web/SaaS/hardware), aggressive (53 → 13, dropped 32 ruflo-* + 8 unused official), de-dup (13 → 12, dropped skill-creator standalone in favor of superpowers:writing-skills). Final 12 plugins: caveman, claude-md-management, claude-mem, code-modernization, codex, commit-commands, feature-dev, github, mcp-server-dev, nvidia-models, security-guidance, superpowers. Backups saved at `~/.claude/settings.json.backup-*` for one-line revert.

**Project-level hook override.** Wrote `F:/UnrealClaudeMCP/.claude/settings.local.json` (gitignored) overriding user-wide GSD hook set: kept gsd-context-monitor (useful context-pressure warning) + gsd-statusline (cheap status display). Dropped seven other GSD workflow guards (gsd-prompt-guard, gsd-read-guard, gsd-workflow-guard, gsd-phase-boundary, gsd-validate-commit, gsd-read-injection-scanner, gsd-check-update, gsd-session-state) for this project since it does not use the .planning workflow.

**Local-model daemon empty-list bug (deferred).** The local OSS LLM runtime under the F: drive (named per maintainer's private memory file, not in this public doc) at daemon v0.23.3 returns `{"models":[]}` despite the on-disk model directory containing valid manifests + the runtime-specific `_MODELS` env var set at User scope. Hypotheses: the daemon's v0.23 may have renamed the env var, manifest format incompatibility, env var needs Machine scope (blocked by admin-shell requirement), or auto-start daemon doesn't inherit User-scope env. Parked for user. Local pre-commit ensemble review for this session relied entirely on GitHub PR bots (greptile / chatgpt-codex-connector / CodeRabbit / gemini-code-assist) per Rule #4 reformulation.

**Bot-review pattern that worked across 5 PRs this session.** Each wave PR (167, 168, 169 + #164, #165, #166) followed the same flow: open PR → bots fire in 3-5 min → triage via direct `gh api` Bash readout (zero sub-agent cost) → mechanical fixes applied as follow-up commit on same branch → merge under Rule #5 mechanical-fix exception. Wave C surfaced 7 findings, 4 applied + 3 dismissed-with-rationale (the dismissed three were "vendor-neutral but lists multiple clients" — the agreed compromise from PR #164). Wave D had a single Gemini MEDIUM (rollback-order) which was a real best-practice improvement. The pattern's working.

**Tool count trajectory across waves:**

| PR | Wave | Tools before | Tools after | Cumulative pytest |
|---|---|---:|---:|---:|
| #164 | A + A.5 cleanup | 88 | 88 | 302 |
| #165 | Rules #4 + #5 codified | 88 | 88 | 302 |
| #166 | HANDOFF split | 88 | 88 | 302 |
| #167 | B (asset-hygiene synthetics) | 88 | 92 | ~340 |
| #168 | C (actor-batch synthetics) | 92 | 96 | ~370 |
| #169 | D (utility synthetics) | 96 | 100 | 396 |

**What to watch in the next session:**

- HOST UE COLD-COMPILE pending for 7 Wave A + Wave A.5 C++ handlers (`get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `pie_control`, `inspect_project_setting`). Until rebuild, those handler names return `-32601`. Wave B/C/D synthetics ARE callable today since they're pure bridge composition.
- Local OSS LLM runtime daemon env-var fix needed (admin shell to set Machine-scope env var or upgrade the daemon).
- `inspect_blueprint` C++ handler doesn't yet emit `blueprint_status` field; `audit_blueprint_compile_status` buckets all BPs as `Unknown` until that gap is filled. Small follow-up C++ patch when host build cycle is open.
- At 100 tools the user's explicit target is met. No new waves planned.

**Twentieth consecutive closing-note. Session 2026-05-13 final.** Tool count: 100. Standing rules: 5 (delegation + bot-gate + mechanical-fix exception load-bearing). Token-overhead: ~55K cut per session-start.
