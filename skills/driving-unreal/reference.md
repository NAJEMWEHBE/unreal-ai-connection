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

## 7. Widget / UMG authoring (MATURE)

Goal: build or mutate a `UWidgetBlueprint` / `UEditorUtilityWidgetBlueprint` hierarchy — including
the UE 5.7 EUW `WidgetTree` population that Python reflection can't reach.

1. **Inspect first** — `inspect_widget_blueprint` for structural facts (parent class, compile status,
   animations, delegate bindings, inherited named slots); `inspect_widget_tree` for the current
   widget hierarchy. Cross-link both via the shared asset path. (`inspect_blueprint` covers the
   variables + graphs side, inherited from `UBlueprint`.)
2. **Set the root** — `edit_widget_tree` with `op=set_root`, a `class`, and a `name`. A multi-child
   panel (`VerticalBox` / `HorizontalBox` / `CanvasPanel`) makes a useful root; leaf classes
   (`TextBlock` / `Image` / `Spacer`) can't take children. Omitting `name` defaults it to the class
   name.
3. **Add children** — `op=add_child` with `parent` (an existing widget name), `class`, and `name`.
   The parent must be a container: multi-child panels (VerticalBox / HorizontalBox / CanvasPanel) or
   single-child content widgets (Border / Button). Adding to a leaf returns a "not a panel" error.
   Build top-down: root first, then its children.
4. **Set properties** — `op=set_property` with `widget`, `property`, and a string `value` (coerced to
   the target type). Native support: `text` on TextBlock/EditableTextBox, plus any string / float /
   int / bool `UProperty`. Other property types return "type not yet supported" — drop to
   `execute_unreal_python` for those.
5. **Compile once, at the end** — pass `compile: true` on the **final** op only. Compiling on every
   edit in quick succession crashes the editor (see the gotcha table). For a standalone recompile
   after external mutation, use `compile_blueprint`.

Notes: `class` accepts the shorthand names above or a fully-qualified `UClass` path. Every
`edit_widget_tree` call marks the asset dirty and **auto-saves** the widget tree — no separate
`save_dirty_assets` is needed for the tree itself, but the `compile: true` final op is what bakes the
generated class. After a batch, `inspect_widget_tree` to confirm the hierarchy before relying on it.

## 8. PIE (Play-In-Editor) validation loop (PROVEN)

Goal: launch a Play-In-Editor session to validate live behavior, observe it, then tear it down — the
"did my edit actually work?" loop, with no human keypress.

1. **Start** — `pie_control` with `action=start` and `mode` = `play` (full PIE in the active
   viewport) or `simulate` (ticks the world without spawning a Player Controller). The call is
   **asynchronous**: it queues the request and returns immediately; the session is not live yet.
2. **Confirm it started** — on a later tick, `pie_control` with `action=query` and check the
   `is_playing` (and `is_simulating`) flags. Don't act on the session until query reports
   `is_playing` true.
3. **Observe / act** — inspect the running world (`get_actors_in_level`, `get_selected_actors`,
   `execute_unreal_python`) while the session is live.
4. **Stop** — `pie_control` with `action=stop`. Also asynchronous (defers end-play to the next tick).
   If a start request was still queued and never ticked, stop cancels that queued request instead.
5. **Confirm shutdown** — `action=query` again; proceed only once `is_playing` is false.

Notes: starting while a session is running or queued returns `pie_already_active`; stopping with
nothing running or queued returns `pie_not_active`. Because both start and stop defer to the next
editor tick, never infer state from the start/stop return — always read it back with `action=query`.

## 9. Component authoring (PROVEN)

Goal: attach a component to an existing actor and configure it.

1. **Target the actor** — get its name/label from `get_actors_in_level`, `find_actors_by_class`, or
   `get_selected_actors` (the editor's current selection, in selection order — the last entry is the
   most-recently-selected).
2. **Add the component** — `add_component` with `actor_name` (label or FName; an ambiguous label
   returns `ambiguous_actor` with candidates — retry with the FName) and `class_path` (a concrete
   `UActorComponent` subclass path, e.g. `/Script/Engine.PointLightComponent`; abstract or deprecated
   classes are rejected). Optional `component_name`.
3. **Place scene components** — for `USceneComponent` subclasses, optional `attach_to` (an existing
   component name on the actor; defaults to the root), `socket`, and `relative_transform`
   (`location` / `rotation` / `scale`). Non-scene components just register.
4. **Configure** — set further properties with `set_actor_property`, then read them back before
   relying on them.
5. **Persist** — `add_component` marks the actor dirty but does **not** auto-save; run
   `save_dirty_assets` so the component survives an editor restart.

Notes: actor targeting is the same hybrid label/FName lookup used across the actor tools. The handler
runs `RerunConstructionScripts` after attaching, so construction-script-driven actors re-evaluate
with the new component in place. Supplying a duplicate `component_name` is not explicitly documented —
UE auto-suffixes the FName to avoid collisions; use distinct names to avoid ambiguity.

## 10. Console variable management (PROVEN)

Goal: discover, read, tweak, and restore UE console variables (CVars) without leaving Python or
touching the editor's Output Log manually.

1. **Discover** — `find_console_variables` with a `prefix` (e.g. `r.Shadow`, `r.Screen`) to list
   matching CVar names, their types (`int|float|bool|string`), and read-only flags. Omit `prefix` to
   dump the full registry (default `limit=100`, hard max 1000; always pass a `prefix` or set `limit`
   explicitly for practical use).
