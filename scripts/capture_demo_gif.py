#!/usr/bin/env python3
r"""
Demo-GIF capture pipeline for the Unreal AI Connection plugin.

What this is
------------
A standalone, stdlib-only MCP client that drives a LIVE Unreal Engine 5.7
editor over the local TCP socket (default 127.0.0.1:18888) to produce the
animated demo loop embedded in the README:

  1. builds a dependency-free procedural city blockout in the editor
     (the Rivendell-style elven city from scripts/elven_city_scene.py,
     executed remotely via the execute_unreal_python handler),
  2. spawns an orbit camera actor and walks it around the city on a
     circle, calling render_camera_to_png (camera_label off-screen mode)
     for each frame so capture is deterministic and headless-safe,
  3. assembles the captured PNG frames into docs/images/demo.gif with
     ffmpeg using a two-pass palettegen for quality at a small size.

The wire protocol is open MCP / JSON-RPC 2.0 over a length-prefixed TCP
frame — no vendor SDK, no model-specific anything. Any MCP-capable client
could drive the same handlers; this script is just a thin reproducible
driver for the documentation asset.

Requirements
------------
* A LIVE Unreal Editor with the host UnrealClaudeMCP plugin REBUILT
  (so render_camera_to_png and the other handlers are compiled in and
  answer on the socket). A handler that is declared but not linked
  manifests as JSON-RPC error -32601 (method-not-found) — this script
  detects that and exits with a clear "host plugin not rebuilt?" message.
* ffmpeg on PATH (the GIF is assembled with ffmpeg's palettegen; no
  Pillow / imageio / numpy is used or required).
* System Python 3 only — no third-party packages.

Usage
-----
    python scripts/capture_demo_gif.py
    python scripts/capture_demo_gif.py --host 127.0.0.1 --port 18888
    python scripts/capture_demo_gif.py --frames 48 --fps 20 --scale-w 960
    python scripts/capture_demo_gif.py --skip-build      # capture current level as-is
    python scripts/capture_demo_gif.py --keep-frames     # keep the PNG frame dir

Exit code is 0 only if the GIF was produced; any failure prints a clear
FAIL line and exits non-zero.
"""

import argparse
import json
import os
import shutil
import socket
import struct  # noqa: F401 -- kept in the stdlib import set per the wire-framing spec
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------------------
# Console encoding hardening.
#
# This driver prints a few status lines that contain non-ASCII glyphs (e.g.
# the "<-"/"->" arrows used in the progress banners). On Windows the default
# console code page is cp1252, which cannot encode those characters, and
# Python raises UnicodeEncodeError mid-run. Force the script's OWN stdout/
# stderr to UTF-8 (with a safe fallback) so progress output never aborts the
# capture. This does NOT touch the MCP wire framing, which already encodes
# its JSON bodies as explicit UTF-8 bytes independently of this setting.
# ---------------------------------------------------------------------------

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        # Non-reconfigurable stream (e.g. already wrapped / redirected to a
        # pipe that doesn't support reconfigure): best-effort only.
        pass

# ---------------------------------------------------------------------------
# Wire-framing helpers.
#
# SOURCE OF TRUTH: examples/smoke_test.py (its _send_framed / _recv_exact /
# _recv_framed), mirrored verbatim in behaviour by examples/verify_wave_a.py.
# These are replicated here (rather than imported) because cross-importing a
# module out of examples/ into scripts/ is fragile (sys.path / packaging), and
# this driver must stay a single self-contained file. If the framing ever
# changes, change smoke_test.py FIRST, then mirror it here and in
# verify_wave_a.py. Style matched to verify_wave_a.py.
#
# Every TCP message is:
#   <8-byte big-endian uint64 body length> <N bytes of UTF-8 JSON body>
# ---------------------------------------------------------------------------

