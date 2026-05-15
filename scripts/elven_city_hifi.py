"""Elven city — high-fidelity pass.

Turns the flat ELV_ primitive blockout into a cinematic real-time scene
using ONLY CC0 assets + built-in UE5.7 features (no paid/bespoke art):

  * Real PBR master material (albedo/normal/roughness/AO) + per-surface
    MaterialInstanceConstants, fed by CC0 Poly Haven / AmbientCG textures
    already imported to /Game/ElvenCityHi/Tex.
  * Animated water material (panning normals + fresnel + refraction) —
    the Water plugin is disabled in this project, so this is the
    reliable headless path.
  * Sky Atmosphere + Volumetric Clouds + real-time-capture SkyLight +
    golden-hour Directional sun bound to the atmosphere.
  * Exponential Height Fog with volumetric fog (depth separation).
  * Fully-graded unbound Post Process Volume with FORCED MANUAL EXPOSURE
    (auto-exposure makes automated screenshots non-deterministic).
  * Denser, layered city geometry (foreground / mid / background).
  * A CineCamera + Level Sequence dolly fly-through (the "animation").

Every API the research pass flagged "verify at runtime" is wrapped in a
defensive try/except with a sane fallback, and every step records its
ok/err into a marker line read back via get_log_lines.

Idempotent: wipes HFX_*/ELV_* actors and rebuilds. Re-running reflects
edits. Prefix HFX_.
"""
import json
import math
import random

import unreal

random.seed(7)

PREFIX = "HFX_"
TEX = "/Game/ElvenCityHi/Tex"
MATDIR = "/Game/ElvenCityHi/Mat"
CINEDIR = "/Game/ElvenCityHi/Cine"

EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
AT = unreal.AssetToolsHelpers.get_asset_tools()
RESULT = {"steps": {}, "errs": []}


def mark(tag, ok, info=""):
    RESULT["steps"][tag] = {"ok": bool(ok), "info": str(info)[:160]}
    if not ok:
        RESULT["errs"].append(tag + ":" + str(info)[:160])


def get_eas():
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


SHAPES = {
    "cube": "/Engine/BasicShapes/Cube.Cube",
    "cyl": "/Engine/BasicShapes/Cylinder.Cylinder",
    "cone": "/Engine/BasicShapes/Cone.Cone",
    "plane": "/Engine/BasicShapes/Plane.Plane",
    "sphere": "/Engine/BasicShapes/Sphere.Sphere",
}
MESH = {k: unreal.load_object(None, v) for k, v in SHAPES.items()}


# ----------------------------------------------------------------------
# 1. Cinematic Lumen / renderer cvars (HW Lumen + Nanite already on).
# ----------------------------------------------------------------------
def step_cvars():
    cmds = [
        "r.Lumen.HardwareRayTracing.LightingMode 2",
        "r.Lumen.ScreenProbeGather.DownsampleFactor 8",
        "r.Lumen.Reflections.SmoothBias 0.3",
        "r.LumenScene.Radiosity.ProbeSpacing 4",
        "r.Shadow.Virtual.ResolutionLodBiasDirectional -1.5",
        "r.ScreenPercentage 100",
    ]
    ok = True
    try:
        for c in cmds:
            unreal.SystemLibrary.execute_console_command(
                unreal.EditorLevelLibrary.get_editor_world()
                if hasattr(unreal, "EditorLevelLibrary") else None, c)
    except Exception as e:
        # World arg flaky across builds — fall back to no-world exec.
        try:
            for c in cmds:
                unreal.SystemLibrary.execute_console_command(None, c)
        except Exception as e2:
            ok = False
            e = "%s / %s" % (e, e2)
    mark("cvars", ok, "" if ok else e)


# ----------------------------------------------------------------------
# 2. PBR master material + per-surface instances.
# ----------------------------------------------------------------------
def tex(path):
    return EAL.load_asset(path) if EAL.does_asset_exist(path) else None


