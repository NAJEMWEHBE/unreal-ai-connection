# ADR-0002: High-Fidelity Asset Authoring Uses External DCC Tooling — Documented as a Workflow, Never Bundled

## Status

Accepted (2026-05-18)

## Context

This plugin is a pure Unreal Engine editor-automation surface: a C++ editor
module plus a Python stdio↔TCP bridge that lets any MCP-compliant client drive
the running editor. It deliberately does **not** model or generate art assets
itself — it *consumes* assets that already exist in the project's content
tree (spawn, transform, light, render, inspect).

Producing genuinely high-fidelity assets (clean topology, proper UVs, real
PBR texture sets, sane LODs) is the job of a Digital Content Creation (DCC)
tool — Blender being the obvious free option, drivable headlessly via an
external Blender-automation MCP server. The temptation, when an external
Blender MCP is available on the same machine, is to fold it into this project:
add it as a plugin dependency, write a C++ handler that shells out to Blender,
or otherwise couple the two so a single client call "models and imports an
asset" end to end.

That coupling would violate the constraints this repo already operates under:

- **ADR-0001** scopes the plugin to a single officially-supported surface
  (UE 5.7 automation) and keeps the foundation small; bundling a second,
  unrelated automation runtime is the opposite of that.
- **CLAUDE.md house rules** — "one handler = one `.cpp`; don't grow the
  foundation, add leaves" and "vendor-neutral framing in anything that
  ships". A Blender-specific C++ handler is both a foundation change and a
  named-third-party coupling.
- The plugin's value proposition is that it is **self-contained**: drop one
  folder into `Plugins/`, build, and every tool works with zero external
  services beyond the bridge. A hard Blender dependency breaks that for every
  user who only wants editor automation.

The asset-authoring need is real, but it belongs **beside** the plugin as a
documented, user-side workflow — not **inside** it as code.

## Decision

**High-fidelity asset authoring is performed by external DCC tooling that
stays a separate, user-side server. It is documented as a reproducible
workflow; it is never bundled into the Unreal plugin.**

Concretely:

- **No new plugin dependency.** The `.uplugin` gains no Blender (or any DCC)
  dependency. Building and running the plugin never requires Blender or any
  external authoring server.
- **No external-tool C++ handler.** There is no `Handler_Blender*.cpp` (or
  any DCC-shelling handler). The plugin's handler set stays exactly what it
  is — pure Unreal automation. Assets authored externally enter the project
  through the **existing** seam: `execute_unreal_python` running an
  `unreal.AssetImportTask` (and `import_texture` for standalone textures).
  No new foundation is grown for this.
- **Two-server separation.** The external Blender-automation MCP authors
  assets; *this* MCP consumes them. The two servers are independent
  processes with independent lifecycles and never merge. Nothing
  Blender-specific ships in this repository — the plugin stays
  vendor-neutral and self-contained.
- **Generated assets are not committed.** `.blend` project files and exported
  meshes/textures are build *inputs* produced per the documented workflow,
  not source. They are `.gitignore`d (see `.gitignore` and the workflow doc),
  exactly as `/dist/` and the harness-dropped `/hf_*.png` validation inputs
  already are.
- **The workflow lives in docs.** `docs/ASSET-PIPELINE-BLENDER.md` is the
  long-term, vendor-neutral, reproducible reference: author one asset at a
  time in Blender with CC0 PBR, export to a gitignored host scratch dir,
  import via the existing `execute_unreal_python` seam, continue with the
  plugin's existing tools.
- **Fully reversible.** This decision adds documentation and a `.gitignore`
  block only. No code is added or removed, so it can be revisited later
  (e.g. if a future contributor proposes an *optional, opt-in* integration)
  without unwinding anything in the tree.

## Consequences

- Contributors who use a DCC tool to produce assets **document the workflow
  in `docs/`** and record honest validation evidence (what was built, how it
  was verified — mirroring the existing `docs/validation/` notes style),
  rather than adding authoring code to the plugin.
- The plugin remains **pure Unreal automation**: one folder, build, done — no
  second runtime, no DCC service, no named third-party coupling in anything
  that ships. The vendor-neutral / self-contained guarantees of ADR-0001 and
  the house rules are preserved.
- Users who only want editor automation are **unaffected** — they never
  install Blender or any external authoring server.
- The asset-quality ceiling for the automated path is bounded by what the
  external DCC workflow + CC0 assets can produce; this is stated plainly in
  `docs/ASSET-PIPELINE-BLENDER.md` rather than implied to be unlimited.
- **Reversible / expandable**: because nothing is coded into the plugin, a
  future opt-in integration (if ever justified) can be added without
  reconstructing or untangling anything; equally, the doc can evolve as the
  external tooling changes, with no plugin churn.

## References

- [`ADR-0001`](ADR-0001-ue57-only-freeze-cross-engine-compat.md) — UE 5.7 is
  the single officially-supported surface; keep the foundation small.
- [`docs/ASSET-PIPELINE-BLENDER.md`](../ASSET-PIPELINE-BLENDER.md) — the
  external DCC authoring workflow this ADR points to (vendor-neutral,
  reproducible, honest about limits).
- `CLAUDE.md` house rules — "one handler = one `.cpp`; don't grow the
  foundation, add leaves" and the vendor-neutral framing rule.
- `.gitignore` — the external-DCC asset-scratch exclusion block (generated
  inputs, never committed), alongside the existing `/dist/` and `/hf_*.png`
  rationale.
