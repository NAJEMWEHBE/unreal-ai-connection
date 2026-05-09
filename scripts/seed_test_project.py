"""
Seed the host UE project with throwaway assets so smoke_test.py can exercise
the v0.8.0 (sequencer) and v0.9.0 (materials) runtime paths that would
otherwise be skipped on a fresh project.

Creates (idempotent -- skip if already present):
  /Game/SmokeTest_M    UMaterial with one scalar parameter "Brightness"
  /Game/SmokeTest_MI   UMaterialInstanceConstant parented to SmokeTest_M,
                       with one Brightness=0.5 scalar override
  /Game/SmokeTest_LS   empty ULevelSequence

After seeding, run smoke_test.py with --material-instance and --sequence
pointed at the seeded assets to exercise the inspect_material,
inspect_material_instance, set_mi_parameter, and inspect_sequence handlers
end-to-end.

Run from any Python (does NOT need to be UE's embedded interpreter):

    py scripts\\seed_test_project.py

Override host/port via env: UCMCP_HOST, UCMCP_PORT.

Why Python: this is a "bespoke per-asset operation" per HANDOFF directive #3
-- the kind of thing that routes through execute_unreal_python rather than
getting its own dedicated handler. The bridge is Python; the smoke test is
Python; reaching for any other language to call execute_unreal_python over
TCP would be ceremony for no benefit.
"""

import json
import os
import socket
import sys