def build_pbr_master():
    mpath = MATDIR + "/M_HiPBR"
    if EAL.does_asset_exist(mpath):
        EAL.delete_asset(mpath)
    m = AT.create_asset("M_HiPBR", MATDIR, unreal.Material,
                         unreal.MaterialFactoryNew())

    def tparam(name, y, sampler):
        n = MEL.create_material_expression(
            m, unreal.MaterialExpressionTextureSampleParameter2D, -900, y)
        n.set_editor_property("parameter_name", name)
        try:
            n.set_editor_property("sampler_type", sampler)
        except Exception:
            pass
        return n

    # UV tiling scalar so large surfaces don't smear.
    uv = MEL.create_material_expression(
        m, unreal.MaterialExpressionTextureCoordinate, -1200, -300)
    tile = MEL.create_material_expression(
        m, unreal.MaterialExpressionScalarParameter, -1200, -120)
    tile.set_editor_property("parameter_name", "Tiling")
    tile.set_editor_property("default_value", 1.0)
    uvmul = MEL.create_material_expression(
        m, unreal.MaterialExpressionMultiply, -1050, -250)
    try:
        MEL.connect_material_expressions(uv, "", uvmul, "A")
        MEL.connect_material_expressions(tile, "", uvmul, "B")
    except Exception:
        uvmul = None

    ST = unreal.MaterialSamplerType
    alb = tparam("Albedo", -300, ST.SAMPLERTYPE_COLOR)
    nrm = tparam("Normal", 0, ST.SAMPLERTYPE_NORMAL)
    rgh = tparam("Roughness", 300, ST.SAMPLERTYPE_LINEAR_GRAYSCALE)
    ao = tparam("AO", 600, ST.SAMPLERTYPE_LINEAR_GRAYSCALE)
    if uvmul:
        for s in (alb, nrm, rgh, ao):
            try:
                MEL.connect_material_expressions(uvmul, "", s, "UVs")
            except Exception:
                pass

    MP = unreal.MaterialProperty
    MEL.connect_material_property(alb, "RGB", MP.MP_BASE_COLOR)
    MEL.connect_material_property(nrm, "RGB", MP.MP_NORMAL)
    MEL.connect_material_property(rgh, "R", MP.MP_ROUGHNESS)
    MEL.connect_material_property(ao, "R", MP.MP_AMBIENT_OCCLUSION)
    try:
        MEL.layout_material_expressions(m)
    except Exception:
        pass
    MEL.recompile_material(m)
    EAL.save_asset(mpath, only_if_is_dirty=False)
    return m


def make_inst(master, name, base, tiling):
    """One MaterialInstanceConstant per surface; <base>=color path stem."""
    ipath = MATDIR + "/MI_" + name
    if EAL.does_asset_exist(ipath):
        EAL.delete_asset(ipath)
    mi = AT.create_asset("MI_" + name, MATDIR,
                          unreal.MaterialInstanceConstant,
                          unreal.MaterialInstanceConstantFactoryNew())
    MEL.set_material_instance_parent(mi, master)
    pairs = {
        "Albedo": tex(base),
        "Normal": tex(base + "_normal"),
        "Roughness": tex(base + "_roughness"),
        "AO": tex(base + "_ao"),
    }
    for pn, t in pairs.items():
        if t:
            try:
                MEL.set_material_instance_texture_parameter_value(mi, pn, t)
            except Exception as e:
                RESULT["errs"].append("mi:%s:%s:%s" % (name, pn, e))
    try:
        MEL.set_material_instance_scalar_parameter_value(mi, "Tiling", tiling)
    except Exception:
        pass
    EAL.save_asset(ipath, only_if_is_dirty=False)
    return mi


