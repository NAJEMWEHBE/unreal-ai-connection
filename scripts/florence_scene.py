"""Compose the Florentine plaza demo scene end-to-end.

This is the consolidated, idempotent scene builder for `L_Florence_Plaza`.
It replaces the chain of single-purpose iteration scripts that lived
under `scripts/` during scene development (compose / fix lighting /
polish / closeup / hires shot / rebuild).

What it does (in one pass):

1. Open the target level `/Game/Validation/Florence/L_Florence_Plaza`
   (creates it fresh if missing — caller can drop the existing one to
   start from zero).
2. Wipe any leftover actors from prior runs (`Plaza_Floor`, `Marble_Dais`,
   `Wall_*`, `Column_*`, `Bench_*`, plus any stray `SkyLight` /
   `ExponentialHeightFog` / `SkyAtmosphere` / `PostProcessVolume` /
   `DirectionalLight` duplicates).
3. Build four master materials (M_Marble_012, M_Travertine_009,
   M_WoodFloor_051, M_Granite_Tile) wiring Color/Normal/Roughness/AO
   from the textures already imported to `/Game/Validation/Florence/`.
4. Spawn plaza geometry: granite floor + marble dais + travertine walls
   + four travertine columns + two wooden benches.
5. Build the lighting rig: SkyAtmosphere, a single golden-hour
   DirectionalLight flagged as atmosphere sun, a real-time-capturing
   SkyLight, soft exponential height fog, and an unbound
   PostProcessVolume with histogram auto-exposure + filmic tone.
6. Frame the editor viewport for the hero shot from the SE corner
   looking NW so the sun lights the camera-facing wall + dais front.
7. Save.

Assumptions:

* The 4 PBR sets + the longlat HDRI must already be imported under
  `/Game/Validation/Florence/`. Use `marketplace_import` with
  `multi_map=true` against AmbientCG `Marble012`, `Travertine009`,
  `WoodFloor051` and Polyhaven `granite_tile` before running this.
  HDRI: Polyhaven `venice_sunset` (single-map / hdri path).

Run from a bridge MCP subprocess like:

    {"name":"run_python_file","arguments":{
        "path":"F:/UnrealClaudeMCP/scripts/florence_scene.py",
        "capture_output":true}}
"""
import unreal

PKG = "/Game/Validation/Florence"
LEVEL = f"{PKG}/L_Florence_Plaza"

ASSETS = {
    "Marble_012":     {"has_ao": False, "tiling": 4.0},
    "Travertine_009": {"has_ao": True,  "tiling": 2.0},
    "WoodFloor_051":  {"has_ao": True,  "tiling": 3.0},
    "Granite_Tile":   {"has_ao": True,  "tiling": 6.0},
}

# Actors we own; everything else in the level is left alone.
KILL_LABELS = {
    "Plaza_Floor", "Marble_Dais", "Wall_West", "Wall_East", "Wall_North",
    "Bench_West", "Bench_East", "Sun_Golden_Hour", "SkyAtmosphere",
    "SkyLight_Atmosphere", "Fog_Soft", "PP_Cinematic",
}
KILL_LABEL_PREFIXES = ("Column_",)
KILL_DUPLICATE_CLASSES = (
    unreal.SkyLight, unreal.ExponentialHeightFog, unreal.SkyAtmosphere,
    unreal.PostProcessVolume, unreal.DirectionalLight,
)

el = unreal.EditorAssetLibrary
ll = unreal.EditorLevelLibrary
mel = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
els = unreal.EditorActorSubsystem()


def try_set(obj, name, value):
    try:
        obj.set_editor_property(name, value)
    except Exception as e:
        print(f"  skip {type(obj).__name__}.{name}: {e}")


