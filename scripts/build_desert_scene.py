# build_desert_scene.py — dense desert sunset reconstruction (v3)
#
# Engine-internal assets only (no external downloads). Builds a high-density
# scene matching the source photo's COMPOSITION:
#  - Stepped pyramid base, central scaffolded tower with vertical cables,
#    ladder rungs, derrick crown, multiple horizontal catwalks
#  - Foreground gantry frames + connecting catwalks
#  - 50+ scattered industrial crates (varied sizes, stacked clusters)
#  - 30+ boulder field rocks
#  - 30+ buried sand-dune saucers
#  - 12 distant rocky-ridge silhouettes (tinted dark, set deep into haze)
#  - SkyAtmosphere + low warm sun + dense volumetric ExponentialHeightFog
#    + SkyLight realtime + VolumetricCloud
#  - Niagara dust particles (BlowingParticles template) at 4 locations
#  - Editor camera at low front angle for the hero composition
#
# Idempotent: any prior actor with label starting "Val" or "Desert_" is
# destroyed; competing project sky / atmosphere actors are temporarily hidden.

import unreal
import math
import random
import sys
import builtins

random.seed(42)

ell = unreal.EditorLevelLibrary
ELLib = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f'[desert] {msg}')


# Staged-capture support. External orchestrators (e.g. the workflow-screenshot
# series in docs/validation/workflow/) set `builtins.DESERT_BUILD_STAGE` to an
# integer 0..4 before invoking this script via run_python_file; the script then
# builds up to that stage, frames the camera, logs a `STAGE_DONE_T<N>` marker,
# and exits cleanly. Default (no flag) = 99 = full build, identical to v3
# behavior. Stage map: 0=wipe, 1=atmosphere, 2=geometry skeleton, 3=props
# (containers+pipes), 4=full hero (effects + camera).
_raw_stage = getattr(builtins, 'DESERT_BUILD_STAGE', None)
try:
    _BUILD_STAGE = int(_raw_stage) if _raw_stage is not None else 99
except (TypeError, ValueError):
    _BUILD_STAGE = 99
    log(f'invalid DESERT_BUILD_STAGE={_raw_stage!r}; defaulting to full build')
# One-shot: `builtins` persists across UE Python runs within the same editor
# session, so leaving the attribute set would silently re-apply the staged
# value on later direct invocations of this script. Delete it after read so
# subsequent runs default back to full build unless the orchestrator
# re-injects the flag.
if hasattr(builtins, 'DESERT_BUILD_STAGE'):
    try:
        delattr(builtins, 'DESERT_BUILD_STAGE')
    except Exception:
        pass


def _apply_hero_camera():
    cam_loc = unreal.Vector(-3000, 250, 750)
    cam_rot = unreal.Rotator(roll=0, pitch=6, yaw=-4)
    try:
        LES = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        LES.editor_set_game_view(True)
    except Exception as e:
        log(f'LES game view skip: {e}')
    try:
        UES = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        UES.set_level_viewport_camera_info(cam_loc, cam_rot)
    except Exception as e:
        log(f'cam info set failed: {e}')
    try:
        LES = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        LES.editor_invalidate_viewports()
    except Exception:
        pass


def _stop_after(stage, label):
    """If the external orchestrator requested an early stop at or below
    `stage`, frame the camera, log a STAGE_DONE marker, and exit cleanly so
    the orchestrator can trigger a HighResShot before the next stage call.
    No-op when DESERT_BUILD_STAGE wasn't set (default full build)."""
    if _BUILD_STAGE <= stage:
        _apply_hero_camera()
        log(f'STAGE_DONE_T{stage}_{label}')
        sys.exit(0)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def make_mi(name, parent_path, color, roughness=None):
    """Create-or-replace a MaterialInstanceConstant under /Game/Validation/Desert/."""
    dest_folder = '/Game/Validation/Desert'
    dest_path = f'{dest_folder}/{name}'
    if ELLib.does_asset_exist(dest_path):
        ELLib.delete_asset(dest_path)
    parent = unreal.load_asset(parent_path)
    factory = unreal.MaterialInstanceConstantFactoryNew()
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    mi = asset_tools.create_asset(name, dest_folder, unreal.MaterialInstanceConstant, factory)
    if mi is None:
        log(f'ERROR: failed to create MI {name}')
        return None
    try:
        mi.set_editor_property('parent', parent)
    except Exception as e:
        log(f'set parent on {name} failed: {e}')
    try:
        unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(mi, 'Color', color)
    except Exception as e:
        log(f'set Color on {name} failed: {e}')
    if roughness is not None:
        try:
            unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(mi, 'Roughness', roughness)
        except Exception as e:
            log(f'set Roughness on {name} failed: {e}')
    ELLib.save_asset(dest_path, only_if_is_dirty=False)
    return mi


def spawn_static(mesh_path, location, rotation, scale, label, material=None):
    mesh = unreal.load_asset(mesh_path)
    actor = ell.spawn_actor_from_class(unreal.StaticMeshActor, location, rotation)
    actor.set_actor_label(label)
    actor.set_actor_scale3d(scale)
    smc = actor.static_mesh_component
    smc.set_mobility(unreal.ComponentMobility.STATIC)
    smc.set_static_mesh(mesh)
    if material is not None:
        try:
            num_slots = max(1, smc.get_num_materials())
        except Exception:
            num_slots = 1
        for s in range(num_slots):
            try:
                smc.set_material(s, material)
            except Exception:
                pass
    return actor


