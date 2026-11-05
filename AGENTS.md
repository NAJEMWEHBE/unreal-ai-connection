# AGENTS.md

Context for any coding agent working on Unreal AI Connection: a UE 5.7 editor plugin plus a stdlib-only Python bridge that serves editor automation to MCP clients over `127.0.0.1:18888`. The wire protocol is open MCP, so everything that ships stays vendor-neutral.

**Start here:** read the "At a glance" section of [`docs/HANDOFF.md`](docs/HANDOFF.md) for the current state and open work. If a gitignored `CLAUDE.local.md` exists, read it too: it holds this machine's engine and host-project paths and the maintainer's standing permissions.

## Layout

- **C++ handlers:** `UnrealAIConnection/Source/UnrealAIConnection/Private/MCP/Handlers/Handler_*.cpp`, one MCP method each (`GetMethodName()`), registered in the `Reg.Register(...)` block of `UnrealAIConnectionModule.cpp`. Optional companion plugins (`UnrealAIConnectionDMX`, `-Geometry`, `-NDisplay`, `-OCIO`) register their own handlers the same way.
- **Bridge:** `bridge/unreal_ai_connection_bridge.py`. `TOOLS` is the advertised catalog; `SYNTHETIC_TOOLS` maps the bridge-side tools (composed from existing handlers or run as editor Python) to their functions; everything else is forwarded to UE.
- **Catalog, three places:** `TOOLS`, `UnrealAIConnection/Resources/mcp_manifest.json` and `docs/TOOLS.md`. `tests/test_manifest_sync.py` and `tests/test_catalog_sync.py` fail when they disagree with each other or with the C++ handlers.
- **Counts:** the exact counts are `EXPECTED_*` in `tests/conftest.py`, tied to the code by `test_catalog_sync`. Prose says "150+ tools"; exact numbers appear only in the README badge and Status row, the `docs/TOOLS.md` intro and the shipped metadata (`.uplugin`, manifest, `.claude-plugin/*.json`).
- **Reference:** `docs/ARCHITECTURE.md` (design and UE 5.7 API gotchas), `docs/TOOLS.md` (per-tool schemas and examples), `docs/HANDOFF.md` (state, runbook, closing notes).

## House rules

- **One handler, one `.cpp`:** a new native tool is a new `Handler_*.cpp` plus one `extern` declaration and one `Reg.Register(...)` line. Add leaves; leave the foundation alone.
- **Verify every UE API against UE 5.7 source** before committing C++. Reviewer agents have asserted UE APIs that don't exist.
- **Vendor-neutral copy** in everything that ships: the `.uplugin` Description, README, tool descriptions, metadata. Name MCP clients as examples, never as the target.
- **Feature branches and PRs only;** the maintainer merges.
- **Bot-review gate:** after CI is green and before merging, read every bot review on the PR. Apply each finding as a follow-up commit on the branch, or dismiss it in a PR comment with a verifiable reason. A follow-up that only applies bot fixes surgically can merge without a second bot pass; new logic needs one.
- **C++ is verified on a host build:** CI and pytest never compile C++. A C++ PR merges only after a UE 5.7 host build (`Build.bat`), an editor load and a smoke test (`examples/smoke_test.py` against a live editor).

## Checks before a PR

```bash
python -m pytest                    # bridge suite, no UE needed (Windows: py -m pytest)
python -m ruff check . && python -m ruff format --check .
python scripts/drift_sweep.py       # stale counts and versions in docs and metadata
```

CI runs all three on every PR, plus a job that builds the wheel and runs its `unreal-ai-connection` console script.

## Traps

- **Manifest "required" substring:** `test_manifest_sync.py` greps the word "required" in manifest param descriptions. Word conditional params as "needed when X".
- **`call_ue` returns** `{"jsonrpc", "id", "result"}` or `{"jsonrpc", "id", "error"}`, never a top-level `ok`; branch on `"error" in resp`. On `tools/call` the bridge turns tool failures into results with `isError: true` (the error object as JSON text); only an unknown tool or a malformed request stays a JSON-RPC error.
- **Transport errors keep their code** through synthetic tools: -32099 not reachable, -32098 `ue_busy_timeout` (the request was sent and may still apply), -32700 bad frame. Pass them through; don't remap to -32603.
- **`execute_unreal_python`:** `result.output` is the traceback on failure, not stdout; captured `print()` output comes back in `result.stdout`. `unreal.log()` goes to `LogPython`, read back with `get_log_lines` (see `synthetic_get_camera_transform`).
- **UE 5.7 protected members:** use `USoundCue::GetSubtitlePriority()`, `USoundWave::GetSampleRateForCurrentPlatform()`, `UTexture::GetCompositeTexture()`; handler modules build with warnings as errors. The full list is under "UE 5.7 traps already mapped" in `docs/HANDOFF.md`.
- **Launching the editor from PowerShell:** pre-quote a project path that has spaces inside the argument, `Start-Process '<UE>\Engine\Binaries\Win64\UnrealEditor.exe' -ArgumentList '"<path with spaces>\Project.uproject"'`; otherwise UE opens the Project Browser and port 18888 never binds. A cold launch takes about 2 minutes to bind.

## Registering the bridge

Claude Code, Copilot CLI and Cursor read the project's `.mcp.json` (copy `examples/.mcp.json.example`). Codex CLI:

```bash
codex mcp add unreal-ai-connection -- python <repo>/bridge/unreal_ai_connection_bridge.py
```

On Windows, use `py` instead of `python` if `python` resolves to the Microsoft Store stub. Per-client guides: `docs/setup/`.

## Briefing another agent

A coding brief that ships on the first try (proven with Codex and Copilot CLI):

1. Name a template by file path and line range for the agent to mirror.
2. Spell out the contract: return shapes, error versus success keys.
3. Name the shortcut it must not take.
4. Pin the test style: mock library and assertion style.
5. Number the reading order.

## Finishing a session

Add a closing note to `docs/HANDOFF.md` for any session that ships meaningful changes, and update its "At a glance" section. Closing notes record history: leave older ones as they are.
