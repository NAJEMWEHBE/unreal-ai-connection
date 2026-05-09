#!/usr/bin/env python
"""
UnrealClaudeMCP <-> Claude Code bridge.

Claude Code (and any MCP client) speaks the MCP protocol
(initialize / tools/list / tools/call) over stdio. The UnrealClaudeMCP
plugin speaks raw JSON-RPC over a local TCP socket (default
127.0.0.1:18888). This script translates between the two:

  Claude Code (stdin, MCP)  ->  this bridge  ->  TCP 127.0.0.1:18888 (raw JSON-RPC)
  Claude Code (stdout, MCP) <-  this bridge  <-  TCP 127.0.0.1:18888

Behaviour:
  - "initialize"             returned synthetically (does NOT hit the UE server)
  - "notifications/*"        consumed silently
  - "tools/list"             returns a static list mirroring the 32 handlers
  - "tools/call"             unpacks {name, arguments} and forwards to the
                             UE server as the matching method
  - All other methods        proxied as-is

The bridge tolerates the UE server being down: it returns a JSON-RPC error
rather than crashing, so the MCP client can show "MCP server not running -
launch UE editor with the UnrealClaudeMCP plugin enabled".

Override host/port via env: UCMCP_HOST, UCMCP_PORT.
"""

import json
import os
import socket
import sys

UE_HOST = os.environ.get("UCMCP_HOST", "127.0.0.1")
UE_PORT = int(os.environ.get("UCMCP_PORT", "18888"))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "unreal-claude-mcp"
SERVER_VERSION = "0.9.1"

