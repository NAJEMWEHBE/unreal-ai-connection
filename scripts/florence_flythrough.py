"""Florence plaza fly-through: bridge-driver demo of sequencer_add_transform_keyframe.

End-to-end orchestration of a 6-keyframe orbital camera arc around the
marble dais in L_Florence_Plaza, driving the bridge MCP server over
stdio. First production use of the `sequencer_add_transform_keyframe`
synthetic shipped in PR #194.

Steps (each one a proper MCP tools/call where a native or synthetic
tool exists; only the CineCameraActor spawn + playhead scrub helper
fall back to `execute_unreal_python`):

  1.  load_level_by_path                          /Game/Validation/Florence/L_Florence_Plaza
  2.  create_sequence                             SEQ_Florence_Flythrough (idempotent)
  3.  execute_unreal_python                       spawn Cam_Florence_Hero (CineCameraActor, 35mm/f2.8)
  4.  bind_actor_to_sequence                      -> binding_guid
  5.  sequencer_add_transform_keyframe x6         orbit SE -> S -> SW -> W
  6.  inspect_sequence                            live verification of bound tracks
  7.  set_camera_transform + get_viewport_screenshot
      hero PNG at docs/validation/florence-flythrough-hero-<date>.png

Re-running this script is safe: existing sequence is reused, existing
camera actor is reused (no double-bind). Each output line is a single
`[FLYTHROUGH] step=<n> tool=<name> ok=<bool> ...` so a watcher can
read progress without parsing JSON.

Usage:
    py scripts/florence_flythrough.py [--bridge bridge/unreal_claude_mcp_bridge.py]
"""

import argparse
import base64
import datetime as _dt
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BRIDGE = REPO_ROOT / "bridge" / "unreal_claude_mcp_bridge.py"

LEVEL_PATH = "/Game/Validation/Florence/L_Florence_Plaza"
SEQ_FOLDER = "/Game/Validation/Florence"
SEQ_NAME = "SEQ_Florence_Flythrough"
SEQ_PATH = f"{SEQ_FOLDER}/{SEQ_NAME}"  # asset path (no .ext)
CAMERA_LABEL = "Cam_Florence_Hero"
CAMERA_CLASS_PATH = "/Script/CinematicCamera.CineCameraActor"

# 6-keyframe orbital arc. Position the camera south of the dais then walk
# clockwise SE -> S -> SW -> W. Yaw is the world-space facing direction;
# UE default yaw=0 faces +X, so a camera at +X (east) looking back at the
# origin needs yaw=180; from south (-Y) looking back needs yaw=90; etc.
# The dais center is roughly (0, 0, 15). We compute the look-at yaw from
# the per-keyframe position so the camera always faces the dais.
DAIS_CENTER = (0.0, 0.0, 15.0)


def _look_at_yaw(cam_x: float, cam_y: float) -> float:
    """Yaw (degrees) that makes a camera at (cam_x, cam_y) point at the dais.

    UE convention: yaw=0 looks toward +X, yaw rotates clockwise looking
    down on the XY plane. So the world-space direction from camera to
    target on the XY plane is atan2(dy, dx).
    """
    import math
    dx = DAIS_CENTER[0] - cam_x
    dy = DAIS_CENTER[1] - cam_y
    return math.degrees(math.atan2(dy, dx))


# t,  pos,                                pitch
_KEYS_RAW = [
    (0.0,  (1500.0,  -866.0, 280.0), -8.0),
    (2.0,  ( 866.0, -1500.0, 285.0), -8.0),
    (4.0,  (   0.0, -1730.0, 290.0), -8.0),
    (6.0,  (-866.0, -1500.0, 295.0), -8.0),
    (8.0,  (-1500.0, -866.0, 300.0), -8.0),
    (10.0, (-1730.0,    0.0, 320.0), -8.0),
]


def keyframes():
    """Yield (t, location, rotation_pitch_yaw_roll) tuples for each waypoint."""
    out = []
    for t, pos, pitch in _KEYS_RAW:
        yaw = _look_at_yaw(pos[0], pos[1])
        out.append((t, list(pos), [pitch, yaw, 0.0]))
    return out


# ---------------------------------------------------------------------------
# MCP stdio client
# ---------------------------------------------------------------------------