def _send_framed(sock: socket.socket, body_bytes: bytes) -> None:
    """Prepend the 8-byte big-endian length prefix and send the whole frame."""
    length_prefix = len(body_bytes).to_bytes(8, byteorder="big", signed=False)
    sock.sendall(length_prefix + body_bytes)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from sock, accumulating across multiple recv() calls."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def _recv_framed(sock: socket.socket) -> bytes:
    """Read one length-prefixed frame and return the body bytes."""
    length_bytes = _recv_exact(sock, 8)
    length = int.from_bytes(length_bytes, byteorder="big", signed=False)
    if length == 0:
        raise ValueError("framing_error: zero-length body")
    if length > 1024 * 1024 * 1024:
        raise ValueError(f"framing_error: length {length} exceeds 1 GB cap")
    return _recv_exact(sock, length)


def call(host: str, port: int, method: str,
         params: dict | None = None, request_id: int = 1,
         timeout: float = 120.0) -> dict:
    """One framed JSON-RPC round-trip. Mirrors verify_wave_a.call() but takes
    host/port explicitly and a per-call timeout (a full scene build inside the
    editor can take well over the 30 s verify_wave_a uses). Transport errors
    are returned as {"_error": ...}; JSON-RPC errors stay in resp["error"]."""
    msg = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    raw = json.dumps(msg).encode("utf-8")

    s = socket.socket()
    s.settimeout(timeout)
    try:
        try:
            s.connect((host, port))
        except (ConnectionRefusedError, OSError) as e:
            return {"_error": f"Cannot reach UE at {host}:{port}: {e}. "
                              f"Is the editor open with the rebuilt "
                              f"UnrealClaudeMCP plugin?"}
        try:
            _send_framed(s, raw)
            payload_bytes = _recv_framed(s)
        except (ConnectionError, ValueError, socket.timeout) as e:
            return {"_error": f"framing error: {e}"}
    finally:
        s.close()

    payload = payload_bytes.decode("utf-8", errors="replace")
    if not payload:
        return {"_raw": "", "_error": "empty response"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        return {"_raw": payload[:500], "_decode_error": str(e)}


# ---------------------------------------------------------------------------
# Result unwrapping.
#
# A JSON-RPC -32601 means the handler is declared but not linked into the
# module — i.e. the host plugin was NOT rebuilt with it compiled in. That is
# the single most common reason this pipeline fails, so it gets a dedicated,
# unmistakable message and a non-zero exit (handled by die()).
# ---------------------------------------------------------------------------

class CaptureError(RuntimeError):
    """Any unrecoverable failure in the pipeline."""


def die(msg: str) -> "None":
    """Print a clear FAIL line and exit non-zero."""
    print(f"\n!! FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def unwrap(resp: dict, label: str) -> dict:
    """Return resp['result'] or raise CaptureError with a clear message.

    A -32601 is special-cased: it means the named handler is inert (host
    plugin not rebuilt), which is reported verbatim so the operator knows
    exactly what to fix.
    """
    if "_error" in resp or "_decode_error" in resp:
        raise CaptureError(f"[{label}] transport-level failure: {resp}")
    if not isinstance(resp, dict) or resp.get("jsonrpc") != "2.0":
        raise CaptureError(f"[{label}] not a JSON-RPC 2.0 response: {resp}")
    if "error" in resp:
        err = resp["error"]
        code = err.get("code") if isinstance(err, dict) else None
        if code == -32601:
            raise CaptureError(
                f"[{label}] JSON-RPC -32601 method-not-found: the '{label}' "
                f"handler is not linked into the module. The host plugin was "
                f"NOT rebuilt with this handler compiled in. error={err}"
            )
        raise CaptureError(f"[{label}] JSON-RPC error: {err}")
    if "result" not in resp:
        raise CaptureError(f"[{label}] missing 'result' field: {resp}")
    result = resp["result"]
    if not isinstance(result, dict):
        raise CaptureError(
            f"[{label}] expected an object 'result', got "
            f"{type(result).__name__}: {result}"
        )
    return result


# ---------------------------------------------------------------------------
# Scene / actor helpers.
# ---------------------------------------------------------------------------

# scripts/elven_city_scene.py is a *pure top-level* module: it has NO
# `if __name__ == "__main__":` guard and NO main()/entrypoint function. Its
# build logic runs as a side effect of import/exec — wipe() at the top, then
# the city is constructed at module top level, finishing with
# save_current_level() and unreal.log("ELV_SCENE_OK actors=%d" % ...). It is
# idempotent (it deletes every ELV_* actor first), so re-running is safe.
#
# Therefore the remote-execution mechanism is simply: read the file's text
# client-side and hand it verbatim to the execute_unreal_python handler, which
# exec()s it inside the editor's embedded Python. No entrypoint call needs to
# be appended. The "ELV_SCENE_OK" log line is its own success sentinel.
SCENE_SCRIPT_RELPATH = os.path.join("scripts", "elven_city_scene.py")
SCENE_OK_SENTINEL = "ELV_SCENE_OK"

ORBIT_CAM_LABEL = "DemoOrbitCam"
CAMERA_CLASS_PATH = "/Script/Engine.CameraActor"


def repo_root() -> str:
    """Repo root = parent of the scripts/ dir this file lives in."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_scene_code() -> str:
    """Read scripts/elven_city_scene.py text relative to the repo root.

    elven_city_scene.py has no __main__ guard / no entrypoint function, so the
    text is exec-ready as-is; nothing is appended.
    """
    path = os.path.join(repo_root(), SCENE_SCRIPT_RELPATH)
    if not os.path.isfile(path):
        raise CaptureError(
            f"scene script not found at {path} — run this from a clean "
            f"checkout (expected {SCENE_SCRIPT_RELPATH} beside scripts/)."
        )
    with open(path, "r", encoding="utf-8") as fh:
        code = fh.read()
    if "__main__" in code:
        # Defensive: the current script has no guard. If a future edit adds
        # one, exec() of the file text would no-op the build. Surface that
        # loudly rather than silently capture an empty level.
        raise CaptureError(
            f"{SCENE_SCRIPT_RELPATH} now contains '__main__' — this driver "
            f"assumes a guard-free top-level script and would exec it to a "
            f"no-op. Add an explicit entrypoint call here before continuing."
        )
    return code


def get_actor_labels(host: str, port: int, request_id: int) -> list[str]:
    """get_actors_in_level → list of actor labels (robust to shape variance)."""
    resp = call(host, port, "get_actors_in_level", {}, request_id=request_id)
    result = unwrap(resp, "get_actors_in_level")
    actors = result.get("actors")
    if not isinstance(actors, list):
        raise CaptureError(
            f"[get_actors_in_level] 'actors' not a list: {result}"
        )
    labels: list[str] = []
    for a in actors:
        if isinstance(a, str):
            labels.append(a)
        elif isinstance(a, dict):
            lbl = a.get("label") or a.get("name") or a.get("actor_label")
            if isinstance(lbl, str):
                labels.append(lbl)
    return labels


# ---------------------------------------------------------------------------
# Geometry.
#
# Scene extents (read from scripts/elven_city_scene.py placement bounds): the
# structural mass — stacked halls, cathedral spire, the great viaduct, cliff
# faces, peaks — spans roughly X in [-1800, 6200], Y in [-3600, 3400], Z in
# [0, ~6000]. The visual centre of that mass sits near (X≈2000, Y≈0). The
# furthest structures are ~6500 cm from there, so an orbit radius of ~9000 cm
# at Z≈5000 frames the whole city with a gentle downward pitch and keeps the
# spires (top ~Z6000) inside frame. ALL of these are exposed as CLI flags so
# the live stage can re-frame without editing code.
# ---------------------------------------------------------------------------

import math  # noqa: E402 -- grouped with the geometry section it serves


def orbit_pose(angle_deg: float, radius: float, height_z: float,
                cx: float, cy: float, cz: float,
                pitch_deg: float) -> tuple[dict, dict]:
    """Camera location on a circle of `radius` around (cx,cy) at `height_z`,
    plus a rotation whose yaw faces the centre (cx,cy,cz) and whose pitch is
    a gentle downward tilt. Returns (location, rotation) dicts in the
    {x,y,z} / {roll,pitch,yaw} shape the handlers expect."""
    a = math.radians(angle_deg)
    x = cx + radius * math.cos(a)
    y = cy + radius * math.sin(a)
    z = height_z
    # Yaw to look from (x,y) back toward the centre (cx,cy).
    yaw = math.degrees(math.atan2(cy - y, cx - x))
    location = {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3)}
    rotation = {"roll": 0.0, "pitch": round(pitch_deg, 3), "yaw": round(yaw, 3)}
    return location, rotation


# ---------------------------------------------------------------------------
# ffmpeg assembly.
# ---------------------------------------------------------------------------

def ffmpeg_version_line() -> str:
    """First line of `ffmpeg -version`, or raise CaptureError if missing."""
    if shutil.which("ffmpeg") is None:
        raise CaptureError(
            "ffmpeg not found on PATH — this script assembles the GIF with "
            "ffmpeg's palettegen (no Pillow/imageio fallback). Install ffmpeg."
        )
    try:
        out = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise CaptureError(f"could not run `ffmpeg -version`: {e}") from e
    first = (out.stdout or out.stderr or "").splitlines()
    return first[0].strip() if first else "ffmpeg (version line unavailable)"


def build_ffmpeg_cmd(frames_dir: str, out_gif: str, fps: int,
                      scale_w: int) -> list[str]:
    """Two-pass palettegen GIF command. Single -i over the PNG sequence; the
    palette is generated and applied in one filtergraph (split → palettegen →
    paletteuse) so quality stays high at a small file size."""
    vf = (
        f"fps={fps},scale={scale_w}:-1:flags=lanczos,"
        f"split[a][b];[a]palettegen=stats_mode=diff[p];"
        f"[b][p]paletteuse=dither=bayer:bayer_scale=5"
    )
    return [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "frame_%04d.png"),
        "-vf", vf,
        "-loop", "0",
        out_gif,
    ]


def run_ffmpeg(cmd: list[str]) -> None:
    """Run ffmpeg; raise CaptureError with captured stderr on any failure."""
    print("  ffmpeg: " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=600)
    except (OSError, subprocess.SubprocessError) as e:
        raise CaptureError(f"ffmpeg invocation failed: {e}") from e
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()
        if len(tail) > 1500:
            tail = "..." + tail[-1500:]
        raise CaptureError(
            f"ffmpeg exited {proc.returncode}. stderr:\n{tail}"
        )


# ---------------------------------------------------------------------------
# Pipeline.
# ---------------------------------------------------------------------------

def main() -> None:
    default_out_gif = os.path.join(repo_root(), "docs", "images", "demo.gif")
    default_frames_dir = os.path.join(tempfile.gettempdir(),
                                      "ucmcp_demo_frames")

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host", default="127.0.0.1",
                    help="UE MCP host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=18888,
                    help="UE MCP port (default 18888)")
    ap.add_argument("--frames", type=int, default=48,
                    help="number of orbit frames (default 48)")
    ap.add_argument("--width", type=int, default=1280,
                    help="render width in px (default 1280)")
    ap.add_argument("--height", type=int, default=720,
                    help="render height in px (default 720)")
    ap.add_argument("--fov", type=float, default=70.0,
                    help="camera horizontal FOV in degrees (default 70.0)")
    ap.add_argument("--radius", type=float, default=9000.0,
                    help=("orbit radius in cm around the scene centre. "
                          "Default 9000 frames the elven-city extents "
                          "(structures span ~6500 cm from centre)."))
    ap.add_argument("--height-z", dest="height_z", type=float, default=5000.0,
                    help=("camera Z in cm (default 5000 — above the city; "
                          "spire tops are ~Z6000 so a downward pitch keeps "
                          "them in frame)."))
    ap.add_argument("--center-x", dest="center_x", type=float, default=2000.0,
                    help="orbit centre X in cm (default 2000 — city mass)")
    ap.add_argument("--center-y", dest="center_y", type=float, default=0.0,
                    help="orbit centre Y in cm (default 0)")
    ap.add_argument("--center-z", dest="center_z", type=float, default=1500.0,
                    help="orbit centre Z in cm — yaw look-at height (default 1500)")
    ap.add_argument("--pitch", type=float, default=-15.0,
                    help="camera downward pitch in degrees (default -15.0)")
    ap.add_argument("--out-gif", dest="out_gif", default=default_out_gif,
                    help=f"output GIF path (default {default_out_gif})")
    ap.add_argument("--frames-dir", dest="frames_dir",
                    default=default_frames_dir,
                    help=f"PNG frame scratch dir (default {default_frames_dir})")
    ap.add_argument("--fps", type=int, default=20,
                    help="GIF frame rate (default 20)")
    ap.add_argument("--scale-w", dest="scale_w", type=int, default=960,
                    help="GIF output width in px, aspect-preserved (default 960)")
    ap.add_argument("--keep-frames", dest="keep_frames", action="store_true",
                    help="keep the PNG frame dir instead of deleting it")
    ap.add_argument("--skip-build", dest="skip_build", action="store_true",
                    help=("skip the procedural scene build and capture the "
                          "current level as-is"))
    args = ap.parse_args()

    if args.frames < 2:
        die(f"--frames must be >= 2 (got {args.frames})")
    if args.scale_w < 16:
        die(f"--scale-w must be >= 16 (got {args.scale_w})")

    host, port = args.host, args.port
    print("Demo-GIF capture pipeline")
    print(f"  target     : {host}:{port}")
    print(f"  frames     : {args.frames} @ {args.width}x{args.height} "
          f"fov={args.fov}")
    print(f"  orbit      : radius={args.radius} z={args.height_z} "
          f"centre=({args.center_x},{args.center_y},{args.center_z}) "
          f"pitch={args.pitch}")
    print(f"  out gif    : {args.out_gif}")
    print(f"  frames dir : {args.frames_dir}")
    print(f"  ffmpeg     : {args.fps} fps, scale-w {args.scale_w}")
    print(f"  build scene: {'NO (--skip-build)' if args.skip_build else 'yes'}")

    scene_built = False
    try:
        # ffmpeg is the only assembly tool; fail fast before doing UE work if
        # it is missing so we never capture 48 frames then discover we cannot
        # assemble them.
        ff_version = ffmpeg_version_line()
        print(f"  {ff_version}")

        # --- (a) connectivity probe ------------------------------------
        print("\n[1/5] connectivity probe (get_actors_in_level) ...")
        probe = call(host, port, "get_actors_in_level", {}, request_id=1,
                     timeout=30.0)
        if "_error" in probe:
            raise CaptureError(
                f"cannot reach a live editor at {host}:{port}: "
                f"{probe['_error']}"
            )
        unwrap(probe, "get_actors_in_level")
        print("  editor reachable; handler catalog live.")

        # --- (b) build the scene ---------------------------------------
        if not args.skip_build:
            print("\n[2/5] building procedural city blockout "
                  "(execute_unreal_python ← elven_city_scene.py) ...")
            code = read_scene_code()
            resp = call(host, port, "execute_unreal_python",
                        {"code": code}, request_id=2, timeout=300.0)
            result = unwrap(resp, "execute_unreal_python")
            # The handler's own ok/output shape varies; treat an explicit
            # ok=false as fatal, otherwise look for the scene sentinel in any
            # textual output field so a silent failure cannot pass as success.
            if result.get("ok") is False:
                raise CaptureError(
                    f"[execute_unreal_python] scene build reported "
                    f"ok=false: {result}"
                )
            blob = json.dumps(result, default=str)
            if SCENE_OK_SENTINEL not in blob:
                print(f"  (note: '{SCENE_OK_SENTINEL}' sentinel not echoed in "
                      f"handler output; relying on actor-count sanity below. "
                      f"raw keys: {sorted(result.keys())})")
            scene_built = True
            labels = get_actor_labels(host, port, request_id=3)
            elv = [l for l in labels if l.startswith("ELV_")]
            print(f"  scene built: {len(labels)} actors in level, "
                  f"{len(elv)} ELV_* (expect ~100+).")
            if len(elv) < 50:
                raise CaptureError(
                    f"scene build produced only {len(elv)} ELV_* actors "
                    f"(expected ~100+) — build likely failed mid-script. "
                    f"labels sample: {sorted(elv)[:10]}"
                )
        else:
            print("\n[2/5] --skip-build: capturing the current level as-is.")

        # --- (c) spawn (or reuse) the orbit camera ---------------------
        print(f"\n[3/5] orbit camera '{ORBIT_CAM_LABEL}' ...")
        labels = get_actor_labels(host, port, request_id=4)
        if ORBIT_CAM_LABEL in labels:
            print(f"  '{ORBIT_CAM_LABEL}' already present — reusing it "
                  f"(idempotent re-run).")
        else:
            spawn_resp = call(host, port, "spawn_actor", {
                "class_path": CAMERA_CLASS_PATH,
                "label": ORBIT_CAM_LABEL,
                "location": {"x": 0.0, "y": 0.0, "z": 0.0},
                "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
            }, request_id=5, timeout=60.0)
            unwrap(spawn_resp, "spawn_actor")
            post = get_actor_labels(host, port, request_id=6)
            if ORBIT_CAM_LABEL not in post:
                raise CaptureError(
                    f"spawn_actor returned success but '{ORBIT_CAM_LABEL}' "
                    f"is not in the level afterward — spawn did not stick."
                )
            print(f"  spawned '{ORBIT_CAM_LABEL}' ({CAMERA_CLASS_PATH}).")

        # --- (d) orbit + capture each frame ----------------------------
        frames_dir = os.path.abspath(args.frames_dir)
        os.makedirs(frames_dir, exist_ok=True)
        # Clear any stale frames so size/exists checks measure THIS run.
        for stale in os.listdir(frames_dir):
            if stale.startswith("frame_") and stale.endswith(".png"):
                try:
                    os.remove(os.path.join(frames_dir, stale))
                except OSError:
                    pass

        print(f"\n[4/5] orbit-rendering {args.frames} frames into "
              f"{frames_dir} ...")
        failures: list[str] = []
        for i in range(args.frames):
            angle = 360.0 * i / args.frames
            location, rotation = orbit_pose(
                angle, args.radius, args.height_z,
                args.center_x, args.center_y, args.center_z, args.pitch,
            )
            frame_path = os.path.join(frames_dir, f"frame_{i:04d}.png")
            try:
                if os.path.exists(frame_path):
                    os.remove(frame_path)
            except OSError:
                pass

            mv = call(host, port, "set_actor_transform", {
                "name": ORBIT_CAM_LABEL,
                "location": location,
                "rotation": rotation,
            }, request_id=1000 + i, timeout=60.0)
            try:
                unwrap(mv, f"set_actor_transform[frame {i}]")
            except CaptureError as e:
                failures.append(str(e))
                continue

            rr = call(host, port, "render_camera_to_png", {
                "out_path": frame_path,
                "camera_label": ORBIT_CAM_LABEL,
                "width": args.width,
                "height": args.height,
                "fov": args.fov,
            }, request_id=2000 + i, timeout=120.0)
            try:
                unwrap(rr, f"render_camera_to_png[frame {i}]")
            except CaptureError as e:
                failures.append(str(e))
                continue

            if not os.path.exists(frame_path):
                failures.append(
                    f"[frame {i}] handler ok but no file at {frame_path}"
                )
                continue
            size = os.path.getsize(frame_path)
            if size <= 10240:
                failures.append(
                    f"[frame {i}] {frame_path} is {size} bytes (<=10240) — "
                    f"blank/empty capture"
                )
                continue
            if (i + 1) % 8 == 0 or i == args.frames - 1:
                print(f"  frame {i + 1}/{args.frames} ok "
                      f"(angle={angle:.1f}deg, {size} bytes)")

        if failures:
            shown = failures[:8]
            more = (f"\n  ... and {len(failures) - 8} more"
                    if len(failures) > 8 else "")
            die(
                f"{len(failures)}/{args.frames} frame(s) failed; aborting "
                f"before GIF assembly:\n  " + "\n  ".join(shown) + more
            )

        good = sorted(f for f in os.listdir(frames_dir)
                      if f.startswith("frame_") and f.endswith(".png"))
        if len(good) != args.frames:
            die(
                f"expected {args.frames} frame PNGs in {frames_dir}, found "
                f"{len(good)} — refusing to assemble a partial GIF."
            )
        print(f"  all {args.frames} frames captured.")

        # --- (e) assemble the GIF --------------------------------------
        print("\n[5/5] assembling GIF with ffmpeg (two-pass palettegen) ...")
        out_gif = os.path.abspath(args.out_gif)
        os.makedirs(os.path.dirname(out_gif), exist_ok=True)
        run_ffmpeg(build_ffmpeg_cmd(frames_dir, out_gif, args.fps,
                                    args.scale_w))
        if not os.path.exists(out_gif):
            die(f"ffmpeg reported success but {out_gif} does not exist.")
        gif_bytes = os.path.getsize(out_gif)
        if gif_bytes <= 50 * 1024:
            die(
                f"{out_gif} is {gif_bytes} bytes (<= 50 KiB) — assembly "
                f"produced a degenerate GIF."
            )
        gif_mb = gif_bytes / (1024 * 1024)
        print(f"  GIF written: {out_gif} ({gif_bytes} bytes, {gif_mb:.2f} MB)")

        # Keep the repo light (no LFS): if the first pass is > 20 MB, re-encode
        # once at a smaller width / fps and report the smaller artifact.
        if gif_bytes > 20 * 1024 * 1024:
            print(f"  GIF > 20 MB — re-encoding once at scale-w=800 fps=15 "
                  f"to keep the repo light ...")
            run_ffmpeg(build_ffmpeg_cmd(frames_dir, out_gif, 15, 800))
            if not os.path.exists(out_gif):
                die(f"re-encode reported success but {out_gif} is gone.")
            gif_bytes = os.path.getsize(out_gif)
            gif_mb = gif_bytes / (1024 * 1024)
            print(f"  re-encoded GIF: {out_gif} ({gif_bytes} bytes, "
                  f"{gif_mb:.2f} MB)")

        # --- frame cleanup ---------------------------------------------
        if args.keep_frames:
            print(f"  --keep-frames: PNG frames left in {frames_dir}")
        else:
            shutil.rmtree(frames_dir, ignore_errors=True)
            print(f"  frame dir removed: {frames_dir}")

    except CaptureError as e:
        die(str(e))

    # --- final summary -------------------------------------------------
    print()
    print("=" * 62)
    print("  Demo-GIF capture summary")
    print("=" * 62)
    print(f"  scene built     : "
          f"{'yes' if scene_built else 'no (--skip-build)'}")
    print(f"  frames captured : {args.frames} @ {args.width}x{args.height}")
    print(f"  gif             : {out_gif}")
    print(f"  gif size        : {gif_bytes} bytes ({gif_mb:.2f} MB)")
    print(f"  {ff_version}")
    print("=" * 62)
    print("\n  RESULT: PASS — demo GIF produced.")
    sys.exit(0)


if __name__ == "__main__":
    main()