2. **Read** — `get_console_variable` by exact name. Returns all four value representations
   (string/int/float/bool), the detected type, and the `set_by` field (e.g. `Console`, `DeviceProfile`,
   `Scalability`). Use `set_by` to understand whether a value was overridden from an ini/scalability
   bucket — important before deciding to overwrite it.
3. **Write** — `set_console_variable` with the exact name and a `value` (string, int, float, or bool).
   The bridge sets at `ECVF_SetByConsole` priority (same as a user typing in the console) and
   post-verifies the change landed. Read-only CVars (`read_only: true` in the discovery result) are
   pre-rejected — do not attempt to write them. A mismatch between requested and landed value is
   reported as a `note` field rather than an error; check `value_string` in the reply to confirm the
   actual landed value.
4. **Run a one-off command** — `execute_console_command` for commands that aren't CVar
   assignments: `stat fps`, `r.ScreenPercentage 50`, `memreport`, etc. Returns captured output by
   default. Use this for exec-only commands; prefer `set_console_variable` for persistent CVar state.
5. **Restore on exit** — CVars set via `set_console_variable` survive until the editor session ends
   (or another set overrides them); they are **not** persisted across restarts. If you need rollback,
   read the original value with `get_console_variable` before writing, and restore it at the end of
   the automation run. For batch mutations with automatic rollback, `bulk_set_console_variables`
   composes the read / set / restore cycle internally — pass `assignments: {cvar: value, …}` and
   `rollback_on_error: true` for atomic batch behavior.

Notes: `ECVF_SetByConsole` can override DeviceProfile and scalability buckets — this is intentional
for automation but means changes may disappear if the engine re-applies scalability settings (e.g.
on quality change). `find_console_variables` iterates the live `IConsoleManager` registry, so newly
registered CVars from loaded plugins are included. The `set_by` field helps distinguish CVars you
set from those owned by the engine scalability system.

## 11. Blender → Unreal asset (PROVEN)

Goal: get genuinely-modelled 3D geometry into the level **for free** — author in
Blender (free, open-source; drive it via a separate Blender-automation MCP server if one is
connected, or by hand), then ingest through this plugin. No paid text-to-3D service is required;
this is the open-stack alternative to bundled Meshy/Tripo-style generators.

This plugin does **not** model assets — it consumes finished ones (two-MCP separation, see
[`docs/ASSET-PIPELINE-BLENDER.md`](../../docs/ASSET-PIPELINE-BLENDER.md) and ADR-0002). The Blender
server authors; this server imports/places/lights/renders.

1. **Author in Blender** — model + UV-unwrap + PBR-texture **one asset at a time**. For UE 5.7:
   single non-overlapping UV set (add a 2nd lightmap UV if not relying purely on Lumen), proportionate
   tri-count, and **apply transforms** (scale=1, rotation=0) before export so it lands at a predictable
   size. CC0 PBR (Poly Haven / AmbientCG) into a principled BSDF gives a sane exported material.
2. **Export to a gitignored scratch dir** — prefer **`.glb`** (glTF 2.0 binary, embedded — geometry +
   textures in one file). `.fbx` + a sidecar texture folder also works. Scratch dirs (`blender-exports/`,
   `asset-src/`, `*.blend`) are gitignored build inputs, never committed.
3. **Import into Unreal** — `import_mesh` with `source_path` (the `.glb`/`.gltf`/`.fbx`/`.obj` on disk),
   `dest_path` (under `/Game/`), and `import_materials` (bool). It runs UE's **Interchange** pipeline and
   returns the created `static_meshes` / `created` asset paths. (`import_mesh` is the supported wrapper
   over the import seam — it honors ADR-0002: a bridge synthetic over `execute_unreal_python`, not new
   plugin C++.) For a standalone loose texture not embedded in the file, use `import_texture` +
   `configure_texture` instead.
4. **Verify the import** — `inspect_static_mesh` on a returned path (LODs, materials, bounds) and
   `inspect_material_instance` if `import_materials=true`. Import success means the file parsed, **not**
   that it looks right — Interchange often leaves material texture params on white placeholders; fix with
   `set_mi_parameter` (textures by full asset path) or `create_material_instance`.
5. **Place + dress** — `spawn_actor` a `StaticMeshActor` (or set the mesh via `set_actor_property`'s
   `StaticMeshComponent.StaticMesh` / `OverrideMaterials` array), `set_actor_transform` to position,
   then run the **Photo → Unreal scene** atmosphere rig (recipe 5) for lighting.
6. **Capture + persist** — `render_camera_to_png` for a deterministic hero still; `save_dirty_assets`.

Notes: honest ceiling — the seam imports **faithfully**, it does not fix authoring mistakes (overlapping
UVs, missing lightmap UV, un-applied transforms all survive into UE). No automatic LOD generation —
author LODs in Blender or accept single-LOD. CC0 + procedural is a *competent* real-time result, not
hand-authored AAA. This round-trip was host-verified live on UE 5.7 (2026-05-18) — see
`docs/validation/blender-to-unreal-hifi-2026-05-18.md`.

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
| `pie_control` start/stop are async | Session isn't live (or torn down) on return — they defer to the next tick | Read back with `action=query`; don't infer state from the start/stop return |
| `load_level_by_path` needs an exact package path | A wrong/partial path fails to load | `list_levels` first (optional `path_under` / `name_contains`) to discover the path |
| `set_console_variable` priority | Sets at `ECVF_SetByConsole`; survives until session end but scalability re-apply can reset it | Read original with `get_console_variable` first; restore at end of run |
| `find_console_variables` without prefix | Dumps up to 1000 CVars — very large response | Always pass a `prefix`; use `limit` to cap further |

When any tool name here disagrees with live `list_tools`, the live catalog wins — update this file.
