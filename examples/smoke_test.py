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

    s.sendall(raw)

    chunks = []
    while True:
        try:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
            if data.endswith(b"}"):
                break
        except socket.timeout:
            break
    s.close()

    payload = b"".join(chunks).decode("utf-8", errors="replace")
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

    header("1. list_tools (should list 11 tool names)")
    def t1():
        resp = call("list_tools")
        show(resp)
        result = assert_ok(resp, "list_tools")
        tools = result.get("tools") or result.get("methods") or []
        if len(tools) != 11:
            raise SmokeFailure(f"[list_tools] expected 11 tools, got {len(tools)}: {tools}")
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
        for field in ("project_name", "engine_version"):
            if field not in result:
                raise SmokeFailure(f"[get_project_summary] missing '{field}': {result}")
    step("get_project_summary", t3)

    header("4. get_actors_in_level (no filter)")
    def t4():
        resp = call("get_actors_in_level")
        show(resp)
        result = assert_ok(resp, "get_actors_in_level")
        actors = result.get("actors")
        if not isinstance(actors, list):
            raise SmokeFailure(f"[get_actors_in_level] 'actors' not a list: {result}")
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
        if not result.get("png_base64"):
            raise SmokeFailure("[get_viewport_screenshot] empty png_base64")
    step("get_viewport_screenshot", t6)

    if args.bp:
        header(f"7. inspect_blueprint  ({args.bp})")
        def t7():
            resp = call("inspect_blueprint", {"path": args.bp})
            show(resp)
            assert_ok(resp, "inspect_blueprint")
        step("inspect_blueprint", t7)

    if args.widget:
        wbp = args.widget

        header(f"8a. inspect_widget_tree (BEFORE)  ({wbp})")
        def t8a():
            resp = call("inspect_widget_tree", {"path": wbp})
            show(resp)
            assert_ok(resp, "inspect_widget_tree.before")
        step("inspect_widget_tree.before", t8a)

        header("8b. edit_widget_tree -- set_root VerticalBox")
        def t8b():
            resp = call("edit_widget_tree", {
                "path": wbp, "op": "set_root", "class": "VerticalBox", "name": "RootVB",
            })
            show(resp)
            assert_ok(resp, "edit_widget_tree.set_root")
        step("edit_widget_tree.set_root", t8b)

        header("8c. edit_widget_tree -- add_child TextBlock 'Title'")
        def t8c():
            resp = call("edit_widget_tree", {
                "path": wbp, "op": "add_child", "parent": "RootVB",
                "class": "TextBlock", "name": "Title",
            })
            show(resp)
            assert_ok(resp, "edit_widget_tree.add_child")
        step("edit_widget_tree.add_child", t8c)

        header("8d. edit_widget_tree -- set_property Title.text (with compile)")
        def t8d():
            resp = call("edit_widget_tree", {
                "path": wbp, "op": "set_property", "widget": "Title",
                "property": "text", "value": "Hello from UnrealClaudeMCP",
                "compile": True,
            })
            show(resp)
            assert_ok(resp, "edit_widget_tree.set_property")
        step("edit_widget_tree.set_property", t8d)

        header("8e. inspect_widget_tree (AFTER; should show RootVB + Title)")
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
        header(f"9. load_level_by_path  ({args.level})")
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
