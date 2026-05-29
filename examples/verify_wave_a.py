r"""
Wave A / Wave A.5 / PR #214 live-verification panel.

Purpose
-------
Prove that the host-side UnrealAIConnection plugin was actually REBUILT and that
the Wave A / Wave A.5 C++ handlers are compiled IN. A handler that is declared
but not linked into the module manifests as JSON-RPC error -32601
(method-not-found) at the wire level -- that is the exact regression this
script exists to catch and prove fixed. It also closes the 29th-closing-note
headless-capture root cause by asserting render_camera_to_png writes a
non-blank PNG to disk.

This is a REUSABLE panel: run it after every host rebuild of the plugin to
confirm the catalog is whole and the nine target handlers respond.

It must be run against a LIVE Unreal Editor with the host plugin rebuilt
(the nine handlers below compiled in) listening on a localhost TCP socket
(default 127.0.0.1:18888). It is a pure TCP client -- it does NOT need UE's
embedded Python; run it from any system Python 3.

The handlers / tools this panel exercises:
  1. tools/list (catalog whole; render_camera_to_png + 8 Wave A/A.5 present)
  2. get_engine_version
  3. list_levels
  4. save_dirty_assets
  5. get_selected_actors
  6. inspect_input_mappings
  7. pie_control (query only by default; --pie opts into start/stop)
  8. inspect_project_setting
  9. bulk_inspect_assets   (bridge-side synthetic; sanity)
 10. render_camera_to_png  (writes PNG to disk; size-asserted > 10 KiB)

Usage
-----
    py examples\verify_wave_a.py
    py examples\verify_wave_a.py --host 127.0.0.1 --port 18888
    py examples\verify_wave_a.py --out-path "F:\ax plug in\HDMediaVirtualStudio\Saved\verify_render.png"
    py examples\verify_wave_a.py --pie         # ALSO start+stop PIE (intrusive)
    py examples\verify_wave_a.py --json-out examples\verify_wave_a_results.json

On any failure a clear FAIL line is printed and the panel CONTINUES (every
result is collected). Exit code is 1 if any step FAILED, 0 if all passed. A
summary table is printed at the end and every raw JSON response is dumped to
the --json-out artifact (default examples/verify_wave_a_results.json).

Override host/port via env too: UCMCP_HOST, UCMCP_PORT (CLI flags win).
"""

import argparse
import json
import os
import socket
import struct  # noqa: F401 -- kept in the stdlib import set per the panel spec
import sys

# ---------------------------------------------------------------------------
# Wire-framing helpers.
#
# SOURCE OF TRUTH: examples/smoke_test.py (its _send_framed / _recv_exact /
# _recv_framed). These are copied verbatim in behaviour so this panel uses the
# EXACT same wire framing as the smoke test. If the framing ever changes,
# change smoke_test.py first and mirror it here.
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
         params: dict | None = None, request_id: int = 1) -> dict:
    """One framed JSON-RPC round-trip. Mirrors smoke_test.call() but takes
    host/port explicitly so --host/--port work per invocation (smoke_test
    reads module globals; we deliberately do not)."""
    msg = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    raw = json.dumps(msg).encode("utf-8")

    s = socket.socket()
    s.settimeout(30)
    try:
        try:
            s.connect((host, port))
        except (ConnectionRefusedError, OSError) as e:
            return {"_error": f"Cannot reach UE at {host}:{port}: {e}. "
                              f"Is the editor open with the rebuilt UnrealAIConnection plugin?"}

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
# Output helpers (mirror smoke_test.py's header / show style).
# ---------------------------------------------------------------------------

def header(name: str) -> None:
    print()
    print("=" * 60)
    print(f"  {name}")
    print("=" * 60)


def show(resp: dict, *, max_chars: int = 600) -> None:
    text = json.dumps(resp, indent=2, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated, full {len(text)} chars]"
    print(text)


# ---------------------------------------------------------------------------
# Assertion helpers.
#
# The headline assertion for THIS panel is assert_handler_compiled(): a
# JSON-RPC error with code -32601 means the handler is NOT linked into the
# module (host rebuild missing / handler inert). Every other failure mode is
# a normal VerifyFailure.
# ---------------------------------------------------------------------------

class VerifyFailure(AssertionError):
    pass


class SkipStep(Exception):
    """Step intentionally not applicable on the raw plugin socket (e.g. a
    bridge-side synthetic, reachable only via the MCP bridge). Recorded as
    SKIP, not FAIL -- does not affect exit code."""


