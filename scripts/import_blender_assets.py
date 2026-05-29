#!/usr/bin/env python3
r"""
Blender-asset import driver for the Unreal AI Connection plugin.

What this is
------------
A standalone, stdlib-only MCP client that drives a LIVE Unreal Engine 5.7
editor over the local TCP socket (default 127.0.0.1:18888) to import assets
that were authored OUTSIDE this repo (in Blender, via a separate
Blender-automation MCP server — see docs/ASSET-PIPELINE-BLENDER.md and
docs/adr/ADR-0002-external-asset-authoring-not-bundled.md).

For every `.glb` / `.fbx` in the source scratch dir it:

  1. imports the file through the EXISTING seam — there is no mesh-import
     C++ handler in this plugin and, per ADR-0002, there will not be one;
     assets enter via `execute_unreal_python` running UE's canonical
     `unreal.AssetImportTask` (the same code block documented in
     docs/ASSET-PIPELINE-BLENDER.md),
  2. verifies the imported asset actually exists in the project,
  3. spawns it into a DEDICATED throwaway scratch level so the user's open
     map is never dirtied,
  4. frames the viewport on it as a quick eyeball/proof,

then writes a machine-readable result file and exits non-zero if ANY asset
failed.

The wire protocol is open MCP / JSON-RPC 2.0 over a length-prefixed TCP
frame — no vendor SDK, no model-specific anything. Any MCP-capable client
could drive the same handlers; this script is just a thin reproducible
driver over the documented import workflow. It adds NO plugin code.

Clean-exit note
---------------
The import + spawn happen in a dedicated `/Game/_McpScratch/` level (not
the user's open map). If a stale Restore-Packages dialog ever appears from
a previous unclean run, answer **"Skip Restore"** — these runs build a
throwaway scratch level, never user content. The specific UE clean-exit
APIs used here are written defensively/best-effort and are empirically
validated only in a live proof run (UE is not running while this file is
authored — treat them as proof-pending until then).

Credit
------
The framed JSON-RPC client below (the 8-byte big-endian length-prefix
`_send_framed` / `_recv_exact` / `_recv_framed` / `call`, and the
`unwrap` / `die` / `CaptureError` result-unwrapping) is REPLICATED from
`scripts/capture_demo_gif.py` (which in turn mirrors
`examples/smoke_test.py`, the framing source of truth). It is replicated
rather than imported because cross-importing a sibling script is fragile
(sys.path / packaging) and this driver must stay a single self-contained
file. If the wire framing ever changes, change `examples/smoke_test.py`
FIRST, then mirror it here and in `capture_demo_gif.py`.

Requirements
------------
* A LIVE Unreal Editor with the host UnrealAIConnection plugin built (so
  `execute_unreal_python`, `find_assets`, `spawn_actor`, `focus_actor`
  answer on the socket). A handler that is declared but not linked
  manifests as JSON-RPC error -32601 (method-not-found) — detected here
  with a clear "host plugin not built?" message.
* System Python 3 only — no third-party packages.

Usage
-----
    python scripts/import_blender_assets.py
    python scripts/import_blender_assets.py --host 127.0.0.1 --port 18888
    python scripts/import_blender_assets.py --src-dir F:\blender-exports
    python scripts/import_blender_assets.py --dest /Game/BlenderImports \
        --level /Game/_McpScratch/AssetProof

Environment overrides: UCMCP_HOST, UCMCP_PORT (CLI flags win over env).

Exit code is 0 only if EVERY discovered asset imported, verified, and
spawned; any failure prints a clear FAIL line and exits non-zero. A
per-asset JSON report is always written to
scripts/import_blender_assets_results.json.
"""

import argparse
import json
import os
import socket
import struct  # noqa: F401 -- kept in the stdlib import set per the wire-framing spec
import sys