class BridgeClient:
    """Spawns the bridge subprocess and speaks MCP JSON-RPC over stdin/stdout.

    The bridge handles initialize + tools/list synthetically and forwards
    tools/call either to a local SYNTHETIC_TOOLS handler or out to the
    UE plugin over TCP at 127.0.0.1:18888.
    """

    # Default per-call read timeouts (seconds). The slow tools — sequence
    # creation, screenshot capture, keyframe writes with auto_extend_section
    # — get bumped to 120s; everything else uses 60s.
    DEFAULT_READ_TIMEOUT = 60.0
    SLOW_TOOL_TIMEOUT = 120.0
    SLOW_TOOLS = frozenset({
        "create_sequence",
        "get_viewport_screenshot",
        "sequencer_add_transform_keyframe",
        "load_level_by_path",
    })

    def __init__(self, bridge_path: Path):
        self.bridge_path = bridge_path
        self.proc: subprocess.Popen | None = None
        self._next_id = 1
        self._stdout_q: "queue.Queue[str]" = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def _stdout_pump(self) -> None:
        """Background thread: shovel bridge stdout lines into a queue."""
        assert self.proc is not None and self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                self._stdout_q.put(line)
        except Exception:
            pass
        finally:
            self._stdout_q.put("")  # sentinel for EOF

    def __enter__(self):
        env = dict(os.environ)
        # Ensure unbuffered I/O so the JSON-RPC frames arrive promptly.
        env.setdefault("PYTHONUNBUFFERED", "1")
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(self.bridge_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self._reader_thread = threading.Thread(target=self._stdout_pump, daemon=True)
        self._reader_thread.start()
        # MCP handshake.
        self._send({
            "jsonrpc": "2.0", "id": self._take_id(), "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "florence-flythrough-driver", "version": "0.1.0"},
            },
        })
        self._read(context="initialize")  # discard handshake response
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            # Drain stderr so any final bridge complaint surfaces.
            try:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                if err:
                    sys.stderr.write(err)
            except Exception:
                pass

    def _take_id(self) -> int:
        n = self._next_id
        self._next_id += 1
        return n

    def _send(self, obj: dict) -> None:
        assert self.proc is not None
        line = json.dumps(obj) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

    def _read(self, timeout: float | None = None, context: str | None = None) -> dict:
        """Read one JSON-RPC frame from the bridge.

        Raises RuntimeError on timeout (with `context` in the message) or
        on unexpected stdout closure. Default timeout is
        DEFAULT_READ_TIMEOUT; pass a larger value for slow tools so they
        fail fast on hangs instead of waiting forever.
        """
        assert self.proc is not None
        eff_timeout = timeout if timeout is not None else self.DEFAULT_READ_TIMEOUT
        try:
            line = self._stdout_q.get(timeout=eff_timeout)
        except queue.Empty:
            # Don't block on stderr read (it could itself hang); just
            # snapshot whatever's already accumulated by the thread pump.
            ctx = context or "<unknown>"
            raise RuntimeError(
                f"bridge_read_timeout: no response within {eff_timeout:.1f}s "
                f"(context={ctx})"
            )
        if not line:
            err = ""
            if self.proc.stderr is not None:
                try:
                    err = self.proc.stderr.read() or ""
                except Exception:
                    pass
            ctx = context or "<unknown>"
            raise RuntimeError(
                f"bridge closed stdout unexpectedly (context={ctx}). stderr={err!r}"
            )
        return json.loads(line)

    def call_tool(self, name: str, arguments: dict | None = None,
                  timeout: float | None = None) -> dict:
        """Send tools/call and return the raw JSON-RPC response.

        `timeout` overrides the per-tool default if provided.
        """
        req_id = self._take_id()
        if timeout is None:
            timeout = (self.SLOW_TOOL_TIMEOUT if name in self.SLOW_TOOLS
                       else self.DEFAULT_READ_TIMEOUT)
        self._send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        resp = self._read(timeout=timeout, context=f"tools/call name={name} id={req_id}")
        # The bridge can emit a response with a different id if it spoke
        # multiple round-trips internally; just trust the next line and
        # warn if id drift shows up.
        if resp.get("id") != req_id and "id" in resp:
            # Drain until we get our id, but bridge is single-flighted so
            # this should not happen in practice.
            pass
        return resp


# ---------------------------------------------------------------------------
# Result unpacking helpers — MCP wraps every tools/call result inside a
# `content: [{type:"text", text: <json string>}]` envelope.
# ---------------------------------------------------------------------------

