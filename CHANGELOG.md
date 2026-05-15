# Changelog

All notable changes to UnrealClaudeMCP are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-tool details live in [`docs/TOOLS.md`](docs/TOOLS.md); architecture and the C++/synthetic boundary live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); session-level chronology lives in [`docs/HANDOFF.md`](docs/HANDOFF.md). This file is the human-facing summary.

## [Unreleased]

### Added

Entries listed reverse-chronologically by PR number (newest first).

- **21st HANDOFF closing-note** ([PR #185](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/185)). Single-session note for the PR #184 AFK-resume window. Rotated the 18th note to archive per the rolling-three invariant.
- **`marketplace_search` + `marketplace_import` synthetic tools** ([PR #184](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/184)). Browse public CC0 asset libraries (Polyhaven default + AmbientCG fan-out via `source=all`) and pull textures / HDRIs straight into the project as `UTexture2D` via the native `import_texture` handler. stdlib-only (`urllib.request`), no auth, no API key. Client-side AND-token filter across slug + name + tags + categories with `download_count`-desc ranking. Per-source quota when `source=all`. URL-encoded slug + non-https URL guard + allowlist-sanitised `resolution` / `fmt` before composing the temp filename. `.part` cleanup on failure. Format-fallback returns the chosen format so the temp suffix matches the actual downloaded body. Polyhaven API access terms (non-commercial / academic) called out in the tool descriptions distinct from the CC0 asset terms.
- **Scene-build v3 → v7 trajectory** ([PR #181](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/181) and [PR #184](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/184)). Reconstructed desert scene from UE primitives + atmosphere. Brightness retune v3 burnout → v4 hell-red → v6/v6.1/v7 daylight (sun 2600K → 5500K, pitch −3° → −35°, fog density 0.12 → 0.04, post-process bias −1.8 → 0.0, saturation neutralised). Staged-capture flag (`builtins.DESERT_BUILD_STAGE`) for orchestrated workflow captures. Textured-MI rebuild over five Polyhaven CC0 assets via a procedurally-built `M_TexturedSurface` master material.
- **`inspect_blueprint` emits `blueprint_status` field** ([PR #183](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/183)). Closes the `audit_blueprint_compile_status` gap that was bucketing every Blueprint as `Unknown`.
- **Wave D — utility synthetics** ([PR #169](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/169)). `compare_assets`, `bulk_set_console_variables`, `inspect_dependency_graph`, `bulk_fix_redirectors`. Tool count 96 → 100 ← user-defined milestone.
- **Wave C — actor-batch synthetics** ([PR #168](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/168)). `find_actors_by_class`, `bulk_focus_actors`, `bulk_screenshot_actors`, `bulk_set_actor_property`. Tool count 92 → 96.
- **Wave B — asset-hygiene synthetics** ([PR #167](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/167)). `find_unused_assets`, `get_reference_chain`, `bulk_compile_blueprints`, `audit_blueprint_compile_status`. Tool count 88 → 92.
- **HANDOFF active/archive split** ([PR #166](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/166)). HANDOFF.md from 1509 → 516 lines (active rolling-3 invariant); 941 lines moved to HANDOFF-archive.md. ~36K tokens saved per session-start.
- **Standing rules #4 + #5 codified** ([PR #165](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/165)). Delegation-by-default (rule #4) and bot-review gate before merge (rule #5), with mechanical-fix follow-up exception.
- **Wave A.5 — `pie_control` + `inspect_project_setting`** ([PR #162](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/162)). `pie_control` wraps `GEditor->RequestPlaySession` / `RequestEndPlayMap` / `IsPlayingSessionInEditor` with action=start|stop|query + mode=play|simulate. `inspect_project_setting` reflects any `UDeveloperSettings` subclass. Tool count 86 → 88. First pre-COMMIT multi-agent ensemble review window (caught one BLOCKER + two MAJOR findings at design phase, zero rework cost).
- **Wave A — quick-win tools** ([PR #161](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/161)). `get_engine_version`, `list_levels`, `save_dirty_assets`, `get_selected_actors`, `inspect_input_mappings`, `bulk_inspect_assets`. Tool count 80 → 86.
- **UE-close-when-idle companion rule** (HANDOFF.md, [PR #156](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/156)). Close UE when verification finishes — Editor mode reserves ~4 GB RAM. Cadence: open, verify, close.
- **UE-launch standing permission rule** (HANDOFF.md, [PR #155](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/155)). UE 5.7 editor launches are pre-authorized in every session — never ask, never skip live verification. Path-quoting recipe + PowerShell-vs-Bash gotcha documented.
- **Multi-agent ensemble standing rule** (HANDOFF.md, [PR #153](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/153)). Every substantive change is reviewed by at least one external model before merge. Slot-level documentation in the project's public docs; specific provider/model identifiers stay private per the personal-leaks policy.
- **Invalid-arguments guard coverage** ([PR #152](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/152)). Parametrized test fans out across 6 synthetics × 4 bad-args shapes (None / `[]` / `"string"` / `42`), locking [PR #146](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/146)'s `isinstance(args, dict)` guards against future regression. 24 new test cases.
- **Reverse-direction manifest_sync check** ([PR #151](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/151)). New test asserts every bridge `required[]` entry is documented in `manifest.params` (or via free-form `"see docs/TOOLS.md"`). Catches orphan-required-in-bridge drift.
- **Bridge type-hint sweep** ([PR #150](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/150)). Every public function in `bridge/unreal_claude_mcp_bridge.py` carries type hints — 16 synthetics + 5 helpers + `handle()` + `main()`. `req_id` intentionally untyped (MCP allows int/str/null IDs).
- **`set_camera_transform` no-op-read branch test** + **`make_response` non-integer req_id round-trip test** ([PR #148](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/148)). When both `location` and `rotation` are omitted, `set_camera_transform` forwards to `get_camera_transform` — covered explicitly. Plus `make_response` parametrized over string id, null id, and large-int.
- **`isinstance(args, dict)` guards on 6 synthetics** ([PR #146](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/146)). `wait_for_events`, `get_camera_transform`, `set_camera_transform`, `screenshot_actor`, `compile_mod_pak`, `compile_mod_pak_direct` now return a clean `-32602 invalid_arguments` envelope instead of AttributeError when called with non-dict args. Brings the bridge to parity with the 10 synthetics that already had the guard.
- **`bulk_*_assets` continue-on-error=True coverage** ([PR #145](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/145)). New tests for the default-on partial-failure branch of bulk_move, bulk_rename, bulk_duplicate. Plus two missing bulk_duplicate edge-case rejections.
- **Inspect-synthetic error-branch test parity** ([PR #144](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/144)). 8 new tests bringing `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`, `inspect_metasound` to the same error-branch coverage as `inspect_sound_class` / `inspect_data_asset`.
- **7 missing synthetic-tool sections in `docs/TOOLS.md`** ([PR #143](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/143)). `bulk_move_assets`, `bulk_rename_assets`, `bulk_duplicate_assets`, `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`, `inspect_metasound` now each have their own section with params, results, error codes, and JSON examples.
- **Error-format annotations on 11 legacy handlers** ([PR #142](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/142)). The 9 free-form-OutError legacy handlers now carry an explicit "Error format: free-form OutError strings" comment so bridge consumers don't expect a `<tool>: <code>: <detail>` shape. The 2 no-error meta-handlers (`list_tools`, `get_project_summary`) carry an explicit "No error paths" comment.

### Changed

Entries listed reverse-chronologically by PR number (newest first).

- **Bot-review gate hardened across PR #184's six commits** ([PR #184](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/184)). Real bugs caught and fixed in flight: SSRF via non-https URL in `_marketplace_http_download`, format-fallback / temp-suffix mismatch in `_polyhaven_pick_file`, missing `STAGE_DONE_T4_hero` marker in `build_desert_scene.py`, `mi_crate` gated on the wrong texture branch, `replace_existing` bool coercion accepting truthy strings, `source=all` fan-out skipping AmbientCG when Polyhaven filled the quota, `builtins.DESERT_BUILD_STAGE` leaking across UE Python runs, path-traversal vector in temp filename from caller-controlled `resolution` / `fmt`, dead `if status < 200 or status >= 300` branches in `urlopen` wrappers (unreachable since urlopen raises HTTPError pre-return), missing `.part` cleanup on download failure, missing URL-encode on `/files/{slug}` slug.
- **Vendor-neutral manifest description** ([PR #184](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/184)). Hard-coded product-name list removed from the shipped `mcp_manifest.json` description field. Generic "any MCP-compliant client" framing.
- **`set_actor_property` polymorphic-typed schema** ([PR #182](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/182)). `value` field accepts the multi-typed union (`bool`, `number`, `string`, object) declared as `oneOf` rather than untyped.
- **Plugin diet** (HANDOFF.md, post-2026-05-13). User-wide plugin set trimmed 68 → 12. Project-level `.claude/settings.local.json` override kept the cheap GSD hooks (context-monitor + statusline), dropped the workflow-guard set this project doesn't use. ~55-65K tokens saved per session-start.
- **README polish** ([PR #154](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/154)). Added `pytest 282 passing` + `tools 80` static badges; new "Development workflow" Status row documenting the multi-agent ensemble (slot-level — specific identifiers stay private); expanded the "Tools" row to spell out the C++ / synthetic boundary explicitly.
- **`docs/TOOLS.md` `bulk_rename` / `bulk_duplicate` param names corrected** ([PR #149](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/149)). The PR3 sections shipped with `items[]` placeholders; the canonical names are `renames[]` and `duplicates[]` respectively. Removed a false claim that `bulk_duplicate_assets` validates `new_name` for slashes/dots — it doesn't (forwards as-is to `duplicate_asset`).
- **`scripts/drift_sweep.py` scan surface extended** ([PR #147](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/147)). `bridge/unreal_claude_mcp_bridge.py`, `UnrealClaudeMCP/Resources/mcp_manifest.json`, and `docs/ARCHITECTURE.md` are now scanned. Manifest description and README hero blurb rewritten with digit counts so the existing regex patterns enforce them. The class of drift that PR1 cleaned up manually can no longer recur silently.
- **Drift narrative cleanup** ([PR #141](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/141)). Bridge module docstring + TOOLS preamble comment + manifest description + TOOLS.md L16 + ARCHITECTURE.md mermaid Bridge node label all reconciled to the canonical 80 / 64 / 16 counts (was claiming 75 / 11).

### Internal

- Tool count: 80 → **102** (+22 across two waves windows + the AFK-resume hardening). Split: 64 C++ + 16 synthetic → **71 C++ + 31 synthetic**. New synthetics: 6 Wave A + 2 Wave A.5 + 4 Wave B + 4 Wave C + 4 Wave D + 2 marketplace.
- `pytest` cases: 243 → **400** (+157) across the full Unreleased span.
- **41 PRs across the #141 → #185 range** (gap: #157 → #160 belong to the prior milestone window and are excluded from this Unreleased section). Split across three windows: 2026-05-12 → 13 hardening (#141 → #156, 16 PRs), 2026-05-13 → 14 autopilot extension (#161 → #169, 9 PRs), 2026-05-14 → 15 AFK-resume + scene-v7 + marketplace hardening (#181 → #185, 5 PRs); intermediate PRs #170 → #180 are 11 misc cleanups not enumerated above. 16 + 9 + 11 + 5 = 41.

## [0.9.1] — 2026-05-08

The `bulk_*_assets` family completion plus the inspect-synthetic round-out.

### Added

- **`bulk_duplicate_assets`** ([PR #138](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/138)). Fourth + final member of the `bulk_*_assets` family. Per-entry `{path, dest_path, new_name?}` mapping. Unlike rename/move, does NOT leave a redirector at the source.
- **`bulk_rename_assets`** ([PR #136](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/136)). Per-entry `{path, new_name}` mapping. `new_name` validated for slashes/dots (rename_asset is name-only).
- **`bulk_move_assets`** ([PR #133](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/133)). Flat `paths[]` + single shared `dest_folder`. Mirrors `bulk_delete_assets` shape.
- **`inspect_metasound`** ([PR #135](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/135)). Accepts both `UMetaSoundSource` (emitter-attached) and `UMetaSoundPatch` (reusable subgraph). Surface-level metadata only; graph-level traversal is deferred.
- **`inspect_material_function`**, **`inspect_audio_bus`**, **`inspect_sound_submix`** (earlier in the milestone). Round out the marker-pattern inspect_* family.
- **`compile_mod_pak_direct`** (cherry-picked from a contributor PR). Bypasses RunUAT entirely by shelling out to UnrealPak.exe directly with a response file. Workaround for Dev Kits in 'installed-build mode' where BuildMod fails on UAT ScriptModules scans.

### Internal

- Tool count: 75 → 80 (+5; one cherry-pick + four net-new synthetics shipped in autopilot).
- pytest cases: 202 → 243 (+41).
- Two LIVE-FOUND bugs fixed mid-milestone: PR #127 (UE 5.7 Python `unreal.Rotator` constructor takes args in struct-memory order, NOT named-property order — positional form was silently scrambling rotations); PR #126 (inspect_* `asset_not_found` error message shape aligned to canonical `<tool>: asset_not_found: <path>`).

## [0.9.0 and earlier]

Pre-0.9.1 history is captured in `docs/HANDOFF.md` (per-session chronology) and the git log. The repository began as a UE 5.7 editor-automation prototype and grew to 75 tools across multiple sessions in 2026-04 and 2026-05.

[Unreleased]: https://github.com/NAJEMWEHBE/UnrealClaudeMCP/compare/v0.9.1...HEAD
[0.9.1]: https://github.com/NAJEMWEHBE/UnrealClaudeMCP/releases/tag/v0.9.1
