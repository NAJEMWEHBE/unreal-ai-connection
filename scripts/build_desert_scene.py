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

random.seed(42)

ell = unreal.EditorLevelLibrary
ELLib = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f'[desert] {msg}')


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
    elif cls in ('DirectionalLight', 'SkyAtmosphere', 'ExponentialHeightFog', 'SkyLight', 'VolumetricCloud'):
        try:
            a.set_actor_hidden_in_game(True)
            a.set_is_temporarily_hidden_in_editor(True)
            hidden_lights += 1
        except Exception:
            pass
log(f'wiped {removed}; hid {hidden} sky meshes, {hidden_lights} atmosphere/light actors')

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
log(f'materials made')

# ----------------------------------------------------------------------------
# 3. Atmosphere stack
# ----------------------------------------------------------------------------

sky_atm = ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
sky_atm.set_actor_label('Desert_SkyAtmosphere')
try:
    sky_atm_comp = sky_atm.get_component_by_class(unreal.SkyAtmosphereComponent)
    sky_atm_comp.set_editor_property('rayleigh_scattering', unreal.LinearColor(0.20, 0.10, 0.045, 1.0))
    sky_atm_comp.set_editor_property('rayleigh_scattering_scale', 0.04)
    sky_atm_comp.set_editor_property('mie_scattering_scale', 0.005)
    sky_atm_comp.set_editor_property('multi_scattering_factor', 1.5)
except Exception as e:
    log(f'sky atm tune skip: {e}')

sun = ell.spawn_actor_from_class(
    unreal.DirectionalLight,
    unreal.Vector(0, 0, 500),
    unreal.Rotator(roll=0.0, pitch=-3.0, yaw=-45.0),  # named args — unreal.Rotator positional order is (roll, pitch, yaw), counter-intuitive vs the dict-display {pitch, yaw, roll}
)
sun.set_actor_label('Desert_Sun')
sun_comp = sun.light_component
sun_comp.set_intensity(20.0)
sun_comp.set_light_color(unreal.LinearColor(1.0, 0.55, 0.30, 1.0))
for prop, val in [
    ('atmosphere_sun_light', True),
    ('use_temperature', True),
    ('temperature', 2800.0),
    ('volumetric_scattering_intensity', 2.0),
]:
    try:
        sun_comp.set_editor_property(prop, val)
    except Exception:
        pass

fog = ell.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
fog.set_actor_label('Desert_Fog')
fog_comp = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
fog_props = [
    ('fog_density', 0.18),
    ('fog_height_falloff', 0.06),
    ('fog_inscattering_luminance', unreal.LinearColor(1.0, 0.55, 0.28, 1.0)),
    ('fog_inscattering_color', unreal.LinearColor(1.0, 0.55, 0.28, 1.0)),
    ('directional_inscattering_color', unreal.LinearColor(1.0, 0.40, 0.15, 1.0)),
    ('directional_inscattering_exponent', 6.0),
    ('directional_inscattering_start_distance', 800.0),
    ('volumetric_fog', True),
    ('volumetric_fog_distance', 80000.0),
    ('volumetric_fog_extinction_scale', 1.5),
    ('start_distance', 80.0),
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
    sky_light.light_component.set_editor_property('intensity', 0.8)
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
    s.bloom_intensity = 1.4
    s.override_auto_exposure_bias = True
    s.auto_exposure_bias = -0.4
    s.override_color_saturation = True
    s.color_saturation = unreal.Vector4(1.05, 1.0, 0.85, 1.0)
    s.override_color_gain = True
    s.color_gain = unreal.Vector4(1.05, 0.95, 0.85, 1.0)
    s.override_film_toe = True
    s.film_toe = 0.95
    ppv.settings = s
except Exception as e:
    log(f'post-process tune skip: {e}')

log('atmosphere + post-fx spawned')

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

# ----------------------------------------------------------------------------
# 11. Crates — 50, varied sizes, in 8 clusters skewed toward camera
# ----------------------------------------------------------------------------

crate_clusters = [
    (-1100,  450, 8, 250),
    (-1100, -450, 8, 250),
    (-1500,  150, 6, 180),
    (-700,  650, 6, 200),
    (-700, -650, 6, 200),
    (-300,  400, 5, 150),
    (-300, -400, 5, 150),
    ( 350,  -50, 6, 200),
]
ci = 0
for (cx, cy, count, spread) in crate_clusters:
    for k in range(count):
        x = cx + random.uniform(-spread, spread)
        y = cy + random.uniform(-spread, spread)
        yaw = random.uniform(0, 360)
        # Sometimes stack two crates
        stacks = random.choice([1, 1, 1, 2])
        z_base = -50
        for s in range(stacks):
            sx = random.uniform(2.0, 3.5)
            sy = random.uniform(1.0, 1.8)
            sz = random.uniform(0.9, 1.4)
            spawn_static(
                '/Engine/BasicShapes/Cube.Cube',
                unreal.Vector(x, y, z_base + sz * 50),
                unreal.Rotator(0, 0, yaw + random.uniform(-15, 15)),
                unreal.Vector(sx, sy, sz),
                f'Desert_Crate_{ci:02d}_S{s}',
                material=mi_crate if (s == 0) else mi_dark,
            )
            z_base += sz * 100
        ci += 1

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

# Use an unlit emissive MaterialInstance pointing toward camera
mi_sun = make_mi('MI_SunGlow', basic_mat, unreal.LinearColor(8.0, 4.8, 2.0, 1.0), 1.0)
# Place a large plane behind the tower, facing camera at -X
spawn_static(
    '/Engine/BasicShapes/Plane.Plane',
    unreal.Vector(8000, -800, 1200),
    unreal.Rotator(0, 90, 0),  # face -X
    unreal.Vector(20, 20, 1),
    'Desert_SunDisk',
    material=mi_sun,
)

# ----------------------------------------------------------------------------
# 14. Camera framing
# ----------------------------------------------------------------------------

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

log('SCENE_BUILD_COMPLETE_V3')
