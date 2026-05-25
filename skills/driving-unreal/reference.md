# driving-unreal — workflow recipes & UE gotchas

Detailed companion to [`SKILL.md`](SKILL.md). Every tool named here is verified against the bridge
`TOOLS` catalog. When in doubt, call `list_tools` against the live editor and trust that over any
document. Tools are invoked as `mcp__unreal-ai-connection__<name>`.

Read the **cross-cutting operating patterns** in SKILL.md first — they apply to all recipes below
(hybrid actor lookup, capture/verify, async events, bulk envelope, read-back-and-verify, save before
restart, the `execute_unreal_python` escape hatch and struct-order traps).

---

## 1. Build-a-level (PROVEN)

Goal: construct a scene from primitives, materials, lighting, and a camera.

1. **Baseline / idempotency** — `find_actors_by_class` for your prefix, then `delete_actor` each, so
   re-runs are clean.
2. **Discover assets** — `find_assets` (meshes, textures, parent materials available to use).
3. **Place geometry** — `spawn_actor` (primitives at origin), then `set_actor_transform` (absolute,
   then relative) for layout. Spawn first, transform second.
4. **Materials** — `create_material_instance` from a parent, `set_mi_parameter` for tints/roughness/
   textures, then assign (see recipe 2). Materials after geometry.
5. **Lighting & atmosphere** — `spawn_actor` for DirectionalLight / SkyAtmosphere / SkyLight /
   ExponentialHeightFog / PostProcessVolume, then `set_actor_property` for intensities and settings.
   Lighting after materials.
6. **Frame & verify** — `set_camera_transform`, then `render_camera_to_png` (deterministic). Capture
   after each major structural step, not just at the end.
7. **Persist** — `save_dirty_assets`.

Checkpoints: screenshot the silhouette after step 3 before adding detail; re-screenshot after
lighting. **Materials on actors:** `set_actor_property` accepts JSON-**array** values for TArray/TSet
props (e.g. `OverrideMaterials`) — pass the material asset paths as a JSON array; no
`execute_unreal_python` fallback needed. `bulk_set_actor_property` covers scalar/bool/string props
across many actors at once.

## 2. Material-instance customization (PROVEN)

Goal: derive parameter-overridden material instances and apply them.

1. **Discover params** — `inspect_material` on the parent to read exact scalar/vector/texture
   parameter names (names are case-sensitive — copy them exactly).
2. **Create** — `create_material_instance` under e.g. `/Game/Materials/`.
3. **Override** — `set_mi_parameter`: textures by **full asset path** (`/Game/Tex/T_X.T_X`), colors
   as `{r,g,b,a}`, scalars as floats.
4. **Verify** — `inspect_material_instance` to confirm parent + overrides before applying to actors.
5. **Apply** — assign to a spawned actor's mesh via `set_actor_property`; `OverrideMaterials` takes a
   JSON array of material asset paths (no `execute_unreal_python` needed).

Notes: an instance's parent may itself be another instance. Static-switch parameters may be
inspectable but not settable — verify with `inspect_material_instance` rather than assuming a write
took.

## 3. Asset hygiene (MATURE)

Goal: query/move/rename/delete assets with referential safety.

1. `find_assets` (by class + path + name substring).
2. `inspect_asset` (dependencies, referencers, size) before touching anything.
3. `bulk_move_assets` / `move_asset`, `bulk_rename_assets` / `rename_asset` to reorganize.
4. `delete_asset` — refuses when the asset is referenced unless `force=true`; it lists referencers in
   the error. Read them before deciding to force.
5. `fix_up_redirectors` / `bulk_fix_redirectors` — renames/moves leave redirectors at the old path
   that shadow deletion; run this to convert them to real deletes.
6. `compare_assets` after batch operations to confirm no silent reference loss.

Maintenance helpers: `find_unused_assets`, `get_reference_chain`, `inspect_dependency_graph`.

## 4. Round-trip authoring (PROVEN)

Goal: author an asset end to end without silent loss.

1. `find_assets` → locate a parent (material/blueprint).
2. `duplicate_asset` or `create_material_instance` → make the variant.
3. Configure (`set_mi_parameter`, or `set_actor_property` for actor-bound config).
4. `inspect_asset` / `inspect_material_instance` → read back; `compare_assets` against the source.
5. Compile if it's a blueprint: `compile_blueprint`, or `bulk_compile_blueprints` for many, then
   `audit_blueprint_compile_status` to bucket by status.