# ----------------------------------------------------------------------------
# 1. Wipe prior + hide competing actors
# ----------------------------------------------------------------------------

# LEGACY_VAL_CLEANUP: opt-in flag. The earlier v1/v2 builds spawned actors with
# `Val*` labels (Stage 4-5 of the original validation run). They no longer
# appear in v3+ runs, so wiping any `Val*` actor by default risks deleting
# unrelated level content in shared maps. Default off; set True when re-running
# on a level that still has the legacy validation actors lingering.
LEGACY_VAL_CLEANUP = False

removed = 0
hidden = 0
hidden_lights = 0
# v6.1: also hide any StaticMeshActor whose material is WorldGridMaterial — the
# UE default-template "Floor" actor uses this material and shows through as a
# checker pattern under our sand plane in HighResShot captures (visible in
# PR #1's T4-hero.png). We HIDE rather than destroy because the actor may be
# user-owned level content; hiding is reversible by the user.
def _uses_world_grid(actor):
    try:
        smc = actor.get_component_by_class(unreal.StaticMeshComponent)
        if smc is None:
            return False
        for i in range(max(1, smc.get_num_materials())):
            mat = smc.get_material(i)
            if mat is None:
                continue
            # Walk parent chain — WorldGridMaterial may be referenced directly
            # or via a MaterialInstance whose parent is WorldGridMaterial.
            cur = mat
            for _ in range(4):
                if 'WorldGridMaterial' in cur.get_path_name():
                    return True
                parent = None
                try:
                    parent = cur.get_editor_property('parent')
                except Exception:
                    parent = None
                if parent is None or parent == cur:
                    break
                cur = parent
    except Exception:
        pass
    return False

for a in list(ell.get_all_level_actors()):
    label = a.get_actor_label()
    cls = a.get_class().get_name()
    if label.startswith('Desert_') or (LEGACY_VAL_CLEANUP and label.startswith('Val')):
        ell.destroy_actor(a)
        removed += 1
    elif label in ('SM_SkySphere', 'SkySphereBlueprint', 'Sky_Sphere'):
        try:
            a.set_actor_hidden_in_game(True)
            a.set_is_temporarily_hidden_in_editor(True)
            hidden += 1
        except Exception:
            pass
    elif cls == 'StaticMeshActor' and _uses_world_grid(a):
        try:
            a.set_actor_hidden_in_game(True)
            a.set_is_temporarily_hidden_in_editor(True)
            hidden += 1
        except Exception:
            pass
    elif cls in ('DirectionalLight', 'SkyAtmosphere', 'ExponentialHeightFog', 'SkyLight', 'VolumetricCloud'):
        try:
            a.set_actor_hidden_in_game(True)
            a.set_is_temporarily_hidden_in_editor(True)
            hidden_lights += 1
        except Exception:
            pass
log(f'wiped {removed}; hid {hidden} sky meshes, {hidden_lights} atmosphere/light actors')
_stop_after(0, 'empty')

# ----------------------------------------------------------------------------
# 2. Materials
# ----------------------------------------------------------------------------

basic_mat = '/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial'
mi_sand_dark = make_mi('MI_SandDark', basic_mat, unreal.LinearColor(0.55, 0.32, 0.15, 1.0), 0.95)
mi_sand_light = make_mi('MI_SandLight', basic_mat, unreal.LinearColor(0.72, 0.45, 0.22, 1.0), 0.95)
mi_rock = make_mi('MI_Rock', basic_mat, unreal.LinearColor(0.18, 0.13, 0.10, 1.0), 0.90)
mi_metal = make_mi('MI_TowerMetal', basic_mat, unreal.LinearColor(0.10, 0.07, 0.05, 1.0), 0.55)
mi_metal_rust = make_mi('MI_RustMetal', basic_mat, unreal.LinearColor(0.22, 0.10, 0.06, 1.0), 0.65)
mi_crate = make_mi('MI_Crate', basic_mat, unreal.LinearColor(0.28, 0.18, 0.13, 1.0), 0.60)
mi_dark = make_mi('MI_Dark', basic_mat, unreal.LinearColor(0.06, 0.05, 0.04, 1.0), 0.50)

# v7: high-quality Polyhaven-textured MIs (imported via marketplace_import).
# Each one falls back to the flat-color counterpart above when the textured
# asset is missing — so the script keeps working in a fresh project where
# the CC0 imports haven't run yet.
def _load_or_fallback(textured_path, fallback_mi):
    try:
        tex_mi = unreal.load_asset(textured_path)
        if tex_mi is not None:
            return tex_mi
    except Exception:
        pass
    return fallback_mi

