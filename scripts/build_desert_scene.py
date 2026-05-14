# build_desert_scene.py — reconstruct the Mars/desert sunset photo composition in UE 5.7
#
# Composes warm sunset desert scene from /Engine/BasicShapes primitives + atmosphere actors.
# Idempotent: any prior actor with label starting "Val" or "Desert_" is destroyed at start.
#
# Match target (from the user's reference photo):
#  - Stepped pyramid base (3 stacked dark cubes)
#  - Tall central lattice tower (4 cylinder legs + cube horizontal/diagonal braces + cone cap)
#  - Vertical cable rising from tower top
#  - 4 flanking foreground gantries (2 legs + 3 horizontal braces each)
#  - Scattered metal shipping containers around base
#  - 12 dune-shaped scaled spheres around mid-distance
#  - 6 distant rocky cones for mountain silhouettes
#  - Big sand ground plane
#  - SkyAtmosphere + low warm DirectionalLight + ExponentialHeightFog (volumetric, orange)
#    + SkyLight + VolumetricCloud
#  - Editor camera framed low-front looking at the tower
#
# Honest scope: no photogrammetry / no AI mesh-from-photo / no PBR sand textures. Compositional
# match using primitive geometry + flat-tinted MaterialInstanceConstants of BasicShapeMaterial
# (only Color vector + Roughness scalar params exposed).

import unreal
import math
import random

random.seed(42)

ell = unreal.EditorLevelLibrary
ELLib = unreal.EditorAssetLibrary


def log(msg):
    unreal.log(f'[desert] {msg}')


# ---------------------------------------------------------------- Materials

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
    # UE 5.7 removed factory.initial_parent — set parent post-creation.
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
    smc.set_mobility(unreal.ComponentMobility.STATIC)  # set mobility BEFORE mesh assignment
    smc.set_static_mesh(mesh)
    if material is not None:
        # apply to every material slot — some meshes (Cone, Sphere) have multiple slots
        try:
            num_slots = smc.get_num_materials()
        except Exception:
            num_slots = 1
        for s in range(max(num_slots, 1)):
            try:
                smc.set_material(s, material)
            except Exception as e:
                log(f'set_material slot {s} on {label} skip: {e}')
    return actor


# ---------------------------------------------------------------- 1. Wipe prior + hide competing actors

removed = 0
hidden = 0
hidden_lights = 0
for a in list(ell.get_all_level_actors()):
    label = a.get_actor_label()
    cls = a.get_class().get_name()
    if label.startswith('Val') or label.startswith('Desert_'):
        ell.destroy_actor(a)
        removed += 1
    elif label in ('SM_SkySphere', 'SkySphereBlueprint', 'Sky_Sphere'):
        try:
            a.set_actor_hidden_in_game(True)
            a.set_is_temporarily_hidden_in_editor(True)
            hidden += 1
        except Exception as e:
            log(f'hide {label} skip: {e}')
    elif cls in ('DirectionalLight', 'SkyAtmosphere', 'ExponentialHeightFog', 'SkyLight', 'VolumetricCloud'):
        # Hide competing atmosphere actors so ours dominate
        try:
            a.set_actor_hidden_in_game(True)
            a.set_is_temporarily_hidden_in_editor(True)
            hidden_lights += 1
        except Exception as e:
            log(f'hide light {label}/{cls} skip: {e}')
log(f'wiped {removed} prior Desert/Val actors; hid {hidden} sky meshes; hid {hidden_lights} competing atmosphere/light actors')

# ---------------------------------------------------------------- 2. Materials