6. `save_dirty_assets`.

Note: a freshly `duplicate_asset`'d asset has a stale dependency graph until saved — `inspect_asset`
post-duplicate to confirm before relying on it.

## 5. Photo → Unreal scene (FLAGSHIP)

Goal: reconstruct a compositionally faithful blockout + atmosphere from a reference image.

1. **Ingest** — `import_texture`, then `configure_texture` (sRGB on, World LOD group) for color
   fidelity.
2. **Blockout** — run **Build-a-level** (recipe 1) for the major shapes.
3. **Materials** — run **Material-instance customization** (recipe 2) for surface variants.
4. **Atmosphere rig — order is critical:**
   - Spawn DirectionalLight and set its atmosphere-sun flag **before** spawning SkyAtmosphere /
     VolumetricCloud / SkyLight / fog, so they inherit the sun direction.
   - Add a PostProcessVolume and force manual exposure (disable auto-exposure, set a fixed
     compensation) so headless captures are deterministic instead of flickering with ambient changes.
5. **Capture** — `set_camera_transform` + `render_camera_to_png` for hero angles.
6. **Persist** — `save_dirty_assets`.

Capture caveat: if you must use `get_viewport_screenshot` / `take_high_res_screenshot`, keep the
editor window foreground or the frame freezes; prefer `render_camera_to_png` for automation. Fidelity
is bounded by the available mesh library — substitute real meshes/PBR for closer matches.

## 6. Sequencer shot (EXPERIMENTAL)

Goal: scaffold a Level Sequence and author camera motion.

1. `create_sequence` (set fps + frame range) under e.g. `/Game/Cinematics/`.
2. `bind_actor_to_sequence` — bind an actor (must already exist/loaded in the level) as a possessable.
3. `inspect_sequence` — confirm fps, frame range, and bindings.
4. **Keyframes** — `sequencer_add_transform_keyframe` for transform tracks: pass `time_seconds`
   (seconds, display rate; converted internally to ticks) and optional location / rotation / scale
   triples (rotation is `[pitch, yaw, roll]`). For non-transform channels use `execute_unreal_python`
   then `inspect_sequence` to confirm.

Notes: keyframe times are passed in **seconds** (`time_seconds`), not frame indices — the bridge
converts to tick-resolution internally. `inspect_sequence` surfaces that internal `tick_resolution`
separately from the display fps. Possessable binding resolves at sequence-save time. The automated
capture-from-sequence path shares the backgrounded-viewport limitation above.

---

## UE 5.x behavior notes (caller-relevant)

These shape how you call the tools — not internal implementation you control.

| Behavior | Consequence | What to do |
| --- | --- | --- |
| `set_*` partial updates default omitted fields | Omitting a field on a transform/struct can snap it to identity/origin (destructive) | Read-modify-write: include all fields, or read current state first |
| Spawned actors / edits are unsaved | Revert on editor restart | `save_dirty_assets` before any pause/restart |
| Rename/move leaves redirectors | Old paths still resolve; clutter accumulates | `fix_up_redirectors` / `bulk_fix_redirectors` after the batch |
| `delete_asset` refuses referenced assets | Fails unless `force=true` | Inspect referencers; only force when intentional |
| Widget-tree edits | Compiling on every edit in quick succession is unstable | `edit_widget_tree` with `compile: true` on the **final** edit only |
| Viewport capture when backgrounded | Returns a frozen frame | Use `render_camera_to_png`, or keep the editor foreground |
| TArray/TSet properties (e.g. `OverrideMaterials`) | Supported — pass a JSON **array** value to `set_actor_property` | Send the array directly; no `execute_unreal_python` fallback |
| `unreal.Rotator` / `unreal.Color` positional args | Rotator is (roll,pitch,yaw); Color is BGRA | Build empty struct, assign by property name |
| `compile_mod_pak` | **Blocking** RunUAT call (up to ~30 min) — returns no task id, not pollable | Wait for the tool to return; tune `timeout_sec` (default 1800) |

When any tool name here disagrees with live `list_tools`, the live catalog wins — update this file.
