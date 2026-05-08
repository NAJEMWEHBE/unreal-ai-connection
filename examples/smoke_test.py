"""
Smoke test the UnrealClaudeMCP server on 127.0.0.1:18888.

Run from any Python (does NOT need to be UE's embedded interpreter -- this is
a pure TCP client that hits the server FROM OUTSIDE):

    py examples\smoke_test.py

Optional: exercise the widget-tree round-trip against a Widget Blueprint of
your choice. The test creates a root VerticalBox + a TextBlock named "Title"
and reads them back. Use a *throwaway* widget BP -- this WILL mutate it:

    py examples\smoke_test.py --widget /Game/UI/WBP_SmokeTest.WBP_SmokeTest

The level-load test is also opt-in for the same reason:

    py examples\smoke_test.py --level /Game/Maps/MyMap

Override host/port via env: UCMCP_HOST, UCMCP_PORT.
"""

import argparse
import json
import os
import socket
import sys

HOST = os.environ.get("UCMCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("UCMCP_PORT", "18888"))


# ---------------------------------------------------------------------------
# Wire-framing helpers  (v0.5.0)
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


def call(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    raw = json.dumps(msg).encode("utf-8")

    s = socket.socket()
    s.settimeout(15)
    try:
        s.connect((HOST, PORT))
    except (ConnectionRefusedError, OSError) as e:
        return {"_error": f"Cannot reach UE at {HOST}:{PORT}: {e}. Is the editor open with UnrealClaudeMCP enabled?"}

    try:
        _send_framed(s, raw)
        payload_bytes = _recv_framed(s)
    except (ConnectionError, ValueError, socket.timeout) as e:
        s.close()
        return {"_error": f"framing error: {e}"}
    s.close()

    payload = payload_bytes.decode("utf-8", errors="replace")
    if not payload:
        return {"_raw": "", "_error": "empty response"}
    try:
        return json.loads(payload)
    except json.JSONDecodeError as e:
        return {"_raw": payload[:500], "_decode_error": str(e)}


def header(name: str):
    print()
    print("=" * 60)
    print(f"  {name}")
    print("=" * 60)


def show(resp: dict, *, max_chars: int = 600):
    text = json.dumps(resp, indent=2)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [truncated, full {len(text)} chars]"
    print(text)


# ---------------------------------------------------------------------------
# Assertion helpers. These convert the previously print-only smoke test into
# something that fails loudly on regression. We still print so a human run
# stays readable; a non-zero exit code now surfaces broken behaviour to CI
# / the caller.
# ---------------------------------------------------------------------------

class SmokeFailure(AssertionError):
    pass


def assert_no_transport_error(resp: dict, label: str) -> None:
    if "_error" in resp or "_decode_error" in resp:
        raise SmokeFailure(f"[{label}] transport-level failure: {resp}")
    if not isinstance(resp, dict) or resp.get("jsonrpc") != "2.0":
        raise SmokeFailure(f"[{label}] not a JSON-RPC 2.0 response: {resp}")


def assert_ok(resp: dict, label: str) -> dict:
    """Assert success and return resp['result']."""
    assert_no_transport_error(resp, label)
    if "error" in resp:
        raise SmokeFailure(f"[{label}] unexpected JSON-RPC error: {resp['error']}")
    if "result" not in resp:
        raise SmokeFailure(f"[{label}] missing 'result' field: {resp}")
    return resp["result"]


def assert_error_code(resp: dict, code: int, label: str) -> None:
    assert_no_transport_error(resp, label)
    if "error" not in resp:
        raise SmokeFailure(f"[{label}] expected error {code}, got success: {resp}")
    actual = resp["error"].get("code")
    if actual != code:
        raise SmokeFailure(f"[{label}] expected error {code}, got {actual}: {resp['error']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--widget", help="Widget BP asset path to mutate (creates RootVB+Title)")
    ap.add_argument("--level", help="Level package path to load")
    ap.add_argument("--bp", help="Blueprint asset path to inspect")
    args = ap.parse_args()

    failures: list[str] = []

    def step(label: str, fn):
        try:
            fn()
        except SmokeFailure as e:
            failures.append(str(e))
            print(f"\n!! FAIL: {e}")

    header("0. unknown method (should return error -32601)")
    def t0():
        resp = call("does_not_exist")
        show(resp)
        assert_error_code(resp, -32601, "unknown_method")
    step("unknown_method", t0)

    header("1. list_tools (should list 19 tool names)")
    def t1():
        resp = call("list_tools")
        show(resp)
        result = assert_ok(resp, "list_tools")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise SmokeFailure(f"[list_tools] 'tools' not a list: {result}")
        if len(tools) != 19:
            raise SmokeFailure(f"[list_tools] expected 19 tools, got {len(tools)}: {tools}")
        if result.get("count") != len(tools):
            raise SmokeFailure(f"[list_tools] 'count' ({result.get('count')}) != len(tools) ({len(tools)})")
    step("list_tools", t1)

    header("2. execute_unreal_python")
    def t2():
        resp = call(
            "execute_unreal_python",
            {"code": "import unreal\nunreal.log('hello from UnrealClaudeMCP smoke test')"},
        )
        show(resp)
        assert_ok(resp, "execute_unreal_python")
    step("execute_unreal_python", t2)

    header("3. get_project_summary")
    def t3():
        resp = call("get_project_summary")
        show(resp)
        result = assert_ok(resp, "get_project_summary")
        for field in ("project_name", "engine_version", "plugins", "asset_count"):
            if field not in result:
                raise SmokeFailure(f"[get_project_summary] missing '{field}': {result}")
        if not isinstance(result["plugins"], list):
            raise SmokeFailure(f"[get_project_summary] 'plugins' not a list")
        if not isinstance(result["asset_count"], int):
            raise SmokeFailure(f"[get_project_summary] 'asset_count' not int: {result['asset_count']}")
    step("get_project_summary", t3)

    header("4. get_actors_in_level (no filter)")
    def t4():
        resp = call("get_actors_in_level")
        show(resp)
        result = assert_ok(resp, "get_actors_in_level")
        actors = result.get("actors")
        if not isinstance(actors, list):
            raise SmokeFailure(f"[get_actors_in_level] 'actors' not a list: {result}")
        total = result.get("total_actors")
        returned = result.get("returned")
        if not isinstance(total, int) or not isinstance(returned, int):
            raise SmokeFailure(f"[get_actors_in_level] missing/invalid total_actors/returned")
        if returned > total:
            raise SmokeFailure(f"[get_actors_in_level] returned ({returned}) > total ({total})")
    step("get_actors_in_level", t4)

    header("5. take_high_res_screenshot (multiplier=1)")
    def t5():
        resp = call("take_high_res_screenshot", {"multiplier": 1})
        show(resp)
        assert_ok(resp, "take_high_res_screenshot")
    step("take_high_res_screenshot", t5)

    header("6. get_viewport_screenshot (response truncated for readability)")
    def t6():
        resp = call("get_viewport_screenshot")
        if "result" in resp:
            r = resp["result"]
            print(json.dumps({
                "jsonrpc": resp.get("jsonrpc"),
                "id": resp.get("id"),
                "result": {
                    "width": r.get("width"),
                    "height": r.get("height"),
                    "png_bytes": r.get("png_bytes"),
                    "png_base64_len": len(r.get("png_base64", "")),
                    "png_base64_first_64": (r.get("png_base64", "")[:64] + "..."),
                },
            }, indent=2))
        else:
            show(resp)
        result = assert_ok(resp, "get_viewport_screenshot")
        if not isinstance(result.get("width"), int) or result["width"] <= 0:
            raise SmokeFailure(f"[get_viewport_screenshot] bad width: {result.get('width')}")
        if not isinstance(result.get("height"), int) or result["height"] <= 0:
            raise SmokeFailure(f"[get_viewport_screenshot] bad height: {result.get('height')}")
        if not isinstance(result.get("png_bytes"), int) or result["png_bytes"] <= 0:
            raise SmokeFailure(f"[get_viewport_screenshot] bad png_bytes: {result.get('png_bytes')}")
        if not result.get("png_base64"):
            raise SmokeFailure("[get_viewport_screenshot] empty png_base64")
    step("get_viewport_screenshot", t6)

    header("7. texture pipeline round-trip (import + configure + cleanup)")
    def t_texture():
        fixture = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..",
                         "tests", "fixtures", "test_texture.png"))
        if not os.path.exists(fixture):
            raise SmokeFailure(f"[texture] fixture missing: {fixture}")

        smoke_dest = "/Game/_UnrealClaudeMCPSmoke"
        asset_name = "T_PipelineSmoke"
        asset_path = f"{smoke_dest}/{asset_name}.{asset_name}"

        # Import
        imp = call("import_texture", {
            "source_path": fixture,
            "dest_path": smoke_dest,
            "dest_name": asset_name,
            "replace_existing": True,
        })
        result = assert_ok(imp, "import_texture")
        if not (result.get("width") == 256 and result.get("height") == 256):
            raise SmokeFailure(f"[import_texture] unexpected dims: {result}")

        # Configure
        cfg = call("configure_texture", {
            "path": asset_path,
            "srgb": False,
            "compression": "Normalmap",
            "lod_group": "WorldNormalMap",
        })
        cfg_res = assert_ok(cfg, "configure_texture")
        applied = cfg_res.get("applied") or {}
        if applied.get("srgb") is not False:
            raise SmokeFailure(f"[configure_texture] srgb not applied: {cfg_res}")
        if applied.get("compression") != "Normalmap":
            raise SmokeFailure(f"[configure_texture] compression not applied: {cfg_res}")

        # Clean up via execute_unreal_python so we don't leave smoke assets behind
        cleanup = call("execute_unreal_python", {
            "code": (
                "import unreal\n"
                f"unreal.EditorAssetLibrary.delete_directory('{smoke_dest}')\n"
            )
        })
        assert_ok(cleanup, "texture_pipeline.cleanup")
    step("texture_pipeline", t_texture)

    header("8. build-a-level round-trip (find_assets + spawn + transform + property + component + delete)")
    def t_buildalevel():
        # 1. Discover what's available to spawn (basic shapes from /Engine/)
        find = call("find_assets", {
            "class_path": "/Script/Engine.StaticMesh",
            "path_under": "/Engine/BasicShapes/",
            "limit": 10,
        })
        find_res = assert_ok(find, "find_assets")
        if find_res.get("returned", 0) == 0:
            raise SmokeFailure(f"[find_assets] no basic shapes found: {find_res}")

        # 2. Spawn two StaticMeshActors with distinct labels
        s1 = call("spawn_actor", {
            "class_path": "/Script/Engine.StaticMeshActor",
            "location": {"x": 0, "y": 0, "z": 0},
            "label": "SmokeBuildCube1",
        })
        s1_res = assert_ok(s1, "spawn_actor.cube1")
        cube1_fname = s1_res["name"]

        s2 = call("spawn_actor", {
            "class_path": "/Script/Engine.StaticMeshActor",
            "location": {"x": 200, "y": 0, "z": 0},
            "label": "SmokeBuildCube2",
        })
        s2_res = assert_ok(s2, "spawn_actor.cube2")

        # 3. Move + rotate cube2
        t = call("set_actor_transform", {
            "name": "SmokeBuildCube2",
            "location": {"x": 200, "y": 200, "z": 50},
            "rotation": {"pitch": 0, "yaw": 45, "roll": 0},
        })
        t_res = assert_ok(t, "set_actor_transform")
        if "location" not in (t_res.get("applied") or {}):
            raise SmokeFailure(f"[set_actor_transform] location not in applied: {t_res}")

        # 4. Set a UPROPERTY on cube1 (bHidden)
        p = call("set_actor_property", {
            "name": "SmokeBuildCube1",
            "property": "bHidden",
            "value": False,
        })
        assert_ok(p, "set_actor_property")

        # 5. Add a PointLightComponent to cube1
        c = call("add_component", {
            "actor_name": "SmokeBuildCube1",
            "class_path": "/Script/Engine.PointLightComponent",
        })
        c_res = assert_ok(c, "add_component")
        if c_res.get("class") != "PointLightComponent":
            raise SmokeFailure(f"[add_component] unexpected class: {c_res}")

        # 6. Delete both actors (cube1 first with force=true since it has the new component)
        d1 = call("delete_actor", {"name": "SmokeBuildCube1", "force": True})
        assert_ok(d1, "delete_actor.cube1")
        d2 = call("delete_actor", {"name": "SmokeBuildCube2"})
        assert_ok(d2, "delete_actor.cube2")

        # 7. Verify world is back to baseline (no SmokeBuild* actors remain)
        actors = call("get_actors_in_level", {"name_contains": "SmokeBuild"})
        a_res = assert_ok(actors, "get_actors_in_level.cleanup_check")
        if a_res.get("returned", 0) != 0:
            raise SmokeFailure(f"[cleanup] residual actors found: {a_res}")
    step("buildalevel", t_buildalevel)

    header("9. advanced property types round-trip (USTRUCT + TArray + path traversal)")
    def t_advanced_props():
        # 1. Spawn a test actor
        spawn_resp = call("spawn_actor", {
            "class_path": "/Script/Engine.StaticMeshActor",
            "location": {"x": 0, "y": 0, "z": 0},
            "label": "AdvSmoke",
        })
        spawn_res = assert_ok(spawn_resp, "spawn_actor.adv")
        actor_name = spawn_res["name"]

        # 2. Set nested USTRUCT via path traversal: RootComponent.RelativeLocation
        set_loc = call("set_actor_property", {
            "name": actor_name,
            "property": "RootComponent.RelativeLocation",
            "value": {"x": 100.0, "y": 200.0, "z": 50.0},
        })
        set_loc_res = assert_ok(set_loc, "set_actor_property.struct_path")
        applied_loc = set_loc_res.get("new_value") or {}
        if not (applied_loc.get("x") == 100.0 and applied_loc.get("y") == 200.0 and applied_loc.get("z") == 50.0):
            raise SmokeFailure(f"USTRUCT round-trip failed: {set_loc_res}")

        # 3. Set TArray<FName> on the actor itself: Tags
        set_tags = call("set_actor_property", {
            "name": actor_name,
            "property": "Tags",
            "value": ["foo", "bar", "baz"],
        })
        set_tags_res = assert_ok(set_tags, "set_actor_property.tarray")
        new_tags = set_tags_res.get("new_value") or []
        # FName encoding round-trip: list of strings
        if list(new_tags) != ["foo", "bar", "baz"]:
            raise SmokeFailure(f"TArray<FName> round-trip failed: {set_tags_res}")

        # 4. Cleanup
        delete = call("delete_actor", {"name": actor_name, "force": True})
        assert_ok(delete, "delete_actor.adv")
    step("advanced_property_types", t_advanced_props)

    if args.bp:
        header(f"10. inspect_blueprint  ({args.bp})")
        def t7():
            resp = call("inspect_blueprint", {"path": args.bp})
            show(resp)
            assert_ok(resp, "inspect_blueprint")
        step("inspect_blueprint", t7)

    if args.widget:
        wbp = args.widget

        header(f"11a. inspect_widget_tree (BEFORE)  ({wbp})")
        def t8a():
            resp = call("inspect_widget_tree", {"path": wbp})
            show(resp)
            assert_ok(resp, "inspect_widget_tree.before")
        step("inspect_widget_tree.before", t8a)

        header("11b. edit_widget_tree -- set_root VerticalBox")
        def t8b():
            resp = call("edit_widget_tree", {
                "path": wbp, "op": "set_root", "class": "VerticalBox", "name": "RootVB",
            })
            show(resp)
            assert_ok(resp, "edit_widget_tree.set_root")
        step("edit_widget_tree.set_root", t8b)

        header("11c. edit_widget_tree -- add_child TextBlock 'Title'")
        def t8c():
            resp = call("edit_widget_tree", {
                "path": wbp, "op": "add_child", "parent": "RootVB",
                "class": "TextBlock", "name": "Title",
            })
            show(resp)
            assert_ok(resp, "edit_widget_tree.add_child")
        step("edit_widget_tree.add_child", t8c)

        header("11d. edit_widget_tree -- set_property Title.text (with compile)")
        def t8d():
            resp = call("edit_widget_tree", {
                "path": wbp, "op": "set_property", "widget": "Title",
                "property": "text", "value": "Hello from UnrealClaudeMCP",
                "compile": True,
            })
            show(resp)
            assert_ok(resp, "edit_widget_tree.set_property")
        step("edit_widget_tree.set_property", t8d)

        header("11e. inspect_widget_tree (AFTER; should show RootVB + Title)")
        def t8e():
            resp = call("inspect_widget_tree", {"path": wbp})
            show(resp)
            result = assert_ok(resp, "inspect_widget_tree.after")
            tree_text = json.dumps(result)
            if "RootVB" not in tree_text or "Title" not in tree_text:
                raise SmokeFailure(
                    f"[inspect_widget_tree.after] expected RootVB+Title in tree: {tree_text[:300]}"
                )
        step("inspect_widget_tree.after", t8e)

    if args.level:
        header(f"12. load_level_by_path  ({args.level})")
        def t9():
            resp = call("load_level_by_path", {"path": args.level})
            show(resp)
            assert_ok(resp, "load_level_by_path")
        step("load_level_by_path", t9)

    print()
    print("=" * 60)
    if failures:
        print(f"  Smoke test FAILED: {len(failures)} step(s)")
        for f in failures:
            print(f"   - {f}")
        print("=" * 60)
        sys.exit(1)
    print("  Smoke test complete - all assertions passed.")
    print("=" * 60)


if __name__ == "__main__":
    main()
