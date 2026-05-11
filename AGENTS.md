# AGENTS.md — universal agent context for UnrealClaudeMCP

This file is read by Codex CLI, Copilot CLI, Gemini CLI, Cursor, and any other coding agent that respects the `AGENTS.md` convention. Claude Code reads [`CLAUDE.md`](CLAUDE.md) instead (same content semantically; CLAUDE.md is the canonical version).

**All agents: read [`docs/HANDOFF.md`](docs/HANDOFF.md) before any substantive work.** The "Closing notes from prior sessions" section at the bottom of HANDOFF.md captures the operative state for resumption. **The latest closing note wins** if anything contradicts the at-a-glance counts at the top of HANDOFF.md.

## Quick orientation

UE 5.7 plugin + Python bridge exposing editor automation to MCP-compliant clients (Claude Code, Codex CLI, Copilot CLI, Cursor, Gemini CLI, Continue, …) over a localhost TCP socket. **75 tools total: 64 native C++ handlers + 11 bridge-side synthetic tools.** Vendor-neutral by design — the wire protocol is open MCP; the "Claude" in the repo name is decorative.

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
- **Push to feature branches, never directly to `main`.** Open a PR; merge after CI green.
- **Cold-compile-before-merge for C++ changes.** Bridge-only Python/doc changes can self-merge on CI green per directive #7 (recorded in HANDOFF).

## MCP server setup per agent

The bridge is registered as `unreal-claude-mcp` in this project's `.mcp.json` (read by Claude Code, Copilot CLI, Cursor). Codex CLI uses `~/.codex/config.toml` — register with:
```
codex mcp add unreal-claude-mcp -- py F:\UnrealClaudeMCP\bridge\unreal_claude_mcp_bridge.py
```
After registration, all 75 tools become available through the standard MCP `tools/list` + `tools/call` flow. Open the host UE project with the plugin enabled before any tool call (the bridge surfaces a clear error otherwise).

## Local LLMs via Ollama (free; no cloud / no quota)

Ollama is installed at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`. Models live on `F:\ollama\models` (the `OLLAMA_MODELS` user env var redirects from the default `%USERPROFILE%\.ollama\models` so the 1.5 TB on F: takes the storage hit instead of C:). The server auto-starts on user login and listens on `http://127.0.0.1:11434`.

**Models pulled** (all free open-weights, can be removed via `ollama rm <name>` if you want the disk back):
- `qwen3.6:27b` (17 GB) — coding-focused reasoning model with `thinking` mode. Best general-coding pick for this rig when you can wait. Inference ~3-6 tok/s on the 12 GB RTX 4070 Ti (CPU offload for the part that doesn't fit VRAM). Toggle thinking off with `--think=false` (REST API: `"think": false`) for ~2x speed when you don't need chain-of-thought.
- `gemma4:e4b` (9.6 GB) — Google's small-but-capable Gemma 4 variant. Fits VRAM cleanly → ~30-50 tok/s. Use for quick replies, multimodal (vision/audio tags), and parallel-dispatch-when-Qwen-is-busy. Less strong on heavy coding than Qwen.
- `nemotron3:33b` (27 GB, PR #104) — NVIDIA Nemotron 3 Nano Omni. Multimodal (vision + tools + thinking + audio). NVIDIA-tuned for RTX hardware → ~15 tok/s post-warmup despite the bigger 27 GB size (faster than Qwen 3.6 27b on the same offload). Use for: enterprise-grade Q&A, summarization, transcription, document intelligence, and any multimodal workload that needs audio understanding alongside code.

**DeepSeek-V4-Pro was intentionally NOT pulled.** The ollama.com page tags it `cloud` (no local size tags) — cloud-only, not free for local inference. Skipped per the user's "must be free" constraint.

**Dispatch via Codex CLI (recommended — same dispatch UX as the cloud Codex flow):**
```
codex exec --oss --local-provider ollama -m qwen3.6:27b -s read-only "<prompt>"
# faster small-model dispatches:
codex exec --oss --local-provider ollama -m gemma4:e4b -s read-only "<prompt>"
# multimodal / audio / NVIDIA-optimized:
codex exec --oss --local-provider ollama -m nemotron3:33b -s read-only "<prompt>"
```
The agentic loop (read files, edit files, search) all works the same — just the LLM is local instead of OpenAI cloud. Token cost: zero. Inference cost: time + electricity.

**Dispatch via raw REST (for ad-hoc smoke tests / shim scripts):**
```bash
curl -s -X POST http://127.0.0.1:11434/api/generate -d '{"model":"qwen3.6:27b","prompt":"...","stream":false,"think":false,"options":{"temperature":0}}'
```

**When to use local vs cloud:**
- **Cloud Codex / Copilot** — high-stakes synthetics, complex graph APIs, anything that needs first-try ship-ready output. Token cost: real. Speed: 60-150 tok/s.
- **Local Qwen 3.6 27b** — bulk dispatches, "what would you do here?" exploratory pings, anything where waiting 5-15 min is fine. Free, no quota, runs offline. Speed: 3-6 tok/s.
- **Local Nemotron 3 33b** — multimodal (vision / audio / docs) workflows, NVIDIA-optimized so faster than Qwen 27b on this rig despite the bigger size. ~15 tok/s. Free, no quota.
- **Local Gemma 4 e4b** — quick reply / yes-no / pattern-match / outline drafts. Fast (30-50 tok/s) but weaker reasoning. Free, no quota.

**Prompt-discipline recipe transfers to local models too** — the same 5-step recipe below (literal template, upstream contract, forbid the shortcut, pin test style, ordered reading) works for local Qwen and Gemma. The same wrong-API-naming traps still apply. Local-model dispatches should follow the SAME prompt template that PR #92 and PR #99 used for Copilot retries.

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