mi_sand_textured = _load_or_fallback('/Game/Validation/Materials/MI_T_Sand', mi_sand_dark)
mi_rock_textured = _load_or_fallback('/Game/Validation/Materials/MI_T_Rock', mi_rock)
mi_metal_rust_textured = _load_or_fallback('/Game/Validation/Materials/MI_T_MetalRust', mi_metal_rust)
mi_metal_plate_textured = _load_or_fallback('/Game/Validation/Materials/MI_T_MetalPlate', mi_metal)
_textured_sand_ok = mi_sand_textured is not mi_sand_dark
_textured_rock_ok = mi_rock_textured is not mi_rock
_textured_rust_ok = mi_metal_rust_textured is not mi_metal_rust
_textured_plate_ok = mi_metal_plate_textured is not mi_metal
log(f'materials made (textured layers — sand={_textured_sand_ok}, rock={_textured_rock_ok}, rust={_textured_rust_ok}, plate={_textured_plate_ok})')

# v7: When textured MIs are present, promote them as the primary material
# bindings used by the rest of the script. The legacy flat-color MIs above
# stay as the fallback path so the script still produces something usable
# in a fresh project that hasn't run the marketplace_import bootstrap.
if _textured_sand_ok:
    mi_sand_dark = mi_sand_textured
    mi_sand_light = mi_sand_textured
if _textured_rock_ok:
    mi_rock = mi_rock_textured
if _textured_rust_ok:
    mi_metal_rust = mi_metal_rust_textured
    mi_crate = mi_metal_rust_textured  # crates read better with rust grit; gated on rust, not plate
if _textured_plate_ok:
    # Tower metal + gantry metal use the plate material so the lattice
    # picks up real surface detail under the daylight HDRI.
    mi_metal = mi_metal_plate_textured

# ----------------------------------------------------------------------------
# 3. Atmosphere stack
# ----------------------------------------------------------------------------

sky_atm = ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
sky_atm.set_actor_label('Desert_SkyAtmosphere')
try:
    sky_atm_comp = sky_atm.get_component_by_class(unreal.SkyAtmosphereComponent)
    # v6: default Rayleigh = blue-sky daylight scattering. Skip custom red-shifted values; UE defaults produce normal blue sky.
    sky_atm_comp.set_editor_property('multi_scattering_factor', 1.0)
except Exception as e:
    log(f'sky atm tune skip: {e}')

sun = ell.spawn_actor_from_class(
    unreal.DirectionalLight,
    unreal.Vector(0, 0, 500),
    unreal.Rotator(roll=0.0, pitch=-35.0, yaw=-45.0),  # v6: pitch -3 (horizon-skimming) -> -35 (midday overhead). Named args; Rotator positional order is (pitch, yaw, roll) per Rotator.h:103.
)
sun.set_actor_label('Desert_Sun')
sun_comp = sun.light_component
sun_comp.set_intensity(10.0)  # v6: standard midday sunlight lux
sun_comp.set_light_color(unreal.LinearColor(1.0, 0.97, 0.92, 1.0))  # v6: neutral warm-white sunlight (was 1.0,0.72,0.48 sunset orange)
for prop, val in [
    ('atmosphere_sun_light', True),
    ('use_temperature', True),
    ('temperature', 5500.0),  # v6: neutral daylight white balance (was 3400 sunset amber)
    ('volumetric_scattering_intensity', 0.1),  # v6: minimal scattering — no sunset godrays
]:
    try:
        sun_comp.set_editor_property(prop, val)
    except Exception:
        pass

fog = ell.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
fog.set_actor_label('Desert_Fog')
fog_comp = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
fog_props = [
    ('fog_density', 0.04),  # v6: 0.10 → 0.04 — light atmospheric haze only, not sunset soup
    ('fog_height_falloff', 0.20),  # v6: 0.10 → 0.20 — fog stays low so distant geometry reads clean
    ('fog_inscattering_luminance', unreal.LinearColor(0.70, 0.78, 0.88, 1.0)),  # v6: neutral sky-haze blue (was sunset amber 0.75,0.50,0.28)
    ('fog_inscattering_color', unreal.LinearColor(0.70, 0.78, 0.88, 1.0)),
    ('directional_inscattering_color', unreal.LinearColor(1.0, 0.97, 0.92, 1.0)),  # v6: warm-white sunlight (was sunset gold 0.95,0.55,0.25)
    ('directional_inscattering_exponent', 4.0),  # v6: 7 → 4 — much wider, softer cone (no tight sunset hot-spot)
    ('directional_inscattering_start_distance', 2500.0),  # v6: 1200 → 2500 — push the warm cone deep so foreground stays neutral
    ('volumetric_fog', True),
    ('volumetric_fog_distance', 60000.0),
    ('volumetric_fog_extinction_scale', 0.2),  # v6: 0.4 → 0.2 — thinner volumetric layer
    ('start_distance', 500.0),  # v6: 200 → 500 — clear air near camera, fog only far away
]
for prop, val in fog_props:
    try:
        fog_comp.set_editor_property(prop, val)
    except Exception:
        pass

sky_light = ell.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 200))
sky_light.set_actor_label('Desert_SkyLight')
try:
    sky_light.light_component.set_editor_property('real_time_capture', True)
    sky_light.light_component.set_editor_property('intensity', 1.6)  # v6: 1.4 → 1.6 — generous ambient fill for daylight feel
except Exception:
    pass

clouds = ell.spawn_actor_from_class(unreal.VolumetricCloud, unreal.Vector(0, 0, 0))
clouds.set_actor_label('Desert_Clouds')

