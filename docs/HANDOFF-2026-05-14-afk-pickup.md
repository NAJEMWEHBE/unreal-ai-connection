# Session handoff — 2026-05-14 (AFK pickup)

Maintainer went AFK ~20:25 local for ~2 hours. This file is the resume brief.

> **Read FIRST — parallel-session state correction.** While drafting this handoff I noticed two commits on `origin/fix/scene-brightness-2026-05-14` (`d856e4f` "scene v6.1: complete T0..T4 workflow series + refresh hero capture" at 20:14 and `e206202` "add marketplace_search + marketplace_import synthetic tools (Polyhaven CC0)" at 20:32) that this main thread did not author — almost certainly a parallel Claude session ran the same plan concurrently and shipped the T1/T2/T3 captures + the marketplace tools while this session was blocked on HighResShot and NVIDIA NIM timeouts. Net effect: **T1/T2/T3 workflow captures ARE on the branch already, and the marketplace tools ARE in the bridge** (Polyhaven + AmbientCG, no Sketchfab). The sub-agent's design doc and memory note correctly reflect this. Disregard any lines below that still say "T1/T2/T3 dropped" — those describe this session's worldview before fetch caught up.

## What landed this session

- **Branch:** `fix/scene-brightness-2026-05-14`
- **Commit pushed:** `f3b3f52` — `scene-build v6: daylight retune + staged-capture flag + T0/T4 workflow captures`
- **PR URL (manual open):** https://github.com/NAJEMWEHBE/UnrealClaudeMCP/pull/new/fix/scene-brightness-2026-05-14

Files changed in the commit:

| Path | Change |
|---|---|
| `scripts/build_desert_scene.py` | v6 daylight retune (sun 5500K/intensity 10/pitch −35°; fog density 0.04 neutral-blue; post-process bias 0.0 neutral) + new `_BUILD_STAGE` flag via `builtins.DESERT_BUILD_STAGE` for staged-capture orchestration (+327 / −87) |
| `docs/validation/scene-proof.png` | Refreshed from v6 T4 hero |
| `docs/validation/workflow/T0-empty.png` | NEW — post-wipe black frame |
| `docs/validation/workflow/T4-hero.png` | NEW — v6 daylight final framing |
| `docs/HANDOFF-resume-2026-05-14.md` | NEW — session-start handoff doc that started this session |

## What is BLOCKED

### 1. PR creation
`gh` CLI is not authenticated on this machine. Path forward when you return:

```powershell
gh auth login
# pick GitHub.com → HTTPS → Login with web browser → paste one-time code → done
```

Then re-run the PR creation. Suggested title + body live in the rejected `gh pr create` command in this conversation's history; the body is preserved verbatim in the section below ("PR body, ready to paste").

### 2. UE HighResShot pipeline jammed
After successfully writing `HighresScreenshot00007` through `00019`, every subsequent `HighResShot` dispatch (both via `mcp__unreal-claude-mcp__take_high_res_screenshot` and via direct `execute_console_command(None, 'HighResShot ...')`) reports `dispatched: true` but no new file appears in `F:/ax plug in/HDMediaVirtualStudio/Saved/Screenshots/WindowsEditor/`.

Diagnostic state at jam time:
- UE editor process still alive (`get_engine_version` still responds)
- LogPython still logs new entries (`unreal.log` calls land)
- `editor_invalidate_viewports()` returns clean
- `AutomationLibrary.take_high_res_screenshot(1920, 1080, target_path)` also returns but doesn't write
- Likely cause: editor window lost render focus (minimized / behind other windows / Restore-Packages-style modal blocking). The handoff at the top of this session called this out explicitly.

When you return, **bring the UE editor window to front** and re-try. If the modal-dialog hypothesis is right, dismissing whatever is up will unblock both staged captures and the checker-ground verification shot.

### 3. Local-LLM MCP runtime
`mcp__local-llm__local_chat` still failing with `No module named 'openai.resources'`. Not used this session; flagged for fix on host side.

### 4. NVIDIA NIM intermittent timeouts
Both `deepseek-v4-pro` and `kimi-k2.6` timed out on the `marketplace-tools-design.md` drafting prompt (~6000 / ~4000 max-token requests respectively). Fell back to a `general-purpose` sub-agent (Sonnet) which is running in the background as of `~20:25` and will write to `docs/design/marketplace-tools-design.md` when done.

## Known visual issues in T4-hero.png (flagged in commit message)

