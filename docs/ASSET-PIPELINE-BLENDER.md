# Authoring high-fidelity assets in Blender, consuming them in Unreal

## TL;DR

This plugin automates the **Unreal** editor. It does not model assets. To get
genuinely high-fidelity geometry into a scene you author the asset in an
external DCC tool — Blender, drivable headlessly via a separate
Blender-automation MCP server — export it to a local scratch folder, and then
**import it through this plugin's existing seam** (`execute_unreal_python`
running an `unreal.AssetImportTask`; `import_texture` for standalone images).
Nothing Blender-specific ships in this repo. The two servers stay separate.
Exported `.blend`/mesh/texture files are gitignored build inputs, never
committed. This document is the long-term, reproducible reference; it states
the real ceiling plainly rather than implying the automated path produces
bespoke AAA art.

## The two-MCP separation principle (read this first)

There are **two independent MCP servers** in this picture, and they never
merge:

| Server | Role | Ships in this repo? |
|--------|------|---------------------|
| **Blender automation MCP** (external, user-side) | *Authors* high-fidelity assets — model, UV, PBR-texture, export | **No.** Not a dependency, not a submodule, not a handler. |
| **This Unreal MCP** (this repo) | *Consumes* finished assets — import, place, light, render, inspect | Yes — pure Unreal automation. |

This split is a hard architectural decision, recorded in
[`ADR-0002`](adr/ADR-0002-external-asset-authoring-not-bundled.md):

- The Unreal plugin gains **no** Blender dependency in its `.uplugin`.
- There is **no** Blender-shelling C++ handler (no `Handler_Blender*.cpp`).
  Per the house rule "one handler = one `.cpp`; don't grow the foundation",
  assets enter through the **existing** Python-execution seam, not new
  plugin code.
- The plugin stays self-contained and vendor-neutral: someone who only wants
  editor automation never installs Blender or any authoring server.
- This is fully reversible — it is documentation plus a `.gitignore` block,
  no code added or removed.

If you find yourself wanting to "wire Blender into the plugin", stop: that is
explicitly out of scope. The seam below is the supported path.

## Step 1 — Author the asset in Blender

Drive Blender via your external Blender-automation MCP server (or by hand —
the workflow is the same). Principles that keep an asset usable in UE 5.7:

- **One asset at a time.** Model, UV-unwrap, and texture a single mesh per
  pass. Batch "scenes" produced blind tend to import as a tangle; a clean
  single asset is reviewable and reusable.
- **Real PBR from CC0 sources.** Use CC0 texture sets — Poly Haven and
  AmbientCG are the reliable, license-clean options (the same sources the
  `docs/validation/` notes used: AmbientCG `Marble012`, Poly Haven
  `aerial_rocks_02` / `forrest_ground_03`, full albedo / normal / roughness
  / AO / displacement sets). Hook them into a principled BSDF so the export
  carries a sane material.
- **Sane polycount and UVs for UE.** Keep triangle counts proportionate to
  the asset's on-screen size, give every mesh a single non-overlapping UV
  set (a second UV channel for lightmaps if you are not relying purely on
  Lumen), and apply transforms (scale = 1, rotation = 0) before export so
  the asset lands at a predictable size in Unreal.

## Step 2 — Export to a gitignored host scratch dir

Export from Blender to a local scratch directory. Recommended layout:

- **Scratch dir:** `F:\blender-exports\` (Windows host convention used by the
  helper script's defaults; override with `--src-dir` or `UCMCP_*` env).
- **Format:** prefer **`.glb`** (glTF 2.0 binary, *GLB / embedded* —
  geometry + textures in one file, simplest to move and import). `.fbx`
  plus a sidecar texture folder also works if your pipeline needs FBX.

This directory is **gitignored** (`*.blend`, `*.blend1`, `/blender-exports/`,
`/asset-src/` — see [`.gitignore`](../.gitignore) and
[`ADR-0002`](adr/ADR-0002-external-asset-authoring-not-bundled.md)). These are
generated build *inputs*, not source — they are never committed, exactly like
the existing `/dist/` release artifacts and the `/hf_*.png` validation
inputs.

## Step 3 — Import into Unreal via the existing seam

**There is no mesh-import handler in this plugin, and by design there will
not be one** (ADR-0002 / house rules). The supported import path is the
universal escape hatch — `execute_unreal_python` running UE's canonical
`unreal.AssetImportTask`. Paste this block to a live editor through the
`execute_unreal_python` tool (adjust the three paths):

```python
import unreal
task = unreal.AssetImportTask()
task.filename = r"F:\blender-exports\hero.glb"
task.destination_path = "/Game/BlenderImports"
task.destination_name = "SM_Hero"
task.automated = True
task.save = True
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
```

Notes:

- **Standalone textures** (a loose PNG/EXR not embedded in a GLB) have a
  dedicated native handler: `import_texture` (`Handler_ImportTexture.cpp`)
  brings an image in as a `UTexture2D` via UE's canonical import path; pair
  it with `configure_texture` for sRGB / compression flags.
- **Material assignment / tuning** after import is done with the existing
  tools: `execute_unreal_python` for arbitrary wiring, plus
  `create_material_instance` / `set_mi_parameter` for MI parameter
  overrides. No new plugin surface is needed.
- `scripts/import_blender_assets.py` automates exactly this loop
  (import → verify the asset exists → spawn into a scratch level → frame
  it) for every `.glb`/`.fbx` in the scratch dir; it is a thin reproducible
  driver over this same seam, not new plugin code.

## Step 4 — Continue in Unreal with the existing MCP tools

Once the asset is a `/Game/...` asset it is ordinary Unreal content. Use the
plugin's existing tools as normal:

- `spawn_actor` to place a `StaticMeshActor` referencing the imported mesh
  (or set the mesh via `set_actor_property` / `execute_unreal_python`).
- `set_actor_transform` to position it.
- The lighting / atmosphere / post-process recipe documented in
  `docs/validation/elven-hifi-2026-05-16-NOTES.md` (Sky Atmosphere +
  Volumetric Clouds + golden-hour Directional sun + a forced-manual-exposure
  Post-Process Volume) for a cinematic frame.
- `render_camera_to_png` for a deterministic headless capture (this is the
  handler that makes headless render reliable — see the README roadmap note;
  it requires a host UE 5.7 rebuild to be live).

## Clean-exit requirement

Automation that spawns actors into the editor's **open, unsaved** world and
is then force-killed leaves a dirty `Untitled` map behind; UE then shows a
**Restore-Packages** dialog on the next launch. To avoid this, automated
asset/demo runs **build into a dedicated throwaway scratch level**, not the
user's open map, and attempt a **graceful editor quit** before any
force-kill. This is implemented in `scripts/import_blender_assets.py` and in
the clean-exit fix to `scripts/capture_demo_gif.py` (dedicated
`/Game/_McpScratch/...` level + best-effort `unreal.SystemLibrary.quit_editor()`
with `Stop-Process -Force` kept only as a last resort).

> **If a stale Restore-Packages dialog ever appears, answer "Skip
> Restore".** These automation runs build a throwaway scratch level, not
> user content — there is nothing in the autosave worth recovering, and
> restoring it just re-dirties the editor.

The specific UE editor APIs used for the new-level creation and the graceful
quit (`unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).new_level(...)`,
`unreal.SystemLibrary.quit_editor()`, with `unreal.EditorLevelLibrary`
fallbacks) are written **defensively / best-effort** — each call is wrapped
so a failure degrades to the next fallback rather than aborting. They are
**proof-pending**: validated end to end only in a live Stage-C run (UE is not
running while this document is authored). They mirror the proven
`get_editor_subsystem(LevelEditorSubsystem)` → `EditorLevelLibrary` fallback
pattern already used by `scripts/elven_city_hifi.py`.

## Honest ceiling / limits

State this plainly, the way `docs/validation/elven-hifi-2026-05-16-NOTES.md`
does — do not let this read as "the automated path makes AAA art":

- **No automatic LOD generation.** UE will not invent good LODs for an
  imported mesh; author LODs in Blender or accept a single-LOD asset.
- **UVs and scale are your responsibility.** Overlapping UVs, a missing
  lightmap UV, or un-applied Blender transforms all survive the import and
  show up as artifacts or wrong-sized actors. The seam imports faithfully;
  it does not fix authoring mistakes.
- **CC0 + procedural is a competent ceiling, not a bespoke one.** A clean
  Blender asset with real CC0 PBR, imported and lit with the existing
  atmosphere/grading recipe, is a *competent high-fidelity real-time
  result*. It is **not** equivalent to hand-authored AAA-studio art
  (artist-years, paid pipelines, thousands of bespoke assets). Crossing into
  that tier needs Fab / paid assets + artist time and is out of scope for an
  automated CC0 workflow — exactly the boundary stated in the elven-hifi
  validation notes.
- **Import is not validation.** A successful `import_asset_tasks` call means
  the file parsed, not that the asset looks right. Verify with the plugin's
  introspection tools (`inspect_static_mesh`, `inspect_material_instance`)
  and an actual render, and record honest evidence in `docs/validation/`.
