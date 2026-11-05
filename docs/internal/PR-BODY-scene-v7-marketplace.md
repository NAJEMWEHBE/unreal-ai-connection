## Summary

- **Marketplace tools (new).** Two bridge-side synthetic MCP tools — `marketplace_search` and `marketplace_import` — surface CC0 / free-to-use 3D assets from Polyhaven (and AmbientCG search) directly into the editor. No auth, no API key, stdlib `urllib` only. v1 supports textures (diffuse) + HDRIs; models parked for v2.
- **High-quality textured rebuild (v7).** Procedurally-built `M_TexturedSurface` master material + four child MIs bound to Polyhaven sand / rock / rust / metal-plate textures. `build_desert_scene.py` promotes them over the legacy flat-color `BasicShapeMaterial` MIs with a `_load_or_fallback` helper, so the script still produces a runnable scene when the marketplace imports haven't run.
- **Brightness fix.** v4 was too dark and red ("I'm in hell"). v6/v6.1 retunes to neutral midday daylight: sun 2600K → 5500K, pitch −3° → −35°, `SkyAtmosphere` defaults restored, fog density 0.12 → 0.04, post-process bias −1.8 → 0.0, saturation neutralised.
- **Staged-capture flag.** `build_desert_scene.py` exposes a `builtins.DESERT_BUILD_STAGE` switch so an external orchestrator can stop the build after wipe / atmosphere / geometry / props for workflow-progression captures. No behavior change when unset.
- **Workflow captures.** `docs/validation/workflow/T0-empty.png` (post-wipe sanity check) and `docs/validation/workflow/T4-hero.png` (v6 daylight final). `docs/validation/scene-proof.png` refreshed.
- **Tool count: 100 → 102** (71 native C++ + 31 synthetic). All catalog plumbing (bridge `TOOLS` list, `SYNTHETIC_TOOLS` dict, `mcp_manifest.json`, `docs/TOOLS.md`) in sync; `tests/conftest.py::EXPECTED_SYNTHETIC_TOOL_COUNT` bumped 29 → 31; doc-drift counts updated across nine status docs.

## Test plan

- [ ] CI green (400 tests, including manifest-sync drift suite + doc-drift sweep + personal-leak scan).
- [ ] Smoke `marketplace_search` against Polyhaven for "sand desert texture" — expect `coast_sand_rocks_02` near the top of the result list.
- [ ] Smoke `marketplace_import` with `slug=aerial_beach_01 resolution=1k format=jpg` — expect a `T_aerial_beach_01` `UTexture2D` to materialise under `/Game/Marketplace/`.
- [ ] Re-run `scripts/build_desert_scene.py` end-to-end in UE 5.7 with no `DESERT_BUILD_STAGE` flag set → `SCENE_BUILD_COMPLETE_V7_TEXTURED` in `LogPython`, hero shot reads as bright daylight desert (not crimson burnout).
- [ ] Bot-review gate (Gemini / CodeRabbit / chatgpt-codex-connector / greptile-apps / Copilot CLI) — apply findings as follow-up commits or dismiss with verifiable reason.

## Known follow-ups

- Re-capture T1/T2/T3 with v7 textured lighting once the HighResShot pipeline is reliable (UE window-focus state currently affects it).
- Switch `SkyLight` to specified-cubemap mode pointing at the imported Polyhaven daylight HDRI (`HDRI_Sky_Daylight`) once a clean longlat→cubemap Python conversion is verified.
- Multi-map PBR import (Diffuse + Normal + Rough + AO + Disp) for textures + zip-archive support for AmbientCG → v2 of `marketplace_import`.
- Niagara dust template `BlowingParticles` is a `NiagaraEmitter`; `NiagaraComponent.set_asset` expects a `NiagaraSystem`. Pre-existing v3 issue, non-blocking. Fix path: swap to a `NiagaraSystem` template or wrap the emitter.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