def unpack_tool_result(resp: dict) -> dict:
    """Return the inner payload dict for a successful tools/call response.

    Raises RuntimeError on JSON-RPC error or malformed envelope.
    """
    if "error" in resp:
        raise RuntimeError(f"JSON-RPC error: {resp['error']}")
    result = resp.get("result") or {}
    content = result.get("content")
    if not content:
        # Some bridge paths return a bare result dict already.
        return result
    first = content[0]
    text = first.get("text", "")
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Not JSON-encoded -- return as-is for the caller to inspect.
        return {"_raw_text": text}


# ---------------------------------------------------------------------------
# Step helpers — each one prints a single structured progress line.
# ---------------------------------------------------------------------------

def log_step(n: int, tool: str, ok: bool, **extra) -> None:
    extras = " ".join(f"{k}={v}" for k, v in extra.items())
    print(f"[FLYTHROUGH] step={n} tool={tool} ok={ok} {extras}".rstrip())


def step_load_level(client: BridgeClient) -> None:
    resp = client.call_tool("load_level_by_path", {"path": LEVEL_PATH})
    payload = unpack_tool_result(resp)
    # load_level_by_path returns {"path": ..., "loaded": true}; surface both.
    log_step(1, "load_level_by_path", bool(payload.get("loaded")),
             path=payload.get("path"))
    if not payload.get("loaded"):
        raise RuntimeError(f"load_level_by_path failed: {payload}")


def step_create_or_reuse_sequence(client: BridgeClient) -> str:
    """Returns the asset path of the sequence (no .ext suffix)."""
    resp = client.call_tool("create_sequence", {
        "path": SEQ_FOLDER,
        "name": SEQ_NAME,
        "display_rate_fps": 30.0,
        "playback_end_frames": 30 * 11,  # one frame of slack past t=10s
    })
    if "error" in resp:
        msg = resp["error"].get("message", "")
        if "dest_exists" in msg:
            log_step(2, "create_sequence", True, reused=True, path=SEQ_PATH)
            return SEQ_PATH
        raise RuntimeError(f"create_sequence failed: {resp['error']}")
    payload = unpack_tool_result(resp)
    log_step(2, "create_sequence", bool(payload.get("ok")),
             reused=False, path=payload.get("package_path"))
    return payload.get("package_path", SEQ_PATH)


# Inline UE-python: spawn (or find existing) CineCameraActor by label,
# configure focal length + aperture. The spawn_actor handler can spawn
# any AActor subclass including CineCameraActor, but it doesn't take
# cinematic-specific properties, so we use execute_unreal_python.
#
# Marker output goes through unreal.log() so we can scrape it from the
# LogPython ring buffer via get_log_lines — UE 5.7's execute_unreal_python
# does not reliably flush print() into the captured 'output' field on every
# execution path (same gotcha the sequencer synthetic works around).
_SPAWN_CAMERA_PY = """
import json
import unreal

label = {label!r}
token = {token!r}
ll = unreal.EditorLevelLibrary
els = unreal.EditorActorSubsystem()

existing = [a for a in els.get_all_level_actors() if a.get_actor_label() == label]
if existing:
    cam = existing[0]
    reused = True
else:
    cam = ll.spawn_actor_from_class(
        unreal.CineCameraActor,
        unreal.Vector(1500.0, -866.0, 280.0),
        unreal.Rotator(0.0, 0.0, 0.0),
    )
    cam.set_actor_label(label)
    reused = False

cam_comp = cam.get_cine_camera_component()
cam_comp.set_editor_property("current_focal_length", 35.0)
cam_comp.set_editor_property("current_aperture", 2.8)

unreal.log("FLYTHROUGH_CAMERA_OK::" + token + "::" + json.dumps({{
    "name": cam.get_name(),
    "label": cam.get_actor_label(),
    "reused": reused,
    "focal_length": cam_comp.get_editor_property("current_focal_length"),
    "aperture": cam_comp.get_editor_property("current_aperture"),
}}) + "::__END__")
"""