HOST = os.environ.get("UCMCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("UCMCP_PORT", "18888"))

MATERIAL_PATH = "/Game/SmokeTest_M"
MATERIAL_INSTANCE_PATH = "/Game/SmokeTest_MI"
LEVEL_SEQUENCE_PATH = "/Game/SmokeTest_LS"


# ---------------------------------------------------------------------------
# Wire-framing helpers (same shape as examples/smoke_test.py).
# ---------------------------------------------------------------------------

def _send_framed(sock: socket.socket, body_bytes: bytes) -> None:
    length_prefix = len(body_bytes).to_bytes(8, byteorder="big", signed=False)
    sock.sendall(length_prefix + body_bytes)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def _recv_framed(sock: socket.socket) -> bytes:
    length_bytes = _recv_exact(sock, 8)
    length = int.from_bytes(length_bytes, byteorder="big", signed=False)
    return _recv_exact(sock, length)


def call(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    """Send a JSON-RPC call over the framed TCP wire. Returns either the
    parsed JSON response (success path) or a dict with `_error` (transport
    failure path: socket refused, timeout, framing, decode). Mirrors the
    smoke_test.py contract so callers can distinguish transport failures
    from JSON-RPC error responses without exception handling at every site.
    """
    msg = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    raw = json.dumps(msg).encode("utf-8")

    s = socket.socket()
    s.settimeout(60)  # asset creation can be slow on cold DDC

    try:
        s.connect((HOST, PORT))
    except (ConnectionRefusedError, OSError) as e:
        s.close()
        return {"_error": f"Cannot reach UE at {HOST}:{PORT}: {e}. "
                          f"Is the editor open with UnrealClaudeMCP enabled?"}

    try:
        _send_framed(s, raw)
        payload_bytes = _recv_framed(s)
    except (ConnectionError, ValueError, socket.timeout) as e:
        return {"_error": f"framing error: {e}"}
    finally:
        s.close()

    try:
        return json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as e:
        return {"_error": f"decode error: {e}"}


# ---------------------------------------------------------------------------
# UE-side seed script. Executed in-editor via execute_unreal_python.
# ---------------------------------------------------------------------------

# Each block is independently idempotent so partial failures (e.g., crash
# between MI creation and save) leave a recoverable state.
SEED_PYTHON = r"""
import unreal

result = {"created": [], "skipped": [], "errors": []}

MATERIAL_PATH = "/Game/SmokeTest_M"
MATERIAL_INSTANCE_PATH = "/Game/SmokeTest_MI"
LEVEL_SEQUENCE_PATH = "/Game/SmokeTest_LS"

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
editor_lib = unreal.EditorAssetLibrary

# 1. Material with a "Brightness" scalar parameter.
if editor_lib.does_asset_exist(MATERIAL_PATH):
    result["skipped"].append(MATERIAL_PATH)
else:
    try:
        mat_factory = unreal.MaterialFactoryNew()
        mat = asset_tools.create_asset("SmokeTest_M", "/Game", unreal.Material, mat_factory)
        # Add a scalar parameter expression. Connection to the material
        # output is unnecessary for inspect_material -- the parameter is
        # declared regardless. We leave it floating to keep the seed minimal.
        scalar = unreal.MaterialEditingLibrary.create_material_expression(
            mat, unreal.MaterialExpressionScalarParameter, -200, 0
        )
        scalar.set_editor_property("parameter_name", "Brightness")
        scalar.set_editor_property("default_value", 1.0)
        unreal.MaterialEditingLibrary.recompile_material(mat)
        editor_lib.save_asset(MATERIAL_PATH)
        result["created"].append(MATERIAL_PATH)
    except Exception as e:
        result["errors"].append(f"{MATERIAL_PATH}: {e}")

# 2. Material instance child, with Brightness=0.5 override applied so
#    inspect_material_instance.scalar_overrides has at least one entry.
#    Note: MaterialInstanceConstantFactoryNew::InitialParent is a bare
#    UPROPERTY() without EditAnywhere/BlueprintReadWrite, so it's not
#    reachable via set_editor_property from Python. We set the parent
#    on the MI's own Parent property instead (UMaterialInstance::Parent
#    at MaterialInstance.h:646 is EditAnywhere, so it IS reflectable).
if editor_lib.does_asset_exist(MATERIAL_INSTANCE_PATH):
    result["skipped"].append(MATERIAL_INSTANCE_PATH)
else:
    try:
        parent = editor_lib.load_asset(MATERIAL_PATH)
        if parent is None:
            raise RuntimeError("parent material not loadable")
        mi_factory = unreal.MaterialInstanceConstantFactoryNew()
        mi = asset_tools.create_asset(
            "SmokeTest_MI", "/Game", unreal.MaterialInstanceConstant, mi_factory
        )
        mi.set_editor_property("parent", parent)
        unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
            mi, "Brightness", 0.5
        )
        editor_lib.save_asset(MATERIAL_INSTANCE_PATH)
        result["created"].append(MATERIAL_INSTANCE_PATH)
    except Exception as e:
        result["errors"].append(f"{MATERIAL_INSTANCE_PATH}: {e}")

# 3. Empty Level Sequence.
if editor_lib.does_asset_exist(LEVEL_SEQUENCE_PATH):
    result["skipped"].append(LEVEL_SEQUENCE_PATH)
else:
    try:
        ls_factory = unreal.LevelSequenceFactoryNew()
        asset_tools.create_asset(
            "SmokeTest_LS", "/Game", unreal.LevelSequence, ls_factory
        )
        editor_lib.save_asset(LEVEL_SEQUENCE_PATH)
        result["created"].append(LEVEL_SEQUENCE_PATH)
    except Exception as e:
        result["errors"].append(f"{LEVEL_SEQUENCE_PATH}: {e}")

# Emit a single-line JSON marker via unreal.log so the bridge can fish
# it out of LogPython entries via get_log_lines. ExecuteFile mode does
# not capture print/eval output back through FPythonCommandEx, so the
# log path is the reliable round-trip channel.
import json as _json
unreal.log("__SEED_RESULT__{token}__" + _json.dumps(result) + "__SEED_END__")
"""


def main() -> int:
    print(f"Seeding {HOST}:{PORT} with smoke-test fixtures...")

    # Per-run token disambiguates this run's marker from any leftover
    # markers in the log ring buffer from prior seed attempts.
    import uuid
    token = uuid.uuid4().hex[:8]
    code = SEED_PYTHON.replace("{token}", token)

    resp = call("execute_unreal_python", {"code": code})

    if "_error" in resp:
        print(f"ERROR: transport failure: {resp['_error']}", file=sys.stderr)
        return 2

    if "error" in resp:
        print(f"ERROR: execute_unreal_python failed: {resp['error']}", file=sys.stderr)
        return 2

    if "result" not in resp:
        print(f"ERROR: malformed response: {resp}", file=sys.stderr)
        return 2

    py_result = resp["result"]
    if not py_result.get("ok"):
        print(f"ERROR: execute_unreal_python returned not-ok: {py_result}", file=sys.stderr)
        return 2

    # Fish the structured seed-result out of LogPython lines via get_log_lines.
    # ExecuteFile mode does not return script stdout/eval-result through
    # FPythonCommandEx, so we round-trip via the log capture system.
    log_resp = call("get_log_lines", {"count": 200, "category_filter": "LogPython"})
    if "_error" in log_resp:
        print(f"ERROR: get_log_lines transport failure: {log_resp['_error']}", file=sys.stderr)
        return 2
    if "error" in log_resp or "result" not in log_resp:
        print(f"ERROR: get_log_lines failed: {log_resp}", file=sys.stderr)
        return 2

    lines = log_resp["result"].get("lines", [])
    start_marker = f"__SEED_RESULT__{token}__"
    end_marker = "__SEED_END__"
    seed_json = None
    for line in lines:
        msg = line.get("message", "") if isinstance(line, dict) else str(line)
        s = msg.find(start_marker)
        e = msg.find(end_marker)
        if s >= 0 and e >= 0 and e > s:
            seed_json = msg[s + len(start_marker):e]
            break

    if seed_json is None:
        print(
            f"ERROR: seed-result marker '{start_marker}' not found in last "
            f"{len(lines)} LogPython lines.",
            file=sys.stderr,
        )
        return 2

    try:
        seed = json.loads(seed_json)
    except json.JSONDecodeError as ex:
        print(f"ERROR: cannot parse seed JSON: {ex}\nraw: {seed_json}", file=sys.stderr)
        return 2

    for path in seed["created"]:
        print(f"  created  {path}")
    for path in seed["skipped"]:
        print(f"  skipped  {path} (already exists)")
    for err in seed["errors"]:
        print(f"  ERROR    {err}", file=sys.stderr)

    if seed["errors"]:
        print(f"\n{len(seed['errors'])} seed error(s) -- run aborted.", file=sys.stderr)
        return 1

    total = len(seed["created"]) + len(seed["skipped"])
    if total != 3:
        print(f"\nERROR: expected 3 assets seeded or skipped, got {total}.", file=sys.stderr)
        return 2

    print(
        f"\nSeed complete: {len(seed['created'])} created, "
        f"{len(seed['skipped'])} already-present.\n"
        f"\nNext: py examples\\smoke_test.py "
        f"--material-instance {MATERIAL_INSTANCE_PATH} "
        f"--sequence {LEVEL_SEQUENCE_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