# Post-process for warm color grade + bloom
ppv = ell.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
ppv.set_actor_label('Desert_PostFX')
ppv.unbound = True
try:
    s = ppv.settings
    s.override_bloom_intensity = True
    s.bloom_intensity = 0.4  # v6: gentle natural bloom
    s.override_auto_exposure_bias = True
    s.auto_exposure_bias = 0.0  # v6: neutral exposure — let UE pick what looks natural
    s.override_auto_exposure_min_brightness = True
    s.auto_exposure_min_brightness = 0.1
    s.override_auto_exposure_max_brightness = True
    s.auto_exposure_max_brightness = 3.0  # v6: 1.2 → 3.0 — wide range so daylight reads bright but mid-tones don't bloom
    s.override_color_saturation = True
    s.color_saturation = unreal.Vector4(1.0, 1.0, 1.0, 1.0)  # v6: neutral saturation (no amber lift)
    s.override_color_gain = True
    s.color_gain = unreal.Vector4(1.0, 1.0, 1.0, 1.0)  # v6: neutral white balance
    s.override_film_toe = True
    s.film_toe = 0.85  # v6: 0.95 → 0.85 — softer toe so shadows don't crush black
    ppv.settings = s
except Exception as e:
    log(f'post-process tune skip: {e}')

log('atmosphere + post-fx spawned')
_stop_after(1, 'atmosphere')

# ----------------------------------------------------------------------------
# 4. Ground (large sand plane)
# ----------------------------------------------------------------------------

spawn_static(
    '/Engine/BasicShapes/Plane.Plane',
    unreal.Vector(0, 0, -50),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(400, 400, 1),
    'Desert_Ground',
    material=mi_sand_dark,
)

# ----------------------------------------------------------------------------
# 5. Sand dunes — 36 buried-sphere saucers in 2 concentric rings
# ----------------------------------------------------------------------------

for ring, (count, base_r, r_jitter) in enumerate([(20, 1700, 600), (16, 3200, 800)]):
    for i in range(count):
        angle = (i / count) * 2 * math.pi
        radius = base_r + random.uniform(-r_jitter * 0.3, r_jitter)
        x = math.cos(angle) * radius
        y = math.sin(angle) * radius
        z = -130 + random.uniform(-30, 40)
        sx = random.uniform(10, 22)
        sy = random.uniform(10, 22)
        sz = random.uniform(0.4, 0.95)
        yaw = random.uniform(0, 360)
        spawn_static(
            '/Engine/BasicShapes/Sphere.Sphere',
            unreal.Vector(x, y, z),
            unreal.Rotator(0, 0, yaw),
            unreal.Vector(sx, sy, sz),
            f'Desert_Dune_R{ring}_{i:02d}',
            material=mi_sand_light if (i % 3 == 0) else mi_sand_dark,
        )

# ----------------------------------------------------------------------------
# 6. Distant rocky-ridge silhouettes (12 wedges deep in the haze)
# ----------------------------------------------------------------------------

for i in range(12):
    angle_deg = (i / 12.0) * 360 + random.uniform(-10, 10)
    angle = math.radians(angle_deg)
    radius = 18000 + random.uniform(-2000, 2000)
    x = math.cos(angle) * radius
    y = math.sin(angle) * radius
    z = -200 + random.uniform(-100, 100)
    sx = random.uniform(40, 80)
    sy = random.uniform(80, 130)
    sz = random.uniform(15, 35)
    yaw = random.uniform(0, 90)
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(x, y, z),
        unreal.Rotator(0, random.uniform(-15, 15), yaw),
        unreal.Vector(sx, sy, sz),
        f'Desert_Mountain_{i:02d}',
        material=mi_rock,
    )

# ----------------------------------------------------------------------------
# 6b. Metal foundation slab — large industrial platform UNDER the tower base.
# Composed of: 1 big slab + 4 corner support cylinders + 32 rivets along edges +
# 1 recessed center plate (composite, 39 props total).
# ----------------------------------------------------------------------------

foundation_x = 0
foundation_y = 0
foundation_z = -45
# Main slab (15m x 15m x 0.4m)
spawn_static(
    '/Engine/BasicShapes/Cube.Cube',
    unreal.Vector(foundation_x, foundation_y, foundation_z),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(15.0, 15.0, 0.4),
    'Desert_Foundation_Slab',
    material=mi_metal_rust,
)
# Recessed inner ring/disc (slightly raised to read as a service plate)
spawn_static(
    '/Engine/BasicShapes/Cylinder.Cylinder',
    unreal.Vector(foundation_x, foundation_y, foundation_z + 25),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(8.0, 8.0, 0.2),
    'Desert_Foundation_Plate',
    material=mi_dark,
)
# 4 corner support posts (chunky cylinders embedded into the slab)
for ci, (cx, cy) in enumerate([(-700, -700), (700, -700), (-700, 700), (700, 700)]):
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(cx, cy, foundation_z + 60),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.6, 0.6, 1.4),
        f'Desert_Foundation_CornerPost_{ci}',
        material=mi_dark,
    )