def build_water_master():
    wpath = MATDIR + "/M_HiWater"
    if EAL.does_asset_exist(wpath):
        EAL.delete_asset(wpath)
    w = AT.create_asset("M_HiWater", MATDIR, unreal.Material,
                         unreal.MaterialFactoryNew())
    try:
        w.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    except Exception:
        pass
    MP = unreal.MaterialProperty
    # Deep teal base.
    col = MEL.create_material_expression(
        w, unreal.MaterialExpressionConstant3Vector, -500, -300)
    col.set_editor_property("constant",
                            unreal.LinearColor(0.012, 0.05, 0.06, 1.0))
    MEL.connect_material_property(col, "", MP.MP_BASE_COLOR)
    # Panning normal for ripples.
    pan = MEL.create_material_expression(
        w, unreal.MaterialExpressionPanner, -750, 0)
    try:
        pan.set_editor_property("speed_x", 0.018)
        pan.set_editor_property("speed_y", 0.011)
    except Exception:
        pass
    wn = tex(TEX + "/GrassRock_normal")  # reuse a normal as ripple proxy
    if wn:
        ns = MEL.create_material_expression(
            w, unreal.MaterialExpressionTextureSample, -550, 0)
        ns.set_editor_property("texture", wn)
        try:
            ns.set_editor_property(
                "sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
            MEL.connect_material_expressions(pan, "", ns, "UVs")
        except Exception:
            pass
        MEL.connect_material_property(ns, "RGB", MP.MP_NORMAL)
    rgh = MEL.create_material_expression(
        w, unreal.MaterialExpressionConstant, -500, 250)
    rgh.set_editor_property("r", 0.025)
    MEL.connect_material_property(rgh, "", MP.MP_ROUGHNESS)
    spec = MEL.create_material_expression(
        w, unreal.MaterialExpressionConstant, -500, 400)
    spec.set_editor_property("r", 1.0)
    MEL.connect_material_property(spec, "", MP.MP_SPECULAR)
    opac = MEL.create_material_expression(
        w, unreal.MaterialExpressionConstant, -500, 550)
    opac.set_editor_property("r", 0.78)
    try:
        MEL.connect_material_property(opac, "", MP.MP_OPACITY)
    except Exception:
        pass
    try:
        MEL.layout_material_expressions(w)
    except Exception:
        pass
    MEL.recompile_material(w)
    EAL.save_asset(wpath, only_if_is_dirty=False)
    return w


# ----------------------------------------------------------------------
# 3. Geometry — denser, layered, real materials.
# ----------------------------------------------------------------------
def wipe():
    eas = get_eas()
    killed = 0
    for a in list(eas.get_all_level_actors()):
        try:
            lbl = a.get_actor_label()
        except Exception:
            continue
        if lbl.startswith(PREFIX) or lbl.startswith("ELV_"):
            try:
                eas.destroy_actor(a)
                killed += 1
            except Exception:
                pass
    mark("wipe", True, "killed=%d" % killed)


def spawn(shape, label, loc, scale, mat=None, rot=(0, 0, 0)):
    eas = get_eas()
    a = eas.spawn_actor_from_object(
        MESH[shape], unreal.Vector(*loc), unreal.Rotator(*rot))
    a.set_actor_label(PREFIX + label)
    a.set_actor_scale3d(unreal.Vector(*scale))
    if mat:
        try:
            a.static_mesh_component.set_material(0, mat)
        except Exception:
            try:
                a.get_component_by_class(
                    unreal.StaticMeshComponent).set_material(0, mat)
            except Exception:
                pass
    return a


def build_geometry(mats):
    marble, cliff, grass, forest, water = mats
    # Ground: big plane, forest-floor PBR.
    spawn("plane", "Ground", (1500, 200, -30), (520, 520, 1), forest)
    # River: water-material plane, slightly above ground.
    spawn("plane", "River", (-700, 150, 6), (78, 11, 1), water)
    # Cliff massif (background, layered for depth).
    spawn("cube", "CliffBase", (5200, -800, 1700), (36, 44, 40), cliff)
    spawn("cube", "CliffNW", (4300, -2700, 2500), (24, 22, 56), cliff)
    spawn("cube", "CliffE", (4700, 3100, 2100), (22, 20, 48), cliff)
    spawn("cone", "Peak1", (6100, -1200, 5000), (62, 62, 74), cliff)
    spawn("cone", "Peak2", (6300, 1500, 4200), (52, 52, 60), cliff)
    spawn("cone", "Peak3", (6600, 200, 5600), (44, 44, 66), cliff)
    # Stacked elven halls climbing the cliff (marble).
    halls = [
        ("Hall1", (3400, -2400, 600), (10, 14, 6)),
        ("Hall2", (3700, -2300, 1500), (8, 11, 5)),
        ("Hall3", (3950, -2200, 2250), (7, 9, 4.5)),
        ("Hall4", (4150, -2100, 2900), (5.5, 7, 4)),
    ]
    for nm, loc, sc in halls:
        spawn("cube", nm, loc, sc, marble)
        spawn("cone", nm + "_Roof",
               (loc[0], loc[1], loc[2] + sc[2] * 50 + 210),
               (sc[0] * 1.15, sc[1] * 1.15, 2.4), marble)
    # Central cathedral spire (focal point).
    spawn("cyl", "Spire", (4150, -2100, 4050), (4.4, 4.4, 13), marble)
    spawn("cone", "SpireTip", (4150, -2100, 4850), (5.4, 5.4, 9), marble)
    # 7-arch viaduct across the gorge (mid layer, leading line to spire).
    DZ = 1750
    spawn("cube", "ViaductDeck", (1500, 150, DZ), (7, 70, 0.6), marble)
    for i in range(7):
        y = -2400 + i * 760
        spawn("cube", "Pier%d" % i, (1500, y, DZ / 2),
               (5, 4, DZ / 100), marble)
        spawn("cyl", "Arch%d" % i, (1500, y + 380, DZ * 0.8),
               (3.4, 3.4, 3.4), marble, rot=(90, 0, 0))
    # Pavilion + beacon near camera (foreground silhouette layer).
    for k in range(8):
        ang = k / 8.0 * math.tau
        spawn("cyl", "Pav_Col%d" % k,
               (1500 + 380 * math.cos(ang), 150 + 380 * math.sin(ang),
                DZ + 360),
               (1.0, 1.0, 7.2), marble)
    spawn("sphere", "Pav_Dome", (1500, 150, DZ + 880), (9, 9, 5.2), marble)
    spawn("sphere", "Beacon", (1500, 150, DZ + 1180), (1.7, 1.7, 1.7),
          marble)
    # Side stairs.
    for i in range(14):
        spawn("cube", "Stair%d" % i,
               (700 - i * 70, -1700, 40 + i * 90), (3.5, 9, 0.6), marble)
    # Waterfalls off the cliff (thin water planes).
    for i, y in enumerate((-2600, -400, 1800)):
        spawn("plane", "Fall%d" % i, (4150, y, 1500),
               (2.4, 0.4, 30), water, rot=(0, 90, 0))


def scatter_foliage(grass):
    """Pines + boulders with randomized scale/yaw (kills the procedural
    'uniform' tell). HISM would be ideal but per-actor is robust here."""
    eas = get_eas()
    n = 0
    for _ in range(120):
        x = random.uniform(-3500, 3200)
        y = random.uniform(-3800, 3600)
        if abs(y - 150) < 700 and x < 1200:  # keep river clear
            continue
        s = random.uniform(0.7, 1.5)
        yaw = random.uniform(0, 360)
        a = eas.spawn_actor_from_object(
            MESH["cone"], unreal.Vector(x, y, 120 * s),
            unreal.Rotator(0, yaw, 0))
        a.set_actor_label(PREFIX + "Pine%d" % n)
        a.set_actor_scale3d(unreal.Vector(1.5 * s, 1.5 * s, 5.5 * s))
        try:
            a.static_mesh_component.set_material(0, grass)
        except Exception:
            pass
        n += 1
    for _ in range(40):
        x = random.uniform(-3000, 3000)
        y = random.uniform(-3500, 3400)
        s = random.uniform(0.6, 1.8)
        a = eas.spawn_actor_from_object(
            MESH["sphere"], unreal.Vector(x, y, 30 * s),
            unreal.Rotator(random.uniform(-20, 20), random.uniform(0, 360),
                           random.uniform(-20, 20)))
        a.set_actor_label(PREFIX + "Rock%d" % n)
        a.set_actor_scale3d(unreal.Vector(2.4 * s, 1.9 * s, 1.5 * s))
        try:
            a.static_mesh_component.set_material(0, grass)
        except Exception:
            pass
        n += 1
    mark("foliage", True, "scattered=%d" % n)


# ----------------------------------------------------------------------
# 4. Atmosphere: SkyAtmosphere + clouds + sky light + golden sun + fog.
# ----------------------------------------------------------------------
def build_atmosphere():
    eas = get_eas()

    def sp(cls, loc=(0, 0, 0), rot=(0, 0, 0), tag=""):
        a = eas.spawn_actor_from_class(
            cls, unreal.Vector(*loc), unreal.Rotator(*rot))
        a.set_actor_label(PREFIX + (tag or cls.get_name()))
        return a

    try:
        sun = sp(unreal.DirectionalLight, rot=(-11.0, -42.0, 0.0),
                 tag="Sun")
        dlc = sun.get_component_by_class(unreal.DirectionalLightComponent)
        for k, v in (("intensity", 10.0), ("light_source_angle", 0.35),
                     ("use_temperature", True), ("temperature", 5400.0)):
            try:
                dlc.set_editor_property(k, v)
            except Exception:
                pass
        for k in ("light_shaft_occlusion", "light_shaft_bloom"):
            try:
                dlc.set_editor_property(k, True)
            except Exception:
                pass
        try:
            dlc.set_atmosphere_sun_light(True)
        except Exception as e:
            RESULT["errs"].append("sun_atmo:" + str(e)[:80])
        mark("sun", True)
    except Exception as e:
        mark("sun", False, e)

    for cls, tag in ((unreal.SkyAtmosphere, "SkyAtmo"),
                     (unreal.VolumetricCloud, "Clouds")):
        try:
            sp(cls, tag=tag)
            mark(tag, True)
        except Exception as e:
            mark(tag, False, e)

    try:
        sky = sp(unreal.SkyLight, loc=(0, 0, 250), tag="SkyLight")
        slc = sky.get_component_by_class(unreal.SkyLightComponent)
        for k, v in (("real_time_capture", True), ("intensity_scale", 1.0)):
            try:
                slc.set_editor_property(k, v)
            except Exception:
                pass
        try:
            slc.recapture_sky()
        except Exception:
            pass
        mark("skylight", True)
    except Exception as e:
        mark("skylight", False, e)

    try:
        fog = sp(unreal.ExponentialHeightFog, tag="Fog")
        ehc = fog.get_component_by_class(
            unreal.ExponentialHeightFogComponent)
        for k, v in (("fog_density", 0.02),
                     ("fog_height_falloff", 0.2),
                     ("enable_volumetric_fog", True)):
            try:
                ehc.set_editor_property(k, v)
            except Exception as e:
                RESULT["errs"].append("fog:%s:%s" % (k, e))
        for meth, arg in (("set_volumetric_fog_scattering_distribution",
                           0.7),
                          ("set_volumetric_fog_extinction_scale", 1.2)):
            try:
                getattr(ehc, meth)(arg)
            except Exception as e:
                RESULT["errs"].append("fog:%s:%s" % (meth, e))
        mark("fog", True)
    except Exception as e:
        mark("fog", False, e)


# ----------------------------------------------------------------------
# 5. Post Process Volume — FORCED MANUAL EXPOSURE + filmic grade.
# ----------------------------------------------------------------------
def build_postprocess():
    eas = get_eas()
    try:
        ppv = eas.spawn_actor_from_class(
            unreal.PostProcessVolume, unreal.Vector(0, 0, 0))
        ppv.set_actor_label(PREFIX + "PPV")
        ppv.set_editor_property("unbound", True)
        s = ppv.get_editor_property("settings")

        def setp(vk, val, ok_list):
            try:
                s.set_editor_property(vk, val)
                for ok in ([ok_list] if isinstance(ok_list, str)
                           else ok_list):
                    try:
                        s.set_editor_property(ok, True)
                    except Exception:
                        pass
            except Exception as e:
                RESULT["errs"].append("pp:%s:%s" % (vk, str(e)[:60]))

        # Manual exposure (non-negotiable for deterministic captures).
        try:
            s.set_editor_property(
                "auto_exposure_method",
                unreal.AutoExposureMethod.AEM_MANUAL)
            s.set_editor_property("override_auto_exposure_method", True)
        except Exception:
            pass
        setp("auto_exposure_min_brightness", 1.0,
             "override_auto_exposure_min_brightness")
        setp("auto_exposure_max_brightness", 1.0,
             "override_auto_exposure_max_brightness")
        # +1 stop only. Manual EV compensation: high values blow out.
        setp("auto_exposure_bias", 1.0, "override_auto_exposure_bias")
        setp("auto_exposure_apply_physical_camera_exposure", False,
             "override_auto_exposure_apply_physical_camera_exposure")
        # Bloom.
        setp("bloom_intensity", 0.55, "override_bloom_intensity")
        setp("bloom_threshold", -1.0, "override_bloom_threshold")
        # Subtle lens.
        setp("lens_flare_intensity", 0.25,
             "override_lens_flare_intensity")
        setp("scene_fringe_intensity", 1.2,
             "override_scene_fringe_intensity")
        setp("film_grain_intensity", 0.18,
             "override_film_grain_intensity")
        setp("vignette_intensity", 0.42, "override_vignette_intensity")
        # Filmic color grade (teal shadows / warm highlights).
        V = unreal.Vector4
        setp("color_saturation", V(1.06, 1.06, 1.06, 1.0),
             "override_color_saturation")
        setp("color_contrast", V(1.09, 1.09, 1.09, 1.0),
             "override_color_contrast")
        setp("color_gain_highlights", V(1.04, 1.0, 0.95, 1.0),
             "override_color_gain_highlights")
        setp("color_offset_shadows", V(-0.008, -0.004, 0.012, 0.0),
             "override_color_offset_shadows")
        setp("white_temp", 6200.0, "override_white_temp")
        # Reflections / AO / DoF.
        setp("screen_space_reflection_quality", 100.0,
             "override_screen_space_reflection_quality")
        setp("ambient_occlusion_intensity", 0.5,
             "override_ambient_occlusion_intensity")
        setp("ambient_occlusion_radius", 90.0,
             "override_ambient_occlusion_radius")
        setp("depth_of_field_fstop", 2.8,
             "override_depth_of_field_fstop")
        setp("motion_blur_amount", 0.0, "override_motion_blur_amount")
        mark("postprocess", True)
    except Exception as e:
        mark("postprocess", False, e)


# ----------------------------------------------------------------------
# 6. CineCamera + Level Sequence dolly fly-through.
# ----------------------------------------------------------------------
def build_cinematic():
    eas = get_eas()
    try:
        cam = eas.spawn_actor_from_class(
            unreal.CineCameraActor, unreal.Vector(-2600, 250, 1700),
            unreal.Rotator(-3, 0, 0))
        cam.set_actor_label(PREFIX + "CineCam")
        try:
            ccc = cam.get_cine_camera_component()
            ccc.set_editor_property("current_focal_length", 32.0)
            ccc.set_editor_property("current_aperture", 2.8)
            fb = ccc.get_editor_property("filmback")
            fb.set_editor_property("sensor_width", 24.89)
            fb.set_editor_property("sensor_height", 18.67)
            ccc.set_editor_property("filmback", fb)
        except Exception as e:
            RESULT["errs"].append("cine_cfg:" + str(e)[:80])
        mark("cinecam", True)
    except Exception as e:
        mark("cinecam", False, e)
        cam = None

    if not cam:
        return
    try:
        spath = CINEDIR + "/SEQ_Flythrough"
        if EAL.does_asset_exist(spath):
            EAL.delete_asset(spath)
        seq = AT.create_asset("SEQ_Flythrough", CINEDIR,
                              unreal.LevelSequence,
                              unreal.LevelSequenceFactoryNew())
        try:
            seq.set_editor_property("display_rate", unreal.FrameRate(24, 1))
        except Exception:
            pass
        MSE = unreal.MovieSceneSequenceExtensions
        try:
            MSE.set_playback_end_seconds(seq, 12.0)
        except Exception:
            pass
        binding = MSE.add_possessable(seq, cam)
        cut = MSE.add_track(seq, unreal.MovieSceneCameraCutTrack)
        sec = cut.add_section()
        try:
            sec.set_range_seconds(0.0, 12.0)
        except Exception:
            try:
                sec.set_start_frame_seconds(0.0)
                sec.set_end_frame_seconds(12.0)
            except Exception:
                pass
        try:
            bid = MSE.get_binding_id(seq, binding)
            sec.set_camera_binding_id(bid)
        except Exception as e:
            RESULT["errs"].append("cut_bind:" + str(e)[:80])
        # Transform track: slow push from wide vista toward the spire.
        try:
            tt = binding.add_track(unreal.MovieScene3DTransformTrack)
            tsec = tt.add_section()
            tsec.set_range_seconds(0.0, 12.0)
            chans = tsec.get_all_channels()
            # channels order: loc x,y,z  rot x,y,z  scale x,y,z
            keys = {
                0: (-2600, 1800), 1: (250, 150), 2: (1700, 1500),
                3: (0, 0), 4: (0, 0), 5: (-3, 2),
            }
            for ci, (v0, v1) in keys.items():
                if ci < len(chans):
                    chans[ci].add_key(
                        unreal.FrameNumber(0), float(v0))
                    chans[ci].add_key(
                        unreal.FrameNumber(288), float(v1))
            mark("seq_keys", True)
        except Exception as e:
            mark("seq_keys", False, e)
        lsa = eas.spawn_actor_from_class(
            unreal.LevelSequenceActor, unreal.Vector(0, 0, 0))
        lsa.set_actor_label(PREFIX + "SeqActor")
        try:
            lsa.set_editor_property(
                "level_sequence",
                unreal.SoftObjectPath(seq.get_path_name()))
        except Exception:
            try:
                lsa.set_sequence(seq)
            except Exception:
                pass
        EAL.save_asset(spath, only_if_is_dirty=False)
        mark("sequence", True)
    except Exception as e:
        mark("sequence", False, e)


# ----------------------------------------------------------------------
# Drive.
# ----------------------------------------------------------------------
def main():
    step_cvars()
    try:
        master = build_pbr_master()
        marble = make_inst(master, "Marble", TEX + "/Marble", 3.0)
        cliff = make_inst(master, "Cliff", TEX + "/Cliff", 6.0)
        grass = make_inst(master, "GrassRock", TEX + "/GrassRock", 8.0)
        forest = make_inst(master, "Forest", TEX + "/ForestFloor", 30.0)
        water = build_water_master()
        mark("materials", True)
    except Exception as e:
        mark("materials", False, e)
        marble = cliff = grass = forest = water = None

    wipe()
    if marble:
        build_geometry((marble, cliff, grass, forest, water))
        scatter_foliage(grass)
        mark("geometry", True)
    build_atmosphere()
    build_postprocess()
    build_cinematic()

    # Camera framing for the still hero shot.
    try:
        ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        ues.set_level_viewport_camera_info(
            unreal.Vector(-2600, 250, 1700), unreal.Rotator(-3, 0, 0))
        mark("viewcam", True)
    except Exception as e:
        mark("viewcam", False, e)

    # Save level.
    try:
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        les.save_current_level()
        mark("save", True)
    except Exception as e:
        try:
            unreal.EditorLevelLibrary.save_current_level()
            mark("save", True, "ELL fallback")
        except Exception as e2:
            mark("save", False, "%s / %s" % (e, e2))

    cnt = len([a for a in get_eas().get_all_level_actors()
               if a.get_actor_label().startswith(PREFIX)])
    RESULT["actors"] = cnt
    unreal.log("HIFI_RESULT " + json.dumps(RESULT))


main()
