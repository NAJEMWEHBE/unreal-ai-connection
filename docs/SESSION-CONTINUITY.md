# Session continuity — operating model + photo→Unreal validation plan

> **Audience:** any agent (or person) picking up work on this repo cold, after the previous session ended or the environment was reset.
>
> **Purpose:** capture (1) how the multi-agent collaboration on this project actually runs, and (2) the next concrete validation milestone — a real photo turned into an Unreal output, used to exercise all 102 MCP tools. Both pieces in one durable file so nothing has to be re-derived from chat history.

---

## Part 1 — Project state snapshot

| Field | Value |
|---|---|
| Tool count | **102** (71 native C++ handlers + 31 bridge-side synthetic tools) |
| Plugin version | `0.9.1` |
| UE target | `5.7` |
| pytest baseline | **396** passing |
| Latest landed PR | **#172** — README interactive upgrade (TOC + sequence diagram + collapsibles + MCP pitch). HANDOFF.md tracks the latest *closing-note* milestone (#170) separately; the two metrics intentionally diverge. |
| Source of truth | `tests/conftest.py` `EXPECTED_TOOL_COUNT` / `EXPECTED_SYNTHETIC_TOOL_COUNT` |
| Live HEAD | run `git log -1 origin/main` (intentionally not pinned in docs) |

For the live "at a glance" detail, see `docs/HANDOFF.md` (line 9 onward). For per-tool schemas and examples, see `docs/TOOLS.md`.

---

## Part 2 — Operating model (the captain-and-fleet pattern)

The main thread (Opus) acts as **leader / integrator / decision-maker only**. Every concrete work step is delegated to a sub-agent. The main thread never does work a sub-agent can do; it reserves itself for orchestration + synthesis + final calls.

**Reason:** the maintainer's session-token budget is the constraining resource. Sub-agent runs are billed separately from the main turn's context, so delegating large reads / writes / explorations keeps the main thread's context lean across many turns. Sub-agents are the fleet; the main thread is the captain.

### Standing rules (5, all load-bearing)

Canonical text lives in the **"Standing rules"** section of `docs/HANDOFF.md` (line numbers shift as the doc evolves; reference by section name). Quick summary:

1. **Multi-agent ensemble review** on every substantive change. Pre-COMMIT, not post-PR-push. Local OSS LLM runtime + GitHub PR bots primarily; LLM sub-agents reserved for escalation.
2. **UE 5.7 editor launch is pre-authorized** in every session. Path-quoting recipe documented; do not ask permission each session.
3. **UE editor must be closed when verification work finishes.** Cadence is "open, verify, close" — not "open and leave running for the session."
4. **Delegation-by-default** (token conservation). Routing table in the "Delegation-by-default" bullet of `docs/HANDOFF.md`'s "Standing rules" section.
5. **Bot-review gate before any merge.** Apply or dismiss-with-reason. **Mechanical-fix follow-up exception** (reconciles with directive #7): when a follow-up commit on the same branch applies bot findings as direct surgical fixes (no new logic), self-merge is permitted without waiting for a second-pass bot review.

### Operating directives (1–11)

Canonical text in the **"Operating directives"** section of `docs/HANDOFF.md` (reference by section name; line numbers shift over time). Highlights:

1. **"Do everything"** — autonomous execution; pick a reasonable path and ship.
2. **"Don't get hallucinated"** — every UE 5.7 API claim grounded in actual source (`F:/UE_5.7/Engine/Source/...`) with header.h:line citations.
3. **"Use the right tool for the job"** — Python or C++ as fits.
4. **"After every PR, check bot comments, then merge yourself"** — refined by directive #7 + rule #5.
5. **"Make them all"** — when a multi-bundle plan is authorized, push through all of them.
6. **Trap-table awareness** — known UE 5.7 API surface gotchas in HANDOFF.md.
7. **Ship optimistically for mechanical PRs** — read bot reviews post-merge for low-risk doc-only / test-only PRs; mechanical-fix exception explicitly carves this out.
8. **Repo file map awareness** — handler-per-file, single registration line per tool.
9. **Multi-agent fleet expanded** — Codex CLI (C++ specialty), read-only Explore sub-agent (one PR ahead, API research), parallel code-reviewer sub-agent (pre-merge), main thread (final synthesis + integration).
10. **Vendor-neutral framing** in any user-facing copy — no baked-in "Claude Code" specifically.
11. **Standing rules supersede directives where they conflict** — rule #5's bot-review gate overrides directive #4's "merge yourself" if a finding is unresolved.

---

## Part 3 — Agent fleet

| Slot | Role | When to dispatch |
|---|---|---|
| Main thread (host LLM agent) | Orchestrator + final reviewer + integrator | Always. Never delegates "decide" — only "do." |
| `general-purpose` sub-agent | Multi-file edits, exploration with writes, synthesis of long reads | Code/doc writes spanning 3+ files; bot-review readout summaries; memory-file writes |
| `Explore` (read-only sub-agent) | Recon / mapping / inventory passes | Plan-mode Phase 1; pre-flight before complex changes |
| `codex-rescue` (cloud reasoning agent) | Adversarial UE 5.7 API audit | Diff-review of C++ handlers; trap-hunting against UE 5.7 source on disk |
| `feature-dev:code-reviewer` (parallel reviewer sub-agent) | Parallel pre-commit review (escalation) | When local OSS LLM unavailable and a diff is high-stakes |
| Local OSS LLM runtime | Free pre-commit ensemble review (deferred) | Once daemon env-var bug is fixed; primary pre-commit reviewer |
| NVIDIA cloud reasoning models | Trap-hunting, reasoning ensemble for high-stakes diffs | Cross-check critical UE API claims; second opinion on architectural diffs |
| Codex CLI (cloud) | C++ implementation specialty | New C++ handler writes; UE 5.7 API verification |
| GitHub PR bots | Post-push review gate (per rule #5) | Every PR; bots fire automatically on push |

### GitHub bot roster (5)

- **Gemini auto-review** — `gemini-code-assist[bot]`. Daily quota; can be exhausted.
- **CodeRabbit** — `coderabbitai[bot]`. Walkthrough + inline; sometimes "review skipped" for docs-only PRs.
- **chatgpt-codex-connector** — Codex GitHub bot, P0/P1/P2 badge system; strong on C++ API claim verification.
- **greptile-apps** — diff-aware reviewer; surfaces P0/P1/P2 findings (three tiers, no P3). Newest member of the roster.
- **GitHub Copilot CLI** — `gh copilot` for diff explanation pre-merge.

### When to use which (decision tree)

- New C++ handler? → Codex CLI for code; codex-rescue for pre-flight API audit; pre-COMMIT ensemble (local OSS LLM + general-purpose sub-agent review); post-push GitHub bots.
- New bridge-side synthetic? → general-purpose sub-agent writes; main thread reviews; post-push GitHub bots.
- Bug fix < 50 lines? → main thread directly; post-push bots gate the merge.
- Doc-only change? → main thread directly; post-push bots gate (mechanical-fix exception often applies on follow-ups).
- Multi-file mechanical refactor? → general-purpose sub-agent (or `caveman:cavecrew-builder` if ≤2 files).
- Bot-review readout? → direct `gh api` Bash readout (zero sub-agent cost). Delegate only if findings are numerous and need summarization.

---

## Part 4 — Task management

### TodoWrite usage

- Create a todo list when the task has 3+ distinct steps.
- One item in `in_progress` at a time. Mark complete immediately on finish (don't batch).
- Remove stale items; never let the list drift from reality.
- The user sees the list — keep titles human-readable.

### Plan mode workflow

When the user invokes plan mode (or asks "plan it first"):

1. **Phase 1** — up to 3 Explore agents in parallel for codebase mapping. **Read-only.**
2. **Phase 2** — 1 Plan agent (optional for trivial tasks). Validates approach.
3. **Phase 3** — review agent output; read critical files; AskUserQuestion only for ambiguity.
4. **Phase 4** — write final plan to the harness-supplied plan file. Concise but executable.
5. **Phase 5** — call `ExitPlanMode`. Turn must end with that or `AskUserQuestion`.

Never use `AskUserQuestion` to ask "is this plan OK" — that's exactly what `ExitPlanMode` does.

### Auto mode

User triggers with phrases like "go autopilot" / "don't ask, don't come back" / "you decide." Behavior:

- Execute immediately; make reasonable assumptions on low-risk work.
- Prefer action over planning unless the user explicitly invokes plan mode.
- Anything destructive (delete data, modify shared/production systems) still requires explicit confirmation.
- Push to feature branches; merge under Rule #5; never blind-merge.

---

## Part 5 — Review gate (Rule #5 in detail)

### Pre-merge protocol (per PR)

1. Push to feature branch (`gh push -u origin <branch>`).
2. Wait 1–5 minutes for bots to fire. Use a background `sleep N` Bash so the harness fires a notification when it's safe to recheck.
3. Pull all reviews + inline comments via direct `gh api` Bash:
   - `gh api repos/.../pulls/<N>/reviews`
   - `gh api repos/.../pulls/<N>/comments`
   - `gh api repos/.../issues/<N>/comments`
4. Triage each finding:
   - **APPLY** as a follow-up commit on the same branch (preferred for small fixes), OR
   - **DISMISS** with explicit reason posted as a PR comment (e.g., "false positive: Build.cs:19 already has the dep" — verifiable claim).
5. Once findings clear: `gh pr merge <N> --squash --admin --delete-branch`.

### Mechanical-fix follow-up exception

When a follow-up commit on the same branch applies bot findings as **direct surgical fixes (no new logic)** — e.g., add quote-around-identifier, split error-code, cache a state read, restore a field name for parity — **self-merge is permitted without waiting for a second-pass bot review** since the bots' first pass already directed the fix. New-logic commits still require a fresh bot pass before merge.

### Worked examples from this session

| PR | Bot findings | Result |
|---|---|---|
| #161 | chatgpt-codex-connector caught **P0** non-existent `UInputSettings::GetActionMappings(NAME_None, ...)` overload post-merge | Fixed in PR #164 |
| #162 | CodeRabbit MAJOR vendor-neutral manifest regression | Fixed in PR #164 |
| #164 | 11 findings (1 P0 + several P2 mechanical) | Applied + merged |
| #167 (Wave B) | Bot pass clean | Self-merged |
| #168 (Wave C) | 7 inline findings: 4 real bugs (trailing-dot, settle-delay race, JSONDecodeError silent success, vendor-neutral) + 3 dismissals (lists-of-clients pattern is the agreed neutralization) | Applied + dismissed-with-rationale + merged |
| #169 (Wave D) | 1 Gemini MEDIUM: rollback order should be reversed | Applied + merged |
| #170 (closing-note) | 1 Gemini MEDIUM: British→US spelling | Applied + merged |
| #171 (HANDOFF hash fix) | 1 greptile P2: self-recursion (the fix recreates the drift on its own merge) | Refactored to remove pinned hash + merged |
| #172 (README upgrade) | 2 findings: summary-format consistency + extra blank line in 2 details blocks | Applied + merged |

---

## Part 6 — Materials

### The 102 tools

- **71 native C++ handlers** registered by the plugin DLL at editor startup (`UnrealClaudeMCPModule.cpp`).
- **31 bridge-side synthetic tools** — pure Python composition in `bridge/unreal_claude_mcp_bridge.py`'s `SYNTHETIC_TOOLS`; no UE rebuild needed.
- Per-tool schemas and examples: `docs/TOOLS.md`.
- Three-way sync points (manual): `UnrealClaudeMCP/Resources/mcp_manifest.json`, `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, `docs/TOOLS.md`. `tests/test_manifest_sync.py` catches drift between the first two.

### Plugin diet — 12 enabled

After three rounds of cuts this session (68 → 53 → 13 → 12):

- `caveman`
- `claude-md-management`
- `claude-mem`
- `code-modernization`
- `codex`
- `commit-commands`
- `feature-dev`
- `github`
- `mcp-server-dev`
- `nvidia-models`
- `security-guidance`
- `superpowers`

Backups preserved at `~/.claude/settings.json.backup-*` for one-line revert.

### Project hook override

`<repo>/.claude/settings.local.json` — gitignored, overrides user-wide GSD hooks for this project only. Keeps `gsd-context-monitor` (useful context-pressure warning) + `gsd-statusline` (cheap status display). Drops the seven workflow guards that don't apply to non-GSD projects.

### Memory archive

- **Live:** `~/.claude/projects/F--UnrealClaudeMCP/memory/` — 10 entries indexed in `MEMORY.md`. Reloaded into every new session.
- **Repo snapshot:** `docs/session-memory-archive/` — older snapshot kept in-repo for format-survival. Restore live from snapshot if the user's machine is reformatted.

Memory entries to know about (live index):

- Codex co-developer model
- Codex invocation settings
- Multi-agent workflow
- Vendor-neutral MCP
- Codex `dotnet.exe` popup = UBT crash trap
- Codex usage limits
- UE launch standing permission
- Delegation-by-default (rule #4)
- Bot-review gate before merge (rule #5)
- Local OSS LLM runtime under the F: drive (daemon env-var bug deferred; specifics in off-repo personal config)

---

## Part 7 — Privacy + neutrality

### Public-doc privacy policy

`tests/test_no_personal_leaks.py` blocks these tokens in any tracked file:

- Names of specific local OSS LLM tooling (e.g., the daemon name, the specific 27B / e4b / 33B model identifiers, the Nvidia-tuned 49B model name)
- Maintainer's Windows username
- Any other personal-path token that surfaces in the maintainer's environment

**Workaround:** paraphrase. Use "local OSS LLM runtime" / "local reasoning model" / "local fast small model" without naming the runtime, the models, or the maintainer's user folder. Off-repo memory files + the maintainer's personal workflow-config file (kept outside the repo) hold the specifics.

### Vendor-neutral framing

The plugin is vendor-neutral by design — the wire protocol is open MCP. Any conforming client (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, Zed, Cline, ...) works without changes.

- **OK:** "Drive Unreal Engine 5 from any MCP-compliant client (Claude Code, Codex CLI, Cursor, ...)."
- **Not OK:** "Drive Unreal Engine 5 from Claude Code." (Bakes only one vendor.)

Repo / folder names retain "Claude" for legacy reasons but tool descriptions and user-facing copy stay neutral.

---

## Part 8 — Photo→Unreal validation plan

### Concept

User brings a real photo. The plugin turns it into an Unreal output — studio backdrop, 3D object, environment, or world — using **only the 102 MCP tools** (plus optional external AI for photo→mesh generation if needed). The validation IS the readiness check: if every tool that should fire does fire and returns the expected shape, the plugin is ready.

### Asset inputs (user-supplied)

- One or more photos (JPG / PNG / TGA / BMP / HDR / EXR — anything UE's `Import` path accepts)
- Optional: external AI-generated 3D mesh from the photo (Meshy / Tripo / Rodin / RealityCapture → FBX or glTF)
- Optional: target host UE project (defaults to the canonical host at `F:/ax plug in/HDMediaVirtualStudio/HDMediaVirtualStudio.uproject`)

### Pre-requisites

1. **Host UE 5.7 cold-compile** — still pending from Wave A / A.5. See `docs/HANDOFF.md` "Verification runbook" (steps 1–6). Without the rebuild, 7 C++ handlers return JSON-RPC `-32601` (method not found).
2. UE editor running with the plugin loaded; TCP server bound on `127.0.0.1:18888`.
3. MCP client connected (bridge wired in client's `.mcp.json`).
4. (Optional, for local pre-commit ensemble review during the test run) admin-shell fix for the local-daemon env-var bug.

### Test stages — 18 stages, ~95+ tools exercised

#### Stage 1 — Prep (host rebuild + launch)

Run the 6-step verification runbook on the host machine. End state: `list_tools` returns 102.

#### Stage 2 — Baseline snapshot

| Tool | Expected result |
|---|---|
| `get_project_summary` | Project name + engine version + plugin list + asset count |
| `get_engine_version` | Structured `{major, minor, patch, changelist, branch, minor_dotted}` |
| `list_levels` | UWorld asset registry entries under `/Game` |
| `list_tools` | All 102 names |
| `list_tasks` | Empty or pre-existing |

#### Stage 3 — Photo intake

| Tool | Expected result |
|---|---|
| `import_texture` | Photo imported as `UTexture2D` asset |
| `configure_texture` | sRGB on, compression for backdrop, LOD group |
| `inspect_texture` | Verifies class + size + sRGB + compression |

#### Stage 4 — Material from photo

| Tool | Expected result |
|---|---|
| `find_assets` | Locate a parent `UMaterial` with `BaseColor` texture param |
| `create_material_instance` | `UMaterialInstanceConstant` with parent set |
| `set_mi_parameter` | Override `BaseColor` to imported texture |
| `inspect_material` | Lists parent's parameter declarations |
| `inspect_material_instance` | Lists parent + active overrides |
| `inspect_material_function` | (If scene uses any MFs) |

#### Stage 5 — Studio backdrop spawn

| Tool | Expected result |
|---|---|
| `spawn_actor` | `StaticMeshActor` on `/Engine/BasicShapes/Plane` |
| `set_actor_transform` | Scale + place the plane |
| `set_actor_property` | Override Material 0 with new MI |
| `add_component` | Directional + point lights attached |
| `focus_actor` | Frame the viewport on backdrop |
| `get_camera_transform` | Read current camera |
| `set_camera_transform` | Precise camera pose |

#### Stage 6 — 3D object from photo

Externally generated mesh (Meshy / Tripo / Rodin / RealityCapture → FBX / glTF) imported manually.

| Tool | Expected result |
|---|---|
| `find_assets` | Locate the imported mesh |
| `inspect_asset` | Class + tags + dependencies |
| `inspect_static_mesh` | LODs, materials, collision, bounds |
| `inspect_skeletal_mesh` | (If the asset is skeletal) |
| `inspect_physics_asset` | (If rigged with physics) |
| `inspect_anim_blueprint` | (If animated) |
| `inspect_anim_montage` | (If has montages) |
| `spawn_actor` | StaticMeshActor pointing at the imported mesh |
| `set_actor_transform` | Place the object |
| `bulk_set_actor_property` | Apply N property tweaks in one call |
| `find_actors_by_class` | Verify class filter |
| `bulk_focus_actors` | Walk the viewport across actors |
| `bulk_screenshot_actors` | Capture each one |

#### Stage 7 — Environment build

| Tool | Expected result |
|---|---|
| `find_unused_assets` | Pre-build scan |
| `import_texture` × N | Multiple env panels |
| `bulk_inspect_assets` | Batch verify |
| `spawn_actor` × N | Env panels in place |
| `bulk_set_actor_property` | Material + transform per panel |
| `get_reference_chain` | One texture's referencer graph |
| `inspect_dependency_graph` | One MI's dependency tree |
| `compare_assets` | Diff two MI variants |
| `inspect_landscape` | (If scene has landscape) |
| `inspect_niagara_system` | (If VFX present) |
| `inspect_data_table` / `inspect_curve` / `inspect_data_asset` | (If used) |

#### Stage 8 — Audio (optional)

| Tool | Expected result |
|---|---|
| `inspect_sound_cue` / `inspect_sound_wave` / `inspect_sound_attenuation` | If SFX in scene |
| `inspect_sound_class` / `inspect_sound_submix` / `inspect_audio_bus` | Audio routing |
| `inspect_metasound` | (If MetaSound sources used) |

#### Stage 9 — Sequencer fly-through

| Tool | Expected result |
|---|---|
| `create_sequence` | New LevelSequence asset |
| `bind_actor_to_sequence` | Camera bound as possessable |
| `inspect_sequence` | Track + section + binding shape |

#### Stage 10 — Widget HUD (optional)

| Tool | Expected result |
|---|---|
| `find_assets` (Widget class) | Locate or create a WBP |
| `inspect_widget_blueprint` | Animations + bindings + slots |
| `inspect_widget_tree` | Widget hierarchy |
| `edit_widget_tree` | `set_root` / `add_child` / `set_property` |
| `inspect_blueprint` | BP graph + variables |
| `compile_blueprint` | Single-BP compile + report |
| `bulk_compile_blueprints` | Batch compile |
| `audit_blueprint_compile_status` | Currently buckets every BP as `Unknown` — known gap pending C++ patch |

#### Stage 11 — Render settings + high-res

| Tool | Expected result |
|---|---|
| `inspect_project_setting` | `/Script/Engine.RendererSettings` dump |
| `get_console_variable` / `set_console_variable` / `find_console_variables` | One-at-a-time CVar tweaks |
| `bulk_set_console_variables` | Atomic batch with rollback |
| `get_viewport_screenshot` | Base64 PNG |
| `take_high_res_screenshot` | UE's `HighResShot` |

#### Stage 12 — PIE validation

| Tool | Expected result |
|---|---|
| `pie_control` action=query | `{is_playing: false, is_play_queued: false, is_simulating: false}` |
| `pie_control` action=start mode=play | PIE launches |
| `pie_control` action=stop | Cleanly ends |
| `get_selected_actors` | Per-actor name + label + class + class_path + transform |
| `inspect_input_mappings` | Action + axis mappings + `uses_enhanced_input` flag |
| `get_log_lines` | Recent log entries; filter by category |

#### Stage 13 — Asset hygiene

| Tool | Expected result |
|---|---|
| `save_dirty_assets` | `{ok: true, saved_count: N}` |
| `fix_up_redirectors` / `bulk_fix_redirectors` | Redirectors resolved |
| `find_unused_assets` (post-build) | Cleanup check |
| `bulk_delete_assets` / `bulk_move_assets` / `bulk_rename_assets` / `bulk_duplicate_assets` | Batch ops |
| `delete_actor` | Cleanup test actors |
| `move_asset` / `rename_asset` / `duplicate_asset` / `delete_asset` | Single-asset ops |

#### Stage 14 — Python escape hatches

| Tool | Expected result |
|---|---|
| `execute_unreal_python` | Arbitrary `unreal.*` Python |
| `run_python_file` | Disk-loaded `.py` runs |
| `apply_python_to_selection` | Script with `actors` / `assets` bound |
| `exec_python_persistent` | Variables survive into next call |
| `reset_python_state` | Persistent globals wiped |

#### Stage 15 — Task system

| Tool | Expected result |
|---|---|
| `start_sleep_task` | Reference long-running task spawned |
| `poll_task` | State / result read |
| `list_tasks` | All tracked tasks |
| `cancel_task` | Mid-flight cancel |

#### Stage 16 — Event system

| Tool | Expected result |
|---|---|
| `poll_events` | Drain queued editor events |
| `wait_for_events` | Block until matching events or `timeout_ms` |
| `register_subscription` | Per-client filtered stream |
| `poll_subscription` | Drain subscription queue |
| `unsubscribe` | Close it |

#### Stage 17 — Packaging (optional)

| Tool | Expected result |
|---|---|
| `compile_mod_pak` | Headless `.pak` build via RunUAT |
| `compile_mod_pak_direct` | Headless `.pak` via UnrealPak (RunUAT bypass) |

#### Stage 18 — Final captures + report

| Tool | Expected result |
|---|---|
| `bulk_focus_actors` final tour | Each hero actor framed |
| `bulk_screenshot_actors` | Hero PNG per actor |
| `execute_console_command` (`stat fps`) | Performance snapshot |
| `get_log_lines` | Final log capture for issues |
| `screenshot_actor` | Per-hero close-ups |

### Per-tool scorecard template

| Tool | Stage(s) | Result | Notes |
|---|---|---|---|
| `<tool>` | `<N>` | `PASS` / `FAIL` / `SKIP-N/A` / `BLOCKED-host-rebuild` | one-line observation |

Fill in during the test run. SKIP-N/A is valid for category-specific tools (e.g., `inspect_landscape` if there's no landscape; `inspect_niagara_system` if no VFX). FAIL needs a follow-up issue.

### Expected results

- **Without host rebuild:** 95/102 tools work today. The 7 blocked: `get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `pie_control`, `inspect_project_setting` — all return JSON-RPC `-32601` until cold-compile.
- **After host rebuild:** 102/102 callable. Test plan exercises all of them in this scenario.

### Known limitations

- `audit_blueprint_compile_status` buckets every Blueprint as `Unknown` because the C++ `inspect_blueprint` handler doesn't yet emit the `blueprint_status` field. Small C++ patch pending; opens a follow-up issue.
- External photo→3D-mesh AI tooling is out of plugin scope by design. The user supplies the mesh; the plugin imports / inspects / spawns / lights / captures it.
- Live-MCP verification of the 12 new Wave B / C / D synthetics has not yet happened in a real editor session. This test IS that verification.

---

## Part 9 — Next-session reflex

When picking up cold:

1. **Read this file first**, then `docs/HANDOFF.md` "at a glance" (top of the file).
2. Run `git log -1 origin/main` to see the current HEAD.
3. If the user asks "is the tool ready to use?" — answer per Part 8: 95/102 today, 102/102 after host cold-compile. Point them at the verification runbook in `docs/HANDOFF.md`.
4. If the user wants to run the photo→Unreal test — execute Stage 1 first (host rebuild), then walk through stages 2–18 filling in the scorecard.
5. If the user wants new work — follow the standing rules (delegate via Rule #4; gate via Rule #5; never blind-merge; preserve privacy + vendor-neutrality).
6. If the user wants more tools — note that the explicit "stop at 102" directive is in force. New tools require a fresh greenlight.

### Useful commands cheat-sheet

```powershell
# Re-launch UE for live verification (pre-quote inside the array element)
Start-Process 'F:\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe' -ArgumentList '"F:\ax plug in\HDMediaVirtualStudio\HDMediaVirtualStudio.uproject"'

# Cold-compile after a C++ change
& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" HDMediaVirtualStudioEditor Win64 Development -project="F:\ax plug in\HDMediaVirtualStudio\HDMediaVirtualStudio.uproject"

# Close UE when verification finishes
Get-Process UnrealEditor,UnrealTraceServer -ErrorAction SilentlyContinue | Stop-Process -Force
```

```bash
# Bot-review readout (zero sub-agent token cost)
gh pr view <N> --json statusCheckRollup,reviews,comments,mergeStateStatus
gh api repos/NAJEMWEHBE/UnrealClaudeMCP/pulls/<N>/comments
gh api repos/NAJEMWEHBE/UnrealClaudeMCP/pulls/<N>/reviews

# Local pytest + drift check
py -3 -m pytest tests/ -q
py -3 scripts/drift_sweep.py
```

---

**End of session-continuity doc.** Live truth lives in `docs/HANDOFF.md`; this file is the meta-context that makes HANDOFF.md actionable.