# Mirror of UnrealClaudeMCP/Resources/mcp_manifest.json - kept in sync manually.
# v0.9.1: 32 tools (no new handlers — wire-framing partial-message state
#                   machine on the C++ side; bridge wire format unchanged).
TOOLS = [
    {
        "name": "execute_unreal_python",
        "description": "Run arbitrary unreal.* Python in the editor's embedded interpreter (universal escape hatch). Multi-line scripts allowed.",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute"}},
            "required": ["code"],
        },
    },
    {
        "name": "get_project_summary",
        "description": "Project name, engine version, enabled plugins, asset counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_blueprint",
        "description": "Read parent class, declared variables, and function/event graph names of a Blueprint asset.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "e.g. /Game/Blueprints/BP_MyActor.BP_MyActor"}},
            "required": ["path"],
        },
    },
    {
        "name": "inspect_widget_tree",
        "description": "Read the widget hierarchy of a UWidgetBlueprint or UEditorUtilityWidgetBlueprint.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "edit_widget_tree",
        "description": "Mutate a widget tree. ops: set_root | add_child | set_property. Solves UE 5.7 EUW WidgetTree population.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Widget BP asset path"},
                "op": {"type": "string", "enum": ["set_root", "add_child", "set_property"]},
                "class": {"type": "string", "description": "VerticalBox|HorizontalBox|CanvasPanel|TextBlock|Button|Border|Image|Spacer|EditableTextBox or fully-qualified class path"},
                "name": {"type": "string", "description": "widget name to assign"},
                "parent": {"type": "string", "description": "for add_child: the parent panel widget name"},
                "widget": {"type": "string", "description": "for set_property: target widget name"},
                "property": {"type": "string", "description": "for set_property: UProperty name"},
                "value": {"type": "string", "description": "for set_property: string value (coerced to type)"},
                "compile": {"type": "boolean", "description": "compile the BP after the edit (default false; recommend true only on the LAST op of a batch)"},
            },
            "required": ["path", "op"],
        },
    },
    {
        "name": "get_viewport_screenshot",
        "description": "Capture the active editor viewport as a PNG, return base64-encoded inline.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_tools",
        "description": "Return the names of every registered MCP method on the UE server.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_actors_in_level",
        "description": "Return name/class/transform of every actor in the active editor world. Optional name_contains filter.",
        "inputSchema": {
            "type": "object",
            "properties": {"name_contains": {"type": "string", "description": "Substring filter on actor label"}},
        },
    },
    {
        "name": "focus_actor",
        "description": "Select an actor by label or unique name and frame the editor viewport on it.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "load_level_by_path",
        "description": "Load a UE level by package path, e.g. /Game/Maps/MyMap.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "take_high_res_screenshot",
        "description": "Trigger UE's HighResShot. Output -> Saved/Screenshots/<Platform>Editor/ (Windows/Mac/Linux). Optional multiplier (1..8).",
        "inputSchema": {
            "type": "object",
            "properties": {"multiplier": {"type": "number", "default": 1}},
        },
    },
    {
        "name": "import_texture",
        "description": "Import an image file (PNG/JPG/EXR/TGA/BMP/HDR) from disk into the project as a UTexture2D asset, using the canonical UE asset import pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Absolute filesystem path to the source image file."},
                "dest_path": {"type": "string", "description": "UE package path; must start with /Game/ (e.g. /Game/Textures/Environment)."},
                "dest_name": {"type": "string", "description": "Optional asset-name override; defaults to filename stem."},
                "replace_existing": {"type": "boolean", "description": "Overwrite existing asset at dest_path/dest_name (default false)."},
                "automated": {"type": "boolean", "description": "Suppress modal dialogs (default true)."},
                "save": {"type": "boolean", "description": "Save the .uasset to disk after import (default true)."},
            },
            "required": ["source_path", "dest_path"],
        },
    },
    {
        "name": "configure_texture",
        "description": "Adjust SRGB/CompressionSettings/LODGroup/Filter on an existing UTexture asset and persist the change. Triggers UE's standard PreEditChange/PostEditChange flow and rebuilds the GPU resource.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE package path of the existing texture asset, e.g. /Game/Textures/Environment/T_Stone_D."},
                "srgb": {"type": "boolean", "description": "Set UTexture::SRGB."},
                "compression": {"type": "string", "description": "TextureCompressionSettings enum name (e.g. Default, Normalmap, Masks, BC7, HDR)."},
                "lod_group": {"type": "string", "description": "TextureGroup enum name (e.g. World, WorldNormalMap, UI, Lightmap)."},
                "filter": {"type": "string", "enum": ["Nearest", "Bilinear", "Trilinear", "Default"], "description": "TextureFilter enum name: Nearest | Bilinear | Trilinear | Default."},
                "compress": {"type": "boolean", "description": "Call UpdateResource() after mutation (default true). Set false for batches."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "find_assets",
        "description": "Query the asset registry by class + optional path + optional name substring + optional tag filters. Returns matching assets with structured records (name, package_path, class[, tags]).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_path": {"type": "string", "description": "UE class path, e.g. /Script/Engine.StaticMesh, /Script/Engine.Blueprint, /Script/Engine.Texture2D."},
                "path_under": {"type": "string", "description": "Recursive path filter; defaults to /Game/. Must start with /Game/ or /Engine/."},
                "name_contains": {"type": "string", "description": "Case-insensitive substring filter on asset name."},
                "limit": {"type": "integer", "description": "Cap result count. Default 100, max 500."},
                "tags": {"type": "object", "description": "v0.7.0: map of tag-name -> required-value (string) or null (any value). AND-combined."},
                "include_tags": {"type": "boolean", "description": "v0.7.0: when true, each result asset includes a 'tags' map of all its registry tags. Default false."},
            },
            "required": ["class_path"],
        },
    },
    {
        "name": "spawn_actor",
        "description": "Create an actor in the current editor world at a location with optional rotation, label, and initial properties. Class path can be built-in (/Script/Engine.StaticMeshActor) or Blueprint (/Game/Blueprints/BP_X.BP_X_C).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_path": {"type": "string", "description": "Actor class path."},
                "location": {"type": "object", "description": "World-space {x, y, z}. Defaults to {0,0,0}."},
                "rotation": {"type": "object", "description": "{pitch, yaw, roll} in degrees. Defaults to {0,0,0}."},
                "label": {"type": "string", "description": "Visible name in World Outliner; defaults to UE auto-naming."},
                "properties": {"type": "object", "description": "Map of {PropertyName: value} applied immediately after spawn via PropertyCoercion."},
            },
            "required": ["class_path"],
        },
    },
    {
        "name": "set_actor_transform",
        "description": "Move / rotate / scale an existing actor by name (label or FName). Supports both absolute and relative modes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label OR FName. If label is ambiguous, returns ambiguous_actor error."},
                "location": {"type": "object", "description": "{x, y, z}. Omit to leave unchanged."},
                "rotation": {"type": "object", "description": "{pitch, yaw, roll} in degrees. Omit to leave unchanged."},
                "scale": {"type": "object", "description": "{x, y, z} multiplier. Omit to leave unchanged."},
                "relative": {"type": "boolean", "description": "When true, deltas are added to current values instead of replacing. Default false."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "delete_actor",
        "description": "Remove an actor from the editor world by name (label or FName). Children are detached, not destroyed (UE's default behavior). Force flag overrides the children-attached safety check.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label OR FName."},
                "force": {"type": "boolean", "description": "When false (default), refuses to delete if children are attached and returns has_children error. When true, deletes anyway."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "set_actor_property",
        "description": "Mutate any UPROPERTY on an actor. v0.4.0 supports primitives, all common UE structs, enums, TSoftObjectPtr, plus USTRUCT (recursive)/TArray/TMap (string-keyed)/TSet/FObjectProperty (hard UObject pointers via asset path). Property names accept dotted-path syntax for nested traversal (e.g. 'RootComponent.RelativeLocation'). FInstancedStruct deferred to v0.4.x.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label or FName."},
                "property": {"type": "string", "description": "UPROPERTY name (case-sensitive)."},
                "value": {"description": "JSON value coerced based on the FProperty type. See docs/TOOLS.md for the supported types table."},
            },
            "required": ["name", "property", "value"],
        },
    },
    {
        "name": "add_component",
        "description": "Attach a component (UActorComponent or USceneComponent subclass) to an existing actor at runtime, optionally socketed and transformed relative to a parent component.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor_name": {"type": "string", "description": "Host actor label or FName."},
                "class_path": {"type": "string", "description": "Component class path, e.g. /Script/Engine.StaticMeshComponent, /Script/Engine.PointLightComponent."},
                "component_name": {"type": "string", "description": "FName for the new component; defaults to UE auto-naming."},
                "attach_to": {"type": "string", "description": "Existing component name to attach as child of; defaults to root component."},
                "socket": {"type": "string", "description": "Socket name on the parent component."},
                "relative_transform": {"type": "object", "description": "{location, rotation, scale} relative to the parent component."},
            },
            "required": ["actor_name", "class_path"],
        },
    },
    {
        "name": "get_log_lines",
        "description": "Read recent UE Output Log entries from the in-process ring buffer. Supports category substring filter and minimum verbosity filter. Returns up to `count` lines (default 100, max 1000) at or above the requested severity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Max lines to return (default 100, max 1000)."},
                "category_filter": {"type": "string", "description": "Case-insensitive substring filter on log category (e.g. 'LogTemp')."},
                "min_verbosity": {"type": "string", "enum": ["Fatal", "Error", "Warning", "Display", "Log", "Verbose", "VeryVerbose"], "description": "Return lines at or above this severity. Default 'Log'."},
            },
        },
    },
    {
        "name": "execute_console_command",
        "description": "Run a UE console command (e.g. 'stat fps', 'r.ScreenPercentage 50') and optionally capture its output. Executes on the game thread in the editor world context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Console command string to execute."},
                "capture_output": {"type": "boolean", "description": "When true (default), captures and returns the command output. When false, output flows to the normal Output Log."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "inspect_asset",
        "description": "Read everything the asset registry knows about a single asset: class, all registry tags, dependency packages, referencer packages, on-disk file size.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Asset path or package path (e.g. /Game/Textures/T_Stone or /Game/Textures/T_Stone.T_Stone)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "move_asset",
        "description": "Move an asset to a different folder; leaf name unchanged. UE auto-creates a redirector at the source path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source asset path."},
                "dest_folder": {"type": "string", "description": "Destination folder under /Game/ or /Engine/."},
            },
            "required": ["path", "dest_folder"],
        },
    },
    {
        "name": "rename_asset",
        "description": "Rename an asset's leaf name; folder unchanged. UE auto-creates a redirector at the old name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source asset path."},
                "new_name": {"type": "string", "description": "New leaf name (no '/' or '.')."},
            },
            "required": ["path", "new_name"],
        },
    },
    {
        "name": "delete_asset",
        "description": "Delete an asset. Refuses if referenced by other packages unless force=true. WARNING: deletion is permanent within the project; force-delete cannot recover via Undo.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Asset path to delete."},
                "force": {"type": "boolean", "description": "When true, delete even if referenced (default false)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_sequence",
        "description": "Read structure of a Level Sequence asset: tracks, sections, bindings, frame rate, playback range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Level Sequence asset path (object path or package path)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "create_sequence",
        "description": "Create a new Level Sequence asset. Initializes an empty MovieScene with the given display frame rate and playback end-frame.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination folder under /Game/."},
                "name": {"type": "string", "description": "Leaf asset name (no '/' or '.')."},
                "display_rate_fps": {"type": "number", "description": "Display frame rate (default 30.0)."},
                "playback_end_frames": {"type": "integer", "description": "End of playback range in display frames (default 240)."},
            },
            "required": ["path", "name"],
        },
    },
    {
        "name": "bind_actor_to_sequence",
        "description": "Add a level actor as a possessable binding to a Level Sequence. Creates the binding GUID and wires it to the live actor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path."},
                "actor_name": {"type": "string", "description": "Actor label or FName in the current editor world. Hybrid identification: ambiguous labels return ambiguous_actor."},
            },
            "required": ["sequence_path", "actor_name"],
        },
    },
    {
        "name": "create_material_instance",
        "description": "Create a UMaterialInstanceConstant asset and set its parent to an existing UMaterial or UMaterialInstance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent_path": {"type": "string", "description": "Path of the parent UMaterial or UMaterialInstance."},
                "path": {"type": "string", "description": "Destination folder under /Game/."},
                "name": {"type": "string", "description": "Leaf asset name (no '/' or '.')."},
            },
            "required": ["parent_path", "path", "name"],
        },
    },
    {
        "name": "set_mi_parameter",
        "description": "Override a scalar/vector/texture parameter on a UMaterialInstanceConstant. Type discriminator: 'scalar' -> number, 'vector' -> {r,g,b,a}, 'texture' -> asset path string.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Material instance asset path."},
                "parameter": {"type": "string", "description": "Parameter name as declared on the parent material."},
                "type": {"type": "string", "enum": ["scalar", "vector", "texture"], "description": "Parameter type discriminator."},
                "value": {"description": "Value shape varies by type: scalar -> number, vector -> {r,g,b,a}, texture -> string asset path."},
            },
            "required": ["path", "parameter", "type", "value"],
        },
    },
    {
        "name": "inspect_material",
        "description": "List parameter names declared by a UMaterial or UMaterialInstance: scalar, vector, texture, and static-switch parameters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Material asset path (UMaterial or UMaterialInstance)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_material_instance",
        "description": "Read a UMaterialInstanceConstant's parent + currently-overridden parameter values (scalar/vector/texture).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Material instance asset path."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_python_file",
        "description": "Execute a .py file from disk via the editor's embedded Python. Complement to execute_unreal_python -- avoids escaping pain for non-trivial scripts. Output capture caveat: ExecuteFile mode does not return stdout/eval-result; use unreal.log marker + get_log_lines to round-trip results.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filesystem path to a .py file. Absolute or relative; relative paths resolve against the editor's CWD (typically the project root)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "fix_up_redirectors",
        "description": "Cascade-update consumers of UObjectRedirector assets under a folder, then delete the now-redundant redirector .uasset stubs. Cleans up after move_asset / rename_asset workflows. Mirrors the editor's right-click 'Fix Up Redirectors in Folder'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Package path under which to recursively fix up redirectors, e.g. '/Game/' or '/Game/Materials'. Required to avoid accidentally rewriting an entire project."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "apply_python_to_selection",
        "description": "Run user Python with the editor's current selection pre-bound: `selection` (selected level actors) and `selected_assets` (selected content-browser assets). Convenience wrapper around execute_unreal_python that injects the lookup boilerplate. Same output-capture caveat: ExecuteFile mode does not return stdout; use unreal.log marker + get_log_lines.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source. The injected boilerplate makes `selection` (list of AActor) and `selected_assets` (list of UObject) available -- use either name directly."},
            },
            "required": ["code"],
        },
    },
]


