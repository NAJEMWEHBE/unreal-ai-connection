#!/usr/bin/env python3
# Copyright (c) 2026 HD Media. MIT licensed - see LICENSE.
"""Fast A/B look loop: capture the live UE viewport and compare it to a
reference image, in one command, in well under 10 seconds.

Talks the plugin's framed JSON-RPC wire format DIRECTLY (no MCP layer), so it
works from any shell while the editor is open - foreground or backgrounded
(capture goes through take_screenshot, which forces a fresh frame).

    python scripts/look_loop.py --ref path/to/reference.png
    python scripts/look_loop.py --ref ref.png --cam "0,-900,170,0,90,0" --fov 50
    python scripts/look_loop.py --ref ref.png --width 1920 --height 1080

Output: a side-by-side composite PNG (capture | reference) next to the
capture, plus look-match metrics on stdout (mean RGB delta, luminance and
saturation deltas overall and per horizontal third - top/mid/bottom).

Requires Pillow for the comparison step (pip install pillow). The capture
itself is dependency-free; with Pillow missing the script still captures and
reports the path, it just skips the composite + metrics.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time

HOST = os.environ.get("UCMCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("UCMCP_PORT", "18888"))


# --- wire protocol (mirrors bridge send_framed / recv_framed) ---------------

def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("UE server closed the connection mid-frame")
        buf += chunk
    return buf


def call_ue(method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
    s = socket.socket()
    s.settimeout(timeout)
    s.connect((HOST, PORT))
    msg: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        msg["params"] = params
    body = json.dumps(msg).encode("utf-8")
    s.sendall(struct.pack(">Q", len(body)) + body)
    (length,) = struct.unpack(">Q", _recv_exact(s, 8))
    raw = _recv_exact(s, length).decode("utf-8", errors="replace")
    s.close()
    resp = json.loads(raw)
    if "error" in resp:
        raise RuntimeError(f"{method}: {resp['error'].get('message')}")
    return resp.get("result", {}) or {}


# --- camera ------------------------------------------------------------------

SET_CAMERA_PY = """
import unreal
les = unreal.UnrealEditorSubsystem()
les.set_level_viewport_camera_info(
    unreal.Vector({x}, {y}, {z}),
    unreal.Rotator(roll={roll}, pitch={pitch}, yaw={yaw}))
"""


def set_camera(cam: str) -> None:
    parts = [float(v) for v in cam.split(",")]
    if len(parts) != 6:
        raise SystemExit("--cam wants 6 comma-separated numbers: x,y,z,pitch,yaw,roll")
    x, y, z, pitch, yaw, roll = parts
    call_ue("execute_unreal_python", {
        "code": SET_CAMERA_PY.format(x=x, y=y, z=z, pitch=pitch, yaw=yaw, roll=roll)})


# --- comparison ---------------------------------------------------------------

def _rgb_pixels(img):
    """Pixel tuples of an RGB image without Image.getdata() (deprecated in
    Pillow 12, removed in 14): per-band tobytes() zipped back together."""
    r, g, b = (band.tobytes() for band in img.split())
    return list(zip(r, g, b))


def _luma_sat(img):
    """Mean luminance + saturation of a (small) RGB image, 0-255 floats."""
    px = _rgb_pixels(img)
    n = max(1, len(px))
    luma = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px) / n
    sat = sum(max(p) - min(p) for p in px) / n
    return luma, sat


def compare(capture_path: str, ref_path: str, composite_path: str) -> dict:
    from PIL import Image

    cap = Image.open(capture_path).convert("RGB")
    ref = Image.open(ref_path).convert("RGB")

    # Side-by-side composite at capture height.
    ref_scaled = ref.resize(
        (max(1, round(ref.width * cap.height / ref.height)), cap.height))
    combo = Image.new("RGB", (cap.width + ref_scaled.width, cap.height), (16, 16, 16))
    combo.paste(cap, (0, 0))
    combo.paste(ref_scaled, (cap.width, 0))
    combo.save(composite_path)

    # Metrics on a normalized small grid so resolution doesn't matter.
    small_cap = cap.resize((96, 54))
    small_ref = ref.resize((96, 54))
    cap_px, ref_px = _rgb_pixels(small_cap), _rgb_pixels(small_ref)
    mean_rgb_delta = sum(
        (abs(c[0] - r[0]) + abs(c[1] - r[1]) + abs(c[2] - r[2])) / 3.0
        for c, r in zip(cap_px, ref_px)) / len(cap_px)

    metrics = {"mean_rgb_delta": round(mean_rgb_delta, 2), "thirds": {}}
    h = small_cap.height
    for name, (y0, y1) in (("top", (0, h // 3)),
                           ("mid", (h // 3, 2 * h // 3)),
                           ("bottom", (2 * h // 3, h))):
        box = (0, y0, small_cap.width, y1)
        cl, cs = _luma_sat(small_cap.crop(box))
        rl, rs = _luma_sat(small_ref.crop(box))
        metrics["thirds"][name] = {
            "luma_delta": round(cl - rl, 1),   # + = capture brighter than ref
            "sat_delta": round(cs - rs, 1),    # + = capture more saturated
        }
    return metrics


# --- main ---------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ref", required=True, help="Reference image to match (png/jpg).")
    ap.add_argument("--cam", help="Optional camera 'x,y,z,pitch,yaw,roll' set before capture.")
    ap.add_argument("--fov", type=float, default=0.0, help="Optional FOV for the capture.")
    ap.add_argument("--width", type=int, default=0, help="Optional off-screen capture width.")
    ap.add_argument("--height", type=int, default=0, help="Optional off-screen capture height.")
    ap.add_argument("--out", help="Capture out_path (project-relative; default Saved/AIConnection/LookLoop/<ts>.png).")
    args = ap.parse_args()

    if not os.path.isfile(args.ref):
        raise SystemExit(f"reference image not found: {args.ref}")

    t0 = time.monotonic()

    if args.cam:
        set_camera(args.cam)

    out_rel = args.out or f"Saved/AIConnection/LookLoop/look_{time.strftime('%Y%m%d_%H%M%S')}.png"
    params: dict = {"out_path": out_rel}
    if args.width > 0 and args.height > 0:
        params["width"], params["height"] = args.width, args.height
    if args.fov > 0:
        params["fov"] = args.fov

    shot = call_ue("take_screenshot", params)
    capture_path = shot.get("path", "")
    print(f"capture: {capture_path} ({shot.get('width')}x{shot.get('height')}, "
          f"{shot.get('bytes', 0):,} bytes)")

    try:
        composite_path = os.path.splitext(capture_path)[0] + "_vs_ref.png"
        metrics = compare(capture_path, args.ref, composite_path)
        print(f"composite: {composite_path}")
        print(f"metrics: {json.dumps(metrics)}")
    except ImportError:
        print("Pillow not installed - skipped composite + metrics (pip install pillow)")

    print(f"elapsed: {time.monotonic() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