# 32 rivets along the 4 edges (8 per edge), small cylinders flush with slab top
for ei, edge in enumerate([
    [(-630 + i*180, -740) for i in range(8)],
    [(-630 + i*180, +740) for i in range(8)],
    [(-740, -630 + i*180) for i in range(8)],
    [(+740, -630 + i*180) for i in range(8)],
]):
    for ri, (rx, ry) in enumerate(edge):
        spawn_static(
            '/Engine/BasicShapes/Cylinder.Cylinder',
            unreal.Vector(rx, ry, foundation_z + 22),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.12, 0.12, 0.06),
            f'Desert_Foundation_Rivet_{ei}_{ri}',
            material=mi_dark,
        )

# ----------------------------------------------------------------------------
# 7. Stepped pyramid base (3 stacked cubes)
# ----------------------------------------------------------------------------

base_steps = [
    (700, 700, 100),
    (520, 520, 100),
    (340, 340, 100),
]
z_cursor = -50
for i, (sx, sy, sz) in enumerate(base_steps):
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(0, 0, z_cursor + sz / 2),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(sx / 100.0, sy / 100.0, sz / 100.0),
        f'Desert_Base_{i}',
        material=mi_dark,
    )
    z_cursor += sz
base_top_z = z_cursor

# ----------------------------------------------------------------------------
# 8. Central tower — DENSE lattice with derrick crown
# ----------------------------------------------------------------------------

tower_x = 0
tower_y = 0
tower_base_z = base_top_z
tower_height = 1800
leg_offsets = [(+90, +90), (+90, -90), (-90, +90), (-90, -90)]

# Vertical legs (cylinders)
for li, (dx, dy) in enumerate(leg_offsets):
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(tower_x + dx, tower_y + dy, tower_base_z + tower_height / 2),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.20, 0.20, tower_height / 100.0),
        f'Desert_TowerLeg_{li}',
        material=mi_metal,
    )

# Inner stiffener legs (slightly offset toward center for visual depth)
for li, (dx, dy) in enumerate([(+60, +60), (+60, -60), (-60, +60), (-60, -60)]):
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(tower_x + dx, tower_y + dy, tower_base_z + tower_height / 2),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.10, 0.10, tower_height / 100.0),
        f'Desert_TowerInnerLeg_{li}',
        material=mi_metal_rust,
    )

# DENSE horizontal cross-braces every 60 z (was 150) — 30 levels
brace_levels = list(range(60, tower_height, 60))
brace_pairs = [
    ((+90, +90), (+90, -90)),
    ((+90, -90), (-90, -90)),
    ((-90, -90), (-90, +90)),
    ((-90, +90), (+90, +90)),
]
for lvl_idx, dz in enumerate(brace_levels):
    z = tower_base_z + dz
    for pi, (a, b) in enumerate(brace_pairs):
        cx = (a[0] + b[0]) / 2
        cy = (a[1] + b[1]) / 2
        if a[0] == b[0]:
            length = abs(a[1] - b[1])
            scale = unreal.Vector(0.05, length / 100.0, 0.05)
        else:
            length = abs(a[0] - b[0])
            scale = unreal.Vector(length / 100.0, 0.05, 0.05)
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(tower_x + cx, tower_y + cy, z),
            unreal.Rotator(0, 0, 0),
            scale,
            f'Desert_TowerBrace_{lvl_idx:02d}_{pi}',
            material=mi_metal,
        )

# X-pattern diagonal cross-bracing on each face every 120z
diag_levels = list(range(60, tower_height - 60, 120))
for lvl_idx, dz in enumerate(diag_levels):
    z_lo = tower_base_z + dz
    z_hi = tower_base_z + dz + 120
    z_mid = (z_lo + z_hi) / 2
    diag_len_3d = math.sqrt(180**2 + 120**2)
    diag_pitch = math.degrees(math.atan2(120, 180))
    for face_idx, ((ax, ay), (bx, by)) in enumerate(brace_pairs):
        # Two diagonals per face forming X
        for sign in (+1, -1):
            mx = (ax + bx) / 2
            my = (ay + by) / 2
            if ax == bx:
                yaw = 90 if ay < by else -90
            else:
                yaw = 0 if ax < bx else 180
            spawn_static(
                '/Engine/BasicShapes/Cube.Cube',
                unreal.Vector(tower_x + mx, tower_y + my, z_mid),
                unreal.Rotator(0, sign * diag_pitch, yaw),
                unreal.Vector(diag_len_3d / 100.0, 0.04, 0.04),
                f'Desert_TowerDiag_{lvl_idx:02d}_{face_idx}_{1 if sign>0 else 0}',
                material=mi_metal_rust,
            )

# Catwalk platforms every 360z (4 platforms)
for pi, dz in enumerate([300, 700, 1100, 1500]):
    z = tower_base_z + dz
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(tower_x, tower_y, z),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(2.4, 2.4, 0.06),
        f'Desert_TowerCatwalk_{pi}',
        material=mi_metal_rust,
    )

# Vertical ladder rungs on +x face (every 30 z)
for ri, dz in enumerate(range(30, tower_height, 30)):
    z = tower_base_z + dz
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(tower_x + 95, tower_y, z),
        unreal.Rotator(0, 0, 90),
        unreal.Vector(0.40, 0.04, 0.04),
        f'Desert_TowerRung_{ri:02d}',
        material=mi_metal,
    )

