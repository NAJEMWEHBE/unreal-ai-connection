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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--widget", help="Widget BP asset path to mutate (creates RootVB+Title)")
    ap.add_argument("--level", help="Level package path to load")
    ap.add_argument("--bp", help="Blueprint asset path to inspect")
    args = ap.parse_args()

    header("0. unknown method (should return error -32601)")
    show(call("does_not_exist"))

    header("1. list_tools (should list 11 tool names)")
    show(call("list_tools"))

    header("2. execute_unreal_python")
    show(call(
        "execute_unreal_python",
        {"code": "import unreal\nunreal.log('hello from UnrealClaudeMCP smoke test')"},
    ))

    header("3. get_project_summary")
    show(call("get_project_summary"))

    header("4. get_actors_in_level (no filter)")
    show(call("get_actors_in_level"))

    header("5. take_high_res_screenshot (multiplier=1)")
    show(call("take_high_res_screenshot", {"multiplier": 1}))

    header("6. get_viewport_screenshot (response truncated for readability)")
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

    if args.bp:
        header(f"7. inspect_blueprint  ({args.bp})")
        show(call("inspect_blueprint", {"path": args.bp}))

    if args.widget:
        wbp = args.widget
        header(f"8a. inspect_widget_tree (BEFORE)  ({wbp})")
        show(call("inspect_widget_tree", {"path": wbp}))

        header("8b. edit_widget_tree -- set_root VerticalBox")
        show(call("edit_widget_tree", {
            "path": wbp, "op": "set_root", "class": "VerticalBox", "name": "RootVB",
        }))

        header("8c. edit_widget_tree -- add_child TextBlock 'Title'")
        show(call("edit_widget_tree", {
            "path": wbp, "op": "add_child", "parent": "RootVB",
            "class": "TextBlock", "name": "Title",
        }))

        header("8d. edit_widget_tree -- set_property Title.text (with compile)")
        show(call("edit_widget_tree", {
            "path": wbp, "op": "set_property", "widget": "Title",
            "property": "text", "value": "Hello from UnrealClaudeMCP",
            "compile": True,
        }))

        header("8e. inspect_widget_tree (AFTER; should show RootVB + Title)")
        show(call("inspect_widget_tree", {"path": wbp}))

    if args.level:
        header(f"9. load_level_by_path  ({args.level})")
        show(call("load_level_by_path", {"path": args.level}))

    print()
    print("=" * 60)
    print("  Smoke test complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