# ---------------------------------------------------------------------------
# Wire-framing helpers  (v0.5.0)
#
# Every TCP message is:
#   <8-byte big-endian uint64 body length> <N bytes of UTF-8 JSON body>
# ---------------------------------------------------------------------------

def send_framed(sock: socket.socket, body_bytes: bytes) -> None:
    """Prepend the 8-byte big-endian length prefix and send the whole frame."""
    length_prefix = len(body_bytes).to_bytes(8, byteorder="big", signed=False)
    sock.sendall(length_prefix + body_bytes)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from sock, accumulating across multiple recv() calls."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError(f"socket closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def recv_framed(sock: socket.socket) -> bytes:
    """Read one length-prefixed frame and return the body bytes."""
    length_bytes = recv_exact(sock, 8)
    length = int.from_bytes(length_bytes, byteorder="big", signed=False)
    if length == 0:
        raise ValueError("framing_error: zero-length body")
    if length > 1024 * 1024 * 1024:
        raise ValueError(f"framing_error: length {length} exceeds 1 GB cap")
    return recv_exact(sock, length)


def write_msg(obj):
    """Write one MCP message to stdout (newline-delimited)."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def make_response(req_id, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def call_ue(method, params):
    """Send one JSON-RPC request to the UE server, return the response dict."""
    try:
        s = socket.socket()
        s.settimeout(30)
        s.connect((UE_HOST, UE_PORT))
        msg = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params:
            msg["params"] = params
        send_framed(s, json.dumps(msg).encode("utf-8"))
        raw = recv_framed(s).decode("utf-8", errors="replace")
        s.close()
        return json.loads(raw)
    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32099,
                "message": f"UE server not reachable on {UE_HOST}:{UE_PORT}: {e}. Open the UE editor with the UnrealClaudeMCP plugin enabled.",
            },
        }
    except json.JSONDecodeError as e:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32700, "message": f"UE server returned non-JSON: {e}"},
        }


def handle(req):
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params") or {}

    # Notifications: no response per JSON-RPC spec
    if req_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return make_response(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "tools/list":
        return make_response(req_id, {"tools": TOOLS})

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {}) or {}
        if not tool_name:
            return make_response(req_id, error={"code": -32602, "message": "tools/call missing 'name'"})

        ue_resp = call_ue(tool_name, tool_args)
        if "error" in ue_resp:
            return make_response(req_id, error=ue_resp["error"])

        # MCP tools/call expects content array. We wrap the result JSON as one text block.
        result_text = json.dumps(ue_resp.get("result", {}), indent=2)
        return make_response(req_id, {
            "content": [{"type": "text", "text": result_text}],
            "isError": False,
        })

    # Unknown method
    if req_id is not None:
        return make_response(req_id, error={"code": -32601, "message": f"Method not found: {method}"})
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            resp = handle(req)
        except Exception as e:
            req_id = req.get("id") if isinstance(req, dict) else None
            resp = make_response(req_id, error={"code": -32603, "message": f"Bridge internal error: {e}"})
        if resp is not None:
            write_msg(resp)


if __name__ == "__main__":
    main()