def make_material(slug, spec):
    """Build M_<slug> with Color/Normal/Roughness/(optional AO) wired in."""
    name = f"M_{slug}"
    full = f"{PKG}/{name}"
    if el.does_asset_exist(full):
        el.delete_asset(full)
    mat = tools.create_asset(name, PKG, unreal.Material, unreal.MaterialFactoryNew())

    # Fail fast with a useful message when textures aren't imported yet —
    # otherwise the material wiring crashes later with a non-obvious
    # NoneType error. Required: color + normal + roughness. AO optional.
    required = {
        "color":     f"{PKG}/T_{slug}",
        "normal":    f"{PKG}/T_{slug}_normal",
        "roughness": f"{PKG}/T_{slug}_roughness",
    }
    loaded = {}
    missing = []
    for k, path in required.items():
        a = el.load_asset(path)
        if a is None:
            missing.append(path)
        else:
            loaded[k] = a
    if missing:
        raise RuntimeError(
            f"Missing texture assets for {slug}: {', '.join(missing)}. "
            "Run marketplace_import (multi_map=true) for the source slug first."
        )
    color_tex = loaded["color"]
    normal_tex = loaded["normal"]
    rough_tex = loaded["roughness"]
    ao_tex = el.load_asset(f"{PKG}/T_{slug}_ao") if spec["has_ao"] else None

    # Tile via TexCoord scale; cheaper + simpler than a Multiply node.
    texcoord = mel.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -1100, 0)
    texcoord.set_editor_property("u_tiling", spec["tiling"])
    texcoord.set_editor_property("v_tiling", spec["tiling"])

    color_node = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, -400)
    color_node.texture = color_tex
    mel.connect_material_expressions(texcoord, "", color_node, "UVs")

    normal_node = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, -100)
    normal_node.texture = normal_tex
    normal_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL
    mel.connect_material_expressions(texcoord, "", normal_node, "UVs")

    # AmbientCG + Polyhaven roughness/AO maps ship as full-color JPGs.
    # SAMPLERTYPE_COLOR avoids the "should be Color" compile error.
    rough_node = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, 200)
    rough_node.texture = rough_tex
    rough_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR
    mel.connect_material_expressions(texcoord, "", rough_node, "UVs")

    mel.connect_material_property(color_node, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    mel.connect_material_property(normal_node, "RGB", unreal.MaterialProperty.MP_NORMAL)
    mel.connect_material_property(rough_node, "R", unreal.MaterialProperty.MP_ROUGHNESS)

    if ao_tex is not None:
        ao_node = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, 500)
        ao_node.texture = ao_tex
        ao_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR
        mel.connect_material_expressions(texcoord, "", ao_node, "UVs")
        mel.connect_material_property(ao_node, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)

    mel.recompile_material(mat)
    el.save_loaded_asset(mat)
    return mat


def wipe_owned_actors():
    """Destroy actors we previously spawned + any UNLABELED duplicate
    lighting fixtures so a re-run starts from a clean slate. Labeled
    lighting actors placed intentionally by a designer are left alone."""
    destroyed = 0
    for a in list(els.get_all_level_actors()):
        label = a.get_actor_label()
        if (label in KILL_LABELS) or any(label.startswith(p) for p in KILL_LABEL_PREFIXES):
            els.destroy_actor(a)
            destroyed += 1
            continue
        # Class-based delete only fires for actors with NO label — i.e.
        # leftovers from template spawns (`DirectionalLight_0` etc).
        # Anything a designer labeled survives.
        if isinstance(a, KILL_DUPLICATE_CLASSES) and not label:
            els.destroy_actor(a)
            destroyed += 1
    print(f"wiped {destroyed} actors before rebuild")


def spawn_plane(mat, loc, scale, rot=(0, 0, 0), label=""):
    p = ll.spawn_actor_from_object(
        el.load_asset("/Engine/BasicShapes/Plane"),
        unreal.Vector(*loc), unreal.Rotator(roll=rot[0], pitch=rot[1], yaw=rot[2]))
    p.set_actor_scale3d(unreal.Vector(*scale))
    p.static_mesh_component.set_material(0, mat)
    if label:
        p.set_actor_label(label)
    return p


def spawn_cube(mat, loc, scale, rot=(0, 0, 0), label=""):
    c = ll.spawn_actor_from_object(
        el.load_asset("/Engine/BasicShapes/Cube"),
        unreal.Vector(*loc), unreal.Rotator(roll=rot[0], pitch=rot[1], yaw=rot[2]))
    c.set_actor_scale3d(unreal.Vector(*scale))
    c.static_mesh_component.set_material(0, mat)
    if label:
        c.set_actor_label(label)
    return c


# --- 1. open or create the target level ------------------------------------
if el.does_asset_exist(LEVEL):
    ll.load_level(LEVEL)
else:
    ll.new_level(LEVEL)
print(f"working in level: {LEVEL}")

# --- 2. wipe leftover actors -----------------------------------------------
wipe_owned_actors()

# --- 3. materials (idempotent — recreated each run) ------------------------
mats = {slug: make_material(slug, spec) for slug, spec in ASSETS.items()}
for k, m in mats.items():
    print(f"  M_{k} -> {m.get_path_name()}")

