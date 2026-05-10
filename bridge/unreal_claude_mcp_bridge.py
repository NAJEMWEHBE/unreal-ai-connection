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
  - "tools/list"             returns a static list of all 60 tools (56
                             dispatched to the UE plugin's C++ handlers
                             plus 4 bridge-side synthetic tools served by
                             SYNTHETIC_TOOLS without crossing the wire as
                             a single UE round-trip)
  - "tools/call"             unpacks {name, arguments} and forwards to the
                             UE server as the matching method
  - All other methods        proxied as-is

The bridge tolerates the UE server being down: it returns a JSON-RPC error
rather than crashing, so the MCP client can show "MCP server not running -
launch UE editor with the UnrealClaudeMCP plugin enabled".

Override host/port via env: UCMCP_HOST, UCMCP_PORT.
"""

import json
import math
import os
import socket
import sys
import time
import uuid

UE_HOST = os.environ.get("UCMCP_HOST", "127.0.0.1")
UE_PORT = int(os.environ.get("UCMCP_PORT", "18888"))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "unreal-claude-mcp"
SERVER_VERSION = "0.9.1"

# Mirror of UnrealClaudeMCP/Resources/mcp_manifest.json - kept in sync manually.
# 60 tool entries total. 56 are dispatched straight to UE C++ handlers
# (see UnrealClaudeMCPModule.cpp's Reg.Register(...) block). The remaining
# 4 -- wait_for_events, get_camera_transform, set_camera_transform,
# screenshot_actor -- are bridge-side synthetic tools served by
# SYNTHETIC_TOOLS (see below) without a dedicated UE handler: they either
# compose existing handlers (focus + screenshot, repeated poll) or run the
# matching unreal.* Python via execute_unreal_python.
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
        "name": "duplicate_asset",
        "description": "Copy an asset to a new path. Source asset is preserved; destination must not already exist. No redirector is created (callers reference the duplicate by its new path).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Source asset path."},
                "dest_path": {"type": "string", "description": "Destination asset path (must not exist)."},
            },
            "required": ["path", "dest_path"],
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
    {
        "name": "compile_blueprint",
        "description": "Explicit Blueprint recompile via FKismetEditorUtilities::CompileBlueprint. Use when a BP has been mutated externally (e.g. via execute_unreal_python) and needs to be recompiled without further mutation. Pairs with edit_widget_tree's compile=true flag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Blueprint asset path, e.g. /Game/Blueprints/BP_MyActor"},
                "skip_save": {"type": "boolean", "description": "Suppress the project's Save-On-Compile auto-save behavior (default false)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_console_variable",
        "description": "Read a UE Console Variable by name. Returns the current value in all four representations (string/int/float/bool), the detected type (int|float|bool|string), the read-only flag, and the human-readable last-setter (e.g. 'Console', 'DeviceProfile'). Distinct from execute_console_command: this reads CVar state directly, never invokes the console exec engine.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact CVar name, case-sensitive (e.g. 'r.ScreenPercentage', 'Slate.bAllowToolTips')."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "set_console_variable",
        "description": "Mutate a UE Console Variable by name. 'value' is polymorphic: string, number, or bool. Issues the change at ECVF_SetByConsole priority (matches user-typed-in-console semantics) so it overrides ini files and code-set values. Pre-rejects ECVF_ReadOnly CVars (those silently no-op after early init) with a clear error, and post-verifies the change landed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Exact CVar name, case-sensitive."},
                "value": {"type": ["string", "number", "boolean"], "description": "New value. Numbers and bools are coerced to canonical string form before being passed to IConsoleVariable::Set, which parses against the CVar's declared type."},
            },
            "required": ["name", "value"],
        },
    },
    {
        "name": "poll_events",
        "description": "Tier 2 entrypoint: drain editor events fired since the caller's last poll. Today UE pushes events from a starter set of delegates (actor_spawned, actor_deleted, asset_added) into a 1000-entry ring buffer (FUCMCPEventBus); this handler returns the slice with seq >= since_seq (inclusive cursor), capped at max_count. First call: pass since_seq=-1 (default) to discover the current next_seq, then poll with the previous response's next_seq for steady-state delta consumption. Response includes 'dropped' flag if the caller's since_seq fell below the oldest buffered event (i.e. buffer overflowed between polls).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since_seq": {"type": "integer", "description": "Return events with seq >= since_seq (inclusive cursor). Default -1 (from oldest buffered)."},
                "max_count": {"type": "integer", "description": "Cap returned events. Default 100; hard max 1000 (= ring buffer size)."},
                "event_filter": {"type": "array", "items": {"type": "string"}, "description": "Substring-match filters on event type names (e.g. ['actor_spawned', 'asset_']). Multiple entries are OR-combined. Empty / omitted means no filter."},
            },
        },
    },
    {
        "name": "wait_for_events",
        "description": "Bridge-side composition of poll_events: repeatedly polls UE every poll_interval_ms until matching events arrive or timeout_ms expires. Implemented in the bridge (not as a UE handler) so the wait runs in this Python process -- UE's game thread keeps running between polls and game-thread events (actor_spawned, map_changed, etc.) actually fire during the wait. Same response shape and cursor semantics as poll_events, plus a 'timed_out' field. Default timeout 500ms; hard cap 30000ms (30s).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer", "description": "Maximum time to wait in milliseconds. Default 500; hard cap 30000 (over-cap requests are clamped, not rejected)."},
                "poll_interval_ms": {"type": "integer", "description": "Bridge-side polling cadence in milliseconds. Default 100; min 25; max 1000. Lower values reduce latency at the cost of more frequent UE round-trips."},
                "since_seq": {"type": "integer", "description": "Same as poll_events: events with seq >= since_seq are returned. Default -1 (from oldest buffered)."},
                "max_count": {"type": "integer", "description": "Cap returned events. Default 100; hard max 1000."},
                "event_filter": {"type": "array", "items": {"type": "string"}, "description": "Substring-match filters on event type names; OR-combined."},
            },
        },
    },
    {
        "name": "register_subscription",
        "description": "Tier 2 PR #43: create a server-side cursor + filter on the FUCMCPEventBus. Returns a subscription_id (FGuid string) usable with poll_subscription (drain matched events) and unsubscribe (release). The cursor starts at the bus's current next_seq -- subscribers see events fired AFTER subscription, not historical ones. PR #43 ships subscriptions WITHOUT TTL: they live until explicit unsubscribe.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_filter": {"type": "array", "items": {"type": "string"}, "description": "Substring-match filters on event type names; OR-combined. Empty / omitted means no filter."},
            },
        },
    },
    {
        "name": "unsubscribe",
        "description": "Remove a subscription created via register_subscription. Idempotent: calling on an unknown id returns ok=true with was_present=false rather than an error, so callers can blanket-unsubscribe on shutdown without worrying about partial state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string", "description": "Subscription id returned by register_subscription."},
            },
            "required": ["subscription_id"],
        },
    },
    {
        "name": "poll_subscription",
        "description": "Drain events for a server-side subscription. Per-sub cursor advances atomically with the read -- a successful poll never returns the same events twice. No since_seq param (cursor is server-side); no event_filter param (filter was set at register_subscription time and is immutable for that sub -- re-register if you need a different filter).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subscription_id": {"type": "string", "description": "Subscription id returned by register_subscription."},
                "max_count": {"type": "integer", "description": "Cap returned events. Default 100; hard max 1000."},
            },
            "required": ["subscription_id"],
        },
    },
    {
        "name": "start_sleep_task",
        "description": "Tier 2 PR #44 framework tracer: spawn a background task that sleeps for duration_ms then completes. Returns immediately with a task_id; poll via poll_task or cancel via cancel_task. Useful by itself for 'wait N ms and then do something' workflows; primary purpose is to exercise the FUCMCPTaskRegistry threading + cancellation paths. Hard cap on duration_ms is 1 hour.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "duration_ms": {"type": "integer", "description": "How long the task should sleep (1 to 3600000 ms / 1 hour). Required."},
            },
            "required": ["duration_ms"],
        },
    },
    {
        "name": "poll_task",
        "description": "Read current state of a task started via any start_*_task handler. Non-blocking: returns the registry snapshot and never waits for the task to advance. Status: pending | running | completed | cancelled | failed. Result populated when status=completed; error populated when status=failed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id returned by start_*_task."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "cancel_task",
        "description": "Request COOPERATIVE cancellation of a running task. Sets the task's atomic flag; the worker observes it on its next polling iteration (~50ms) and exits cleanly to status='cancelled'. UE has no safe forced-thread-termination, so workers that don't poll the flag run to completion regardless. Idempotent: returns ok=true with accepted=false for unknown ids and already-terminal tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task id returned by start_*_task."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "description": "Enumerate all tasks in the FUCMCPTaskRegistry with optional status / type filters and a limit. Atomic snapshot under the registry's lock so the result is internally consistent. Returns total/matched/returned counts plus task records mirroring poll_task's shape.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status_filter": {"type": "string", "enum": ["pending", "running", "completed", "cancelled", "failed"], "description": "Optional. If set, only tasks with this status are returned."},
                "type_filter": {"type": "string", "description": "Optional. Exact-match filter on task type (e.g. 'sleep')."},
                "limit": {"type": "integer", "description": "Optional. Max items to return. Default 100; clamped to [1, 500]."},
            },
        },
    },
    {
        "name": "exec_python_persistent",
        "description": "Tier 2 PR #45: like execute_unreal_python but state PERSISTS across calls. Variables, imports, and function/class definitions defined in one call are visible in the next -- letting Claude build up state across turns without re-loading every time. Implemented via UE's FPythonCommandEx with FileExecutionScope=Public (shared globals dict with the editor's Python console). Pairs with reset_python_state. Same output-capture caveat as execute_unreal_python: ExecuteFile mode does not capture stdout via CommandResult; use unreal.log marker + get_log_lines.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute against the persistent globals dict."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "reset_python_state",
        "description": "Clear all user-defined names from UE Python's public (shared-with-console) globals dict. Pairs with exec_python_persistent: lets Claude wipe accumulated state and start fresh without restarting the editor. Names starting with '_' (Python dunders + conventional private) are preserved. Imports the user explicitly added (e.g. 'import unreal') ARE cleared -- re-import in the next exec_python_persistent call.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "find_console_variables",
        "description": "Prefix-search the IConsoleManager registry; returns matching CVar names + types + read-only flags. Pairs with get_console_variable / set_console_variable for discovery workflows. C++ handler -- direct iteration of UE's internal console registry. Part of the language-shim experiment (PR #46): see docs/LANGUAGE-CHOICE-RETROSPECTIVE.md.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "Optional case-sensitive prefix to filter by (e.g. 'r.Screen'). Empty / omitted = all CVars."},
                "limit": {"type": "integer", "description": "Cap returned variables. Default 100; hard max 1000."},
            },
        },
    },
    {
        "name": "inspect_static_mesh",
        "description": "Read structural properties of a UStaticMesh asset: LOD count, per-LOD vertex/triangle counts, bounding box, material slots. Pairs with inspect_asset (registry metadata) and inspect_material (parameters). C++ handler -- benefits from native struct access. Part of the language-shim experiment (PR #46).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UStaticMesh, e.g. /Engine/BasicShapes/Cube."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_niagara_system",
        "description": "Read structural properties of a UNiagaraSystem asset: emitter list (name + enabled + mode), user-exposed parameter list, system-level settings (looping, GPU usage, warmup + tick params when needed, fixed bounds, effect type). C++ handler -- requires Niagara runtime module + EnsureFullyLoaded() before reading lazy-loaded data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UNiagaraSystem, e.g. /Game/FX/NS_MyEffect."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_anim_blueprint",
        "description": "Read structural properties of a UAnimBlueprint asset: parent class, target skeleton, template flag, baked state machines, anim functions (with implemented flag), sync groups, parent anim blueprint chain. C++ handler -- guards UAnimBlueprintGeneratedClass for null (compiled data is empty / is_compiled=false when the blueprint has never been compiled). No new Build.cs deps (Engine module already present).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UAnimBlueprint, e.g. /Game/Animation/ABP_Hero."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_landscape",
        "description": "Read structural properties of an ALandscape (a SCENE ACTOR, not an asset): component dimensions, total component count across loaded streaming proxies, landscape material, world-space bounds, both LandscapeGuid (mutates on PIE/instancing) and OriginalLandscapeGuid (stable). Lookup by actor label or GUID; if neither is given and exactly one landscape exists, that one is returned. Diverges from sibling Inspect* handlers (which take asset paths) because UE landscapes have no .uasset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label of the landscape. Optional. If omitted alongside guid, returns the only landscape if exactly one exists."},
                "guid": {"type": "string", "description": "LandscapeGuid OR OriginalLandscapeGuid string. Optional. Either matches."},
            },
        },
    },
    {
        "name": "inspect_skeletal_mesh",
        "description": "Read structural properties of a USkeletalMesh asset: per-LOD vertex / triangle / section counts, bounding box (min/max/size/center) + sphere radius, target USkeleton, total + raw bone counts, material slots, morph targets (count + names), clothing assets, physics asset. C++ handler; no new Build.cs deps (Engine module covers it). Mirrors inspect_static_mesh's bounds shape for cross-handler consistency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a USkeletalMesh, e.g. /Game/Characters/Hero/SK_Hero."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_anim_montage",
        "description": "Read structural properties of a UAnimMontage asset: target skeleton, play length, frame rate (rational), blend envelope (in/out times + auto-blend trigger), composite sections (with start/end times and next-section linkage), slot animation tracks, notify events. C++ handler; no new Build.cs deps (Engine module covers it). Completes the animation introspection trio with inspect_anim_blueprint and inspect_skeletal_mesh.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UAnimMontage, e.g. /Game/Animation/AM_Attack."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_widget_blueprint",
        "description": "Read UWidgetBlueprint-specific structural properties: parent class, blueprint compile status, palette category, animations (with start/end/length and binding count), delegate property bindings, inherited named slots from parent class, and the property-bindings count. Complements inspect_blueprint (variables + graphs, inherited from UBlueprint) and inspect_widget_tree (widget hierarchy); cross-link via shared asset path. C++ handler; no new Build.cs deps (UMG + UMGEditor already present).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UWidgetBlueprint, e.g. /Game/UI/WBP_HUD."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_data_table",
        "description": "Read structural properties of a UDataTable: RowStruct asset path + name, row count, sorted row names (FName.ToString), per-property name+type for each FProperty on the RowStruct (TFieldIterator with EFieldIterationFlags::None to skip super fields), client-strip flag, missing/extra-field tolerance flags, optional ImportKeyField. C++ handler; no new Build.cs deps (Engine + CoreUObject cover UDataTable / UScriptStruct / FProperty). Null-guards RowStruct (freshly-created DataTables can have no struct assigned).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UDataTable, e.g. /Game/Data/DT_Items."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_texture",
        "description": "Read structural properties of a UTexture asset (UTexture2D / UTextureCube / UTextureRenderTarget / UTexture2DArray / ...): texture class, surface dimensions (width/height/depth via virtual accessors), sRGB, compression settings, filter, LOD group, LOD bias, mip-gen settings, virtual-texture streaming flag, never-stream flag, composite-texture cross-link. UTexture2D-specific: size_x / size_y / num_mips / pixel_format / imported_size_x|y. Pairs with the existing configure_texture handler (mutates these fields) and import_texture (creates the asset). C++ handler; no new Build.cs deps (Engine covers UTexture / UTexture2D / EPixelFormat).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UTexture, e.g. /Game/Textures/Environment/T_Stone_D."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_curve",
        "description": "Read structural properties of a UCurveBase asset (UCurveFloat / UCurveLinearColor / UCurveVector / any subclass): curve class, channel count, global time + value range, and per-channel name + key count + per-channel time/value range. Channel layout: UCurveFloat = 1 channel, UCurveLinearColor = 4 (RGBA), UCurveVector = 3 (XYZ). C++ handler; no new Build.cs deps (Engine covers UCurveBase / FRichCurve).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UCurveBase, e.g. /Game/Curves/Curve_Falloff."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_camera_transform",
        "description": "Read the level-editor viewport camera transform. SYNTHETIC bridge-side handler (PR #46 language-shim experiment): composes execute_unreal_python + get_log_lines via the marker pattern. Returns { location: {x,y,z}, rotation: {pitch,yaw,roll} }.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "set_camera_transform",
        "description": "Set the level-editor viewport camera transform. SYNTHETIC bridge-side handler (PR #46 language-shim experiment): single execute_unreal_python round-trip. All location/rotation fields are optional and default to 0.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {"type": "object", "description": "{x, y, z} world-space; missing fields default to 0."},
                "rotation": {"type": "object", "description": "{pitch, yaw, roll} in degrees; missing fields default to 0."},
            },
        },
    },
    {
        "name": "screenshot_actor",
        "description": "Frame the editor viewport on an actor (by label or unique name) and capture a focused PNG screenshot. SYNTHETIC bridge-side handler: composes focus_actor + get_viewport_screenshot. Returns base64 PNG plus the focused actor's identity and world location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label or unique name to focus on."},
            },
            "required": ["name"],
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


def _wrap_tool_result(req_id, result_obj):
    """Wrap a result object as an MCP tools/call response (JSON-stringified into a text block)."""
    return make_response(req_id, {
        "content": [{"type": "text", "text": json.dumps(result_obj, indent=2)}],
        "isError": False,
    })


def synthetic_wait_for_events(req_id, args):
    """Bridge-side wait_for_events. Polls UE's poll_events handler at
    poll_interval_ms cadence until matching events arrive or timeout_ms
    expires. Lives in the bridge (not UE) because:

      - UE's MCP dispatcher runs on the game thread (FTSTicker callback).
        A C++ wait handler would freeze the same thread that fires most
        editor delegates -- the wait would deterministically time out
        for game-thread events because the game thread is asleep.
      - This Python loop runs in the bridge's separate OS process. UE's
        game thread keeps running between polls (each poll is ~1ms under
        the bus's lock), so events actually fire during the wait.

    Latency is bounded by poll_interval_ms (default 100ms). Caller-supplied
    timeout_ms is clamped to [0, 30000]; poll_interval_ms is clamped to
    [25, 1000] (faster than 25ms is wasteful given network round-trip
    overhead; slower than 1s defeats the purpose of long-poll).
    """
    # --- Validate + clamp params ---
    def _coerce_int(name, default, lo, hi):
        v = args.get(name, default)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return None, f"wait_for_events: '{name}' must be a number, got {type(v).__name__}"
        if v != int(v):
            return None, f"wait_for_events: '{name}' must be an integer, got {v}"
        v = int(v)
        if v < lo or v > hi:
            v_clamped = max(lo, min(hi, v))
            return v_clamped, None  # clamp silently
        return v, None

    timeout_ms, err = _coerce_int("timeout_ms", 500, 0, 30000)
    if err:
        return make_response(req_id, error={"code": -32602, "message": err})

    poll_interval_ms, err = _coerce_int("poll_interval_ms", 100, 25, 1000)
    if err:
        return make_response(req_id, error={"code": -32602, "message": err})

    # --- Forward args (minus our local-only ones) to UE's poll_events ---
    poll_args = {k: v for k, v in args.items() if k not in ("timeout_ms", "poll_interval_ms")}

    deadline = time.monotonic() + (timeout_ms / 1000.0)
    poll_interval_s = poll_interval_ms / 1000.0
    last_result = None

    while True:
        ue_resp = call_ue("poll_events", poll_args)
        if "error" in ue_resp:
            return make_response(req_id, error=ue_resp["error"])

        last_result = ue_resp.get("result", {}) or {}
        events = last_result.get("events", []) or []
        dropped = last_result.get("dropped", False)

        # Match conditions: events arrived, OR caller missed events
        # between polls (dropped state needs to be surfaced regardless),
        # OR the deadline has passed.
        if events or dropped:
            last_result["timed_out"] = False
            return _wrap_tool_result(req_id, last_result)

        if time.monotonic() >= deadline:
            last_result["timed_out"] = True
            return _wrap_tool_result(req_id, last_result)

        time.sleep(poll_interval_s)


def synthetic_get_camera_transform(req_id, args):
    """Bridge-side shim: read the level-editor viewport camera transform.

    Implementation pattern (the canonical "Python shim" in the
    LANGUAGE-CHOICE-RETROSPECTIVE.md addendum from PR #46):
      1. Generate a UUID marker token (per-call unique)
      2. Build Python that calls UnrealEditorSubsystem.get_level_viewport_camera_info()
         and emits the result as `unreal.log("__CAM_<marker>__" + json + "__END__")`
      3. Run via execute_unreal_python (one UE round-trip)
      4. Read recent LogPython lines via get_log_lines (second UE round-trip)
      5. Find the marker, parse the JSON payload, return

    The two-round-trip cost vs an equivalent C++ handler is the main trade-off
    measured in the experiment. Marker-pattern reliability risks (log buffer
    overflow between exec and log read) are mitigated by the per-call UUID
    and the LogCapture ring's 1000-line capacity.
    """
    marker = uuid.uuid4().hex[:12]
    py_code = (
        "import unreal, json\n"
        "sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)\n"
        "loc, rot = sub.get_level_viewport_camera_info()\n"
        "_data = {\n"
        "    'location': {'x': loc.x, 'y': loc.y, 'z': loc.z},\n"
        "    'rotation': {'pitch': rot.pitch, 'yaw': rot.yaw, 'roll': rot.roll},\n"
        "}\n"
        f"unreal.log('__CAM_{marker}__' + json.dumps(_data) + '__END__')\n"
    )

    exec_resp = call_ue("execute_unreal_python", {"code": py_code})
    if "error" in exec_resp:
        return make_response(req_id, error=exec_resp["error"])
    if not exec_resp.get("result", {}).get("ok", False):
        output = exec_resp.get("result", {}).get("output", "")
        return make_response(req_id, error={
            "code": -32603,
            "message": f"get_camera_transform: python_failed: {output}",
        })

    # Fetch the FULL ring (1000 lines) -- the LogCapture ring's capacity. A
    # smaller window risked missing the marker if >window LogPython lines
    # arrived between our exec and our read. (Caught by Codex P2 + Gemini
    # medium on PR #46, both bots converged on the same fix.)
    log_resp = call_ue("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    if "error" in log_resp:
        return make_response(req_id, error=log_resp["error"])

    lines = log_resp.get("result", {}).get("lines", []) or []
    needle = f"__CAM_{marker}__"
    end_token = "__END__"
    for entry in reversed(lines):
        msg = entry.get("message", "")
        if needle in msg:
            start = msg.index(needle) + len(needle)
            end = msg.find(end_token, start)
            if end < 0:
                continue
            payload = msg[start:end]
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as e:
                return make_response(req_id, error={
                    "code": -32603,
                    "message": f"get_camera_transform: marker_parse_failed: {e}",
                })
            return _wrap_tool_result(req_id, {"ok": True, **data})

    return make_response(req_id, error={
        "code": -32603,
        "message": (f"get_camera_transform: marker_not_found: '{needle}' did not appear in "
                    f"last {len(lines)} LogPython lines (log buffer may have overflowed; "
                    "retry typically resolves)"),
    })


def synthetic_set_camera_transform(req_id, args):
    """Bridge-side shim: set the level-editor viewport camera transform.

    Partial-update semantics: if the caller omits 'location' (or 'rotation'),
    the omitted side is preserved at its current value rather than reset
    to (0,0,0). Without this, calls supplying only one side would silently
    snap the other to the world origin -- destructive surprise. (Caught
    by Codex P1 on PR #46.)

    Implementation: when an omitted side is detected, run get_camera_transform
    first to read the current value (one extra round-trip), then forward
    the full set call. This is a second-order cost of going synthetic --
    in C++ we'd have direct access to UnrealEditorSubsystem's current state.
    """
    location = args.get("location")
    rotation = args.get("rotation")

    if location is None and rotation is None:
        # Both omitted -- treat as a no-op read. Return the current camera
        # state without mutating anything.
        return synthetic_get_camera_transform(req_id, {})

    if location is not None and not isinstance(location, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "set_camera_transform: 'location' must be an object {x, y, z}",
        })
    if rotation is not None and not isinstance(rotation, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "set_camera_transform: 'rotation' must be an object {pitch, yaw, roll}",
        })

    def _num(d, fld, default=0.0):
        v = d.get(fld, default)
        # bool is a subclass of int in Python; reject explicitly so
        # set_camera_transform({"location":{"x":True}}) doesn't silently
        # become x=1.0. NaN/Infinity rejected so they don't generate
        # malformed Python like 'unreal.Vector(nan, ...)'.
        # (Gemini medium on PR #46.)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"'{fld}' must be a number, got {type(v).__name__}")
        if not math.isfinite(v):
            raise ValueError(f"'{fld}' must be a finite number, got {v}")
        return float(v)

    # Read current camera state if we need to preserve either side. Extra
    # round-trip on partial updates -- the cost of the preservation
    # semantics. For full updates (both location AND rotation supplied),
    # we skip the read entirely.
    current_loc = None
    current_rot = None
    if location is None or rotation is None:
        get_resp_envelope = synthetic_get_camera_transform(0, {})
        if "error" in get_resp_envelope:
            return make_response(req_id, error={
                "code": -32603,
                "message": (f"set_camera_transform: failed to read current camera state for "
                            f"partial-update preservation: {get_resp_envelope['error'].get('message', '')}"),
            })
        try:
            inner = json.loads(get_resp_envelope["result"]["content"][0]["text"])
            current_loc = inner.get("location") or {}
            current_rot = inner.get("rotation") or {}
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            return make_response(req_id, error={
                "code": -32603,
                "message": f"set_camera_transform: failed to parse current camera state: {e}",
            })

    try:
        if location is not None:
            lx = _num(location, "x"); ly = _num(location, "y"); lz = _num(location, "z")
        else:
            lx = float(current_loc.get("x", 0)); ly = float(current_loc.get("y", 0)); lz = float(current_loc.get("z", 0))

        if rotation is not None:
            rp = _num(rotation, "pitch"); ry = _num(rotation, "yaw"); rr = _num(rotation, "roll")
        else:
            rp = float(current_rot.get("pitch", 0)); ry = float(current_rot.get("yaw", 0)); rr = float(current_rot.get("roll", 0))
    except ValueError as e:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"set_camera_transform: invalid_value_shape: {e}",
        })

    py_code = (
        "import unreal\n"
        "sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)\n"
        f"sub.set_level_viewport_camera_info(unreal.Vector({lx}, {ly}, {lz}), unreal.Rotator({rp}, {ry}, {rr}))\n"
    )

    exec_resp = call_ue("execute_unreal_python", {"code": py_code})
    if "error" in exec_resp:
        return make_response(req_id, error=exec_resp["error"])
    if not exec_resp.get("result", {}).get("ok", False):
        output = exec_resp.get("result", {}).get("output", "")
        return make_response(req_id, error={
            "code": -32603,
            "message": f"set_camera_transform: python_failed: {output}",
        })

    return _wrap_tool_result(req_id, {
        "ok": True,
        "location": {"x": lx, "y": ly, "z": lz},
        "rotation": {"pitch": rp, "yaw": ry, "roll": rr},
        "preserved": {
            "location": location is None,
            "rotation": rotation is None,
        },
    })


def synthetic_screenshot_actor(req_id, args):
    """Bridge-side composition: frame the viewport on an actor, then capture
    a screenshot. Useful for asset-pipeline thumbnail generation and for
    giving the LLM "look at this specific thing" context.

    Composition:
      1. focus_actor {name} -- selects + frames the viewport on the actor
      2. get_viewport_screenshot {} -- captures the (now-framed) viewport
         as base64 PNG

    Synthetic rather than C++ because both UE handlers already exist; a
    C++ handler would just duplicate their logic. Per the
    LANGUAGE-CHOICE-RETROSPECTIVE.md decision flow, this is a clean win
    for the synthetic-tool pattern (composition of existing handlers, no
    new UE-side state, no marker-pattern fragility).

    Note on timing: the camera-move-then-capture sequence is structurally
    correct only because the two call_ue() calls are separate JSON-RPC
    round-trips with at least one UE tick between them. A single C++
    handler doing both ops in one game-thread call would race the
    camera move against the readback.
    """
    name = args.get("name")
    if not isinstance(name, str) or not name:
        return make_response(req_id, error={
            "code": -32602,
            "message": "screenshot_actor: missing_required_field: 'name' must be a non-empty string",
        })

    focus_resp = call_ue("focus_actor", {"name": name})
    if "error" in focus_resp:
        # Preserve the upstream RPC error code so callers can distinguish
        # transport-level failures (-32099 UE unreachable, -32700 non-JSON)
        # from logical focus_actor failures (-32603 internal). Per PR #48
        # Codex P2 review: hardcoding -32603 here masked retryable
        # connectivity errors as logical errors.
        upstream_err = focus_resp["error"]
        return make_response(req_id, error={
            "code": upstream_err.get("code", -32603),
            "message": f"screenshot_actor: focus_failed: {upstream_err.get('message', '')}",
        })
    focus_result = focus_resp.get("result", {}) or {}

    shot_resp = call_ue("get_viewport_screenshot", {})
    if "error" in shot_resp:
        upstream_err = shot_resp["error"]
        return make_response(req_id, error={
            "code": upstream_err.get("code", -32603),
            "message": f"screenshot_actor: screenshot_failed: {upstream_err.get('message', '')}",
        })
    shot_result = shot_resp.get("result", {}) or {}

    return _wrap_tool_result(req_id, {
        "ok": True,
        "focused": focus_result.get("focused"),
        "name": focus_result.get("name"),
        "loc": {
            "x": focus_result.get("loc_x"),
            "y": focus_result.get("loc_y"),
            "z": focus_result.get("loc_z"),
        },
        "width": shot_result.get("width"),
        "height": shot_result.get("height"),
        "png_bytes": shot_result.get("png_bytes"),
        "png_base64": shot_result.get("png_base64"),
    })


# Map of tool-name -> bridge-side synthetic implementation. These are
# tools that don't have a corresponding UE handler -- the bridge composes
# existing UE handlers (or implements pure-protocol logic) to serve them.
SYNTHETIC_TOOLS = {
    "wait_for_events": synthetic_wait_for_events,
    "get_camera_transform": synthetic_get_camera_transform,
    "set_camera_transform": synthetic_set_camera_transform,
    "screenshot_actor": synthetic_screenshot_actor,
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

        # Synthetic tools are served bridge-side without a UE round-trip
        # (or, in wait_for_events's case, with multiple UE round-trips
        # composed into one logical operation).
        if tool_name in SYNTHETIC_TOOLS:
            return SYNTHETIC_TOOLS[tool_name](req_id, tool_args)

        ue_resp = call_ue(tool_name, tool_args)
        if "error" in ue_resp:
            return make_response(req_id, error=ue_resp["error"])

        return _wrap_tool_result(req_id, ue_resp.get("result", {}))

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