# ---------------------------------------------------------------------------
# Console encoding hardening (mirrors scripts/capture_demo_gif.py).
#
# Status lines contain non-ASCII glyphs (arrows in progress banners). On
# Windows the default console code page is cp1252 and Python raises
# UnicodeEncodeError mid-run. Force this script's OWN stdout/stderr to UTF-8
# with a safe fallback. This does NOT touch the MCP wire framing, which
# encodes its JSON bodies as explicit UTF-8 bytes independently.
# ---------------------------------------------------------------------------

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------
# Wire-framing helpers.
#
# REPLICATED from scripts/capture_demo_gif.py (its _send_framed /
# _recv_exact / _recv_framed / call), which mirrors examples/smoke_test.py
# (the framing source of truth). Replicated, not imported, so this driver
# stays a single self-contained file. If the framing changes, change
# smoke_test.py FIRST, then mirror it here and in capture_demo_gif.py.
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
    """One framed JSON-RPC round-trip. Mirrors capture_demo_gif.call(): takes
    host/port explicitly and a per-call timeout (an asset import inside the
    editor can take well over 30 s). Transport errors are returned as
    {"_error": ...}; JSON-RPC errors stay in resp["error"]."""
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
                              f"Is the editor open with the built "
                              f"UnrealAIConnection plugin?"}
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
# Result unwrapping (mirrors scripts/capture_demo_gif.py).
#
# A JSON-RPC -32601 means the handler is declared but not linked into the
# module — the host plugin was NOT built with it compiled in. That is a
# common reason this driver fails, so it gets a dedicated, unmistakable
# message and a non-zero exit (handled by die()).
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
    plugin not built), reported verbatim so the operator knows what to fix.
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
                f"NOT built with this handler compiled in. error={err}"
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
# Constants.
# ---------------------------------------------------------------------------

DEFAULT_SRC_DIR = r"F:\blender-exports"
DEFAULT_DEST = "/Game/BlenderImports"
DEFAULT_SCRATCH_LEVEL = "/Game/_McpScratch/AssetProof"
STATIC_MESH_ACTOR_CLASS = "/Script/Engine.StaticMeshActor"
RESULTS_RELPATH = os.path.join("scripts", "import_blender_assets_results.json")

# Success sentinels for the remote snippets (logged via unreal.log and read
# back out of the result blob — the same marker pattern the bridge's camera
# synthetic tools and capture_demo_gif.py already rely on).
SCRATCH_LEVEL_OK = "UCMCP_SCRATCH_LEVEL_OK"
IMPORT_OK = "UCMCP_IMPORT_OK"
ASSET_FOUND = "UCMCP_ASSET_FOUND"

# Importable extensions. .glb is preferred (glTF binary, geometry + textures
# in one file); .fbx is supported for FBX pipelines.
IMPORTABLE_EXTS = (".glb", ".fbx")


