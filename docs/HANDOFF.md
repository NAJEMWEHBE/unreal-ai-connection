# Handoff document

Single source of truth for resuming work on Unreal AI Connection in a fresh session of any MCP-compliant client. Read this first; it captures everything carried in the prior session's working memory.

> Closing notes 1 through 36 (sessions 2026-05-09 through 2026-05-26) and the May 2026 open-work log are archived in [`HANDOFF-archive.md`](HANDOFF-archive.md). This file keeps the current state, the runbook and the latest closing notes.

---

## At a glance

*Updated 2026-11-05. Update this section whenever a session changes the state; the closing notes below are history.*

**What this is:** a UE 5.7 editor plugin plus a stdlib-only Python bridge that serves editor automation to any MCP client over `127.0.0.1:18888`. Each native tool is one C++ handler (`Handler_*.cpp`); the bridge adds synthetic tools composed from them. Optional companion plugins add DMX, Geometry Script, nDisplay and OCIO tools.

**Where it stands:**

- **Maintained again** since 2026-11-05 (the 2026-07-17 deprecation is reversed). Latest release: `v0.9.1` (2026-05-22); `CHANGELOG.md` `[Unreleased]` holds everything since.
- **Tools:** 150+; the exact counts are in the README Status row and `EXPECTED_*` in `tests/conftest.py`, checked against the code by `tests/test_catalog_sync.py`.
- **Engines:** UE 5.7 is the official, host-tested target; 5.6 was host-certified from source on 2026-05-25; other versions are best-effort through the ADR-0001 compat scaffold.
- **CI:** pytest on Python 3.11-3.14, ruff, the doc-drift sweep and a wheel build + console-script smoke test, on every PR.

## Open work