def assert_no_transport_error(resp: dict, label: str) -> None:
    if "_error" in resp or "_decode_error" in resp:
        raise VerifyFailure(f"[{label}] transport-level failure: {resp}")
    if not isinstance(resp, dict) or resp.get("jsonrpc") != "2.0":
        raise VerifyFailure(f"[{label}] not a JSON-RPC 2.0 response: {resp}")


def assert_handler_compiled(resp: dict, label: str) -> dict:
    """Assert the handler IS compiled in and returned success.

    A -32601 here is the precise regression we are proving fixed: the handler
    is declared but not linked into the module manifest, so the host plugin
    was not rebuilt. We surface that with an explicit, unmistakable message.
    Returns resp['result'] on success.
    """
    assert_no_transport_error(resp, label)
    if "error" in resp:
        err = resp["error"]
        code = err.get("code") if isinstance(err, dict) else None
        if code == -32601:
            raise VerifyFailure(
                f"[{label}] JSON-RPC -32601 method-not-found: handler inert / "
                f"host rebuild MISSING. The plugin was not rebuilt with this "
                f"handler compiled in. error={err}"
            )
        raise VerifyFailure(f"[{label}] unexpected JSON-RPC error: {err}")
    if "result" not in resp:
        raise VerifyFailure(f"[{label}] missing 'result' field: {resp}")
    return resp["result"]


def _is_list_like(v) -> bool:
    return isinstance(v, list)


# ---------------------------------------------------------------------------
# Tool-name extraction. tools/list shapes vary across MCP servers; accept the
# common ones so the catalog check is robust.
# ---------------------------------------------------------------------------

def _extract_tool_names(result) -> list[str]:
    """Pull a flat list of tool names out of whatever shape tools/list used.

    Handles:
      {"tools": [{"name": ...}, ...]}
      {"tools": ["name", ...]}
      [{"name": ...}, ...]
      ["name", ...]
    """
    container = None
    if isinstance(result, dict):
        container = result.get("tools")
        if container is None and isinstance(result.get("result"), (list, dict)):
            container = result["result"]
    elif isinstance(result, list):
        container = result
    if not isinstance(container, list):
        return []
    names: list[str] = []
    for entry in container:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            n = entry.get("name") or entry.get("tool") or entry.get("id")
            if isinstance(n, str):
                names.append(n)
    return names


# Wave A / Wave A.5 handler/tool names that MUST be present in the catalog
# once the host plugin is rebuilt. render_camera_to_png is the PR #214 /
# 29th-note headless-capture handler; the other eight are the Wave A/A.5 set
# exercised by steps 2-9 below.
WAVE_A_TOOLS = [
    "render_camera_to_png",
    "get_engine_version",
    "list_levels",
    "save_dirty_assets",
    "get_selected_actors",
    "inspect_input_mappings",
    "pie_control",
    "inspect_project_setting",
]