basic_mat = '/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial'
mi_sand = make_mi('MI_Sand', basic_mat, unreal.LinearColor(0.85, 0.55, 0.30, 1.0), 0.95)
mi_rock = make_mi('MI_Rock', basic_mat, unreal.LinearColor(0.25, 0.18, 0.15, 1.0), 0.85)
mi_metal = make_mi('MI_TowerMetal', basic_mat, unreal.LinearColor(0.18, 0.12, 0.10, 1.0), 0.55)
mi_crate = make_mi('MI_Crate', basic_mat, unreal.LinearColor(0.30, 0.22, 0.18, 1.0), 0.55)
mi_dark = make_mi('MI_Dark', basic_mat, unreal.LinearColor(0.12, 0.10, 0.08, 1.0), 0.50)
log(f'materials: sand={mi_sand} rock={mi_rock} metal={mi_metal} crate={mi_crate} dark={mi_dark}')

# ---------------------------------------------------------------- 3. Atmosphere

sky_atm = ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
sky_atm.set_actor_label('Desert_SkyAtmosphere')

# Sun: low + warm + behind-tower angle to backlight the silhouette
sun = ell.spawn_actor_from_class(
    unreal.DirectionalLight,
    unreal.Vector(0, 0, 500),
    unreal.Rotator(0.0, -3.5, -45.0),  # very low pitch for golden hour
)
sun.set_actor_label('Desert_Sun')
sun_comp = sun.light_component
sun_comp.set_intensity(15.0)
sun_comp.set_light_color(unreal.LinearColor(1.0, 0.55, 0.30, 1.0))
for prop, val in [
    ('atmosphere_sun_light', True),
    ('use_temperature', True),
    ('temperature', 3000.0),  # warm color temp
]:
    try:
        sun_comp.set_editor_property(prop, val)
    except Exception as e:
        log(f'sun {prop} skip: {e}')

# Volumetric warm fog — UE 5.7 renamed many ExponentialHeightFogComponent props.
fog = ell.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
fog.set_actor_label('Desert_Fog')
fog_comp = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
fog_props = [
    ('fog_density', 0.12),
    ('fog_height_falloff', 0.08),
    # Try multiple property names for inscattering — UE renamed across versions
    ('fog_inscattering_luminance', unreal.LinearColor(1.0, 0.55, 0.30, 1.0)),
    ('fog_inscattering_color', unreal.LinearColor(1.0, 0.55, 0.30, 1.0)),
    ('sky_atmosphere_ambient_contribution_color_scale', unreal.LinearColor(1.0, 0.55, 0.30, 1.0)),
    ('directional_inscattering_color', unreal.LinearColor(1.0, 0.45, 0.20, 1.0)),
    ('directional_inscattering_exponent', 8.0),
    ('directional_inscattering_start_distance', 1000.0),
    ('volumetric_fog', True),
    ('volumetric_fog_distance', 60000.0),
    ('volumetric_fog_extinction_scale', 1.0),
    ('start_distance', 100.0),
]
for prop, val in fog_props:
    try:
        fog_comp.set_editor_property(prop, val)
    except Exception as e:
        log(f'fog prop {prop} skip: {e}')

# Sky light (recapture from atmosphere)
sky_light = ell.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 200))
sky_light.set_actor_label('Desert_SkyLight')
sl_comp = sky_light.light_component
try:
    sl_comp.set_editor_property('real_time_capture', True)
except Exception as e:
    log(f'skylight realtime skip: {e}')

# Volumetric clouds
clouds = ell.spawn_actor_from_class(unreal.VolumetricCloud, unreal.Vector(0, 0, 0))
clouds.set_actor_label('Desert_Clouds')

log('atmosphere stack spawned')

# ---------------------------------------------------------------- 4. Ground

# Big sand plane. /Engine/BasicShapes/Plane is 100x100 at default scale.
spawn_static(
    '/Engine/BasicShapes/Plane.Plane',
    unreal.Vector(0, 0, -50),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(300, 300, 1),
    'Desert_Ground',
    material=mi_sand,
)

# ---------------------------------------------------------------- 5. Dunes (mid-distance)
# Use stretched flat spheres (saucer-like) but partially BURIED so they read as dunes.
# Z=-100 is well below the ground plane (which is at z=-50 with scale_z=1 = 50-unit thick).

