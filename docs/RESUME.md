# RESUME — first thing the next session reads after a compact

This file captures the live-state snapshot when the prior session compacted its
conversation. Read this first; then read `docs/HANDOFF.md` tail; then read
`CLAUDE.md` / `AGENTS.md`. The drift_sweep + pytest pair below confirms whether
this file is still current.

## State snapshot at compact

**Compacted:** end of session 2026-05-12 (16 windows total — five attended +
five autonomous + six closing notes; see `docs/HANDOFF.md` tail).

| Signal | Value |
|---|---|
| `main` HEAD | `fc388f1` |
| Tools | 80 (64 C++ + 16 bridge-side synthetic) |
| pytest cases | 243 |
| Plugin version | 0.9.1 |
| UE engine minor | 5.7 |
| Drift sweep | clean (6 signals × 8 files) |
| Branch-protection ruleset | `16243165`, active, admin-bypass enabled |
| Today's PRs merged | 30 (#110 → #139) |

Confirm in-tree via:

```powershell
git rev-parse --short HEAD
python scripts/drift_sweep.py
python -m pytest tests/ -q
```

Expected: HEAD at or after `fc388f1`; drift clean with the counts above; 243+
tests passing.

## UE editor + bridge state

UE 5.7 editor was alive at compact with the bridge bound on
`127.0.0.1:18888`. The host project is
`F:/ax plug in/HDMediaVirtualStudio/HDMediaVirtualStudio.uproject`. If the
editor has since been closed, relaunch with the **pre-quoted path** (PR #124's
trap-table entry — `Start-Process -ArgumentList @('path with spaces')`
silently tokenises and UE falls back to the Project Browser):

```powershell
Start-Process 'F:\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe' `
    -ArgumentList '"F:\ax plug in\HDMediaVirtualStudio\HDMediaVirtualStudio.uproject"'
```

UE typically binds the bridge in ~2 minutes. If CPU stays at ~7 % one core and
`Saved/Logs/HDMediaVirtualStudio.log` is stale, the launcher fell through to
Project Browser — re-check the path-quoting.

## Critical first action: restart Claude Code

Twenty-two bridge-touching PRs from the prior session loaded as **stale code**
into the running MCP bridge process. A single Claude Code restart picks them
all up. Affected:

- `#126` `inspect_*` `asset_not_found` message alignment
- `#127` `set_camera_transform` Rotator argument order (CRITICAL — pre-fix
  silently scrambled rotations)
- `#128` `_run_marker_pattern` exception class split
- `#130` `get_camera_transform` helper refactor + `set` lockstep
- `#133` `bulk_move_assets`
- `#135` `inspect_metasound`
- `#136` `bulk_rename_assets`
- `#138` `bulk_duplicate_assets`
- plus the supporting hardening + scanner-extension PRs

## Canonical post-restart live-verification panel

```python
mcp__unreal-ai-connection__list_tools          # expect count=80
mcp__unreal-ai-connection__set_camera_transform(
    {location: {x: 1, y: 2, z: 3},
     rotation: {pitch: -20, yaw: 45, roll: 7}}
)
mcp__unreal-ai-connection__get_camera_transform   # expect lossless round-trip
mcp__unreal-ai-connection__inspect_data_asset({path: "/Game/NoSuch"})
# expect: error_message starts with "inspect_data_asset: asset_not_found:"
mcp__unreal-ai-connection__bulk_move_assets(
    {paths: ["/Game/NoSuch"], dest_folder: "/Game/Archive"}
)   # expect: ok: false with per-path error_code
```

## What's settled

- All deferred bridge-audit findings closed.
- Two live-only bug classes documented (cross-tool convention drift; UE 5.7
  Python wrapper constructor positional-arg-order trap — see
  `docs/ARCHITECTURE.md` § "UE 5.7 API gotchas").
- `bulk_*_assets` family complete: delete + move + rename + duplicate.
- All `inspect_*` deferred handlers from the original HANDOFF roadmap shipped.

## What's pending

- **C++-only deferred handlers:** Sequencer keyframe authoring, Movie Render
  Queue. Both need attended cold-compile + Codex per the multi-agent
  partitioning. Out of scope for autopilot windows.
- **External-contributor PRs:** none open at compact. `#102` + `#105` (David)
  were cherry-picked + closed earlier in the session.
- **Live verification of the 22 stale PRs** — see the panel above.

## Authoritative state files (read order)

1. `docs/RESUME.md` — this file (resumption only)
2. `docs/HANDOFF.md` tail — most recent closing-note (full session detail)
3. `CLAUDE.md` / `AGENTS.md` — house rules + path-quoting recipe + standing
   UE-launch authorization
4. `scripts/drift_sweep.py` + `tests/conftest.py` — canonical-count source
   of truth
5. `docs/ARCHITECTURE.md` § "UE 5.7 API gotchas" — wrapper-trap table

## New-tool playbook (10 steps; mechanical)

For any future bridge-side synthetic:

1. Add `synthetic_<name>(req_id, args)` in `bridge/unreal_ai_connection_bridge.py`
   (mirror the closest existing synthetic for the shape).
2. Add TOOLS schema entry (input + required fields).
3. Add to the `SYNTHETIC_TOOLS = {...}` dispatch dict.
4. Bump `EXPECTED_SYNTHETIC_TOOL_COUNT` in `tests/conftest.py`.
5. Add manifest entry in `UnrealAIConnection/Resources/mcp_manifest.json`.
6. Add tool name to the expected-set in
   `test_tool_names_are_unique_and_match_handlers`.
7. Add behavioural tests in `tests/test_bridge.py` (schema + happy + at least
   one error + at least one input-validation path).
8. Run `python scripts/drift_sweep.py` — apply every doc-bump it flags
   (typically 8 files).
9. Run `pytest tests/` — full suite green.
10. Commit + push + open PR; CI matrix + Gemini auto-review + merge with
    `--admin` after green.

Typical cycle: <30 minutes per new tool.
