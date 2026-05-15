# Picture-to-Unreal test — elven city (2026-05-16)

Final live-UE test requested by the maintainer: turn the reference
images (a Rivendell-style elven city — main vista + a side-angle frame +
a top-down construction-map frame) into a live Unreal scene through the
MCP pipeline.

## What this proves (the actual point of the test)

The full **picture → Unreal** round-trip works end-to-end through the
bridge with zero manual editor clicks:

- `scripts/elven_city_scene.py` ran via the MCP `run_python_file` tool
  against the live UE 5.7 editor.
- It built ~200 actors (cliff/mountain mass, 4 stacked elven hall tiers
  + cathedral spire, a 7-arch viaduct spanning the gorge, a domed
  pavilion with a glowing beacon crystal, descending side-stairs,
  waterfalls, a ~90-tree autumn pine forest) plus 8 in-script
  constant-color master materials and a full golden-hour lighting rig
  (DirectionalLight + SkyAtmosphere + real-time-capture SkyLight +
  ExponentialHeightFog + PostProcessVolume).
- Camera was posed and `take_high_res_screenshot` fired for three hero
  angles matching the three reference frames; the level + the
  `/Game/ElvenCity` materials were saved; the editor was closed
  gracefully with `quit_editor()` (never force-killed).

The script is **idempotent** — it wipes everything labelled `ELV_*` and
rebuilds, so it is safe to re-run and iterate.

## Honest assessment of the visual result

The captures (`elven-city-A/B/C-2026-05-16.png`) are a **functional
blockout, not a finished environment**. It reads as the right
*structure* (vertical layering, central spire, viaduct, scattered
forest) but not the *art* of the reference matte painting. Known gaps,
for the next iteration:

- It was built into the **shared host project** (`HDMediaVirtualStudio`)
  which has its own pre-existing ground/lighting; that fights the scene
  (bright midday wash instead of the intended golden hour, a default
  checker ground showing through). A clean dedicated empty level is the
  right canvas for an art pass.
- Geometry is BasicShapes primitives with flat constant-color materials
  — deliberately dependency-free so the test runs on any project, but it
  caps fidelity. Real meshes + PBR/marketplace materials (the pipeline
  for which already exists — see the Florence work) would close most of
  the gap.
- HighResShot is async and was flaky under rapid repeated calls in the
  shared viewport; the saved frames are the first-pass captures.

## Conclusion

Pipeline: **proven**. Art fidelity: **rough blockout — needs a
dedicated level + real assets for a faithful pass.** Re-run
`scripts/elven_city_scene.py` (idempotent) to iterate.