for i in range(18):
    angle = (i / 18.0) * 2 * math.pi
    radius = 2200 + random.uniform(-400, 800)
    x = math.cos(angle) * radius
    y = math.sin(angle) * radius
    z = -120 + random.uniform(-30, 30)  # mostly buried
    sx = random.uniform(10, 20)
    sy = random.uniform(10, 20)
    sz = random.uniform(0.4, 0.9)
    yaw = random.uniform(0, 360)
    spawn_static(
        '/Engine/BasicShapes/Sphere.Sphere',
        unreal.Vector(x, y, z),
        unreal.Rotator(0, 0, yaw),
        unreal.Vector(sx, sy, sz),
        f'Desert_Dune_{i:02d}',
        material=mi_sand,
    )

# ---------------------------------------------------------------- 6. Distant mountains
# Push much further (X=15000+) so they read as silhouettes not foreground geometry.
# Use scaled cubes rotated for craggy look + mi_rock dark tint.

mountain_specs = [
    # (x, y, z, sx, sy, sz, yaw)
    (15000, -4000, -200, 30, 80, 25, 45),
    (16000,  -800, -200, 25, 100, 30, 10),
    (17000,  2500, -200, 40, 90, 35, -25),
    (15500,  4500, -200, 30, 70, 22, 20),
    (16500,  -2500, -200, 28, 75, 18, -10),
    (-14000,  3500, -200, 25, 80, 20, 0),
    (-15000, -3000, -200, 30, 90, 25, 30),
    (-16000,  500, -200, 28, 100, 28, -15),
]
for i, (x, y, z, sx, sy, sz, yaw) in enumerate(mountain_specs):
    spawn_static(
        '/Engine/BasicShapes/Cube.Cube',
        unreal.Vector(x, y, z),
        unreal.Rotator(0, 0, yaw),
        unreal.Vector(sx, sy, sz),
        f'Desert_Mountain_{i:02d}',
        material=mi_rock,
    )

# ---------------------------------------------------------------- 7. Stepped pyramid base

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

# ---------------------------------------------------------------- 8. Central lattice tower

tower_x = 0
tower_y = 0
tower_base_z = base_top_z
tower_height = 1500
leg_offsets = [(+100, +100), (+100, -100), (-100, +100), (-100, -100)]

# 4 vertical legs (cylinders)
for li, (dx, dy) in enumerate(leg_offsets):
    spawn_static(
        '/Engine/BasicShapes/Cylinder.Cylinder',
        unreal.Vector(tower_x + dx, tower_y + dy, tower_base_z + tower_height / 2),
        unreal.Rotator(0, 0, 0),
        unreal.Vector(0.24, 0.24, tower_height / 100.0),
        f'Desert_TowerLeg_{li}',
        material=mi_metal,
    )

# Horizontal cross-braces every 150z (rectangular ring connecting adjacent legs)
brace_levels = list(range(150, tower_height, 150))
brace_pairs = [
    ((+100, +100), (+100, -100)),  # +x face
    ((+100, -100), (-100, -100)),  # -y face
    ((-100, -100), (-100, +100)),  # -x face
    ((-100, +100), (+100, +100)),  # +y face
]
for lvl_idx, dz in enumerate(brace_levels):
    z = tower_base_z + dz
    for pi, (a, b) in enumerate(brace_pairs):
        cx = (a[0] + b[0]) / 2
        cy = (a[1] + b[1]) / 2
        if a[0] == b[0]:  # along Y
            length = abs(a[1] - b[1])
            scale = unreal.Vector(0.06, length / 100.0, 0.06)
        else:  # along X
            length = abs(a[0] - b[0])
            scale = unreal.Vector(length / 100.0, 0.06, 0.06)
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(tower_x + cx, tower_y + cy, z),
            unreal.Rotator(0, 0, 0),
            scale,
            f'Desert_TowerBrace_{lvl_idx:02d}_{pi}',
            material=mi_metal,
        )

