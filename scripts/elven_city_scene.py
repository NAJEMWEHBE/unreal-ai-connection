"""Idempotent blockout of a Rivendell-style elven city from the
reference set (main vista + Ref2 side-angle + Ref3 top-down map).

Dependency-free: only Engine BasicShapes + in-script constant-color
master materials, so it runs on any UE 5.7 project with no marketplace
assets. Re-running wipes everything labelled ELV_* and rebuilds.

Axis convention (cm): +X = North (into the cliff), -X = South (river
outflow / main camera). +Y = East (golden sun side), -Y = West. Z up.
River sits at Z=0; viaduct deck ~Z1800; cathedral spire tops ~Z6000.
"""
import unreal

PREFIX = "ELV_"
PKG = "/Game/ElvenCity/M"

el = unreal.EditorAssetLibrary
ell = unreal.EditorLevelLibrary
els = unreal.EditorActorSubsystem()
mel = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

SHAPES = {
    "cube": unreal.load_object(None, "/Engine/BasicShapes/Cube.Cube"),
    "cyl": unreal.load_object(None, "/Engine/BasicShapes/Cylinder.Cylinder"),
    "cone": unreal.load_object(None, "/Engine/BasicShapes/Cone.Cone"),
    "plane": unreal.load_object(None, "/Engine/BasicShapes/Plane.Plane"),
    "sphere": unreal.load_object(None, "/Engine/BasicShapes/Sphere.Sphere"),
}


def make_mat(name, rgb, emissive=0.0, rough=0.6):
    path = f"{PKG}_{name}"
    if el.does_asset_exist(path):
        return unreal.load_asset(path)
    m = tools.create_asset(f"M_{name}", "/Game/ElvenCity",
                           unreal.Material, unreal.MaterialFactoryNew())
    col = mel.create_material_expression(m, unreal.MaterialExpressionConstant3Vector, -400, 0)
    col.set_editor_property("constant", unreal.LinearColor(rgb[0], rgb[1], rgb[2], 1.0))
    mel.connect_material_property(col, "", unreal.MaterialProperty.MP_BASE_COLOR)
    r = mel.create_material_expression(m, unreal.MaterialExpressionConstant, -400, 250)
    r.set_editor_property("r", rough)
    mel.connect_material_property(r, "", unreal.MaterialProperty.MP_ROUGHNESS)
    if emissive > 0:
        e = mel.create_material_expression(m, unreal.MaterialExpressionConstant3Vector, -400, -250)
        e.set_editor_property("constant", unreal.LinearColor(rgb[0] * emissive, rgb[1] * emissive, rgb[2] * emissive, 1.0))
        mel.connect_material_property(e, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    mel.recompile_material(m)
    el.save_asset(path)
    return m


def wipe():
    for a in els.get_all_level_actors():
        try:
            if a.get_actor_label().startswith(PREFIX):
                els.destroy_actor(a)
        except Exception:
            pass


def spawn(shape, label, loc, scale, mat, rot=(0, 0, 0)):
    a = ell.spawn_actor_from_object(SHAPES[shape],
                                    unreal.Vector(*loc),
                                    unreal.Rotator(roll=rot[0], pitch=rot[1], yaw=rot[2]))
    a.set_actor_label(PREFIX + label)
    a.set_actor_scale3d(unreal.Vector(*scale))
    smc = a.static_mesh_component
    if mat:
        smc.set_material(0, mat)
    return a


wipe()

# --- materials ---
ROCK = make_mat("Rock", (0.10, 0.09, 0.08), rough=0.9)
STONE = make_mat("Stone", (0.62, 0.58, 0.50), rough=0.7)
GOLD = make_mat("Gold", (0.78, 0.55, 0.22), emissive=0.25, rough=0.35)
WATER = make_mat("Water", (0.06, 0.18, 0.22), emissive=0.15, rough=0.08)
FOLIAGE = make_mat("Foliage", (0.20, 0.24, 0.10), rough=0.8)
TRUNK = make_mat("Trunk", (0.12, 0.08, 0.05), rough=0.9)
CRYSTAL = make_mat("Crystal", (0.30, 0.75, 0.95), emissive=6.0, rough=0.1)
GLASS = make_mat("Glass", (0.55, 0.62, 0.45), emissive=0.5, rough=0.2)

# --- ground terrain (covers default checker) + river ---
spawn("plane", "Terrain", (1500, 200, -30), (420, 420, 1), FOLIAGE)
spawn("plane", "River", (-500, 100, 5), (70, 9, 1), WATER)

# --- mountain / cliff mass (N + NW), vertical layering for Ref2 ---
spawn("cube", "CliffBase", (5200, -800, 1800), (34, 40, 38), ROCK)
spawn("cube", "CliffNW", (4200, -2600, 2600), (22, 20, 54), ROCK)
spawn("cone", "Peak1", (6000, -1200, 5200), (60, 60, 70), ROCK)
spawn("cone", "Peak2", (6200, 1400, 4400), (50, 50, 58), ROCK)
spawn("cube", "CliffE", (4600, 3000, 2200), (20, 18, 46), ROCK)

# --- (1) main city: stacked elven halls up the NW cliff ---
tiers = [
    ("Hall1", (3400, -2200, 900), (9, 14, 9)),
    ("Hall2", (3700, -2600, 1900), (8, 12, 11)),
    ("Hall3", (4000, -2300, 2900), (7, 10, 10)),
    ("Hall4", (4300, -2700, 3800), (6, 8, 9)),
]
for lbl, loc, sc in tiers:
    spawn("cube", lbl, loc, sc, STONE)
    spawn("cone", lbl + "_Roof", (loc[0], loc[1], loc[2] + sc[2] * 50 + 220),
          (sc[0] * 0.9, sc[1] * 0.9, 6), GOLD)
# cathedral spire (top focal of Ref2)
spawn("cyl", "Spire", (4200, -2500, 4400), (4, 4, 12), STONE)
spawn("cone", "SpireTip", (4200, -2500, 5150), (5, 5, 9), GOLD)

# --- (3) the great arched viaduct spanning the gorge (E-W along Y) ---
DECK_Z = 1800
spawn("cube", "ViaductDeck", (1600, 200, DECK_Z), (7, 64, 0.6), STONE)
for i, y in enumerate(range(-2800, 3400, 900)):
    spawn("cube", f"Pier{i}", (1600, y, DECK_Z / 2), (5, 4, DECK_Z / 100), STONE)
    spawn("cyl", f"Arch{i}", (1600, y + 450, DECK_Z * 0.78),
          (3, 7, 3), STONE, rot=(0, 90, 0))

# --- (5) domed pavilion + (7) beacon crystal mid-span ---
import math
for k in range(8):
    ang = math.radians(k * 45)
    spawn("cyl", f"Pav_Col{k}",
          (1600 + 320 * math.cos(ang), 200 + 320 * math.sin(ang), DECK_Z + 380),
          (1.4, 1.4, 7.4), GOLD)
spawn("sphere", "Pav_Dome", (1600, 200, DECK_Z + 780), (8.5, 8.5, 5), GLASS)
spawn("cone", "Beacon", (1600, 200, DECK_Z + 250), (2.2, 2.2, 4.2), CRYSTAL)
spawn("sphere", "BeaconCore", (1600, 200, DECK_Z + 470), (1.6, 1.6, 1.6), CRYSTAL)

# --- (2) descending side-stairs, SW foreground ---
for s in range(14):
    spawn("cube", f"Stair{s}", (-1800 + s * 130, -2400, 60 + s * 95),
          (1.3, 9, 0.9), STONE)

# --- (4) waterfalls down the cliff face ---
for i, (x, y) in enumerate([(3800, -3050), (4250, -3050), (4900, -1900), (3500, 200)]):
    spawn("cube", f"Fall{i}", (x, y, 1500), (0.4, 3.5, 30), WATER)

# --- (6) autumn pine forest, flanking E + W + foreground slopes ---
import random
random.seed(7)
n = 0
for (xc, yc, rad, count) in [(-700, 2600, 1700, 22), (-300, -2600, 1500, 20),
                             (2600, 3400, 1500, 16), (3000, -3600, 1200, 12)]:
    for _ in range(count):
        x = xc + random.uniform(-rad, rad)
        y = yc + random.uniform(-rad, rad)
        h = random.uniform(3.5, 7.0)
        spawn("cyl", f"Pine{n}_T", (x, y, h * 30), (0.7, 0.7, h * 0.6), TRUNK)
        spawn("cone", f"Pine{n}_C", (x, y, h * 95), (h * 0.7, h * 0.7, h * 1.5), FOLIAGE)
        n += 1

# --- lighting + atmosphere (golden-hour sunburst mood) ---
sun = ell.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 9000))
sun.set_actor_label(PREFIX + "Sun")
sun.set_actor_rotation(unreal.Rotator(roll=0, pitch=-7, yaw=125), False)
sun.set_actor_rotation(unreal.Rotator(roll=0, pitch=-12, yaw=130), False)
sc = sun.get_component_by_class(unreal.DirectionalLightComponent)
sc.set_editor_property("intensity", 5.0)
sc.set_editor_property("light_color", unreal.Color(255, 178, 110))
sc.set_editor_property("atmosphere_sun_light", True)

