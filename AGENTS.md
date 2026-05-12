# AGENTS.md — universal agent context for UnrealClaudeMCP

This file is read by Codex CLI, Copilot CLI, Gemini CLI, Cursor, and any other coding agent that respects the `AGENTS.md` convention. Claude Code reads [`CLAUDE.md`](CLAUDE.md) instead (same content semantically; CLAUDE.md is the canonical version).

**All agents: read [`docs/HANDOFF.md`](docs/HANDOFF.md) before any substantive work.** The "Closing notes from prior sessions" section at the bottom of HANDOFF.md captures the operative state for resumption. **The latest closing note wins** if anything contradicts the at-a-glance counts at the top of HANDOFF.md.

## Quick orientation

UE 5.7 plugin + Python bridge exposing editor automation to MCP-compliant clients (Claude Code, Codex CLI, Copilot CLI, Cursor, Gemini CLI, Continue, …) over a localhost TCP socket. **88 tools total: 71 native C++ handlers + 17 bridge-side synthetic tools.** Vendor-neutral by design — the wire protocol is open MCP; the "Claude" in the repo name is decorative.

## Where to look first

- **C++ handlers** (64) — `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_*.cpp`. Registered in `UnrealClaudeMCPModule.cpp`.
- **Bridge-side synthetic tools** (11) — `bridge/unreal_claude_mcp_bridge.py`'s `SYNTHETIC_TOOLS` dict: `wait_for_events`, `get_camera_transform`, `set_camera_transform`, `screenshot_actor`, `compile_mod_pak`, `bulk_delete_assets`, `inspect_data_asset`, `inspect_sound_class`, `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`.
- **Tool catalog (manual 3-place sync)** — `UnrealClaudeMCP/Resources/mcp_manifest.json`, `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, `docs/TOOLS.md`. `tests/test_manifest_sync.py` catches drift between the first two.
- **Architecture + UE 5.7 API gotchas** — `docs/ARCHITECTURE.md`.
- **Host-build runbook** — top of `docs/HANDOFF.md`.
- **Per-tool JSON schemas + examples** — `docs/TOOLS.md`.

## House rules carried across all agents

- **One handler = one `.cpp` file** in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, plus one `extern` declaration and one `Reg.Register(...)` line in `UnrealClaudeMCPModule.cpp`. Don't grow the foundation; add leaves.
- **Verify UE API claims against UE 5.7 source** before committing C++. Past reviewer agents have asserted UE APIs that turned out wrong.
- **Vendor-neutral framing** in any user-facing copy — repo description, `.uplugin` Description, README, tool descriptions. Don't bake "Claude Code" specifically into anything that ships.
- **Smoke test runs against a live UE editor** (`examples/smoke_test.py` hits `127.0.0.1:18888` directly). Bridge unit tests under `tests/` run without UE.
- **Live UE launches are pre-authorized by default in any session.** The maintainer granted standing permission on 2026-05-12: "we always use Unreal for testing if you want." Launch UE 5.7 against the host project at `F:/ax plug in/HDMediaVirtualStudio/HDMediaVirtualStudio.uproject` whenever live verification is the next step. **Path-quoting recipe (critical):** `Start-Process 'F:\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe' -ArgumentList '"F:\ax plug in\HDMediaVirtualStudio\HDMediaVirtualStudio.uproject"'` — pre-quote the path inside the array element so PowerShell doesn't tokenize on whitespace. Without that, UE falls back to Project Browser and the bridge port never binds.
- **Push to feature branches, never directly to `main`.** Open a PR; merge after CI green.
- **Cold-compile-before-merge for C++ changes.** Bridge-only Python/doc changes can self-merge on CI green per directive #7 (recorded in HANDOFF).

## MCP server setup per agent

The bridge is registered as `unreal-claude-mcp` in this project's `.mcp.json` (read by Claude Code, Copilot CLI, Cursor). Codex CLI uses `~/.codex/config.toml` — register with:
```
codex mcp add unreal-claude-mcp -- py F:\UnrealClaudeMCP\bridge\unreal_claude_mcp_bridge.py
```
After registration, all 88 tools become available through the standard MCP `tools/list` + `tools/call` flow. Open the host UE project with the plugin enabled before any tool call (the bridge surfaces a clear error otherwise).

## Cross-agent prompt-discipline recipe (validated PR #90 + #92)

When dispatching a coding task to ANY agent (Codex, Copilot, sub-Claude, future entrants), the 5-step recipe below produces ship-ready output on first try:

1. **Name a literal template file by path + line range.** Not "the pattern" or "the convention" — name the specific function the AI should mirror.
2. **Spell the upstream contract.** Return shapes, error vs success keys, what's in `result.*` vs what goes through a separate channel.
3. **Forbid the shortcut explicitly.** "DO NOT INVENT X" beats "follow Y."
4. **Pin test style.** Project mock library + project assertion style.
5. **Order the reading explicitly.** "Required reading order: 1, 2, 3, 4, 5." Sequential numbered reading makes the grounding step measurable.

This recipe was hardened for Codex over PRs #76 → #81 → #85, then proven to transfer to Copilot CLI in PR #92 with no agent-specific changes. Likely transfers to other agents — cheap to test.

## Trap-table — read before writing C++ or modifying bridge

The full trap-table lives in `docs/HANDOFF.md` closing-notes. Highlights every agent should know:

- **Manifest "required" substring trap.** `test_manifest_sync.py::test_manifest_required_params_match_bridge_required` substring-greps the literal word "required" in manifest param descriptions. Conditional params worded "required for X" trip it. Phrase as "needed when X" / "must be supplied when X".
- **Two-then-three-now-via-conftest count assertions.** `tests/conftest.py` exports `EXPECTED_TOOL_COUNT` as the single source of truth for the 3 hardcoded test count assertions. Bump it there; the 3 tests pick it up.
- **`call_ue` shape.** Returns `{"jsonrpc":..., "id":..., "result": {...}}` OR `{"jsonrpc":..., "id":..., "error": {...}}`. **No top-level `ok` key.** Test with `if "error" in resp:` — never `resp.get("ok")`.
- **`execute_unreal_python` result shape.** `result.output` is the Python traceback on failure, NOT the Python stdout. Stdout goes through `unreal.log()` → `LogPython` → retrieved via a SEPARATE `get_log_lines` second round-trip with the marker pattern. See `synthetic_get_camera_transform` (bridge.py lines 1004-1076) as the canonical example.
- **`call_ue` mid-loop transport failures** preserve the upstream JSON-RPC error code. Don't hardcode `-32603` in synthetic-tool error handlers.
- **UE 5.7 access-modifier traps.** `USoundCue::SubtitlePriority` is protected; use `GetSubtitlePriority()`. `USoundWave::SampleRate` is protected; use `GetSampleRateForCurrentPlatform()`. See HANDOFF.md trap-table for the full list.
- **`UTexture::CompositeTexture` is C4996-deprecated** as of UE 5.7. Use `GetCompositeTexture()`. Each handler module enables warnings-as-errors.

## On finishing work

1. **For C++ changes:** robocopy → Build.bat → editor → smoke test BEFORE git push (PR #81 onward cadence). Pre-merge pytest does NOT compile C++.
2. **For bridge / docs changes:** pytest sufficient. Self-merge on CI green for mechanical PRs per directive #7. The `main` branch has a protection ruleset (PR #96, ruleset `16243165`) — admin owner self-merge requires the `--admin` flag: `gh pr merge <N> --merge --admin --delete-branch`. Without `--admin`, `gh` errors out with "base branch policy prohibits the merge" even though the admin role has bypass permission (verified `current_user_can_bypass: always`).
3. **Always:** doc-drift sweep before close-of-PR. Run:
   ```
   rg -n "\b(56|60|65|68|70|71|72|74|75)\b.*\b(C\+\+|handlers?|tools? total|synthetic)" \
     --glob '!docs/superpowers/**' --glob '!docs/HANDOFF.md'
   ```
   Update CLAUDE.md / AGENTS.md / README.md / TOOLS.md / .uplugin Description / manifest description / HANDOFF.md at-a-glance + runbook step 5. The closing-note records sprint chronology — leave those frozen.
4. **HANDOFF closing-note append** for any session that ships meaningful changes. Cadence: feature PR + HANDOFF-append PR. Pickup is mechanical for the next agent.