def _scrape_marker(client: BridgeClient, prefix: str, captured_output: str = "",
                   token: str | None = None) -> dict | None:
    """Scrape a "<prefix>::<token>::<json>::__END__" line.

    If `token` is provided, only matches lines that carry that token —
    stale markers from prior runs are dropped. This prevents the
    LogPython ring buffer fallback from picking up an old run's marker
    on rerun.

    Tries the captured `output` first (cheaper) then falls back to
    pulling the LogPython ring buffer via the get_log_lines tool. The
    bridge's sequencer synthetic uses the exact same pattern because
    UE 5.7's execute_unreal_python does not reliably flush print() /
    unreal.log() into the captured `output` field across all evaluator
    paths.
    """
    # Build the effective prefix that the marker line must contain. When
    # a token is supplied we expect "<prefix><token>::" — concretely the
    # helper Python emits ``<prefix> + token + "::" + json + "::__END__"``.
    eff_prefix = (prefix + token + "::") if token else prefix

    def _scan(text: str) -> dict | None:
        for line in text.splitlines():
            idx = line.find(eff_prefix)
            end = line.find("::__END__", idx) if idx >= 0 else -1
            if idx >= 0 and end > idx:
                blob = line[idx + len(eff_prefix):end]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    return {"_raw": blob}
        return None

    hit = _scan(captured_output)
    if hit is not None:
        return hit

    log_resp = client.call_tool("get_log_lines", {
        "count": 256,
        "category_filter": "LogPython",
        "min_verbosity": "Log",
    })
    log_payload = unpack_tool_result(log_resp)
    lines = log_payload.get("lines") or []
    for entry in reversed(lines):
        msg = entry.get("message", "") if isinstance(entry, dict) else ""
        idx = msg.find(eff_prefix)
        end = msg.find("::__END__", idx) if idx >= 0 else -1
        if idx >= 0 and end > idx:
            blob = msg[idx + len(eff_prefix):end]
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                return {"_raw": blob}
    return None


def step_spawn_camera(client: BridgeClient, run_token: str) -> dict:
    code = _SPAWN_CAMERA_PY.format(label=CAMERA_LABEL, token=run_token)
    resp = client.call_tool("execute_unreal_python", {
        "code": code,
        "capture_output": True,
    })
    payload = unpack_tool_result(resp)
    if not payload.get("ok"):
        raise RuntimeError(f"spawn camera failed: {payload}")
    info = _scrape_marker(client, "FLYTHROUGH_CAMERA_OK::",
                          payload.get("output", ""), token=run_token) or {}
    if not info:
        raise RuntimeError(
            "spawn camera produced no FLYTHROUGH_CAMERA_OK marker carrying "
            f"token {run_token!r}"
        )
    log_step(3, "execute_unreal_python:spawn_camera", True,
             reused=info.get("reused"), label=info.get("label"))
    return info


_WIPE_BINDINGS_PY = """
import json
import unreal

seq_path = {seq!r}
token = {token!r}
seq = unreal.EditorAssetLibrary.load_asset(seq_path)

# Drop every existing binding so re-runs don't accumulate orphans from
# previous sessions. UE 5.7's MovieSceneBindingProxy exposes a .remove()
# method that detaches the binding (along with any tracks it owned).
removed = 0
for binding in list(seq.get_bindings()):
    try:
        binding.remove()
        removed += 1
    except Exception as e:
        unreal.log_warning("FLYTHROUGH_WIPE: binding.remove() failed: " + str(e))

unreal.EditorAssetLibrary.save_loaded_asset(seq)
unreal.log("FLYTHROUGH_WIPE_OK::" + token + "::" + json.dumps({{
    "removed": removed,
}}) + "::__END__")
"""


def step_wipe_sequence_bindings(client: BridgeClient, sequence_path: str,
                                run_token: str) -> None:
    """Idempotency helper: drop any bindings left over from prior runs.

    The sequence asset persists across runs (saved to disk), but every
    run spawns a fresh camera actor in the freshly loaded level, so old
    bindings would accumulate. Wipe before re-binding.
    """
    resp = client.call_tool("execute_unreal_python", {
        "code": _WIPE_BINDINGS_PY.format(seq=sequence_path, token=run_token),
        "capture_output": True,
    })
    payload = unpack_tool_result(resp)
    if not payload.get("ok"):
        raise RuntimeError(f"wipe sequence bindings failed: {payload}")
    info = _scrape_marker(client, "FLYTHROUGH_WIPE_OK::",
                          payload.get("output", ""), token=run_token) or {}
    if not info:
        raise RuntimeError(
            "wipe sequence bindings produced no FLYTHROUGH_WIPE_OK marker "
            f"carrying token {run_token!r}"
        )
    removed_count = info.get("removed")
    print(f"[FLYTHROUGH] step=3.5 tool=execute_unreal_python:wipe_bindings ok=True "
          f"removed={removed_count}")