atm = ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
atm.set_actor_label(PREFIX + "SkyAtmo")

skyl = ell.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 4000))
skyl.set_actor_label(PREFIX + "SkyLight")
slc = skyl.get_component_by_class(unreal.SkyLightComponent)
slc.set_editor_property("source_type", unreal.SkyLightSourceType.SLS_CAPTURED_SCENE)
slc.set_editor_property("real_time_capture", True)
slc.set_intensity(1.4)

fog = ell.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 200))
fog.set_actor_label(PREFIX + "Fog")
fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
fc.set_editor_property("fog_density", 0.018)
fc.set_editor_property("fog_inscattering_luminance", unreal.LinearColor(0.55, 0.42, 0.28, 1))
for _vf in ("enable_volumetric_fog", "volumetric_fog"):
    try:
        fc.set_editor_property(_vf, True)
        break
    except Exception:
        pass

ppv = ell.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
ppv.set_actor_label(PREFIX + "PostProcess")
ppv.set_editor_property("unbound", True)
ps = ppv.settings
ps.set_editor_property("override_bloom_intensity", True)
ps.set_editor_property("bloom_intensity", 1.6)
ps.set_editor_property("override_auto_exposure_method", True)
ps.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM)
ps.set_editor_property("override_white_temp", True)
ps.set_editor_property("white_temp", 5200.0)
ps.set_editor_property("override_vignette_intensity", True)
ps.set_editor_property("vignette_intensity", 0.45)
ppv.set_editor_property("settings", ps)

unreal.EditorLevelLibrary.save_current_level()
unreal.log("ELV_SCENE_OK actors=%d" % len(els.get_all_level_actors()))
