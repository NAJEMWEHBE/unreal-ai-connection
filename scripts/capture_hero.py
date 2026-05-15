"""Deterministic offscreen capture.

The editor viewport screenshot path returns a frozen backbuffer when the
editor window isn't actively redrawing (headless / background). A
SceneCapture2D rendering into a TextureRenderTarget2D is independent of
viewport state and includes post-process (tonemap, exposure, grade), so
the captured PNG matches the final look. Reusable for any hero framing.
"""
import json

import unreal

SHOTS = [
    ("hero", (-2600, 250, 1700), (0, -3, 0), 70.0),
    ("vista", (-4200, 1400, 2400), (-30, 4, 0), 62.0),
    ("spire", (1200, -1400, 2600), (-150, 6, 0), 55.0),
]
OUTDIR = "F:/UnrealClaudeMCP/docs/validation"
RES = (1600, 900)
out = {"shots": []}

eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
world = None
try:
    ues = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = ues.get_editor_world()
except Exception:
    try:
        world = unreal.EditorLevelLibrary.get_editor_world()
    except Exception:
        pass

if world is None:
    world = eas.get_all_level_actors()[0]  # any actor -> valid world ctx
rt = unreal.RenderingLibrary.create_render_target2d(
    world, RES[0], RES[1], unreal.TextureRenderTargetFormat.RTF_RGBA8)
try:
    rt.set_editor_property("target_gamma", 2.2)
except Exception:
    pass

cap = eas.spawn_actor_from_class(
    unreal.SceneCapture2D, unreal.Vector(0, 0, 0))
cap.set_actor_label("HFX_Capture")
cc = cap.get_component_by_class(unreal.SceneCaptureComponent2D)
cc.set_editor_property("texture_target", rt)
try:
    cc.set_editor_property(
        "capture_source",
        unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
except Exception as e:
    out["src_err"] = str(e)[:80]
for k, v in (("capture_every_frame", False),
             ("capture_on_movement", False)):
    try:
        cc.set_editor_property(k, v)
    except Exception:
        pass

for name, loc, rot, fov in SHOTS:
    cap.set_actor_location_and_rotation(
        unreal.Vector(*loc), unreal.Rotator(rot[1], rot[0], rot[2]),
        False, False)
    try:
        cc.set_editor_property("fov_angle", fov)
    except Exception:
        pass
    try:
        cc.capture_scene()
    except Exception as e:
        out["shots"].append({name: "capture_err:" + str(e)[:80]})
        continue
    ok = False
    try:
        ok = unreal.RenderingLibrary.export_render_target(
            world, rt, OUTDIR, "elven-hifi-%s-2026-05-16.png" % name)
    except Exception as e:
        out["shots"].append({name: "export_err:" + str(e)[:90]})
        continue
    out["shots"].append({name: bool(ok)})

try:
    eas.destroy_actor(cap)
except Exception:
    pass
unreal.log("CAPTURE_RESULT " + json.dumps(out, default=str)[:1500])
