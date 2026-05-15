"""Exposure sweep v2 — deterministic.

Single-shot SceneCapture renders before Lumen/TSR converge -> blank/white.
Fix: bAlwaysPersistRenderingState=True + a warm-up loop of capture_scene()
calls so GI/temporal history converges before export. Wide EV range so
"capture broken" (white even at EV -16) is distinguishable from
"scene over-lit" (gets darker as EV drops) in ONE pass.
"""
import json

import unreal

LOC = (-2600, 250, 1700)
ROT = (-3, 0, 0)
FOV = 70.0
BIASES = [-16.0, -12.0, -8.0, -4.0, 0.0]
WARMUP = 16
OUTDIR = "F:/UnrealClaudeMCP/docs/validation"
out = {"shots": []}

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
world = eas.get_all_level_actors()[0]
rt = unreal.RenderingLibrary.create_render_target2d(
    world, 1280, 720, unreal.TextureRenderTargetFormat.RTF_RGBA8)

cap = eas.spawn_actor_from_class(
    unreal.SceneCapture2D, unreal.Vector(*LOC),
    unreal.Rotator(ROT[0], ROT[1], ROT[2]))
cap.set_actor_label("HFX_ExpSweep")
cc = cap.get_component_by_class(unreal.SceneCaptureComponent2D)
cc.set_editor_property("texture_target", rt)
cc.set_editor_property("fov_angle", FOV)
cc.set_editor_property(
    "capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
cc.set_editor_property("capture_every_frame", False)
cc.set_editor_property("capture_on_movement", False)
for k in ("always_persist_rendering_state",):
    try:
        cc.set_editor_property(k, True)
    except Exception as e:
        out[k + "_err"] = str(e)[:80]

for b in BIASES:
    ps = cc.get_editor_property("post_process_settings")
    try:
        ps.set_editor_property(
            "auto_exposure_method",
            unreal.AutoExposureMethod.AEM_MANUAL)
        ps.set_editor_property("override_auto_exposure_method", True)
        ps.set_editor_property("auto_exposure_bias", b)
        ps.set_editor_property("override_auto_exposure_bias", True)
        ps.set_editor_property(
            "auto_exposure_apply_physical_camera_exposure", False)
        ps.set_editor_property(
            "override_auto_exposure_apply_physical_camera_exposure", True)
        ps.set_editor_property("auto_exposure_min_brightness", 1.0)
        ps.set_editor_property("override_auto_exposure_min_brightness", True)
        ps.set_editor_property("auto_exposure_max_brightness", 1.0)
        ps.set_editor_property("override_auto_exposure_max_brightness", True)
    except Exception as e:
        out["shots"].append({str(b): "ppset_err:" + str(e)[:70]})
        continue
    cc.set_editor_property("post_process_settings", ps)
    cc.set_editor_property("post_process_blend_weight", 1.0)
    try:
        for _ in range(WARMUP):
            cc.capture_scene()
        nm = "expsweep_%s.png" % str(int(b)).replace("-", "m")
        unreal.RenderingLibrary.export_render_target(world, rt, OUTDIR, nm)
        out["shots"].append({str(b): nm})
    except Exception as e:
        out["shots"].append({str(b): "cap_err:" + str(e)[:70]})

try:
    eas.destroy_actor(cap)
except Exception:
    pass
unreal.log("EXPSWEEP2 " + json.dumps(out, default=str)[:1200])