def step_bind_camera(client: BridgeClient, sequence_path: str) -> str:
    resp = client.call_tool("bind_actor_to_sequence", {
        "sequence_path": sequence_path,
        "actor_name": CAMERA_LABEL,
    })
    if "error" in resp:
        raise RuntimeError(f"bind_actor_to_sequence failed: {resp['error']}")
    payload = unpack_tool_result(resp)
    guid = payload.get("binding_guid")
    log_step(4, "bind_actor_to_sequence", bool(payload.get("ok")),
             binding_guid=guid)
    if not guid:
        raise RuntimeError(f"bind_actor_to_sequence returned no GUID: {payload}")
    return guid


def step_add_keyframes(client: BridgeClient, sequence_path: str, binding_guid: str) -> int:
    """Add the 6 keyframes; returns total keys reported across all calls."""
    total_keys = 0
    for i, (t, loc, rot) in enumerate(keyframes()):
        resp = client.call_tool("sequencer_add_transform_keyframe", {
            "sequence_path": sequence_path,
            "binding_id": binding_guid,
            "time_seconds": t,
            "location": loc,
            "rotation": rot,
            "interpolation": "smart_auto",
            "auto_extend_section": True,
        })
        if "error" in resp:
            raise RuntimeError(f"keyframe @ t={t} failed: {resp['error']}")
        payload = unpack_tool_result(resp)
        ok = bool(payload.get("ok"))
        added = int(payload.get("keys_added", 0))
        total_keys += added
        log_step(5, "sequencer_add_transform_keyframe", ok,
                 t=t, loc=f"({loc[0]:.0f},{loc[1]:.0f},{loc[2]:.0f})",
                 yaw=f"{rot[1]:.1f}",
                 keys_added=added)
        if not ok:
            raise RuntimeError(f"keyframe @ t={t} returned not-ok: {payload}")
    return total_keys


def step_inspect_sequence(client: BridgeClient, sequence_path: str,
                          run_token: str) -> dict:
    """Call inspect_sequence + a follow-up unreal.* probe to count keys."""
    resp = client.call_tool("inspect_sequence", {"path": sequence_path})
    if "error" in resp:
        raise RuntimeError(f"inspect_sequence failed: {resp['error']}")
    payload = unpack_tool_result(resp)

    # inspect_sequence reports bindings + tracks (with section_count) but
    # not per-channel key counts. Probe the transform track directly so
    # we have a definitive number to report as the live verification.
    probe = client.call_tool("execute_unreal_python", {
        "code": _KEY_COUNT_PROBE_PY.format(seq=sequence_path, token=run_token),
        "capture_output": True,
    })
    probe_payload = unpack_tool_result(probe)
    if not probe_payload.get("ok"):
        raise RuntimeError(f"key-count probe failed: {probe_payload}")
    probe_out = probe_payload.get("output", "")
    info = _scrape_marker(client, "KEY_COUNT_OK::", probe_out, token=run_token)
    if not info:
        raise RuntimeError(
            "key-count probe produced no KEY_COUNT_OK marker carrying "
            f"token {run_token!r}"
        )
    total_keys = info.get("total_keys")
    per_channel = info.get("per_channel", {})

    log_step(6, "inspect_sequence", bool(payload),
             bindings=len(payload.get("bindings", []) or []),
             tracks=len(payload.get("tracks", []) or []),
             transform_keys_total=total_keys)
    payload["_probe_total_keys"] = total_keys
    payload["_probe_per_channel"] = per_channel
    return payload


# Probe — count keys on every channel of the camera's 3D Transform Track.
_KEY_COUNT_PROBE_PY = """
import json
import unreal

seq_path = {seq!r}
token = {token!r}
seq = unreal.EditorAssetLibrary.load_asset(seq_path)

per_channel = {{}}
total = 0
for binding in seq.get_bindings():
    tracks = binding.find_tracks_by_exact_type(unreal.MovieScene3DTransformTrack)
    for track in tracks:
        for section in track.get_sections():
            for ch in section.get_all_channels():
                n = ch.get_num_keys()
                per_channel[str(ch.channel_name)] = n
                total += n

unreal.log("KEY_COUNT_OK::" + token + "::" + json.dumps({{
    "total_keys": total,
    "per_channel": per_channel,
}}) + "::__END__")
"""