# Tower cap (cone)
spawn_static(
    '/Engine/BasicShapes/Cone.Cone',
    unreal.Vector(tower_x, tower_y, tower_base_z + tower_height + 50),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(2.4, 2.4, 1.0),
    'Desert_TowerCap',
    material=mi_metal,
)
tower_top_z = tower_base_z + tower_height + 100

# ---------------------------------------------------------------- 9. Vertical cable

# Thicker cable (visible against bright sky)
spawn_static(
    '/Engine/BasicShapes/Cylinder.Cylinder',
    unreal.Vector(tower_x, tower_y, tower_top_z + 1500),
    unreal.Rotator(0, 0, 0),
    unreal.Vector(0.16, 0.16, 30.0),
    'Desert_Cable',
    material=mi_dark,
)

# ---------------------------------------------------------------- 10. Foreground gantries

gantry_specs = [
    ( 600,  650, 30),
    ( 600, -650, -30),
    (-200,  900, 50),
    (-200, -900, -50),
]
for gi, (gx, gy, gyaw) in enumerate(gantry_specs):
    for sign in (+1, -1):
        offset_x = math.cos(math.radians(gyaw + 90)) * 80 * sign
        offset_y = math.sin(math.radians(gyaw + 90)) * 80 * sign
        spawn_static(
            '/Engine/BasicShapes/Cylinder.Cylinder',
            unreal.Vector(gx + offset_x, gy + offset_y, 200),
            unreal.Rotator(0, 0, 0),
            unreal.Vector(0.16, 0.16, 4.0),
            f'Desert_GantryLeg_{gi}_{1 if sign > 0 else 0}',
            material=mi_metal,
        )
    for bz in (100, 250, 380):
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(gx, gy, bz),
            unreal.Rotator(0, 0, gyaw + 90),
            unreal.Vector(1.6, 0.06, 0.06),
            f'Desert_GantryBrace_{gi}_{bz}',
            material=mi_metal,
        )

# ---------------------------------------------------------------- 11. Crates
# Skew clusters toward the camera-facing side of the tower (camera at -X).

crate_clusters = [
    # (cx, cy, count, spread)
    (-900, 350, 5, 250),
    (-900, -350, 5, 250),
    (-1300, 100, 4, 150),
    (-700, 500, 3, 200),
    (-700, -500, 3, 200),
]
ci = 0
for (cx, cy, count, spread) in crate_clusters:
    for _ in range(count):
        x = cx + random.uniform(-spread, spread)
        y = cy + random.uniform(-spread, spread)
        yaw = random.uniform(0, 360)
        sx = random.uniform(2.0, 3.2)
        sy = random.uniform(1.0, 1.6)
        sz = random.uniform(0.9, 1.3)
        spawn_static(
            '/Engine/BasicShapes/Cube.Cube',
            unreal.Vector(x, y, sz * 50 - 50),
            unreal.Rotator(0, 0, yaw),
            unreal.Vector(sx, sy, sz),
            f'Desert_Crate_{ci:02d}',
            material=mi_crate,
        )
        ci += 1

# ---------------------------------------------------------------- 12. Camera framing

# Hero shot: low front, pull back + tilt up to fit tower top in frame
cam_loc = unreal.Vector(-3200, 300, 800)
cam_rot = unreal.Rotator(0, 5, -5)  # roll, pitch, yaw

try:
    LES = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    LES.editor_set_game_view(True)
except Exception as e:
    log(f'LES.editor_set_game_view skip: {e}')

try:
    UES = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    UES.set_level_viewport_camera_info(cam_loc, cam_rot)
except Exception as e:
    log(f'set_level_viewport_camera_info failed: {e}')

# Force redraw
try:
    LES = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    LES.editor_invalidate_viewports()
except Exception as e:
    log(f'invalidate viewports failed: {e}')

log('SCENE_BUILD_COMPLETE')