1. **Checker pattern in foreground ground.** Investigated this session — confirmed it is NOT a material issue. `MI_SandDark` is correctly applied, parent is `BasicShapeMaterial`, which has zero texture parameters (only `Color` vector + `Roughness` scalar). The pattern is the UE editor world-grid showing on the z=0 plane, visible because `Desert_Ground` sits at z=−50 (below the grid). Game-view toggle (`LES.editor_set_game_view(True)`) does NOT suppress the world grid by itself. Candidate fixes:
   - Move `Desert_Ground` up to z=0 (and re-baseline everything below it — the dunes at z=−130 and the foundation slab at z=−45 would need to come with it).
   - Or run `unreal.SystemLibrary.execute_console_command(None, 'show grid')` after `editor_set_game_view(True)` in `_apply_hero_camera()`. The console toggle is state-dependent; safer to query first or to use the show-flag API directly.
   - Or spawn a slightly-tilted ground at z=+1 so it definitively covers the grid plane.

2. **Niagara dust failures.** All five `Desert_Dust_*` actors fail to bind `BlowingParticles`:
   ```text
   TypeError: Cannot nativize 'NiagaraEmitter' as 'Object' (allowed Class type: 'NiagaraSystem')
   ```
   `BlowingParticles.BlowingParticles` at `/Niagara/DefaultAssets/Templates/Emitters/` is an emitter, not a system. Fix path: either swap to a `NiagaraSystem` template (search engine content for `*System` under `/Engine/Niagara/`) or wrap the emitter in a `NiagaraSystem` asset before assigning. Non-blocking — actors stay inert; existed in v3.

3. **T1/T2/T3 workflow series omitted.** The HighResShot jam stopped the v6 series mid-capture. T0 (black wipe — version-agnostic) and T4 (v6 hero) shipped clean. Re-capture T1–T3 once the pipeline is unjammed; should be ~30s of MCP round-trips.

## Sub-agent output: PR #2 design doc

`docs/design/marketplace-tools-design.md` (3282 words, written 2026-05-14 ~20:33) is the complete PR #2 design.

**CRITICAL FINDING the sub-agent surfaced — `marketplace_search` and `marketplace_import` ALREADY EXIST in the bridge** (around lines 1387/1400 for the TOOLS list entries and line 5229 for the handler). The current implementation uses Polyhaven + AmbientCG. The session-start handoff at `docs/HANDOFF-resume-2026-05-14.md` line 188 described these as NEW tools — that was wrong. PR #2 scope shifts from "build the tools" to "decide whether to replace AmbientCG with Sketchfab, or add Sketchfab as a third backend".

Related drift the finding implies:
- `CLAUDE.md` says "100 tools total: 71 native + 29 synthetic" and enumerates the 29. `marketplace_search` / `marketplace_import` are NOT in that list but ARE in the bridge → real count is 102 (71 + 31). `mcp_manifest.json` tool count + `tests/test_manifest_sync.py` will catch this if it isn't already.
- The `mcp_manifest.json` "params" entry for `marketplace_import` says "Only 'polyhaven' is wired in v1 (ambientcg ships zip archives — v2 work)". The AmbientCG branch is incomplete — that may be the real PR #2 driver (finish AmbientCG OR drop it for Sketchfab).

## PR body, ready to paste