# Derrick crown (4 angled cubes forming a converging cap)
crown_z = tower_base_z + tower_height + 60
for ci, (dx, dy, yaw) in enumerate([(0, 0, 0), (90, 0, 0), (-90, 0, 0), (0, 90, 90)]):
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(tower_x + dx, tower_y + dy, crown_z),
        unreal.Rotator(0, -25, yaw),
        unreal.Vector(0.06, 0.06, 1.2),
        f'Desert_TowerCrown_{ci}',
        material=mi_metal_rust,
    )

# Tower cap cone
spawn_static(
    '/Engine/BasicShapes/Cone.Cone',
    unreal.Vector(tower_x, tower_y, tower_base_z + tower_height + 130),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(2.0, 2.0, 1.4),
    'Desert_TowerCap',
    material=mi_metal_rust,
)

tower_top_z = tower_base_z + tower_height + 200

# ----------------------------------------------------------------------------
# 9. Multiple vertical cables (3 + 1 main thicker)
# ----------------------------------------------------------------------------

spawn_static(
    '/Engine/BasicShapes/Cylinder.Cylinder',
    unreal.Vector(tower_x, tower_y, tower_top_z + 1500),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(0.20, 0.20, 30.0),
    'Desert_Cable_Main',
    material=mi_dark,
)
for ci, (dx, dy) in enumerate([(40, 40), (-40, 40), (40, -40)]):
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(tower_x + dx, tower_y + dy, tower_top_z + 1200),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.08, 0.08, 24.0),
        f'Desert_Cable_Aux_{ci}',
        material=mi_dark,
    )

# ----------------------------------------------------------------------------
# 10. Foreground gantries (4 frames + connecting catwalks between adjacent)
# ----------------------------------------------------------------------------

gantry_specs = [
    ( 600,  650, 30),
    ( 600, -650, -30),
    (-200,  900, 50),
    (-200, -900, -50),
    (1200,    0, 0),
    (-700,  300, 80),
    (-700, -300, -80),
]
for gi, (gx, gy, gyaw) in enumerate(gantry_specs):
    rad = math.radians(gyaw + 90)
    for sign in (+1, -1):
        offset_x = math.cos(rad) * 80 * sign
        offset_y = math.sin(rad) * 80 * sign
        spawn_static(
            '/Engine/BasicShapes/Cylinder.Cylinder',
            unreal.Vector(gx + offset_x, gy + offset_y, 200),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.14, 0.14, 4.0),
            f'Desert_GantryLeg_{gi}_{1 if sign > 0 else 0}',
            material=mi_metal,
        )
    for bz in (50, 150, 280, 380):
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(gx, gy, bz),
            unreal.Rotator(0, 0, gyaw + 90),
            unreal.Vector(1.6, 0.05, 0.05),
            f'Desert_GantryBrace_{gi}_{bz}',
            material=mi_metal_rust,
        )
    # Diagonal X braces between legs
    for sign in (+1, -1):
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(gx, gy, 215),
            unreal.Rotator(0, sign * 60, gyaw + 90),
            unreal.Vector(1.8, 0.04, 0.04),
            f'Desert_GantryDiag_{gi}_{1 if sign>0 else 0}',
            material=mi_metal_rust,
        )

log('geometry skeleton spawned (ground/dunes/ridges/foundation/pyramid/tower/cables/gantries)')
_stop_after(2, 'geometry')

# ----------------------------------------------------------------------------
# 11. Shipping containers — 8 high-detail composite containers replacing the
# v3 plain-box clusters. Each container = main body + 16 corrugation ribs +
# 4 corner posts + 2 door panels + 2 latch handles + roof ridge. ~26 props
# per container = ~208 props total for this section.
# ----------------------------------------------------------------------------

_container_local_rng = random.Random(7)

container_positions = [
    # (cx, cy, yaw_deg)
    (-1100,  450,   8),
    (-1100, -450,  -7),
    (-1500,  150,  18),
    ( -700,  650, -12),
    ( -700, -650,   5),
    ( -300,  400,  22),
    ( -300, -400, -18),
    (  350,  -50, -25),
]

