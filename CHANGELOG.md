# Changelog

All notable changes to UnrealClaudeMCP are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Per-tool details live in [`docs/TOOLS.md`](docs/TOOLS.md); architecture and the C++/synthetic boundary live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); session-level chronology lives in [`docs/HANDOFF.md`](docs/HANDOFF.md). This file is the human-facing summary.

## [Unreleased]

### Added

- **Multi-agent ensemble standing rule** (HANDOFF.md, [PR #153](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/153)). Every substantive change is reviewed by at least one external model before merge. Slot-level documentation in the project's public docs; specific provider/model identifiers stay private per the personal-leaks policy.
- **UE-launch standing permission rule** (HANDOFF.md, [PR #155](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/155)). UE 5.7 editor launches are pre-authorized in every session — never ask, never skip live verification. Path-quoting recipe + PowerShell-vs-Bash gotcha documented.
- **UE-close-when-idle companion rule** (HANDOFF.md, [PR #156](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/156)). Close UE when verification finishes — Editor mode reserves ~4 GB RAM. Cadence: open, verify, close.
- **Reverse-direction manifest_sync check** ([PR #151](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/151)). New test asserts every bridge `required[]` entry is documented in `manifest.params` (or via free-form `"see docs/TOOLS.md"`). Catches orphan-required-in-bridge drift.
- **Invalid-arguments guard coverage** ([PR #152](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/152)). Parametrized test fans out across 6 synthetics × 4 bad-args shapes (None / `[]` / `"string"` / `42`), locking [PR #146](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/146)'s `isinstance(args, dict)` guards against future regression. 24 new test cases.
- **Bridge type-hint sweep** ([PR #150](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/150)). Every public function in `bridge/unreal_claude_mcp_bridge.py` carries type hints — 16 synthetics + 5 helpers + `handle()` + `main()`. `req_id` intentionally untyped (MCP allows int/str/null IDs).
- **`isinstance(args, dict)` guards on 6 synthetics** ([PR #146](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/146)). `wait_for_events`, `get_camera_transform`, `set_camera_transform`, `screenshot_actor`, `compile_mod_pak`, `compile_mod_pak_direct` now return a clean `-32602 invalid_arguments` envelope instead of AttributeError when called with non-dict args. Brings the bridge to parity with the 10 synthetics that already had the guard.
- **`bulk_*_assets` continue-on-error=True coverage** ([PR #145](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/145)). New tests for the default-on partial-failure branch of bulk_move, bulk_rename, bulk_duplicate. Plus two missing bulk_duplicate edge-case rejections.
- **Inspect-synthetic error-branch test parity** ([PR #144](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/144)). 8 new tests bringing `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`, `inspect_metasound` to the same error-branch coverage as `inspect_sound_class` / `inspect_data_asset`.
- **`set_camera_transform` no-op-read branch test** ([PR #148](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/148)). When both `location` and `rotation` are omitted, `set_camera_transform` forwards to `get_camera_transform` — covered explicitly so callers can rely on it for cheap camera introspection.
- **`make_response` non-integer req_id round-trip test** ([PR #148](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/148)). Parametrized over string id, null id, and large-int — locks JSON-RPC 2.0 / MCP-spec-correct behaviour for clients that don't use integer IDs.
- **7 missing synthetic-tool sections in `docs/TOOLS.md`** ([PR #143](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/143)). `bulk_move_assets`, `bulk_rename_assets`, `bulk_duplicate_assets`, `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`, `inspect_metasound` now each have their own section with params, results, error codes, and JSON examples.
- **Error-format annotations on 11 legacy handlers** ([PR #142](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/142)). The 9 free-form-OutError legacy handlers now carry an explicit "Error format: free-form OutError strings" comment so bridge consumers don't expect a `<tool>: <code>: <detail>` shape. The 2 no-error meta-handlers (`list_tools`, `get_project_summary`) carry an explicit "No error paths" comment.

### Changed

- **`scripts/drift_sweep.py` scan surface extended** ([PR #147](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/147)). `bridge/unreal_claude_mcp_bridge.py`, `UnrealClaudeMCP/Resources/mcp_manifest.json`, and `docs/ARCHITECTURE.md` are now scanned. Manifest description and README hero blurb rewritten with digit counts (e.g. `80 tools total` instead of `Eighty generic editor-automation tools`) so the existing regex patterns enforce them. The class of drift that PR1 cleaned up manually can no longer recur silently.
- **`docs/TOOLS.md` `bulk_rename` / `bulk_duplicate` param names corrected** ([PR #149](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/149)). The PR3 sections shipped with `items[]` placeholders; the canonical names are `renames[]` and `duplicates[]` respectively. Removed a false claim that `bulk_duplicate_assets` validates `new_name` for slashes/dots — it doesn't (forwards as-is to `duplicate_asset`).
- **Drift narrative cleanup** ([PR #141](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/141)). Bridge module docstring + TOOLS preamble comment + manifest description + TOOLS.md L16 + ARCHITECTURE.md mermaid Bridge node label all reconciled to the canonical 80 / 64 / 16 counts (was claiming 75 / 11).
- **README polish** ([PR #154](https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/154)). Added `pytest 282 passing` + `tools 80` static badges; new "Development workflow" Status row documenting the multi-agent ensemble (slot-level — specific identifiers stay private); expanded the "Tools" row to spell out the C++ / synthetic boundary explicitly.

### Internal

- `pytest` cases: 243 → 283 (+40) across the session.
- 80 tools (64 C++ + 16 bridge-side synthetic) unchanged — the focus of this window was hardening the scaffolding around the existing surface, not net-new tools.
- 16 PRs (#141 → #156) merged in one autopilot-extension window 2026-05-12 → 2026-05-13.

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
