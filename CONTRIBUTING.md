# Contributing to UnrealClaudeMCP

Thanks for considering a contribution! This project is **vendor-neutral MCP** — the wire protocol is open and the bridge is intentionally framework-agnostic. Anything that improves the editor-automation surface, hardens the bridge, or polishes the docs is welcome.

Please skim this file before opening a PR. Three of the rules are slightly unusual for a standard open-source project; they exist because the codebase has 80 tools and a non-trivial cross-language split between C++ (UE plugin) and Python (bridge).

---

## Quickstart

```bash
git clone https://github.com/NAJEMWEHBE/UnrealClaudeMCP
cd UnrealClaudeMCP
pip install pytest pytest-cov
pytest tests/                                       # bridge tests, no UE required
python scripts/drift_sweep.py                       # canonical-count guard
```

If you're going to touch C++: have UE 5.7 + Visual Studio Build Tools 2022 ready. The plugin needs to compile against a real UE installation. See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) for the host-build flow.

If you're only touching the bridge / tests / docs: no UE needed. The bridge tests run in pure pytest.

---

## Project layout (where things live)

- `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_*.cpp` — one MCP method per file. **One handler = one .cpp file** (this is the project's load-bearing convention; do not consolidate).
- `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp` — handler registration. Every handler needs a forward `extern` and a `Reg.Register(...)` line here.
- `UnrealClaudeMCP/Resources/mcp_manifest.json` — declarative MCP tool manifest. Mirrors `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list. Drift here is caught by `tests/test_manifest_sync.py`.
- `bridge/unreal_claude_mcp_bridge.py` — the Python stdio↔TCP bridge. Holds the static tool catalog (`TOOLS`), the synthetic-tool dispatch dict (`SYNTHETIC_TOOLS`), and the 16 `synthetic_*` functions that compose existing handlers bridge-side.
- `tests/` — pytest suite for the bridge. **No UE required.** 280+ test cases.
- `scripts/drift_sweep.py` — mechanical doc-drift guard. Scans 11 high-traffic files and rejects stale counts.
- `docs/TOOLS.md` — per-tool reference (params, returns, error codes, examples).
- `docs/ARCHITECTURE.md` — how the pieces fit + UE 5.7 API gotchas + threading notes.
- `docs/HANDOFF.md` — per-session chronology. Maintainer + agent context, not human-visitor reading material.
- `CHANGELOG.md` — human-facing release notes.

---

## What kind of contribution fits where

**Adding a new tool?** Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) first, then follow the 10-step playbook in [`docs/RESUME.md`](docs/RESUME.md):

1. Decide: C++ handler or bridge-side synthetic? Use a synthetic when the new tool *composes* existing handlers or runs `unreal.*` Python via the marker pattern. Use C++ when the tool needs UE native API access that the existing handlers don't expose.
2. C++ track: add `Source/.../Handlers/Handler_<NewTool>.cpp`, add the `extern` + `Reg.Register` line in `UnrealClaudeMCPModule.cpp`.
3. Either track: add the schema entry to `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list and `UnrealClaudeMCP/Resources/mcp_manifest.json`.
4. Synthetic track: add the function `synthetic_<new_tool>(req_id, args: dict) -> dict` and register it in `SYNTHETIC_TOOLS`. Bump `EXPECTED_SYNTHETIC_TOOL_COUNT` in `tests/conftest.py`.
5. Add behavioural tests to `tests/test_bridge.py`: schema + happy path + at least one error path + at least one input-validation path.
6. Run `python scripts/drift_sweep.py` — apply every doc bump it flags (typically 8 files).
7. Run `pytest tests/` — full suite green.
8. Open a PR; let the multi-agent ensemble + Gemini auto-review do their pass before requesting human review.

**Fixing a bug?** Open an issue first if it isn't obvious. Tests-first when reasonable. The bridge has 280+ pytest cases — if the bug is in the bridge, write a failing test, then fix it.

**Improving docs?** Welcome. Just keep the canonical-count phrasing in digit form (`80 tools`, not `Eighty tools`) — `scripts/drift_sweep.py` enforces digit counts, and English-word counts silently survive the sweep.

**Polishing the C++ surface?** Be aware that several handlers carry an explicit `// Error format: free-form OutError strings (legacy surface)` annotation. That annotation is intentional — those handlers predate the canonical `<tool>: <error_code>: <detail>` convention used by later handlers. **Migrating them is a behaviour change**, not a doc fix; please open an issue first to discuss the migration path before sending a PR.

---

## Project conventions

- **One handler = one .cpp file.** Don't grow the foundation; add leaves.
- **`req_id` is intentionally untyped** in `synthetic_*` signatures. JSON-RPC 2.0 / MCP allow string, int, or null IDs; coercing would break correlation for clients using non-integer IDs.
- **Error envelopes use stable codes**: `-32602` (invalid_arguments / missing required field), `-32603` (internal — Python interpreter raised, etc), `-32099` (UE server unreachable), `-32000` (logical error propagated from a C++ handler).
- **Logical errors return as `ok=False` success envelopes**, not as JSON-RPC errors, when the caller might reasonably want to retry or branch. Transport errors stay as JSON-RPC errors. See `inspect_data_asset` for the canonical pattern.
- **Synthetic tools must validate `isinstance(args, dict)` early** and return a clean `-32602 invalid_arguments` envelope on mismatch. All 16 synthetics do this; the test `test_synthetic_returns_invalid_arguments_for_non_dict_args` locks it.
- **Vendor-neutral language in user-facing copy.** The "Claude" in the repo name is decorative; the bridge speaks the open MCP protocol and works with any MCP-compliant client (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, ...). Don't bake "Claude Code" specifically into tool descriptions, the `.uplugin` Description, or new docs.
- **Push to feature branches, never directly to `main`.** Open a PR. CI matrix + Gemini auto-review run on every PR.

---

## How the project gets developed

This is unusual for an open-source repo, so worth flagging up front: **the project uses a multi-agent ensemble for in-flight code review**. Every substantive change passes through 2-4 model reviewers before a maintainer-merge:

- An orchestrating model coordinates the change and integrates the ensemble's findings.
- C++ work goes through a code-specialist model; Python work goes through a Python-fluent model.
- A reasoning-tuned model audits UE 5.7 API surface choices before C++ goes to compile (we have a documented trap-table — see `docs/ARCHITECTURE.md` § "UE 5.7 API gotchas").
- A second-opinion model passes the final diff before push.
- Gemini auto-review fires on every PR open as a post-PR safety net.

Specific provider/model identifiers are configuration, not public documentation. Your PR will still go through human review — the ensemble exists to catch convention drift, missing tests, and the UE 5.7 wrapper-trap class of bugs that a single reviewer would miss. It does not gate merges.

If your PR is rejected by a reviewer's comment that doesn't make sense to you: respond on the PR thread. The maintainer arbitrates.

---

## CI matrix

GitHub Actions runs the bridge test suite on every push and PR across Python 3.11, 3.12, 3.13, 3.14. Plus a `detect changes` job that skips matrix runs for pure-docs PRs (e.g. CHANGELOG-only updates).

The matrix passing is necessary but not sufficient — `python scripts/drift_sweep.py` also has to be clean. CI runs that too.

---

## Reporting security issues

Please do NOT open a public issue for security concerns. The plugin binds 127.0.0.1 only (localhost), so the attack surface is mostly local-user-already-has-shell. But: if you find a way to trigger arbitrary code execution through a crafted MCP tools/call, please disclose privately first via GitHub's private-vulnerability-reporting flow on this repo.

---

## License

By contributing, you agree your contribution is licensed under the project's MIT License. See [`LICENSE`](LICENSE).