def step_hero_screenshot(client: BridgeClient, out_path: Path) -> int:
    """Scrub the editor viewport to the t=2s pose and save a hero PNG."""
    # Pick the t=2 keyframe values directly so the hero matches the
    # sequence pose deterministically.
    hero_t = 2.0
    keys = {t: (loc, rot) for t, loc, rot in keyframes()}
    loc, rot = keys[hero_t]

    # Enable game-view to suppress editor gizmos in the captured frame.
    game_view_resp = client.call_tool("execute_unreal_python", {
        "code": (
            "import unreal\n"
            "le = unreal.LevelEditorSubsystem()\n"
            "le.editor_set_game_view(True)\n"
            "le.editor_invalidate_viewports()\n"
        ),
        "capture_output": True,
    })
    game_view_payload = unpack_tool_result(game_view_resp)
    if not game_view_payload.get("ok"):
        raise RuntimeError(f"enable game view failed: {game_view_payload}")

    # Move the editor viewport camera to the hero pose.
    set_resp = client.call_tool("set_camera_transform", {
        "location": {"x": loc[0], "y": loc[1], "z": loc[2]},
        "rotation": {"pitch": rot[0], "yaw": rot[1], "roll": rot[2]},
    })
    if "error" in set_resp:
        raise RuntimeError(f"set_camera_transform failed: {set_resp['error']}")
    unpack_tool_result(set_resp)

    # Invalidate again post-move so the freshly-positioned frame is rendered.
    invalidate_resp = client.call_tool("execute_unreal_python", {
        "code": (
            "import unreal\n"
            "unreal.LevelEditorSubsystem().editor_invalidate_viewports()\n"
        ),
        "capture_output": True,
    })
    invalidate_payload = unpack_tool_result(invalidate_resp)
    if not invalidate_payload.get("ok"):
        raise RuntimeError(f"viewport invalidate failed: {invalidate_payload}")

    # Capture. get_viewport_screenshot takes no width/height args (it
    # uses the viewport's native size); the request's hero-PNG resolution
    # follows the actual editor viewport.
    shot_resp = client.call_tool("get_viewport_screenshot", {})
    if "error" in shot_resp:
        raise RuntimeError(f"get_viewport_screenshot failed: {shot_resp['error']}")
    shot = unpack_tool_result(shot_resp)
    b64 = shot.get("png_base64") or shot.get("image_base64") or ""
    if not b64:
        raise RuntimeError(f"get_viewport_screenshot returned no png_base64: {shot}")
    raw = base64.b64decode(b64)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)
    log_step(7, "get_viewport_screenshot", True,
             width=shot.get("width"), height=shot.get("height"),
             bytes=len(raw), path=str(out_path))
    return len(raw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bridge", default=str(DEFAULT_BRIDGE),
                    help="Path to unreal_claude_mcp_bridge.py")
    args = ap.parse_args()
    bridge_path = Path(args.bridge).resolve()
    if not bridge_path.exists():
        print(f"[FLYTHROUGH] FAIL: bridge not found: {bridge_path}", file=sys.stderr)
        return 2

    today = _dt.date.today().isoformat()
    hero_path = REPO_ROOT / "docs" / "validation" / f"florence-flythrough-hero-{today}.png"

    # Per-invocation correlation token. Threaded into every helper that
    # emits a marker so the LogPython fallback scraper can drop stale
    # markers left over from earlier runs.
    run_token = uuid.uuid4().hex[:12]
    print(f"[FLYTHROUGH] run_token={run_token}")

    try:
        with BridgeClient(bridge_path) as client:
            step_load_level(client)
            seq_path = step_create_or_reuse_sequence(client)
            step_spawn_camera(client, run_token)
            step_wipe_sequence_bindings(client, seq_path, run_token)
            binding_guid = step_bind_camera(client, seq_path)
            total_keys = step_add_keyframes(client, seq_path, binding_guid)
            print(f"[FLYTHROUGH] add_keyframes summary keys_added_total={total_keys}")
            inspect = step_inspect_sequence(client, seq_path, run_token)
            probe_total = inspect.get("_probe_total_keys")
            per_channel = inspect.get("_probe_per_channel") or {}
            print(f"[FLYTHROUGH] live_verification transform_keys_total={probe_total} "
                  f"per_channel={json.dumps(per_channel, sort_keys=True)}")
            png_bytes = step_hero_screenshot(client, hero_path)
            print(f"[FLYTHROUGH] hero_png path={hero_path} bytes={png_bytes}")
    except Exception as e:
        print(f"[FLYTHROUGH] FAIL: {e}", file=sys.stderr)
        return 1

    print("[FLYTHROUGH] DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