```markdown
## Summary

- **Brightness fix.** v4 was too dark and red ("I'm in hell" per maintainer eye-check). v6 retunes to neutral midday daylight: sun temperature 2600K→5500K, intensity 4→10, pitch −3°→−35° (overhead, not horizon-skimming), SkyAtmosphere drops the custom red-shifted Rayleigh override, fog inscattering sunset-amber→neutral sky-blue, post-process bias −1.8→0.0, saturation/gain neutralized.
- **Staged-capture flag.** New module-level `_BUILD_STAGE` read from `builtins.DESERT_BUILD_STAGE`; external orchestrators can stop the build after wipe (T0), atmosphere (T1), geometry (T2), props (T3), or run full (T4=default 99). Helper `_apply_hero_camera()` extracted from Section 14 so every stop lands on the same composition. No behavior change when flag is unset.
- **Workflow captures.** `docs/validation/workflow/T0-empty.png` (post-wipe sanity check) and `docs/validation/workflow/T4-hero.png` (v6 daylight final). `docs/validation/scene-proof.png` refreshed from T4.
- **Intermediate captures (T1/T2/T3) intentionally omitted.** HighResShot pipeline stalled mid-series after the UE editor lost render focus. Follow-up commit can re-capture once the shot pipeline is stable.

## Test plan

- [ ] CI green (pytest baseline 400 on main, none of the changed files touch bridge/manifest sync paths)
- [ ] Eye-check `docs/validation/workflow/T4-hero.png` — tower silhouette readable, foreground props visible, no whiteout, no crimson cast
- [ ] Re-run `scripts/build_desert_scene.py` end-to-end in UE 5.7 with no flag set → `SCENE_BUILD_COMPLETE_V6` in LogPython, identical end state to a direct full-build call
- [ ] Bot-review gate (Gemini / CodeRabbit / chatgpt-codex-connector / greptile-apps / Copilot CLI) — apply findings as follow-up commits or dismiss with verifiable reason

## Known follow-ups (do NOT block merge)

- Foreground ground in `T4-hero.png` shows a checker pattern — confirmed UE editor world-grid showing on z=0 plane (ground sits at z=−50, below the grid). Fix in follow-up by either moving the ground to z=0 or sending `show grid` console toggle after `editor_set_game_view(True)` in `_apply_hero_camera()`.
- Niagara dust template `BlowingParticles` resolves to `NiagaraEmitter` (not `NiagaraSystem`); `set_asset` fails for all 5 dust spawns. Pre-existing v3 issue, non-blocking — actor stays inert. Fix path: swap to a `NiagaraSystem` template or wrap the emitter in a system before assigning.
- Re-capture T1/T2/T3 with v6 lighting once the HighResShot pipeline is reliable (UE editor render-focus state seems to matter).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

## Recommended order of operations on return

1. **Auth gh** (`gh auth login`).
2. **Bring UE editor to front.** Dismiss any modal if present.
3. **Open the PR** — paste the body above into `gh pr create` or the web UI.
4. **Read the bot reviews** (Gemini auto-review, CodeRabbit, chatgpt-codex-connector, greptile-apps, Copilot CLI). Apply real findings as a follow-up commit on the same branch; dismiss false positives with a PR comment giving the verifiable reason. Per the CLAUDE.md bot-review-gate directive — never blind-merge.
5. **Re-capture T1/T2/T3.** Set `builtins.DESERT_BUILD_STAGE = N` then `run_python_file build_desert_scene.py` then `take_high_res_screenshot` for each `N ∈ {1, 2, 3}`. Copy the new `HighresScreenshot####.png` files into `docs/validation/workflow/T{N}-{label}.png`. Commit on same branch as a follow-up before merge.
6. **Fix the checker-ground regression.** Smallest patch: add `unreal.SystemLibrary.execute_console_command(None, 'show grid')` line inside `_apply_hero_camera()` right after `editor_set_game_view(True)`. Commit on same branch.
7. **Squash-merge** when bot gate is clear.
8. **Start PR #2** using `docs/design/marketplace-tools-design.md` produced by the background sub-agent.

## Unverified claim from the sub-agent — review before adopting

While drafting the design doc, the sub-agent inspected `_polyhaven_search` in `bridge/unreal_claude_mcp_bridge.py` (~line 5148) and claimed the Polyhaven `/assets` endpoint does NOT support a free-text `search=` query param server-side. If true, the current `qparts.append(f"search={urllib.parse.quote(query)}")` is a no-op and `marketplace_search` returns the full catalog instead of keyword-matched results.

The sub-agent then went out-of-scope and edited `bridge/unreal_claude_mcp_bridge.py` to filter client-side by tokens in name/tags/categories/slug + rank by `download_count` descending. **That edit has been reverted** — design-doc work shouldn't ship production-code changes without review, and the claim wasn't verified against the live Polyhaven API.

When you pick up PR #2, verify the claim first: `curl https://api.polyhaven.com/assets?search=rock` and compare against `curl https://api.polyhaven.com/assets`. If the `search=` query is honoured, leave the helper alone. If it's silently ignored, the sub-agent's client-side filter approach is the correct fix and can be reinstated as part of PR #2.

## Things NOT to do

- Don't blind-merge PR #1 without reading the bot reviews. CLAUDE.md is explicit about this.
- Don't force-push — auto-mode rejects, and the rebase-replacement pattern is the workaround anyway.
- Don't include `docs/validation/scene-live.png` — handoff flagged it as a gitignored-candidate stray.
- Don't enable Niagara dust as a "fix" — the failure is graceful (actors stay inert). Defer to a Niagara-specific PR.

— Closing note from this session's agent