- **Audit fixes (2026-11-05):** three PRs from the repo review. Bridge and MCP protocol fixes (isError results, ping, busy-editor timeouts, frame errors, merged `list_tools`, annotations); docs, counts, agent docs, CI and packaging; C++ fixes that need a **host build on the UE 5.7 PC before merge** (CI never compiles C++).
- **Maintainer actions:** cut `v0.10.0`; upload the PyPI package and run `mcp-publisher publish` (`docs/DISTRIBUTION.md`); refresh the GitHub description, topics and homepage.
- **Roadmap:** `get_editor_state` (issue #288); the UE 5.8 toolset port (issue #290, `docs/PORT-SPEC/`); one generated catalog in place of the three hand-synced copies; UE Automation Specs for the pure-logic C++.

## Host build runbook

**Verification runbook** (6 steps, PowerShell, run on the user's host machine):

1. `cd <repo-root> && git pull origin main`  (`<repo-root>` = your local clone of `unreal-ai-connection`)
2. `taskkill /IM UnrealEditor.exe /F` (Live Coding holds the DLL otherwise; safe if UE isn't running). Or, with the module: `Import-Module .\scripts\UnrealAIConnection-Editor.psm1; Stop-UCMCPEditor`.
3. **Sync dev plugin → host plugin.** The host project's `Plugins/UnrealAIConnection/` may be a plain copy on this machine, in which case it drifts from the dev tree silently. Verify with `Get-Item "<host-project>\Plugins\UnrealAIConnection" | Select-Object LinkType` — a `Junction` or `SymbolicLink` value means it auto-tracks; empty means it's a plain copy and you must sync. To sync (always quote both paths — Windows project locations like `F:\ai\ax plug in\…` contain spaces):
   ```
   robocopy "<repo>\UnrealAIConnection" "<host-project>\Plugins\UnrealAIConnection" /MIR /XD Binaries Intermediate .vs /NFL /NDL /NJH /NJS /NP
   ```
   Robocopy exit codes 0–7 mean success. The `/XD Binaries Intermediate` exclusion preserves the host's UBT cache so step 4 stays incremental.
4. `& "<UE_INSTALL>\Engine\Build\BatchFiles\Build.bat" <HostProjectName>Editor Win64 Development -project="<full path to host .uproject>"` — must end with `Result: Succeeded`. The target is `<HostProjectName>Editor`, NOT `<PluginName>Editor`. For the canonical host project, that's `HDMediaVirtualStudioEditor`.
5. Open the host `.uproject` in UE editor (use the path-quoting recipe under Traps in `AGENTS.md`: pre-quote inside the `-ArgumentList` element). Confirm every C++ handler registers in the Output Log: filter by `LogUCMCPHandler` and count the `Registered handler '<name>'` lines against `EXPECTED_CPP_HANDLER_COUNT` in `tests/conftest.py` (companion plugins add their own when enabled). Bridge-side synthetic tools never register in UE.
6. **Smoke** — `py -3 examples\smoke_test.py --material-instance /Game/SmokeTest_MI --sequence /Game/SmokeTest_LS`. Then run the Wave A/A.5 live verification panel above.

**Pause/restart note (PR #174 scorecard follow-up #10):** Long verification runs that span an editor restart (manual or crash) lose every actor and edit that wasn't saved to the level. If you spawn validation actors or mutate the open map and intend to pause, run `save_dirty_assets {}` (or Ctrl+S in the editor) *before* the pause — unsaved actors and properties revert to the last on-disk state on relaunch. The PR #174 scorecard's `delete_actor` row hit this exact case (ValEnvPanelL/R spawned pre-pause, lost on restart, then returned `actor_not_found` on the post-resume delete — correct shape, not a tool defect, but easy to mistake for one).

---

## Operating directives the user has granted

These are explicit user instructions that override default Claude behavior. They have stayed in force across the entire prior session lineage.

1. **"Do everything"** — autonomous execution. Don't ask permission to proceed; pick a reasonable path and ship it. The user steps in only when they want to redirect.
2. **"Don't get hallucinated"** — every UE 5.7 API claim must be grounded in actual source (`<UE_INSTALL>/Engine/Source/...` or `<UE_INSTALL>/Engine/Plugins/...`). Cite line numbers in spec/commit messages. Past sessions caught real defects (`TC_BC4`, `TEXTUREGROUP_Bake`, `FStringOutputDevice`) by grounding before committing.
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
        Handler_*.cpp                          One native handler each (count:
                                               EXPECTED_CPP_HANDLER_COUNT in tests/conftest.py,
                                               checked by tests/test_catalog_sync.py).
                                               NOTE: wait_for_events / get_camera_transform /
                                               set_camera_transform / screenshot_actor / compile_mod_pak
                                               / compile_mod_pak_direct / bulk_* / inspect_data_asset /
                                               inspect_sound_class / inspect_sound_submix /
                                               inspect_audio_bus / inspect_material_function /
                                               inspect_metasound are SYNTHETIC (bridge-side) -- they
                                               do NOT have a Handler_*.cpp file.
    UnrealAIConnection.Build.cs                   Module deps.
  Resources/mcp_manifest.json                  Tool catalog (mirrors bridge TOOLS)
  UnrealAIConnection.uplugin                      Plugin manifest (v0.9.1 / UE 5.7)

bridge/
  unreal_ai_connection_bridge.py                  stdio↔TCP bridge.
                                               - SYNTHETIC_TOOLS dict (bridge-side tools).
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
  drift_sweep.py                               Doc-drift scanner: counts and versions in docs
                                               and shipped metadata. Runs in CI on every PR.

.mcp.json (gitignored)                         Local MCP client config; points at
                                               bridge/unreal_ai_connection_bridge.py.
AGENTS.md                                      Canonical agent context (Codex CLI, Copilot CLI,
                                               Cursor, Gemini CLI; CLAUDE.md imports it).
CLAUDE.local.md (gitignored)                   Per-machine paths and standing permissions.
.github/copilot-instructions.md                Copilot reviewer guidance.

tests/
  conftest.py                                  EXPECTED_TOOL_COUNT (+ CPP / SYNTHETIC splits).
                                               Single source of truth for every count.
  test_catalog_sync.py                         C++ GetMethodName() vs catalog, counts, TOOLS.md
                                               sections.
  test_packaging.py                            pyproject / server.json / .uplugin agree.
  test_bridge.py                               Bridge MCP protocol + schema tests.
  test_bridge_edge_cases.py                    Parametrized test_every_tool_routes_through_tools_call
                                               (excludes synthetic tools from round-trip assertion).
  test_manifest_sync.py                        Drift detection between bridge TOOLS and manifest.
  test_drift_sweep.py                          CI guard: runs scripts/drift_sweep.py + unit tests.
  test_no_personal_leaks.py                    CI guard: forbidden-pattern scan over tracked files.

docs/
  TOOLS.md                                     Per-tool params/results/examples (one section per tool)
  ARCHITECTURE.md                              How pieces fit; UE 5.7 API gotchas
  INSTALLATION.md                              Step-by-step install
  HANDOFF.md                                   This file (current state, runbook, latest notes)
  HANDOFF-archive.md                           Closing notes 1-36 (chronological, append-only)
  internal/                                    Old session handoffs, RESUME playbook, drafts
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
5. The fresh session reads this doc, takes the current state from **At a glance**, and proceeds.

For specific resumption:
- *"Live-verify Waves A + A.5"* → host rebuild via the runbook above, then run the Wave A/A.5 verification panel
- *"Continue with Wave B"* → Blueprint graph mutation (per the community-roadmap research in the 19th closing-note); attended-Codex work, do not auto-dispatch
- *"Run the multi-agent workflow"* → directive #9 + standing rule #1 + `memory/feedback_multi_agent_workflow.md`

---

## Closing notes from prior sessions

> **Note:** Closing notes 1 through 36 (sessions 2026-05-09 through 2026-05-26) are archived in [`HANDOFF-archive.md`](HANDOFF-archive.md). The latest notes stay here.

## Session 2026-07-17 (public deprecation — repo unmaintained; docs-only)

Ruling executed from the maintainer's estate-wide sweep (2026-07-17 decision — final): the project is **publicly deprecated**. Active development stopped 2026-07-03 with the maintainer's pivot to Epic's native plugin for this workflow; this session makes that status visible instead of implied. Docs-only — no `.cpp/.h/.Build.cs` edits, no rebuild.

**What shipped:** README deprecation banner at the very top — unmaintained; last-known-good UE 5.7 official (source build) + UE 5.6 via the prebuilt `v0.9.1-ue5.6` release; other versions best-effort via the kept ADR-0001 compat scaffold; the `BuildConfiguration.xml` `<CompilerVersion>` pin **stays cleared** (the 36th note's carry-forward becomes the permanent end-state — UE ≤5.3 source builds require a self-managed re-pin); no new build promises; MIT forks welcome. Draft PR #278 (`[WIP] audio_analyze_and_place`, Wave 3) closed as **abandoned** with an explicit comment — the Wave-3 prep stays banked on `feat/wave3-audio-analyze`, unmerged by design, available to any fork. Local merged branches `feat/progressive-tool-disclosure` (merged as PR #280) and `refactor/bulk-dispatch-helper` (merged as PR #289) deleted.

**Thirty-seventh consecutive closing-note — the closing one.** Session 2026-07-17: public deprecation executed. README banner shipped; PR #278 closed as abandoned (Wave-3 work banked on its branch); 2 merged local branches deleted; toolchain pin remains cleared as the permanent state. No code touched. Public counts per README at close: 151 tools, 645 tests (badges). Standing rules: **5**. The cadence ends here — the repo is unmaintained; a 38th note exists only if the project is revived.

---

## Session 2026-11-05 (revival: maintenance resumes)

Najem reversed the 2026-07-17 deprecation: the project is maintained and improved again. The README banner is removed; Status gains a **Maintenance** row (active since 2026-11-05) and the UE ≤5.3 toolchain note moves into "Roadmap / status honesty". Earlier the same day: `ruff check` + `ruff format --check` joined CI (PRs #307, #308; `.git-blame-ignore-revs` skips the format commit; ruff pinned 0.17.x). A full repo review ran the same day; its findings live outside the repo, in Najem's workspace audit, and land here as issues or PRs once Najem picks them.

**Thirty-eighth closing note: the revival.** The cadence resumes. Open issues #288 and #290 (UE 5.8 toolset port spec) are live again. Public counts per README: 151 tools, 645 tests. Standing rules: **5**.

---

## Session 2026-11-05 (audit fixes)

A full repo review (2026-11-05) produced 27 findings; three PRs fix them. Bridge: tool failures come back as `isError` results, `ping` answers, a busy editor gets its own timeout error (-32098 `ue_busy_timeout`, `UCMCP_TIMEOUT` sets the wait), bad frames and non-object params answer cleanly, `list_tools` merges the bridge-side tools, screenshots arrive as image content, tools carry annotations. Docs: the 4 DMX tools are advertised, counts have one source (`tests/conftest.py`, tied to the C++ by `test_catalog_sync`), the drift sweep covers the shipped metadata, CI runs on every PR and builds the wheel, `AGENTS.md` is canonical and machine paths moved to a gitignored `CLAUDE.local.md`. C++ fixes wait for a host build.

**Thirty-ninth closing note.** Standing rules: **5**.