def repo_root() -> str:
    """Repo root = parent of the scripts/ dir this file lives in."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sanitize_asset_name(stem: str) -> str:
    """UE asset names must be reasonably tame. Keep alnum + underscore,
    collapse everything else to '_', prefix SM_ if it doesn't start with a
    letter/underscore. Mirrors the SM_<name> convention in
    docs/ASSET-PIPELINE-BLENDER.md."""
    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in stem)
    if not safe or not (safe[0].isalpha() or safe[0] == "_"):
        safe = "SM_" + safe
    return safe


# ---------------------------------------------------------------------------
# Scratch-level + per-asset steps.
#
# Every UE API used here is wrapped defensively: the modern
# LevelEditorSubsystem is tried first with an EditorLevelLibrary fallback
# (the exact proven fallback chain scripts/elven_city_hifi.py uses for
# save_current_level). These clean-exit / level paths are empirically
# validated only in a live proof run; UE is not running while this file is
# authored, so treat them as proof-pending until then.
# ---------------------------------------------------------------------------

def ensure_scratch_level(host: str, port: int, scratch_level: str,
                         request_id: int) -> None:
    """Create (or load) the dedicated scratch level so the import + spawn
    never dirty the user's open map. Fatal only if the editor explicitly
    reports it could not make/load the level; a merely-absent sentinel
    (handler output-shape variance) is downgraded to a warning."""
    # Embed the caller-supplied level path as a JSON-encoded Python string
    # literal (mirrors the filename_lit precedent in import_one, ~L351):
    # json.dumps produces a properly-escaped double-quoted literal that a
    # Windows path with backslashes / embedded quotes round-trips through
    # unchanged and that cannot break out of the string to inject code.
    level_lit = json.dumps(scratch_level)
    snippet = f'''
import unreal
_ok = False
_path = {level_lit}
try:
    _les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    try:
        _les.new_level(_path)
        _ok = True
    except Exception:
        try:
            _les.load_level(_path)
            _ok = True
        except Exception:
            _ok = False
except Exception:
    _ok = False
if not _ok:
    try:
        unreal.EditorLevelLibrary.new_level(_path)
        _ok = True
    except Exception:
        try:
            unreal.EditorLevelLibrary.load_level(_path)
            _ok = True
        except Exception:
            _ok = False
unreal.log("{SCRATCH_LEVEL_OK} " + ("1" if _ok else "0") + " " + _path)
'''
    resp = call(host, port, "execute_unreal_python",
                {"code": snippet}, request_id=request_id, timeout=120.0)
    result = unwrap(resp, "execute_unreal_python[scratch-level]")
    if result.get("ok") is False:
        raise CaptureError(
            f"[scratch-level] execute_unreal_python reported ok=false "
            f"creating {scratch_level}: {result}"
        )
    blob = json.dumps(result, default=str)
    if f"{SCRATCH_LEVEL_OK} 1" in blob:
        print(f"  scratch level ready: {scratch_level}")
    elif f"{SCRATCH_LEVEL_OK} 0" in blob:
        raise CaptureError(
            f"[scratch-level] editor could not create or load "
            f"{scratch_level} (sentinel reported failure). Refusing to "
            f"import into the user's open map. result={result}"
        )
    else:
        print(f"  (note: '{SCRATCH_LEVEL_OK}' sentinel not echoed; "
              f"proceeding — per-asset verification still guards a real "
              f"failure. raw keys: {sorted(result.keys())})")


def import_one(host: str, port: int, src_path: str, dest_path: str,
               asset_name: str, request_id: int) -> str:
    """Import a single mesh file via the EXISTING seam (execute_unreal_python
    running unreal.AssetImportTask — the exact block documented in
    docs/ASSET-PIPELINE-BLENDER.md). Returns the expected /Game asset object
    path on success; raises CaptureError on failure."""
    # Embed the host path as a JSON-encoded (double-quoted, properly
    # backslash-escaped) Python string literal. json.dumps(r"F:\x\y.glb")
    # -> "F:\\x\\y.glb", which as a NORMAL Python string literal in the
    # remote snippet evaluates back to the exact path. (Do NOT use a raw
    # 'r"..."' prefix here: combined with escaped backslashes it would
    # double them and corrupt Windows paths.)
    filename_lit = json.dumps(src_path)
    # Same treatment for the other caller/derived strings embedded into the
    # remote snippet: a JSON-encoded Python string literal round-trips a
    # Windows-path / quote-bearing value unchanged and is injection-proof.
    dest_lit = json.dumps(dest_path)
    name_lit = json.dumps(asset_name)
    asset_obj_lit = json.dumps(f"{dest_path}/{asset_name}")
    snippet = f'''
import unreal
_task = unreal.AssetImportTask()
_task.filename = {filename_lit}
_task.destination_path = {dest_lit}
_task.destination_name = {name_lit}
_task.automated = True
_task.save = True
_imported = []
try:
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([_task])
    try:
        _imported = list(_task.get_editor_property("imported_object_paths") or [])
    except Exception:
        _imported = list(getattr(_task, "imported_object_paths", []) or [])
    unreal.log("{IMPORT_OK} 1 " + {asset_obj_lit})
except Exception as _e:
    unreal.log("{IMPORT_OK} 0 " + str(_e))
'''
    resp = call(host, port, "execute_unreal_python",
                {"code": snippet}, request_id=request_id, timeout=300.0)
    result = unwrap(resp, f"execute_unreal_python[import {asset_name}]")
    if result.get("ok") is False:
        raise CaptureError(
            f"[import {asset_name}] execute_unreal_python reported "
            f"ok=false: {result}"
        )
    blob = json.dumps(result, default=str)
    if f"{IMPORT_OK} 0" in blob:
        raise CaptureError(
            f"[import {asset_name}] AssetImportTask raised inside the "
            f"editor (sentinel reported failure). result={result}"
        )
    if f"{IMPORT_OK} 1" not in blob:
        print(f"  (note: '{IMPORT_OK}' sentinel not echoed for "
              f"{asset_name}; relying on the existence check next. "
              f"raw keys: {sorted(result.keys())})")
    return f"{dest_path}/{asset_name}"


def verify_asset(host: str, port: int, dest_path: str, asset_name: str,
                 request_id: int) -> None:
    """Confirm the imported asset actually exists. Uses find_assets if that
    handler answers; otherwise falls back to an EditorAssetLibrary.does_exist
    probe via execute_unreal_python (robust to a slimmer handler set)."""
    # Preferred: the native find_assets handler scoped to the dest folder.
    fa = call(host, port, "find_assets",
              {"path": dest_path, "recursive": True},
              request_id=request_id, timeout=60.0)
    if "error" not in fa and "_error" not in fa:
        try:
            res = unwrap(fa, "find_assets")
            blob = json.dumps(res, default=str)
            if asset_name in blob:
                print(f"  verified present (find_assets): "
                      f"{dest_path}/{asset_name}")
                return
            # find_assets answered but the asset is not listed — fall
            # through to the authoritative does_exist probe rather than
            # failing on a possibly-narrow find_assets shape.
            print(f"  (find_assets did not list '{asset_name}'; "
                  f"confirming via EditorAssetLibrary.does_exist)")
        except CaptureError:
            pass  # fall through to the does_exist probe

    obj_path = f"{dest_path}/{asset_name}.{asset_name}"
    # JSON-encoded Python string literals for every caller/derived path
    # embedded below (mirrors the filename_lit precedent in import_one):
    # backslash/quote-safe and injection-proof.
    asset_pkg_lit = json.dumps(f"{dest_path}/{asset_name}")
    obj_path_lit = json.dumps(obj_path)
    snippet = f'''
import unreal
_exists = False
try:
    _exists = unreal.EditorAssetLibrary.does_asset_exist({asset_pkg_lit})
except Exception:
    try:
        _exists = unreal.EditorAssetLibrary.does_asset_exist({obj_path_lit})
    except Exception:
        _exists = False
unreal.log("{ASSET_FOUND} " + ("1" if _exists else "0") + " " + {asset_pkg_lit})
'''
    resp = call(host, port, "execute_unreal_python",
                {"code": snippet}, request_id=request_id + 1, timeout=60.0)
    result = unwrap(resp, f"execute_unreal_python[verify {asset_name}]")
    blob = json.dumps(result, default=str)
    if f"{ASSET_FOUND} 1" in blob:
        print(f"  verified present (does_asset_exist): "
              f"{dest_path}/{asset_name}")
        return
    raise CaptureError(
        f"[verify {asset_name}] import call succeeded but the asset is "
        f"NOT in the project at {dest_path}/{asset_name} — import did not "
        f"stick. result={result}"
    )


def spawn_and_frame(host: str, port: int, dest_path: str, asset_name: str,
                    request_id: int) -> str:
    """Spawn a StaticMeshActor referencing the imported mesh into the
    (already-current) scratch level, then frame the viewport on it. Returns
    the spawned actor label. Best-effort framing: a focus failure is logged
    but does NOT fail the asset (the import+spawn already proved out)."""
    actor_label = f"BImp_{asset_name}"
    mesh_obj_path = f"{dest_path}/{asset_name}.{asset_name}"
    # Spawn an empty StaticMeshActor, then assign the StaticMesh component's
    # mesh via execute_unreal_python (set_actor_property cannot resolve a
    # nested component sub-object reliably across builds; the explicit
    # snippet is the robust path and mirrors the doc's guidance).
    spawn = call(host, port, "spawn_actor", {
        "class_path": STATIC_MESH_ACTOR_CLASS,
        "label": actor_label,
        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0},
    }, request_id=request_id, timeout=60.0)
    unwrap(spawn, f"spawn_actor[{actor_label}]")

    # JSON-encoded Python string literals for the caller/derived strings
    # embedded below (mirrors the filename_lit precedent in import_one):
    # a Windows-path / quote-bearing value round-trips unchanged and the
    # snippet cannot be broken out of for injection.
    meshpath_lit = json.dumps(mesh_obj_path)
    label_lit = json.dumps(actor_label)
    assign = f'''
import unreal
_eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
_mesh = unreal.load_asset({meshpath_lit})
_done = False
for _a in _eas.get_all_level_actors():
    try:
        if _a.get_actor_label() == {label_lit}:
            _smc = _a.get_component_by_class(unreal.StaticMeshComponent)
            if _smc and _mesh:
                _smc.set_static_mesh(_mesh)
                _done = True
            break
    except Exception:
        pass
unreal.log("UCMCP_MESH_ASSIGNED " + ("1" if _done else "0") + " " + {label_lit})
'''
    a_resp = call(host, port, "execute_unreal_python",
                  {"code": assign}, request_id=request_id + 1, timeout=120.0)
    a_res = unwrap(a_resp, f"execute_unreal_python[assign {actor_label}]")
    if "UCMCP_MESH_ASSIGNED 1" not in json.dumps(a_res, default=str):
        print(f"  (note: mesh-assign sentinel not confirmed for "
              f"{actor_label}; actor spawned but mesh ref may be unset)")

    # Best-effort frame — never fatal.
    try:
        f_resp = call(host, port, "focus_actor", {"label": actor_label},
                      request_id=request_id + 2, timeout=60.0)
        unwrap(f_resp, f"focus_actor[{actor_label}]")
        print(f"  spawned + framed: {actor_label}")
    except CaptureError as e:
        print(f"  spawned {actor_label}; focus_actor non-fatal miss: {e}")
    return actor_label


# ---------------------------------------------------------------------------
# Pipeline.
# ---------------------------------------------------------------------------

def discover_assets(src_dir: str) -> list[str]:
    """Sorted list of importable files directly in src_dir (non-recursive —
    a flat export scratch dir is the documented convention)."""
    if not os.path.isdir(src_dir):
        raise CaptureError(
            f"source dir not found: {src_dir} — author + export assets "
            f"there first per docs/ASSET-PIPELINE-BLENDER.md, or pass "
            f"--src-dir."
        )
    found = []
    for name in sorted(os.listdir(src_dir)):
        full = os.path.join(src_dir, name)
        if os.path.isfile(full) and name.lower().endswith(IMPORTABLE_EXTS):
            found.append(full)
    return found


def _env_port_default() -> int:
    """Resolve the default port from $UCMCP_PORT with a clear error on a
    malformed value. Without this a non-numeric UCMCP_PORT raised an
    uncaught ValueError while *building* the arg parser (before --port
    could even override it), aborting with an opaque traceback. The CLI
    --port flag still wins — this only supplies argparse's default."""
    raw = os.environ.get("UCMCP_PORT", "18888")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise SystemExit(
            f"!! FAIL: $UCMCP_PORT is not a valid integer: {raw!r}. "
            f"Unset it or pass a numeric --port."
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host",
                    default=os.environ.get("UCMCP_HOST", "127.0.0.1"),
                    help="UE MCP host (default 127.0.0.1 / $UCMCP_HOST)")
    ap.add_argument("--port", type=int,
                    default=_env_port_default(),
                    help="UE MCP port (default 18888 / $UCMCP_PORT)")
    ap.add_argument("--src-dir", dest="src_dir", default=DEFAULT_SRC_DIR,
                    help=(f"dir of exported .glb/.fbx assets to import "
                          f"(default {DEFAULT_SRC_DIR}; gitignored per "
                          f"ADR-0002)"))
    ap.add_argument("--dest", dest="dest", default=DEFAULT_DEST,
                    help=f"UE content path to import into (default {DEFAULT_DEST})")
    ap.add_argument("--level", dest="level", default=DEFAULT_SCRATCH_LEVEL,
                    help=(f"dedicated throwaway scratch level the assets are "
                          f"spawned into so the user's open map is never "
                          f"dirtied (default {DEFAULT_SCRATCH_LEVEL})"))
    ap.add_argument("--results", dest="results", default=None,
                    help=(f"path for the JSON result dump (default "
                          f"<repo>/{RESULTS_RELPATH})"))
    args = ap.parse_args()

    host, port = args.host, args.port
    src_dir = os.path.abspath(args.src_dir)
    results_path = args.results or os.path.join(repo_root(), RESULTS_RELPATH)

    print("Blender-asset import driver")
    print(f"  target     : {host}:{port}")
    print(f"  src dir    : {src_dir}")
    print(f"  dest path  : {args.dest}")
    print(f"  scratch lvl: {args.level}")
    print(f"  results    : {results_path}")

    run_report: dict = {
        "host": host, "port": port, "src_dir": src_dir,
        "dest": args.dest, "scratch_level": args.level,
        "assets": [], "ok": False,
    }

    def _dump_report() -> None:
        try:
            os.makedirs(os.path.dirname(results_path), exist_ok=True)
            with open(results_path, "w", encoding="utf-8") as fh:
                json.dump(run_report, fh, indent=2, default=str)
            print(f"\n  result report written: {results_path}")
        except OSError as e:
            print(f"\n  (could not write result report to {results_path}: "
                  f"{e})", file=sys.stderr)

    try:
        # --- (a) connectivity probe ------------------------------------
        print("\n[1/4] connectivity probe (get_actors_in_level) ...")
        probe = call(host, port, "get_actors_in_level", {}, request_id=1,
                     timeout=30.0)
        if "_error" in probe:
            raise CaptureError(
                f"cannot reach a live editor at {host}:{port}: "
                f"{probe['_error']}"
            )
        unwrap(probe, "get_actors_in_level")
        print("  editor reachable; handler catalog live.")

        # --- (b) discover assets ---------------------------------------
        print(f"\n[2/4] discovering importable assets in {src_dir} ...")
        assets = discover_assets(src_dir)
        if not assets:
            raise CaptureError(
                f"no .glb/.fbx files found in {src_dir} — nothing to "
                f"import. Author + export at least one asset there per "
                f"docs/ASSET-PIPELINE-BLENDER.md."
            )
        print(f"  {len(assets)} asset(s): "
              f"{[os.path.basename(a) for a in assets]}")

        # --- (c) retarget to the dedicated scratch level ---------------
        print(f"\n[3/4] retargeting to scratch level {args.level} (so the "
              f"import + spawn never dirty the open map) ...")
        ensure_scratch_level(host, port, args.level, request_id=2)

        # --- (d) per-asset import → verify → spawn → frame -------------
        print(f"\n[4/4] importing {len(assets)} asset(s) ...")
        failures: list[str] = []
        for idx, src_path in enumerate(assets):
            stem = os.path.splitext(os.path.basename(src_path))[0]
            asset_name = _sanitize_asset_name(stem)
            rid = 100 + idx * 10
            entry: dict = {
                "src": src_path, "asset_name": asset_name,
                "dest": f"{args.dest}/{asset_name}", "ok": False,
            }
            print(f"\n  [{idx + 1}/{len(assets)}] {os.path.basename(src_path)}"
                  f" -> {args.dest}/{asset_name}")
            try:
                asset_path = import_one(host, port, src_path, args.dest,
                                        asset_name, request_id=rid)
                verify_asset(host, port, args.dest, asset_name,
                             request_id=rid + 2)
                actor_label = spawn_and_frame(host, port, args.dest,
                                              asset_name, request_id=rid + 4)
                entry["asset_path"] = asset_path
                entry["actor_label"] = actor_label
                entry["ok"] = True
                print(f"  [{idx + 1}/{len(assets)}] OK")
            except CaptureError as e:
                entry["error"] = str(e)
                failures.append(f"{os.path.basename(src_path)}: {e}")
                print(f"  [{idx + 1}/{len(assets)}] FAILED: {e}",
                      file=sys.stderr)
            except Exception as e:  # pragma: no cover - defensive belt
                entry["error"] = f"unexpected: {e!r}"
                failures.append(f"{os.path.basename(src_path)}: "
                                f"unexpected {e!r}")
                print(f"  [{idx + 1}/{len(assets)}] UNEXPECTED: {e!r}",
                      file=sys.stderr)
            run_report["assets"].append(entry)

        run_report["ok"] = not failures
        _dump_report()

        if failures:
            shown = failures[:8]
            more = (f"\n  ... and {len(failures) - 8} more"
                    if len(failures) > 8 else "")
            die(
                f"{len(failures)}/{len(assets)} asset(s) failed:\n  "
                + "\n  ".join(shown) + more
            )

    except CaptureError as e:
        run_report["ok"] = False
        run_report["fatal"] = str(e)
        _dump_report()
        die(str(e))

    # --- summary -------------------------------------------------------
    print()
    print("=" * 62)
    print("  Blender-asset import summary")
    print("=" * 62)
    n = len(run_report["assets"])
    print(f"  assets imported : {n}/{n} (all OK)")
    print(f"  dest path       : {args.dest}")
    print(f"  scratch level   : {args.level}")
    print(f"  report          : {results_path}")
    print("=" * 62)
    print("\n  RESULT: PASS — all assets imported, verified, spawned.")
    sys.exit(0)


if __name__ == "__main__":
    main()