def spawn_shipping_container(cx, cy, yaw_deg, idx):
    """Compose a detailed shipping container at (cx,cy) with the given yaw."""
    body_len = 240.0
    body_wid = 100.0
    body_hgt = 110.0
    base_z = -50.0 + body_hgt / 2

    yaw_rad = math.radians(yaw_deg)
    rot = unreal.Rotator(0, 0, yaw_deg)

    def place_world(lx, ly):
        wx = cx + math.cos(yaw_rad) * lx - math.sin(yaw_rad) * ly
        wy = cy + math.sin(yaw_rad) * lx + math.cos(yaw_rad) * ly
        return unreal.Vector(wx, wy, base_z)

    # 1) Main body cube
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        place_world(0, 0),
        rot,
        unreal.Vector(body_len / 100.0, body_wid / 100.0, body_hgt / 100.0),
        f'Desert_Container_{idx}_Body',
        material=mi_crate,
    )

    # 2) 16 corrugation ribs — 8 vertical thin cubes along each long side
    rib_count = 8
    for side_sign in (+1, -1):
        for ri in range(rib_count):
            lx = -body_len / 2 + 18 + ri * ((body_len - 36) / (rib_count - 1))
            ly = side_sign * (body_wid / 2 + 2)
            wx = cx + math.cos(yaw_rad) * lx - math.sin(yaw_rad) * ly
            wy = cy + math.sin(yaw_rad) * lx + math.cos(yaw_rad) * ly
            spawn_static(
                '/Engine/BasicShapes/Cube.Cube',
                unreal.Vector(wx, wy, base_z),
                rot,
                unreal.Vector(0.06, 0.04, (body_hgt - 12) / 100.0),
                f'Desert_Container_{idx}_Rib_{0 if side_sign > 0 else 1}_{ri}',
                material=mi_metal_rust,
            )

    # 3) 4 corner posts — chunky cylinders full body height at each corner
    for corner_i, (lx, ly) in enumerate([
        (-body_len / 2 - 2, -body_wid / 2 - 2),
        (+body_len / 2 + 2, -body_wid / 2 - 2),
        (-body_len / 2 - 2, +body_wid / 2 + 2),
        (+body_len / 2 + 2, +body_wid / 2 + 2),
    ]):
        wx = cx + math.cos(yaw_rad) * lx - math.sin(yaw_rad) * ly
        wy = cy + math.sin(yaw_rad) * lx + math.cos(yaw_rad) * ly
        spawn_static(
            '/Engine/BasicShapes/Cylinder.Cylinder',
            unreal.Vector(wx, wy, base_z),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.12, 0.12, (body_hgt + 6) / 100.0),
            f'Desert_Container_{idx}_Corner_{corner_i}',
            material=mi_dark,
        )

    # 4) Roof ridge — raised cube along the top
    roof_z = base_z + body_hgt / 2 + 3
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(cx, cy, roof_z),
        rot,
        unreal.Vector(body_len / 100.0, body_wid / 100.0, 0.06),
        f'Desert_Container_{idx}_Roof',
        material=mi_metal_rust,
    )

    # 5) 2 door panels on the +X short end
    door_end_lx = body_len / 2 + 2
    for dpi, ly in enumerate([-body_wid / 4, +body_wid / 4]):
        wx = cx + math.cos(yaw_rad) * door_end_lx - math.sin(yaw_rad) * ly
        wy = cy + math.sin(yaw_rad) * door_end_lx + math.cos(yaw_rad) * ly
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(wx, wy, base_z),
            rot,
            unreal.Vector(0.04, (body_wid / 2) / 100.0, (body_hgt - 8) / 100.0),
            f'Desert_Container_{idx}_Door_{dpi}',
            material=mi_dark,
        )

    # 6) 2 latch handles on the door end
    for latch_i, ly_handle in enumerate([-body_wid / 5, +body_wid / 5]):
        wx = cx + math.cos(yaw_rad) * (door_end_lx + 6) - math.sin(yaw_rad) * ly_handle
        wy = cy + math.sin(yaw_rad) * (door_end_lx + 6) + math.cos(yaw_rad) * ly_handle
        spawn_static(
            '/Engine/BasicShapes/Cylinder.Cylinder',
            unreal.Vector(wx, wy, base_z),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.04, 0.04, (body_hgt - 24) / 100.0),
            f'Desert_Container_{idx}_Latch_{latch_i}',
            material=mi_dark,
        )

for ci_idx, (cx_pos, cy_pos, yaw_pos) in enumerate(container_positions):
    yaw_final = yaw_pos + _container_local_rng.uniform(-4, 4)
    spawn_shipping_container(cx_pos, cy_pos, yaw_final, ci_idx)

# ----------------------------------------------------------------------------
# 11b. Industrial pipes — 6 horizontal pipes across the foundation + 6
# vertical riser pipes climbing the tower base on the +X face. Joints capped
# with spheres at endpoints for visual punctuation.
# ----------------------------------------------------------------------------

# Horizontal pipes across the foundation (long axis along world X)
for pipe_i in range(6):
    py_pos = -600 + pipe_i * 240
    pz_pos = foundation_z + 30 + (pipe_i % 3) * 8
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(0, py_pos, pz_pos),
        unreal.Rotator(roll=0, pitch=90, yaw=0),
        unreal.Vector(0.12, 0.12, 8.0),
        f'Desert_Pipe_H_{pipe_i}',
        material=mi_metal_rust,
    )
    for joint_i, ex_sign in enumerate([+1, -1]):
        spawn_static(
            '/Engine/BasicShapes/Sphere.Sphere',
            unreal.Vector(ex_sign * 400, py_pos, pz_pos),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.18, 0.18, 0.18),
            f'Desert_Pipe_H_{pipe_i}_Joint_{joint_i}',
            material=mi_metal_rust,
        )

# Vertical riser pipes on the +X face of the tower base
for pipe_i in range(6):
    px_pos = tower_x + 105
    py_pos = tower_y - 75 + pipe_i * 30
    pz_low = tower_base_z + 50
    pz_high = tower_base_z + 350
    pz_mid = (pz_low + pz_high) / 2
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(px_pos, py_pos, pz_mid),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.08, 0.08, (pz_high - pz_low) / 100.0),
        f'Desert_Pipe_V_{pipe_i}',
        material=mi_metal_rust,
    )
    for joint_i, pz in enumerate([pz_low, pz_high]):
        spawn_static(
            '/Engine/BasicShapes/Sphere.Sphere',
            unreal.Vector(px_pos, py_pos, pz),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.14, 0.14, 0.14),
            f'Desert_Pipe_V_{pipe_i}_Joint_{joint_i}',
            material=mi_metal_rust,
        )