# --- 4. plaza geometry -----------------------------------------------------
spawn_plane(mats["Granite_Tile"], (0, 0, 0), (50, 50, 1), label="Plaza_Floor")
spawn_cube(mats["Marble_012"], (0, 0, 15), (10, 10, 0.3), label="Marble_Dais")
spawn_cube(mats["Travertine_009"], (-1000, 0, 250), (0.5, 20, 5), label="Wall_West")
spawn_cube(mats["Travertine_009"], (1000, 0, 250), (0.5, 20, 5), label="Wall_East")
spawn_cube(mats["Travertine_009"], (0, 1000, 250), (20, 0.5, 5), label="Wall_North")
for x, y in [(-400, -400), (400, -400), (-400, 400), (400, 400)]:
    spawn_cube(mats["Travertine_009"], (x, y, 250), (0.8, 0.8, 5), label=f"Column_{x}_{y}")
spawn_cube(mats["WoodFloor_051"], (-700, -850, 35), (1.2, 5, 0.5), label="Bench_West")
spawn_cube(mats["WoodFloor_051"], (700, -850, 35), (1.2, 5, 0.5), label="Bench_East")

# --- 5. lighting rig -------------------------------------------------------
# SkyAtmosphere first so the directional sun has something to scatter
# against — without it the scene renders pitch black under the sun's
# back-side.
sa = ll.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
sa.set_actor_label("SkyAtmosphere")

# Sun — atmospheric-sun-light + warm tint + golden-hour elevation.
# unreal.Rotator constructor positional order is (roll, pitch, yaw); use
# keywords so a future read can't get the angles wrong by accident.
sun = ll.spawn_actor_from_class(
    unreal.DirectionalLight, unreal.Vector(0, 0, 800),
    unreal.Rotator(roll=0.0, pitch=-22.0, yaw=135.0))
sun.set_actor_label("Sun_Golden_Hour")
dl = sun.light_component
dl.set_intensity(8.0)
dl.set_light_color(unreal.LinearColor(1.0, 0.78, 0.50, 1.0))
try_set(dl, "atmosphere_sun_light", True)
try_set(dl, "use_temperature", True)
try_set(dl, "temperature", 3800.0)

# SkyLight — real-time capture from the atmosphere we just placed.
sky = ll.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 500), unreal.Rotator(0, 0, 0))
sky.set_actor_label("SkyLight_Atmosphere")
sl = sky.light_component
try_set(sl, "source_type", unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
try_set(sl, "real_time_capture", True)
sl.set_intensity(2.0)
sl.recapture_sky()

# Exponential height fog tinted to the golden palette.
fog = ll.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
fog.set_actor_label("Fog_Soft")
fc = fog.component
try_set(fc, "fog_density", 0.008)
try_set(fc, "fog_height_falloff", 0.2)
try_set(fc, "start_distance", 300.0)
# 5.7 renamed `fog_inscattering_color` → `fog_inscattering_luminance`.
try_set(fc, "fog_inscattering_luminance", unreal.LinearColor(0.85, 0.65, 0.45, 1.0))

# Post-process — histogram auto-exposure (NOT manual), filmic look.
ppv = ll.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0, 0, 300), unreal.Rotator(0, 0, 0))
ppv.set_actor_label("PP_Cinematic")
try_set(ppv, "unbound", True)
s = ppv.settings
for k, v in [
    ("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM),
    ("auto_exposure_bias", 1.0),
    ("auto_exposure_min_brightness", 0.25),
    ("auto_exposure_max_brightness", 1.5),
    ("bloom_intensity", 0.25),
    ("bloom_threshold", 2.0),
    ("vignette_intensity", 0.30),
    ("film_slope", 0.92),
    ("film_toe", 0.50),
    ("film_shoulder", 0.30),
    ("white_temp", 6000.0),
    ("scene_color_tint", unreal.LinearColor(1.0, 0.96, 0.88, 1.0)),
]:
    try_set(s, k, v)
ppv.settings = s

# --- 6. hero-shot framing --------------------------------------------------
viewport = unreal.UnrealEditorSubsystem()
viewport.set_level_viewport_camera_info(
    unreal.Vector(900.0, -1100.0, 260.0),
    unreal.Rotator(roll=0.0, pitch=-10.0, yaw=135.0))

# Game view hides the editor gizmos so a `get_viewport_screenshot` call
# captures the scene cleanly.
le = unreal.LevelEditorSubsystem()
le.editor_invalidate_viewports()
le.editor_set_game_view(True)

# --- 7. save --------------------------------------------------------------
ll.save_current_level()
print("DONE")
