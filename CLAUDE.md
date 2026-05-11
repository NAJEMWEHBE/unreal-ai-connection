# UnrealClaudeMCP — agent context

**Read [`docs/HANDOFF.md`](docs/HANDOFF.md) before doing any substantive work.** The "Closing notes from prior sessions" section at the bottom captures what the previous agent was carrying in working memory — the latest entry is the operative one for resuming.

## Quick orientation (deeper detail in HANDOFF.md and ARCHITECTURE.md)

UE 5.7 plugin + Python bridge exposing editor automation to MCP-compliant clients (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, …) over a localhost TCP socket. **70 tools total: 64 native C++ handlers + 6 bridge-side synthetic tools.** Vendor-neutral — the wire protocol is open MCP; the "Claude" in the repo name is decorative.

## Where to look first for any change

- **C++ handlers** (64) — `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_*.cpp`, one per tool. Registered in `UnrealClaudeMCPModule.cpp` (the `Reg.Register(...)` block).
- **Bridge-side synthetic tools** (6: `wait_for_events`, `get_camera_transform`, `set_camera_transform`, `screenshot_actor`, `compile_mod_pak`, `bulk_delete_assets`) — `bridge/unreal_claude_mcp_bridge.py`'s `SYNTHETIC_TOOLS` dict.
- **Tool catalog (kept in sync manually across three places)** — `UnrealClaudeMCP/Resources/mcp_manifest.json`, `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, and `docs/TOOLS.md`. The `tests/test_manifest_sync.py` suite catches drift between the first two.
- **Architecture notes + UE 5.7 API gotchas** — `docs/ARCHITECTURE.md`.
- **Host-build runbook** — top of `docs/HANDOFF.md` (steps 1–6, PowerShell). Live verification on the host machine is the perpetual next step.
- **Per-tool JSON schemas + examples** — `docs/TOOLS.md`.
- **Restart-recovery procedure** (use after a fresh OS install / C: format) — [`docs/RESTART-RECOVERY.md`](docs/RESTART-RECOVERY.md). Step-by-step setup (git, gh, Node, Python, VS C++ workload, Codex CLI, Claude Code, UE 5.7) plus how to restore session memory from `docs/session-memory-archive/`.
- **Session memory archive** — [`docs/session-memory-archive/`](docs/session-memory-archive/). Snapshot of Claude Code's per-project memory files (directives, conventions, operational gotchas) — survives format because it lives in the repo. Restore to `~/.claude/projects/.../memory/` post-recovery.

## House rules carried forward across sessions

- **One handler = one `.cpp` file** in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, plus one `extern` declaration and one `Reg.Register(...)` line in `UnrealClaudeMCPModule.cpp`. Don't grow the foundation; add leaves.
- **Verify UE API claims against UE 5.7 source** before committing C++. Past reviewer agents have asserted UE APIs that turned out wrong.
- **Vendor-neutral framing** in any user-facing copy — repo description, `.uplugin` Description, README, tool descriptions. Don't bake "Claude Code" specifically into anything that ships.
- **Smoke test runs against a live UE editor** (`examples/smoke_test.py` hits `127.0.0.1:18888` directly). Bridge unit tests under `tests/` run without UE.
- **Push to feature branches, never directly to `main`.** Open a PR; the user merges.