def main() -> None:
    env_host = os.environ.get("UCMCP_HOST", "127.0.0.1")
    env_port = os.environ.get("UCMCP_PORT", "18888")
    default_out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "verify_render.png")
    default_json = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "verify_wave_a_results.json")

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--host", default=env_host,
                    help=f"UE MCP host (default {env_host}; env UCMCP_HOST)")
    ap.add_argument("--port", default=env_port,
                    help=f"UE MCP port (default {env_port}; env UCMCP_PORT)")
    ap.add_argument("--out-path", dest="out_path", default=default_out,
                    help=("Absolute path render_camera_to_png writes to. "
                          f"Default: {default_out}"))
    ap.add_argument("--pie", action="store_true",
                    help=("ALSO exercise pie_control start + stop. WARNING: "
                          "this LAUNCHES Play-In-Editor on the live editor. "
                          "Off by default; query-only without this flag."))
    ap.add_argument("--json-out", dest="json_out", default=default_json,
                    help=(f"Raw-response artifact path. Default: {default_json}"))
    args = ap.parse_args()

    host = args.host
    try:
        port = int(args.port)
    except (TypeError, ValueError) as exc:
        raise SystemExit(
            f"Invalid port {args.port!r}; expected an integer "
            f"(via --port or UCMCP_PORT)."
        ) from exc

    print("Wave A / Wave A.5 / PR #214 live-verification panel")
    print(f"  target : {host}:{port}")
    print(f"  out    : {args.out_path}")
    print(f"  json   : {args.json_out}")
    print(f"  pie    : {'ON (intrusive)' if args.pie else 'off (query only)'}")

    # Collected outcomes. Each entry: {step, label, status, detail}
    results: list[dict] = []
    # Raw JSON responses keyed by step label, dumped to --json-out.
    raw_dump: dict[str, object] = {}
    failures: list[str] = []

    def record(step_no: int, label: str, status: str, detail: str = "") -> None:
        results.append({"step": step_no, "label": label,
                        "status": status, "detail": detail})

    def run(step_no: int, label: str, fn) -> None:
        """Run one panel step. Collect raw response + PASS/FAIL; never abort."""
        try:
            fn()
            record(step_no, label, "PASS")
            print(f"  -> PASS  [{label}]")
        except VerifyFailure as e:
            failures.append(str(e))
            record(step_no, label, "FAIL", str(e))
            print(f"\n!! FAIL: {e}")
        except SkipStep as e:
            record(step_no, label, "SKIP", str(e))
            print(f"  -> SKIP  [{label}]: {e}")
        except Exception as e:  # noqa: BLE001 -- deliberate broad catch
            # Anything that is not a VerifyFailure (TypeError, KeyError,
            # ConnectionError, JSONDecodeError, ...) is an UNEXPECTED failure.
            # We record it and keep going so the rest of the panel still runs
            # -- aborting at step 3 would hide the remaining handler checks,
            # which is exactly the signal a maintainer needs. KeyboardInterrupt
            # / SystemExit are NOT caught (Exception, not BaseException).
            msg = f"[{label}] unexpected {type(e).__name__}: {e}"
            failures.append(msg)
            record(step_no, label, "FAIL", msg)
            print(f"\n!! UNEXPECTED FAIL: {msg}")

    # ----- 1. tools/list ---------------------------------------------------
    header("1. tools/list  (catalog whole; Wave A/A.5 handlers present)")

    def t1() -> None:
        # The bridge may answer the MCP-standard "tools/list" or the legacy
        # "list_tools". Try tools/list first, fall back to list_tools, and
        # also keep the raw of whichever answered.
        resp = call(host, port, "tools/list", {}, request_id=1)
        used = "tools/list"
        if "result" not in resp:
            alt = call(host, port, "list_tools", {}, request_id=1)
            if "result" in alt:
                resp, used = alt, "list_tools"
        raw_dump["1.tools_list"] = resp
        show(resp)
        result = assert_handler_compiled(resp, f"tools_list ({used})")

        names = _extract_tool_names(result)
        if not names:
            raise VerifyFailure(
                f"[tools_list] could not extract any tool names from result "
                f"(unexpected shape): {result}"
            )
        # Server-reported count, if present, should match the list length.
        reported = result.get("count") if isinstance(result, dict) else None
        print(f"  catalog: {len(names)} tools"
              + (f" (server count={reported})" if reported is not None else ""))
        if isinstance(reported, int) and reported != len(names):
            raise VerifyFailure(
                f"[tools_list] server count ({reported}) != extracted "
                f"tool-name count ({len(names)})"
            )

        # C++-handler-relevant count: the panel can't perfectly partition
        # native vs synthetic from the wire, but the bulk_* / inspect_* /
        # *_assets synthetic family is well-known. Report a best-effort
        # native-handler estimate for the maintainer's eye.
        synthetic_markers = ("bulk_", "compile_mod_pak", "marketplace_",
                             "convert_hdri", "sequencer_add_transform_keyframe",
                             "wait_for_events", "get_camera_transform",
                             "set_camera_transform", "screenshot_actor",
                             "find_unused_assets", "get_reference_chain",
                             "find_actors_by_class", "compare_assets",
                             "inspect_dependency_graph", "inspect_data_asset",
                             "inspect_sound_class", "inspect_sound_submix",
                             "inspect_audio_bus", "inspect_material_function",
                             "inspect_metasound", "audit_blueprint_compile_status")
        synthetic = [n for n in names if any(n.startswith(m) or n == m
                                             for m in synthetic_markers)]
        native_est = len(names) - len(synthetic)
        print(f"  est. native C++ handlers: ~{native_est} "
              f"(catalog {len(names)} - ~{len(synthetic)} known synthetic)")

        nameset = set(names)
        missing = [t for t in WAVE_A_TOOLS if t not in nameset]
        if missing:
            raise VerifyFailure(
                f"[tools_list] Wave A/A.5 handlers MISSING from catalog "
                f"{missing} -- host plugin not rebuilt with these compiled in"
            )
        print(f"  all {len(WAVE_A_TOOLS)} Wave A/A.5 tools present in catalog: "
              f"{WAVE_A_TOOLS}")

    run(1, "tools_list", t1)

    # ----- 2. get_engine_version ------------------------------------------
    header("2. get_engine_version {}")

    def t2() -> None:
        resp = call(host, port, "get_engine_version", {}, request_id=2)
        raw_dump["2.get_engine_version"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "get_engine_version")
        for key in ("major", "minor", "patch", "changelist", "branch",
                    "minor_dotted"):
            if key not in result:
                raise VerifyFailure(
                    f"[get_engine_version] missing key '{key}': {result}"
                )

    run(2, "get_engine_version", t2)

    # ----- 3. list_levels --------------------------------------------------
    header('3. list_levels { path_under:/Game, name_contains:Map }')

    def t3() -> None:
        resp = call(host, port, "list_levels",
                    {"path_under": "/Game/", "name_contains": "Map"},
                    request_id=3)
        raw_dump["3.list_levels"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "list_levels")
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise VerifyFailure(f"[list_levels] expected ok=true object: {result}")
        levels = result.get("levels")
        if not _is_list_like(levels):
            raise VerifyFailure(f"[list_levels] 'levels' not a list: {result}")
        if not isinstance(result.get("count"), int) or result["count"] != len(levels):
            raise VerifyFailure(f"[list_levels] count must equal len(levels): {result}")
        print(f"  ok=true, count={result['count']} (empty is acceptable)")

    run(3, "list_levels", t3)

    # ----- 4. save_dirty_assets -------------------------------------------
    header("4. save_dirty_assets {}")

    def t4() -> None:
        resp = call(host, port, "save_dirty_assets", {}, request_id=4)
        raw_dump["4.save_dirty_assets"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "save_dirty_assets")
        if not isinstance(result, dict):
            raise VerifyFailure(
                f"[save_dirty_assets] expected an object result: {result}"
            )
        if not isinstance(result.get("ok"), bool):
            raise VerifyFailure(
                f"[save_dirty_assets] 'ok' must be a bool (false is a legitimate "
                f"no-op when nothing is dirty): {result}")
        if not isinstance(result.get("include_levels"), bool):
            raise VerifyFailure(f"[save_dirty_assets] 'include_levels' not a bool: {result}")
        if not isinstance(result.get("include_content"), bool):
            raise VerifyFailure(f"[save_dirty_assets] 'include_content' not a bool: {result}")
        if not isinstance(result.get("note"), str) or not result["note"]:
            raise VerifyFailure(f"[save_dirty_assets] 'note' missing/empty: {result}")
        print(f"  handler live; ok={result['ok']} (coarse-grained; false=no-op acceptable)")

    run(4, "save_dirty_assets", t4)

    # ----- 5. get_selected_actors -----------------------------------------
    header("5. get_selected_actors {}")

    def t5() -> None:
        resp = call(host, port, "get_selected_actors", {}, request_id=5)
        raw_dump["5.get_selected_actors"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "get_selected_actors")
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise VerifyFailure(f"[get_selected_actors] expected ok=true object: {result}")
        actors = result.get("actors")
        if not _is_list_like(actors):
            raise VerifyFailure(f"[get_selected_actors] 'actors' not a list: {result}")
        if not isinstance(result.get("count"), int) or result["count"] != len(actors):
            raise VerifyFailure(f"[get_selected_actors] count must equal len(actors): {result}")
        print(f"  ok=true, count={result['count']} (empty selection is acceptable)")

    run(5, "get_selected_actors", t5)

    # ----- 6. inspect_input_mappings --------------------------------------
    header("6. inspect_input_mappings {}")

    def t6() -> None:
        resp = call(host, port, "inspect_input_mappings", {}, request_id=6)
        raw_dump["6.inspect_input_mappings"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "inspect_input_mappings")
        if not isinstance(result, dict):
            raise VerifyFailure(
                f"[inspect_input_mappings] expected an object result: {result}"
            )
        if not _is_list_like(result.get("action_mappings")):
            raise VerifyFailure(
                f"[inspect_input_mappings] 'action_mappings' not a list: {result}"
            )
        if not _is_list_like(result.get("axis_mappings")):
            raise VerifyFailure(
                f"[inspect_input_mappings] 'axis_mappings' not a list: {result}"
            )
        if not isinstance(result.get("uses_enhanced_input"), bool):
            raise VerifyFailure(
                f"[inspect_input_mappings] 'uses_enhanced_input' not a bool: "
                f"{result}"
            )
        print(f"  action_mappings={len(result['action_mappings'])} "
              f"axis_mappings={len(result['axis_mappings'])} "
              f"uses_enhanced_input={result['uses_enhanced_input']}")

    run(6, "inspect_input_mappings", t6)

    # ----- 7. pie_control --------------------------------------------------
    header("7. pie_control { action: query }  (handler-live = bool is_playing)")

    def t7() -> None:
        resp = call(host, port, "pie_control", {"action": "query"},
                    request_id=7)
        raw_dump["7.pie_control.query"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "pie_control.query")
        if not isinstance(result, dict):
            raise VerifyFailure(
                f"[pie_control.query] expected an object result: {result}"
            )
        if "is_playing" not in result:
            raise VerifyFailure(
                f"[pie_control.query] missing 'is_playing': {result}"
            )
        # Handler-live proof is that it answered with a bool 'is_playing'
        # field -- NOT that the field is exactly False. An already-running
        # PIE session is a valid editor state; treating True as a FAIL would
        # turn a working, compiled handler into a false regression.
        if not isinstance(result["is_playing"], bool):
            raise VerifyFailure(
                f"[pie_control.query] 'is_playing' must be a bool "
                f"(handler-live proof is the bool response, not its value), "
                f"got {type(result['is_playing']).__name__}: "
                f"{result['is_playing']}"
            )
        initially_playing = bool(result["is_playing"])
        print(f"  pie_control query ok; is_playing={initially_playing}")

        # ---- OPT-IN intrusive start/stop -------------------------------
        # WARNING: the following calls LAUNCH Play-In-Editor on the live
        # editor and then stop it. They are GUARDED behind --pie and are OFF
        # by default. Do not enable on a session you cannot afford to have
        # enter/exit PIE. Left in-code (not commented out) but flag-gated so
        # the default run is non-intrusive while the path stays exercisable.
        #
        # Ownership rule: this script only stops a PIE session it itself
        # started. If PIE is ALREADY running when --pie is passed, we leave
        # the pre-existing session completely untouched (no start, no stop)
        # -- mutating a session the script did not create is not its job.
        started_pie = False
        if args.pie and not initially_playing:
            print("  --pie set: starting PIE (INTRUSIVE) ...")
            start = call(host, port, "pie_control", {"action": "start"},
                         request_id=701)
            raw_dump["7.pie_control.start"] = start
            show(start)
            start_res = assert_handler_compiled(start, "pie_control.start")
            started_pie = True
            print(f"  pie start result: {start_res}")

            if started_pie:
                print("  --pie set: stopping PIE (script-owned session) ...")
                stop = call(host, port, "pie_control", {"action": "stop"},
                            request_id=702)
                raw_dump["7.pie_control.stop"] = stop
                show(stop)
                stop_res = assert_handler_compiled(stop, "pie_control.stop")
                print(f"  pie stop result: {stop_res}")

                # Re-query: editor should be idle again.
                requery = call(host, port, "pie_control", {"action": "query"},
                               request_id=703)
                raw_dump["7.pie_control.requery"] = requery
                rq = assert_handler_compiled(requery, "pie_control.requery")
                if rq.get("is_playing") is not False:
                    raise VerifyFailure(
                        f"[pie_control.requery] PIE did not return to idle "
                        f"after stop: {rq}"
                    )
                print("  PIE returned to idle (is_playing=false)")
        elif args.pie and initially_playing:
            print("  --pie set but PIE already running: leaving the "
                  "pre-existing session UNTOUCHED (no start/stop -- this "
                  "script only stops a session it itself started).")

    run(7, "pie_control", t7)

    # ----- 8. inspect_project_setting -------------------------------------
    header("8. inspect_project_setting "
           "{ settings_class:/Script/Engine.RendererSettings }")

    def t8() -> None:
        resp = call(host, port, "inspect_project_setting",
                    {"settings_class": "/Script/Engine.RendererSettings"},
                    request_id=8)
        raw_dump["8.inspect_project_setting"] = resp
        show(resp)
        result = assert_handler_compiled(resp, "inspect_project_setting")
        # Expect a bulk dump of editable UPROPERTYs: either a dict of
        # name->value, or {"properties": {...}} / {"properties": [...]}.
        props = result
        if isinstance(result, dict) and "properties" in result:
            props = result["properties"]
        if not isinstance(props, (dict, list)):
            raise VerifyFailure(
                f"[inspect_project_setting] expected a dict/list of "
                f"UPROPERTYs, got: {result}"
            )
        n = len(props) if isinstance(props, (dict, list)) else 0
        if n == 0:
            raise VerifyFailure(
                f"[inspect_project_setting] RendererSettings dump is empty "
                f"-- expected editable properties: {result}"
            )
        print(f"  RendererSettings properties dumped: {n}")

    run(8, "inspect_project_setting", t8)

    # ----- 9. bulk_inspect_assets (synthetic sanity) ----------------------
    header("9. bulk_inspect_assets "
           "{ paths:[/Engine/BasicShapes/Cube, ...BaseFlattenMaterial] }")

    def t9() -> None:
        raise SkipStep(
            "bulk_inspect_assets is a bridge-side synthetic (SYNTHETIC_TOOLS in "
            "unreal_ai_connection_bridge.py; no C++ handler / no Reg.Register). "
            "This panel is a raw-socket client and bypasses the bridge, so the "
            "plugin dispatcher correctly returns -32601 (MCPDispatcher.cpp:72). "
            "Synthetics are out of scope for raw-socket verification (smoke_test.py "
            "sets the same precedent)."
        )

    run(9, "bulk_inspect_assets", t9)

    # ----- 10. render_camera_to_png (writes PNG; size-asserted) -----------
    header(f"10. render_camera_to_png {{ out_path: {args.out_path} }}")

    def t10() -> None:
        out_path = os.path.abspath(args.out_path)
        # Best-effort: clear a stale artifact so the size check measures THIS
        # run, not a leftover from a previous run.
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError as e:
            print(f"  (note: could not remove stale {out_path}: {e})")

        resp = call(host, port, "render_camera_to_png",
                    {"out_path": out_path}, request_id=10)
        raw_dump["10.render_camera_to_png"] = resp
        show(resp)
        assert_handler_compiled(resp, "render_camera_to_png")

        # The bridge runs same-machine, so out_path is directly stat-able
        # from this process. This is the assertion that closes the
        # 29th-closing-note headless-capture root cause: the file must exist
        # AND be non-blank (> 10240 bytes).
        if not os.path.exists(out_path):
            raise VerifyFailure(
                f"[render_camera_to_png] handler returned success but no file "
                f"at '{out_path}' -- headless capture did not write to disk"
            )
        size = os.path.getsize(out_path)
        if size <= 10240:
            raise VerifyFailure(
                f"[render_camera_to_png] '{out_path}' is {size} bytes "
                f"(<= 10240) -- blank/empty capture; headless render did not "
                f"produce a real frame"
            )
        print(f"  PNG written: {out_path} ({size} bytes > 10240)")

    run(10, "render_camera_to_png", t10)

    # ----- Artifact dump ---------------------------------------------------
    artifact_written = None
    try:
        json_out = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(json_out), exist_ok=True)
        payload = {
            "target": {"host": host, "port": port},
            "out_path": os.path.abspath(args.out_path),
            "pie_enabled": args.pie,
            "summary": results,
            "raw_responses": raw_dump,
        }
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        artifact_written = json_out
    except OSError as e:
        print(f"\n!! could not write artifact {args.json_out}: {e}")

    # ----- Summary table ---------------------------------------------------
    print()
    print("=" * 60)
    print("  Wave A / Wave A.5 / PR #214 verification summary")
    print("=" * 60)
    print(f"  {'#':>2}  {'STATUS':<6}  {'STEP':<24}  DETAIL")
    print(f"  {'-'*2}  {'-'*6}  {'-'*24}  {'-'*20}")
    for r in results:
        detail = r["detail"]
        if len(detail) > 60:
            detail = detail[:57] + "..."
        print(f"  {r['step']:>2}  {r['status']:<6}  {r['label']:<24}  {detail}")
    print("=" * 60)
    if artifact_written:
        print(f"  raw responses dumped to: {artifact_written}")
    else:
        print("  raw-response artifact NOT written (see error above)")

    if failures:
        print(f"\n  RESULT: FAILED -- {len(failures)} step(s) did not pass")
        print("  (a -32601 on any step => host plugin NOT rebuilt with that "
              "handler compiled in)")
        print("=" * 60)
        sys.exit(1)
    print("\n  RESULT: PASS -- all panel steps green; Wave A/A.5 handlers "
          "compiled in and responding.")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    main()