# 4 elbow junction spheres at foundation corners
for elbow_i, (ex, ey, ez) in enumerate([
    (400, -600, foundation_z + 30),
    (-400, -600, foundation_z + 30),
    (400, 600, foundation_z + 38),
    (-400, 600, foundation_z + 38),
]):
    spawn_static(
        '/Engine/BasicShapes/Sphere.Sphere',
        unreal.Vector(ex, ey, ez),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.22, 0.22, 0.22),
        f'Desert_Pipe_Elbow_{elbow_i}',
        material=mi_metal_rust,
    )

log('detail: 8 containers + foundation + pipes spawned')
_stop_after(3, 'props')

# ----------------------------------------------------------------------------
# 12. Boulder field — 30 spheres + cubes scattered in foreground
# ----------------------------------------------------------------------------

for bi in range(30):
    angle = random.uniform(0, 2 * math.pi)
    r = random.uniform(800, 2000)
    # Bias to camera-facing arc (-x side)
    if random.random() < 0.6:
        angle = random.uniform(math.radians(120), math.radians(240))
    x = math.cos(angle) * r
    y = math.sin(angle) * r
    sx = random.uniform(0.6, 2.5)
    sy = random.uniform(0.6, 2.5)
    sz = random.uniform(0.4, 1.5)
    yaw = random.uniform(0, 360)
    mesh = '/Engine/BasicShapes/Sphere.Sphere' if (bi % 3) else '/Engine/BasicShapes/Cube.Cube'
    spawn_static(
        mesh,
        unreal.Vector(x, y, -30 + sz * 30),
        unreal.Rotator(random.uniform(-30, 30), random.uniform(-30, 30), yaw),
        unreal.Vector(sx, sy, sz),
        f'Desert_Boulder_{bi:02d}',
        material=mi_rock,
    )

# ----------------------------------------------------------------------------
# 13. Niagara dust — spawn template at multiple locations
# ----------------------------------------------------------------------------

dust_template_paths = [
    '/Niagara/DefaultAssets/Templates/Emitters/BlowingParticles.BlowingParticles',
]
dust_template = None
for p in dust_template_paths:
    try:
        dust_template = unreal.load_asset(p)
        if dust_template is not None:
            log(f'dust template loaded from {p}; class={dust_template.get_class().get_name()}')
            break
    except Exception:
        continue

# Try to spawn regardless of class — if it's a NiagaraSystem, comp.set_asset works.
# If it's a NiagaraEmitter, log and skip (would need NiagaraSystem creation API).
if dust_template is not None:
    dust_locations = [
        unreal.Vector(-800, 200, 100),
        unreal.Vector(-800, -200, 100),
        unreal.Vector(0, 600, 100),
        unreal.Vector(0, -600, 100),
        unreal.Vector(-1500, 0, 80),
    ]
    for di, loc in enumerate(dust_locations):
        try:
            actor = ell.spawn_actor_from_class(unreal.NiagaraActor, loc, unreal.Rotator(0, 0, 0))
            actor.set_actor_label(f'Desert_Dust_{di}')
            actor.set_actor_scale3d(unreal.Vector(12, 12, 6))
            comp = None
            try:
                comp = actor.get_niagara_component()
            except Exception:
                comp = actor.get_component_by_class(unreal.NiagaraComponent)
            if comp is not None:
                try:
                    comp.set_asset(dust_template)
                except Exception as e:
                    log(f'dust set_asset {di} failed (likely emitter not system): {e}')
                    # actor stays but inert; still hides on next wipe
        except Exception as e:
            log(f'dust spawn {di} failed: {e}')
else:
    log('dust template not found; skipping Niagara dust')

# ----------------------------------------------------------------------------
# 13b. Sun-disk billboard — bright emissive glow behind tower for visible sun
# ----------------------------------------------------------------------------

# Use an unlit emissive MaterialInstance pointing toward camera.
# v4: Color values calmed from (8.0, 4.8, 2.0) — those super-1.0 emissive values were
# driving the bloom cone that hurt the user's eyes. (2.0, 1.2, 0.5) still reads as a
# warm sun smear without the burn-out.
# v4: drop sun-disk entirely — auto-exposure clamp + lower bloom + dropped scattering
# make the disk redundant and risk re-introducing the center bloom that hurt eyes.
# Keep the make_mi call so MI_SunGlow asset still exists for re-runs that might enable
# it via flag later, but DON'T spawn the actor.
mi_sun = make_mi('MI_SunGlow', basic_mat, unreal.LinearColor(0.8, 0.45, 0.18, 1.0), 1.0)

# ----------------------------------------------------------------------------
# 14. Camera framing
# ----------------------------------------------------------------------------

_apply_hero_camera()
_stop_after(4, 'hero')

log('SCENE_BUILD_COMPLETE_V7_TEXTURED')
