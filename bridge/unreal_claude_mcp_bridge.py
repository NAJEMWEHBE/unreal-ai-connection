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
  - "tools/list"             returns a static list of all 86 tools (69
                             dispatched to the UE plugin's C++ handlers
                             plus 17 bridge-side synthetic tools served by
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
# 86 tool entries total. 69 are dispatched straight to UE C++ handlers
# (see UnrealClaudeMCPModule.cpp's Reg.Register(...) block). The remaining
# 17 -- wait_for_events, get_camera_transform, set_camera_transform,
# screenshot_actor, compile_mod_pak, compile_mod_pak_direct,
# bulk_delete_assets, bulk_move_assets, bulk_rename_assets,
# bulk_duplicate_assets, bulk_inspect_assets, inspect_data_asset,
# inspect_sound_class, inspect_sound_submix, inspect_audio_bus,
# inspect_material_function, inspect_metasound -- are bridge-side
# synthetic tools served by SYNTHETIC_TOOLS (see below) without a
# dedicated UE handler: they either compose existing handlers (focus +
# screenshot, repeated poll, loop over delete_asset / move_asset /
# rename_asset / duplicate_asset / inspect_asset), run the matching
# unreal.* Python via execute_unreal_python with the marker pattern
# (most inspect_* shims), or (compile_mod_pak / compile_mod_pak_direct)
# shell out to RunUAT.bat entirely outside the UE process.
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
        "name": "get_engine_version",
        "description": "Structured engine-version snapshot — major / minor / patch / changelist / branch as separate fields, plus a 'minor_dotted' convenience like '5.7'. Use this when the LLM needs to branch on engine version without parsing get_project_summary's single 'engine_version' string.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_levels",
        "description": "Enumerate every UWorld asset (level) in the project. Optional path_under defaults to '/Game/'; optional name_contains is case-insensitive substring filter. Closes the gap where load_level_by_path required the caller to already know the package path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_under": {"type": "string", "description": "Recursive package-path filter; defaults to /Game/. Must start with /Game/ or /Engine/."},
                "name_contains": {"type": "string", "description": "Case-insensitive substring filter on the level asset name."},
            },
        },
    },
    {
        "name": "save_dirty_assets",
        "description": "Persist every in-memory-modified asset + map to disk. Same as editor 'Save All'. Closes the gap where edit-side tools (set_actor_property, set_mi_parameter, edit_widget_tree, etc.) mutated UObjects but left them dirty. Optional include_levels + include_content default to true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_levels": {"type": "boolean", "description": "Save dirty .umap level packages (default true)."},
                "include_content": {"type": "boolean", "description": "Save dirty .uasset content packages (default true)."},
            },
        },
    },
    {
        "name": "get_selected_actors",
        "description": "Return name/label/class/transform of every actor currently selected in the editor's World Outliner / viewport. Companion to apply_python_to_selection — lets the LLM observe what is selected before running code against it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_input_mappings",
        "description": "Dump the project's legacy UInputSettings: action_mappings (name+key+modifier flags) and axis_mappings (name+key+scale), plus a uses_enhanced_input flag that signals whether the project has migrated to the Enhanced Input system. The #1 context an LLM needs before touching gameplay code.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "bulk_inspect_assets",
        "description": "Inspect multiple assets in one MCP call by composing the inspect_asset C++ handler bridge-side. Returns per-path inspection data plus aggregate counts; partial failures isolated per result. Mirrors the bulk_*_assets family shape. Use for pipeline audits (e.g. enumerate 500 textures and report which lack a power-of-two source).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Asset object paths to inspect (each non-empty, NUL + '..' segments rejected)."
                },
                "continue_on_error": {
                    "type": "boolean",
                    "description": "Default true. When false, stop at first per-path failure and return the partial results."
                },
            },
            "required": ["paths"],
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
        "name": "compile_mod_pak",
        "description": "Compile a UE mod plugin to a .pak file via RunUAT BuildMod (game Dev Kits like Conan Exiles) or BuildPlugin (vanilla UE5), headless. No UE Editor session required. Especially useful for game Dev Kits in 'installed-build mode' where BuildPlugin is blocked (e.g. Conan Exiles Enhanced UE5) — falling back to BuildMod cleanly. BuildMod path produces a .pak in output_dir; BuildPlugin path produces a redistributable plugin package (no .pak generated by default — ok=true based on exit_code alone).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "Absolute path to .uproject (e.g. C:/.../ConanSandbox.uproject)"},
                "mod_name": {"type": "string", "description": "Mod name; required for BuildMod (matches Content/Mods/<mod_name>/ folder); also used to disambiguate which .pak in output_dir is the intended artefact when multiple are present"},
                "plugin_path": {"type": "string", "description": "Absolute path to .uplugin; required for BuildPlugin"},
                "output_dir": {"type": "string", "description": "Directory for output .pak / package (created if missing; required so success can be verified)"},
                "uat_command": {"type": "string", "enum": ["BuildMod", "BuildPlugin"], "default": "BuildMod", "description": "UAT command (BuildMod for game Dev Kits, BuildPlugin for vanilla UE5)"},
                "run_uat_path": {"type": "string", "description": "Override path to RunUAT.bat; auto-discovered if not set"},
                "extra_args": {"type": "array", "items": {"type": "string"}, "description": "Additional CLI args appended to RunUAT"},
                "timeout_sec": {"type": "integer", "default": 1800, "description": "Max wait time (default 30 min)"},
            },
            "required": ["project_path", "output_dir"],
        },
    },
    {
        "name": "compile_mod_pak_direct",
        "description": "Compile a UE5 mod into a .pak by invoking UnrealPak.exe directly with a response file, bypassing RunUAT entirely. Use when the Dev Kit's RunUAT BuildMod is broken (Funcom Conan Exiles Enhanced UE5 ships a ScriptModules manifest invalid-record bug — UAT deletes its own deps.json before BuildMod can run). Pre-condition: caller has already cooked the .uasset files (e.g. via execute_unreal_python on a running Editor, or a separate `UnrealEditor-Cmd.exe -run=Cook` pass). UnrealPak is a standalone UE binary and works regardless of UAT state — runs in seconds and produces a .pak that deploys directly to the server's Mods/<name>/ folder. Complements compile_mod_pak (which uses RunUAT); use compile_mod_pak_direct when UAT is broken on your Dev Kit. SYNTHETIC bridge-side handler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "unreal_pak_path": {"type": "string", "description": "Absolute path to UnrealPak.exe (e.g. <DevKit>/Engine/Binaries/Win64/UnrealPak.exe)"},
                "response_file": {"type": "string", "description": "Absolute path to UnrealPak response file (.txt). Each line maps an absolute source path to a mount point inside the .pak, in the standard UnrealPak format: \"<absolute_source>\" \"<mount_in_pak>\""},
                "output_pak": {"type": "string", "description": "Absolute path where the .pak should be written (created if parent dir missing; required so success can be verified)"},
                "compression": {"type": "string", "enum": ["Zlib", "Gzip", "Oodle", "None"], "default": "Zlib", "description": "Compression algorithm (passed as -compress<Algo> flag); 'None' omits the flag entirely (uncompressed pak)"},
                "extra_args": {"type": "array", "items": {"type": "string"}, "description": "Additional CLI args appended to UnrealPak.exe (e.g. -encryptionkey)"},
                "timeout_sec": {"type": "integer", "default": 600, "description": "Max wait time in seconds; default 600 (10 min) — UnrealPak is typically much faster than RunUAT"},
            },
            "required": ["unreal_pak_path", "response_file", "output_pak"],
        },
    },
    {
        "name": "bulk_delete_assets",
        "description": "Delete multiple assets by composing the delete_asset C++ handler bridge-side. Returns per-path results plus aggregate counts. By default continues after individual failures (partial success is normal); set continue_on_error=false to stop on first failure. SYNTHETIC bridge-side handler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Asset object paths to delete, e.g. ['/Game/Foo', '/Game/Bar/Baz']. Each path must be a non-empty string; the same path-normalisation rules as the underlying delete_asset handler apply.",
                },
                "continue_on_error": {
                    "type": "boolean",
                    "default": True,
                    "description": "When true (default), keep deleting after an individual path fails and surface the per-path errors in the results array. When false, stop after the first failure and return the partial results collected so far.",
                },
            },
            "required": ["paths"],
        },
    },
    {
        "name": "bulk_duplicate_assets",
        "description": "Duplicate multiple assets in one call by composing the duplicate_asset C++ handler bridge-side. Schema mirrors bulk_rename_assets's per-entry mapping but uses `dest_path` (full destination path) instead of `new_name` (leaf name) since duplicate_asset takes a full destination, not a folder + name split. Unlike rename/move, duplicate does NOT leave a redirector at the source -- the source is preserved at its current path and a new copy is created at `dest_path`. Returns per-entry results plus aggregate counts. SYNTHETIC bridge-side handler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "duplicates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Source asset package path."},
                            "dest_path": {"type": "string", "description": "Destination asset package path (must not exist)."},
                        },
                        "required": ["path", "dest_path"],
                    },
                    "description": "List of {path, dest_path} pairs to duplicate. Both path and dest_path must be non-empty strings with no NUL byte and no '..' segment.",
                },
                "continue_on_error": {
                    "type": "boolean",
                    "default": True,
                    "description": "When true (default), keep duplicating after an individual entry fails and surface per-entry errors in results; when false, stop after the first failure and return partial results.",
                },
            },
            "required": ["duplicates"],
        },
    },
    {
        "name": "bulk_rename_assets",
        "description": "Rename multiple assets in one call by composing the rename_asset C++ handler bridge-side. Each rename leaves a redirector at the source per UE's standard semantics. Schema differs from bulk_delete_assets / bulk_move_assets: takes a `renames` list of {path, new_name} objects so each asset gets a per-entry leaf name. Returns per-entry results plus aggregate counts. Mirrors the bulk_*_assets result-shape convention. SYNTHETIC bridge-side handler.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "renames": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Source asset package path."},
                            "new_name": {"type": "string", "description": "New leaf name (no '/' or '.')."},
                        },
                        "required": ["path", "new_name"],
                    },
                    "description": "List of {path, new_name} pairs to rename. Each path must be a non-empty string with no NUL byte and no '..' segment. Each new_name must be a non-empty leaf name (no '/' or '.').",
                },
                "continue_on_error": {
                    "type": "boolean",
                    "default": True,
                    "description": "When true (default), keep renaming after an individual entry fails and surface per-entry errors in results; when false, stop after the first failure and return partial results.",
                },
            },
            "required": ["renames"],
        },
    },
    {
        "name": "bulk_move_assets",
        "description": "Move multiple assets into a single destination folder by composing the move_asset C++ handler bridge-side. Each move leaves a redirector at the source per UE's standard move semantics. Returns per-path results plus aggregate counts. By default continues after individual failures (partial success is normal); set continue_on_error=false to stop on first failure. SYNTHETIC bridge-side handler — mirrors bulk_delete_assets's shape so client code can switch between the two with a one-tool-name change.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Asset object paths to move, e.g. ['/Game/Foo', '/Game/Bar/Baz']. Each path must be a non-empty string; same path-shape rules as bulk_delete_assets (NUL and '..' segments rejected).",
                },
                "dest_folder": {
                    "type": "string",
                    "description": "Destination folder for ALL moved assets, e.g. '/Game/Archive'. Same folder applies to every path in the call; for per-asset destinations, call move_asset directly.",
                },
                "continue_on_error": {
                    "type": "boolean",
                    "default": True,
                    "description": "When true (default), keep moving after an individual path fails and surface the per-path errors in the results array. When false, stop after the first failure and return the partial results collected so far.",
                },
            },
            "required": ["paths", "dest_folder"],
        },
    },
    {
        "name": "inspect_data_asset",
        "description": "Shallow-reflect a UDataAsset by package path and return class, parent class, package path, and editable property list (name, Python type, stringified value). SYNTHETIC bridge-side handler (PR #92 language-shim experiment): composes execute_unreal_python + get_log_lines via the marker pattern. Property values for nested structs / arrays / dicts are stringified as '<container:type>' or '<unsupported>' — no recursion. Logical errors (asset not found, marker buffer overflow, payload unparseable) return as ok=False success envelopes; transport-level errors return as JSON-RPC errors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Package path to a UDataAsset asset, e.g. /Game/Data/DA_PlayerStats. Must be a non-empty string.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_sound_class",
        "description": "Inspect a USoundClass by package path: returns leaf class name, package path, parent USoundClass asset path (for chaining), child USoundClass asset paths, and the editable FSoundClassProperties values (Volume, Pitch, low-pass filter, attenuation distance scale, voice-center-channel volume, radio-filter volume, eight boolean flags, OutputTarget enum). SYNTHETIC bridge-side handler: composes execute_unreal_python + get_log_lines via the marker pattern. UE Python field names are snake_case but the JSON output remaps to UE's native PascalCase FSoundClassProperties layout. Logical errors (asset_not_found, wrong_asset_type, marker_not_found, invalid_json) return as ok=False success envelopes; transport-level errors return as JSON-RPC errors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Package path to a USoundClass asset, e.g. /Game/Audio/SC_Music. Must be a non-empty string.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_sound_submix",
        "description": "Inspect a USoundSubmix by package path: returns leaf class name, package path, parent USoundSubmix asset path (for chaining), child submix asset paths, and additional editor-accessible UPROPERTYs discovered via dir() permissive enumeration. SYNTHETIC bridge-side handler: composes execute_unreal_python + get_log_lines via the marker pattern. Logical errors (asset_not_found, wrong_asset_type, marker_not_found, invalid_json) return as ok=False success envelopes; transport-level errors return as JSON-RPC errors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Package path to a USoundSubmix asset, e.g. /Game/Audio/SX_Music. Must be a non-empty string.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_audio_bus",
        "description": "Inspect a UAudioBus by package path: returns leaf class name, package path, audio_bus_channels enum stringified (Mono/Stereo/Quad/FivePointOne/SevenPointOne), and additional editor-accessible UPROPERTYs discovered via dir() permissive enumeration. SYNTHETIC bridge-side handler: composes execute_unreal_python + get_log_lines via the marker pattern. Logical errors (asset_not_found, wrong_asset_type, marker_not_found, invalid_json) return as ok=False success envelopes; transport-level errors return as JSON-RPC errors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Package path to a UAudioBus asset, e.g. /Game/Audio/AB_Master. Must be a non-empty string.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_material_function",
        "description": "Inspect a UMaterialFunction by package path: returns leaf class name, package path, description, expose_to_library flag, library_categories (stringified Text values), function inputs (name + input_type enum stringified), function outputs (name), and additional editor-accessible UPROPERTYs via dir() permissive enumeration. SYNTHETIC bridge-side handler: composes execute_unreal_python + get_log_lines via the marker pattern. Logical errors (asset_not_found, wrong_asset_type, marker_not_found, invalid_json) return as ok=False success envelopes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Package path to a UMaterialFunction asset, e.g. /Game/Materials/MF_PackedNormal. Must be a non-empty string.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_metasound",
        "description": "Inspect a MetaSoundSource or MetaSoundPatch asset by package path: returns leaf class name (which of the two it is), package path, and additional editor-accessible UPROPERTYs via dir() permissive enumeration. SYNTHETIC bridge-side handler: composes execute_unreal_python + get_log_lines via the marker pattern. Accepts either MetaSoundSource (emitter-attached) or MetaSoundPatch (reusable subgraph). Graph structure (nodes / connections) is NOT reflected here -- that requires a dedicated traversal pass. For surface-level metadata + exposed UPROPERTYs the permissive enumeration covers the common case. Logical errors (asset_not_found, wrong_asset_type, metasound_unavailable, marker_not_found, invalid_json) return as ok=False success envelopes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Package path to a MetaSoundSource or MetaSoundPatch asset, e.g. /Game/Audio/MS_Music. Must be a non-empty string.",
                },
            },
            "required": ["path"],
        },
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
        "name": "inspect_physics_asset",
        "description": "Read structural properties of a UPhysicsAsset: preview skeletal mesh cross-link, body setups (one per simulated bone with bConsiderForBounds + is_in_bounds_subset flags), constraint setups (joint between two bodies with child/parent bone names), bounds-bodies subset count, named physical-animation profiles, named constraint profiles. Pairs with inspect_skeletal_mesh via shared preview_skeletal_mesh path. C++ handler; no new Build.cs deps (Engine + PhysicsCore cover UPhysicsAsset / USkeletalBodySetup / UPhysicsConstraintTemplate). Null-skips TObjectPtr<USkeletalBodySetup> and TObjectPtr<UPhysicsConstraintTemplate> entries (PR #55->#57 lesson).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UPhysicsAsset, e.g. /Game/Characters/Hero/PHYS_Hero."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_sound_cue",
        "description": "Read structural properties of a USoundCue asset: total duration, max distance, volume + pitch multipliers, subtitle priority, max audible distance, attenuation-settings cross-link, root sound-node class, and the full graph of sound nodes (sorted by name with class taxonomy). C++ handler; no new Build.cs deps (Engine covers USoundCue / USoundBase / USoundNode / USoundAttenuation). Null-skips TObjectPtr<USoundNode> entries (PR #55->#57 lesson).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a USoundCue, e.g. /Game/Audio/SC_Footstep."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_sound_wave",
        "description": "Read structural properties of a USoundWave asset: sample rate, channel count, frame count, duration, compression type + runtime format + (conditional) compressed data size, sound group, looping flag, streaming flag (via IsStreaming() not the deprecated bStreaming), loading behavior, subtitle count + supports flag, cue-point count + loop-region count (separated via GetCuePoints / GetLoopRegions). Editor-only fields (imported_sample_rate, lufs, sample_peak_db, comment) emit conditionally when non-default. C++ handler; no new Build.cs deps (Engine covers USoundWave / USoundBase / FSoundWaveCuePoint / FSubtitleCue). USoundWave's LoadBehavior=LazyOnDemand caveat handled by reading only declarative fields (no transient runtime state).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a USoundWave, e.g. /Game/Audio/SW_Footstep."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_sound_attenuation",
        "description": "Read structural properties of a USoundAttenuation asset (3D playback rules): distance algorithm + shape, spatialization, air-absorption LPF/HPF, listener focus, occlusion tracing, reverb send, priority attenuation, plus a feature_flags sub-object for assorted bool toggles. Each major feature group is collapsed to {\"enabled\":false} when its master gate (bAttenuate / bSpatialize / bAttenuateWithLPF / bEnableListenerFocus / bEnableOcclusion / bEnableReverbSend / bEnablePriorityAttenuation) is off, so the JSON stays compact for default-disabled assets. Completes the audio introspection trio with inspect_sound_cue + inspect_sound_wave. C++ handler; no new Build.cs deps (Engine covers USoundAttenuation / FSoundAttenuationSettings / FBaseAttenuationSettings).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a USoundAttenuation, e.g. /Game/Audio/Atten_Default."},
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


def write_msg(obj: dict) -> None:
    """Write one MCP message to stdout (newline-delimited)."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def make_response(req_id, result=None, error: dict | None = None) -> dict:
    """Build a JSON-RPC 2.0 response envelope. `error` (if non-None) wins over
    `result`. `req_id` is passed through verbatim — JSON-RPC / MCP allow int,
    str, or null ids and the bridge must not coerce."""
    msg: dict = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def call_ue(method: str, params: dict | None) -> dict:
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


def _wrap_tool_result(req_id, result_obj: dict | list | str | int | float | bool | None) -> dict:
    """Wrap a result object as an MCP tools/call response (JSON-stringified into a text block)."""
    return make_response(req_id, {
        "content": [{"type": "text", "text": json.dumps(result_obj, indent=2)}],
        "isError": False,
    })


def _run_marker_pattern(req_id, tool_name: str, marker_prefix: str, py_code: str, context: str = "") -> dict:
    """Canonical Python-shim pattern for synthetic tools that need to run
    arbitrary `unreal.*` Python in the UE editor and read its JSON output.

    Used by every execute_unreal_python-based synthetic (camera transform
    read/write, all inspect_* shims for Python-only asset reflection).
    Originally hand-rolled per-synthetic; extracted into this helper in
    PR #100 once the duplication crossed 5 sites with ~30 lines of shared
    boilerplate each.

    Flow:
      1. `call_ue("execute_unreal_python", {"code": py_code})` -- first
         round-trip. The embedded Python must `unreal.log()` exactly one
         line containing `<marker_prefix><JSON payload>__END__`.
      2. Transport-error short-circuit: return JSON-RPC error if call_ue
         couldn't reach UE.
      3. Python-side failure short-circuit: if the embedded script raised,
         return -32603 with the Python traceback (from `result.output`).
      4. `call_ue("get_log_lines", {"category_filter": "LogPython",
         "count": 1000})` -- second round-trip. The LogCapture ring's
         1000-line capacity is what bounds reliability against concurrent
         Python execution flooding the buffer.
      5. Reverse-scan for `marker_prefix`. Extract payload between
         `marker_prefix` and `__END__`. JSON-decode and return via
         `_wrap_tool_result` (so logical errors with `ok: False` come back
         as MCP success envelopes that callers can inspect).
      6. If marker not found, return a marker_not_found logical-error
         envelope with a "retry typically resolves" hint.
      7. If marker found but payload doesn't JSON-decode, return
         invalid_json logical-error envelope.

    Args:
        req_id: the JSON-RPC id from the caller.
        tool_name: the synthetic tool's name, used as the prefix in error
            messages (e.g. "inspect_data_asset"). Must match the tool's
            registered name so error messages are debuggable.
        marker_prefix: the per-call marker string the embedded Python
            emits before the JSON payload. MUST include the trailing
            double-underscore -- e.g. `f"__DATA_{uuid.uuid4().hex[:12]}__"`.
            Including the per-call UUID is what de-duplicates against log
            buffer carryover from prior calls.
        py_code: the embedded Python source to execute in the editor. Must
            emit exactly one `unreal.log()` line containing the marker
            prefix + JSON payload + `__END__`.
        context: optional caller context (typically the asset path) that
            gets interpolated into the invalid_json error message for
            debuggability. Empty string = no context.

    Returns: an MCP tools/call response envelope. Always returns -- never
    raises. Logical errors (asset_not_found, wrong_asset_type,
    marker_not_found, invalid_json) come back as `ok: False` success
    envelopes; transport-level errors (UE down, Python traceback) come
    back as JSON-RPC errors.
    """
    exec_resp = call_ue("execute_unreal_python", {"code": py_code})
    if "error" in exec_resp:
        return make_response(req_id, error=exec_resp["error"])
    if not exec_resp.get("result", {}).get("ok", False):
        output = exec_resp.get("result", {}).get("output", "")
        return make_response(req_id, error={
            "code": -32603,
            "message": f"{tool_name}: python_failed: {output}",
        })

    log_resp = call_ue("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    if "error" in log_resp:
        return make_response(req_id, error=log_resp["error"])

    lines = log_resp.get("result", {}).get("lines", []) or []
    end_token = "__END__"
    for entry in reversed(lines):
        msg = entry.get("message", "") or ""
        if marker_prefix in msg:
            # Two distinct failure modes share this block; split the except
            # clauses so the error_code returned to the caller matches the
            # actual cause:
            #   1. marker present but __END__ missing -> str.index raises
            #      ValueError. Caller-actionable code: 'marker_truncated'.
            #   2. payload extracted but JSON-parse fails -> json.JSONDecodeError.
            #      Caller-actionable code: 'invalid_json'.
            # (Previously both fell through to 'invalid_json' because
            # json.JSONDecodeError is a ValueError subclass. The conflation
            # made marker-truncation look like a payload-content bug, which
            # is the wrong place to start triaging.)
            try:
                start = msg.index(marker_prefix) + len(marker_prefix)
                end = msg.index(end_token, start)
                payload = msg[start:end]
            except ValueError:
                ctx_suffix = f" for path '{context}'" if context else ""
                return _wrap_tool_result(req_id, {
                    "ok": False,
                    "error_code": "marker_truncated",
                    "error_message": (
                        f"{tool_name}: marker_truncated: end token '{end_token}' missing "
                        f"after marker prefix '{marker_prefix}'{ctx_suffix} (caller can "
                        "retry; LogPython buffer may have truncated the line)"
                    ),
                })
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                ctx_suffix = f" for path '{context}'" if context else ""
                return _wrap_tool_result(req_id, {
                    "ok": False,
                    "error_code": "invalid_json",
                    "error_message": f"{tool_name}: invalid_json: marker payload unparseable{ctx_suffix}",
                })
            return _wrap_tool_result(req_id, data)

    return _wrap_tool_result(req_id, {
        "ok": False,
        "error_code": "marker_not_found",
        "error_message": (f"{tool_name}: marker_not_found: '{marker_prefix}' did not appear in "
                          f"last {len(lines)} LogPython lines (log buffer may have overflowed; "
                          "retry typically resolves)"),
    })


def synthetic_wait_for_events(req_id, args: dict) -> dict:
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
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "wait_for_events: invalid_arguments: arguments must be an object",
        })

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


def synthetic_get_camera_transform(req_id, args: dict) -> dict:
    """Bridge-side shim: read the level-editor viewport camera transform.

    Refactored on 2026-05-12 (deferred bridge-audit #3) to use the shared
    `_run_marker_pattern` helper instead of hand-rolling the marker pattern.
    Behaviour changes from the pre-refactor hand-rolled form:

    - On success: response envelope no longer wraps the payload in
      `{ok: True, ...data}`. The result is now `{location, rotation}`
      directly. (No test or known caller pinned the `ok: True` key.)
    - On `marker_not_found`: now returns a logical-error envelope
      `{ok: False, error_code: 'marker_not_found', ...}` instead of a
      JSON-RPC `-32603` transport error. Matches every other
      `_run_marker_pattern` caller and is the right shape for retry logic
      ("not a transport problem, just retry").
    - On `marker_truncated` / `invalid_json`: same logical-error envelope
      shape (added in PR #128).

    `synthetic_set_camera_transform` is updated in lockstep to handle the
    new logical-error envelope shape -- it previously checked only for
    transport errors and would have silently snapped the camera to (0,0,0)
    if a `marker_not_found` envelope was returned from get.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "get_camera_transform: invalid_arguments: arguments must be an object",
        })

    marker_prefix = f"__CAM_{uuid.uuid4().hex[:12]}__"
    py_code = (
        "import unreal, json\n"
        "sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)\n"
        "loc, rot = sub.get_level_viewport_camera_info()\n"
        "_data = {\n"
        "    'location': {'x': loc.x, 'y': loc.y, 'z': loc.z},\n"
        "    'rotation': {'pitch': rot.pitch, 'yaw': rot.yaw, 'roll': rot.roll},\n"
        "}\n"
        f"unreal.log('{marker_prefix}' + json.dumps(_data) + '__END__')\n"
    )
    return _run_marker_pattern(req_id, "get_camera_transform", marker_prefix, py_code)


def synthetic_set_camera_transform(req_id, args: dict) -> dict:
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
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "set_camera_transform: invalid_arguments: arguments must be an object",
        })

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
        # Layer 1: transport-level failure (UE down, call_ue couldn't reach).
        if "error" in get_resp_envelope:
            return make_response(req_id, error={
                "code": -32603,
                "message": (f"set_camera_transform: failed to read current camera state for "
                            f"partial-update preservation: {get_resp_envelope['error'].get('message', '')}"),
            })
        # Layer 2: parse the success envelope's inner payload.
        try:
            inner = json.loads(get_resp_envelope["result"]["content"][0]["text"])
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            return make_response(req_id, error={
                "code": -32603,
                "message": f"set_camera_transform: failed to parse current camera state: {e}",
            })
        # Layer 3: logical-error envelope from the marker-pattern helper
        # (post-refactor of get_camera_transform on 2026-05-12). The
        # underlying read could have hit marker_not_found / marker_truncated /
        # invalid_json -- previously these were JSON-RPC transport errors
        # caught by layer 1, but the helper-refactor moved them to
        # ok-envelope-with-error_code. Without this layer, the code would
        # fall through to `inner.get("location") or {}` -> empty dict ->
        # camera silently snaps to (0, 0, 0) on the omitted side.
        if isinstance(inner, dict) and (inner.get("ok") is False or "error_code" in inner):
            return make_response(req_id, error={
                "code": -32603,
                "message": (f"set_camera_transform: get_camera_transform returned "
                            f"{inner.get('error_code', 'unknown')} -- cannot preserve omitted "
                            f"side of partial update: {inner.get('error_message', '')}"),
            })
        current_loc = inner.get("location") or {}
        current_rot = inner.get("rotation") or {}

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

    # CRITICAL: UE 5.7 Python `unreal.Rotator(a, b, c)` is `(roll, pitch, yaw)`
    # POSITIONALLY -- the args follow FRotator's struct-memory order, not the
    # named-property order. Live MCP testing on 2026-05-12 confirmed this via a
    # one-line probe: `unreal.Rotator(1, 2, 3)` returns `pitch=2 yaw=3 roll=1`.
    # The earlier positional `Rotator({rp}, {ry}, {rr})` form silently
    # scrambled rotation -- a caller asking for pitch=-20/yaw=45/roll=0 got
    # back pitch=45/yaw=0/roll=-20 from the next get_camera_transform. We sidestep
    # the trap by constructing the rotator then setting properties by name; the
    # observable round-trip is now lossless.
    py_code = (
        "import unreal\n"
        "sub = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)\n"
        "_r = unreal.Rotator()\n"
        f"_r.pitch = {rp}\n"
        f"_r.yaw = {ry}\n"
        f"_r.roll = {rr}\n"
        f"sub.set_level_viewport_camera_info(unreal.Vector({lx}, {ly}, {lz}), _r)\n"
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


def synthetic_screenshot_actor(req_id, args: dict) -> dict:
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
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "screenshot_actor: invalid_arguments: arguments must be an object",
        })

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


def synthetic_compile_mod_pak(req_id, args: dict) -> dict:
    """Bridge-side: compile a UE mod plugin to a .pak file via RunUAT BuildMod
    or BuildPlugin, headless. No UE Editor session required.

    Targets game-specific Dev Kit setups (Conan Exiles, Satisfactory, etc.) that
    ship a custom RunUAT command for cooking + packaging mods. Falls back to
    standard `BuildPlugin` for vanilla UE5 projects.

    Args:
      project_path:   absolute path to .uproject (e.g. C:/.../ConanSandbox.uproject)
      mod_name:       mod name; must match Content/Mods/<mod_name>/ folder for BuildMod
      plugin_path:    optional, for BuildPlugin: absolute path to .uplugin
      output_dir:     where to write the .pak (created if missing)
      uat_command:    "BuildMod" (default, game-specific) or "BuildPlugin" (vanilla UE)
      run_uat_path:   override path to RunUAT.bat; defaults to discovered from project_path
      extra_args:     additional CLI args appended to RunUAT (list of str)
      timeout_sec:    max wait, default 1800 (30 min)

    Returns:
      ok (bool), pak_path (str | null), exit_code (int), stdout_tail (str),
      stderr_tail (str), duration_sec (float)

    Why synthetic: this tool just shells out to RunUAT.bat — no UE-side state
    or in-editor handlers needed. Bridge-side keeps the C++ plugin focused on
    runtime/editor automation and lets CI-style operations live where they
    naturally fit (the host machine running the bridge).

    Useful in CI/CD pipelines: spawn bridge headless via Claude Code, call
    compile_mod_pak, get a .pak in N minutes. Especially valuable for game
    Dev Kits in 'installed-build mode' that block BuildPlugin (e.g. Conan
    Exiles Enhanced) — falling back to BuildMod cleanly.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: invalid_arguments: arguments must be an object",
        })

    import os
    import shutil
    import subprocess
    import time

    project_path = args.get("project_path")
    mod_name = args.get("mod_name")
    plugin_path = args.get("plugin_path")
    output_dir = args.get("output_dir")
    uat_command = args.get("uat_command", "BuildMod")
    run_uat_path = args.get("run_uat_path")
    # extra_args: omitted is OK (defaults to []); wrong TYPE is a contract
    # violation at the tools/call boundary -> fast-fail -32602 instead of
    # silently coercing (per Gemini PR #105 inline review, line 1500).
    extra_args = args.get("extra_args")
    if extra_args is None:
        extra_args = []
    elif not isinstance(extra_args, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: extra_args must be an array of strings",
        })
    # timeout_sec: permissive in FORM (int / float / numeric string all OK —
    # JSON clients that stringify numbers shouldn't break), strict in TYPE
    # (un-parseable -> -32602 rather than silent 1800 fallback that masks
    # caller bugs). float→int truncates by design.
    raw_timeout = args.get("timeout_sec", 1800)
    try:
        timeout_sec = int(float(raw_timeout))
    except (ValueError, TypeError):
        return make_response(req_id, error={
            "code": -32602,
            "message": f"compile_mod_pak: timeout_sec must be numeric (int, float, or numeric string); got {type(raw_timeout).__name__}",
        })
    # Non-positive timeout would cause subprocess.TimeoutExpired immediately
    # (DoS via API).
    if timeout_sec <= 0:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: timeout_sec must be positive (got non-positive after int cast)",
        })

    if not project_path or not os.path.isfile(project_path):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: project_path missing or invalid file",
        })

    # output_dir is required at schema level too -- both BuildMod (for .pak
    # discovery) and BuildPlugin (for package output) need a known
    # destination. Schema enforces presence; this guards against empty string.
    if not output_dir:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: output_dir required (where the .pak or package lands)",
        })

    if uat_command == "BuildMod" and not mod_name:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: mod_name required for BuildMod",
        })

    if uat_command == "BuildPlugin" and not plugin_path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak: plugin_path required for BuildPlugin",
        })

    # Auto-discover RunUAT.bat from project Engine sibling
    if not run_uat_path:
        proj_dir = os.path.dirname(project_path)
        # Look 2 levels up for Engine/Build/BatchFiles/RunUAT.bat
        candidate = os.path.join(os.path.dirname(proj_dir), "Engine", "Build", "BatchFiles", "RunUAT.bat")
        if os.path.isfile(candidate):
            run_uat_path = candidate
        else:
            run_uat_path = shutil.which("RunUAT") or shutil.which("RunUAT.bat")

    if not run_uat_path or not os.path.isfile(run_uat_path):
        return make_response(req_id, error={
            "code": -32603,
            "message": f"compile_mod_pak: RunUAT.bat not found (set run_uat_path or place near {project_path})",
        })

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    cmd = [run_uat_path, uat_command, f"-Project={project_path}"]
    if uat_command == "BuildMod":
        cmd.append(f"-Mod={mod_name}")
        cmd.extend(["-Cook", "-Pak", "-FinalPak"])
        if output_dir:
            cmd.append(f"-Output={output_dir}")
    elif uat_command == "BuildPlugin":
        cmd.append(f"-Plugin={plugin_path}")
        if output_dir:
            cmd.append(f"-Package={output_dir}")
    cmd.extend(extra_args)

    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        duration = time.time() - start
    except subprocess.TimeoutExpired:
        return make_response(req_id, error={
            "code": -32603,
            "message": f"compile_mod_pak: timeout after {timeout_sec}s",
        })
    except Exception as e:
        return make_response(req_id, error={
            "code": -32603,
            "message": f"compile_mod_pak: subprocess exception: {e!r}",
        })

    # Look for the generated .pak in output_dir. Prefer:
    #   1) a .pak whose name contains mod_name (BuildMod path) -- catches the
    #      intended artefact when output_dir is shared across multiple builds
    #   2) otherwise the most-recently-modified .pak (likely THIS build's
    #      output rather than a stale artefact from a previous run)
    pak_path = None
    if os.path.isdir(output_dir):
        paks = []
        for fn in os.listdir(output_dir):
            if not fn.endswith(".pak"):
                continue
            full = os.path.join(output_dir, fn)
            paks.append((full, os.path.getmtime(full)))

        if mod_name:
            mod_lower = mod_name.lower()
            matched = [(p, m) for (p, m) in paks if mod_lower in os.path.basename(p).lower()]
            if matched:
                paks = matched

        if paks:
            # newest first by mtime
            paks.sort(key=lambda item: item[1], reverse=True)
            # ignore stale .paks predating this build (mtime < start - 1s safety)
            for full, mtime in paks:
                if mtime >= start - 1.0:
                    pak_path = full
                    break
            if pak_path is None:
                # no fresh pak; surface newest anyway so the caller can decide
                pak_path = paks[0][0]

    # Success criterion differs per UAT command:
    #   BuildMod    -> needs both exit_code==0 AND a .pak in output_dir;
    #                  the .pak is the deployable artefact callers want.
    #   BuildPlugin -> exit_code==0 is enough; this command produces a
    #                  redistributable plugin package (.uplugin + Binaries/
    #                  + Resources/) under output_dir, NOT a .pak. Insisting
    #                  on a .pak here would mark every successful run as
    #                  ok=false (Gemini PR #84 review).
    if uat_command == "BuildMod":
        ok = (proc.returncode == 0) and (pak_path is not None)
    else:  # BuildPlugin
        ok = (proc.returncode == 0)

    return _wrap_tool_result(req_id, {
        "ok": ok,
        "pak_path": pak_path,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "duration_sec": round(duration, 2),
        "uat_command": uat_command,
        "cmd": cmd,
    })


def synthetic_compile_mod_pak_direct(req_id, args: dict) -> dict:
    """Bridge-side: compile a .pak directly via UnrealPak.exe with a response
    file, bypassing RunUAT entirely.

    Why this complements compile_mod_pak: some Dev Kits ship RunUAT broken.
    Funcom Conan Exiles Enhanced UE5 Dev Kit (mayo 2026) in 'installed-build
    mode' fails BuildMod because UAT scans for a ScriptModules manifest and
    deletes its own deps.json as 'invalid record' before BuildMod can run. The
    workaround verified end-to-end on AEGIS-Admin (Workshop 3724162370):
      1. Cook the .uasset files separately (execute_unreal_python on a
         running Editor, or a discrete UnrealEditor-Cmd.exe -run=Cook pass)
      2. Package them into a .pak with UnrealPak.exe directly
    UnrealPak itself is a standalone UE binary shipped under
    Engine/Binaries/Win64/ and works regardless of UAT state.

    Args:
      unreal_pak_path:  abs path to UnrealPak.exe
      response_file:    abs path to response.txt with `"<src>" "<mount>"` lines
      output_pak:       abs path where .pak should be written (parent dir
                        created if missing)
      compression:      Zlib (default) | Gzip | Oodle | None (omit flag)
      extra_args:       additional CLI args appended
      timeout_sec:      max wait, default 600 (10 min — UnrealPak is fast)

    Returns:
      ok (bool), pak_path (str | null), pak_size_bytes (int | null),
      exit_code (int), stdout_tail (str), stderr_tail (str),
      duration_sec (float), cmd (list)

    Success criterion: exit_code == 0 AND output_pak exists with size > 0.
    Same shape as compile_mod_pak (BuildMod branch) so downstream tooling
    can switch between the two transparently.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak_direct: invalid_arguments: arguments must be an object",
        })

    import os
    import subprocess
    import time

    unreal_pak_path = args.get("unreal_pak_path")
    response_file = args.get("response_file")
    output_pak = args.get("output_pak")
    compression = args.get("compression", "Zlib")
    extra_args = args.get("extra_args")
    if not isinstance(extra_args, list):
        extra_args = []
    try:
        timeout_sec = int(args.get("timeout_sec", 600))
    except (ValueError, TypeError):
        timeout_sec = 600

    if not unreal_pak_path or not os.path.isfile(unreal_pak_path):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak_direct: unreal_pak_path missing or invalid file",
        })

    if not response_file or not os.path.isfile(response_file):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak_direct: response_file missing or invalid file",
        })

    if not output_pak:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compile_mod_pak_direct: output_pak required (success verification needs a known path)",
        })

    # Create parent dir if missing
    parent = os.path.dirname(output_pak)
    if parent:
        os.makedirs(parent, exist_ok=True)

    cmd = [unreal_pak_path, output_pak, f"-Create={response_file}"]
    if compression and compression != "None":
        cmd.append(f"-compress{compression}")
    cmd.extend(extra_args)

    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        duration = time.time() - start
    except subprocess.TimeoutExpired:
        return make_response(req_id, error={
            "code": -32603,
            "message": f"compile_mod_pak_direct: timeout after {timeout_sec}s",
        })
    except Exception as e:
        return make_response(req_id, error={
            "code": -32603,
            "message": f"compile_mod_pak_direct: subprocess exception: {e!r}",
        })

    # Verify pak exists + has nonzero size. UnrealPak occasionally exits 0
    # but writes a zero-byte .pak on malformed response files (rare); the
    # size check catches that.
    pak_path = None
    pak_size_bytes = None
    if os.path.isfile(output_pak):
        pak_size_bytes = os.path.getsize(output_pak)
        if pak_size_bytes > 0:
            pak_path = output_pak

    ok = (proc.returncode == 0) and (pak_path is not None)

    return _wrap_tool_result(req_id, {
        "ok": ok,
        "pak_path": pak_path,
        "pak_size_bytes": pak_size_bytes,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-2000:],
        "duration_sec": round(duration, 2),
        "cmd": cmd,
    })


def synthetic_bulk_delete_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: delete multiple assets via the `delete_asset`
    C++ handler, returning a per-path partial-success structure.

    Loops over `paths` and dispatches one `call_ue("delete_asset", ...)` per
    entry, collecting result records. By default, individual failures do NOT
    abort the loop (`continue_on_error: true`) — partial success is normal
    and propagated via `ok: False` + non-zero `failed` count. With
    `continue_on_error: false` the loop stops on the first failure and
    returns whatever has accumulated.

    Synthetic rather than C++ because the bulk loop is pure protocol-level
    composition over an existing handler. A C++ bulk handler would just
    duplicate `delete_asset`'s logic per path and force partial-failure
    aggregation back into a single envelope on the game thread — needlessly
    coupling N delete operations into one round-trip. The bridge-side loop
    keeps each delete as a discrete UE round-trip, which means in-editor
    events fire per-asset and the caller can watch progress via the event
    bus.

    Originally a Codex parallel-dispatch test (PR #90, 2026-05-11): one of
    two streams in the first three-stream dispatch experiment alongside an
    independent Copilot CLI stream. See HANDOFF.md for the parallel-AI
    workflow learnings.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_delete_assets: invalid_arguments: arguments must be an object",
        })

    if "paths" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_delete_assets: missing_required_field: 'paths' must be supplied as a list of non-empty strings",
        })

    paths = args.get("paths")
    if not isinstance(paths, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_delete_assets: invalid_field: 'paths' must be a list of non-empty strings",
        })

    for i, path in enumerate(paths):
        if not isinstance(path, str) or not path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_delete_assets: invalid_path: paths[{i}] must be a non-empty string",
            })
        # Defensive shape checks. UE asset paths look like `/Game/...`,
        # `/Engine/...`, or `/<MountPoint>/...`. Embedded NUL or `..`
        # segments are never legitimate and almost always indicate either
        # input corruption or path-traversal intent; reject early with a
        # caller-actionable -32602 rather than forwarding a malformed path
        # to delete_asset and letting it surface a confusing UE-side error.
        if "\x00" in path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_delete_assets: invalid_path: paths[{i}] contains a NUL byte",
            })
        # Block `..` as a path SEGMENT (between slashes or at ends), not as
        # a substring -- legitimate asset names like `My..Asset` should
        # still pass. The check covers leading `..`, trailing `..`, and
        # `/../` mid-path.
        segments = path.split("/")
        if any(segment == ".." for segment in segments):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_delete_assets: invalid_path: paths[{i}] contains a '..' segment",
            })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_delete_assets: invalid_field: 'continue_on_error' must be a boolean",
        })

    results = []
    for path in paths:
        delete_resp = call_ue("delete_asset", {"path": path})
        if "error" in delete_resp:
            upstream_err = delete_resp.get("error", {}) or {}
            error_code = upstream_err.get("code", -32603)
            if error_code is None:
                error_code = -32603
            results.append({
                "path": path,
                "ok": False,
                "error_code": error_code,
                "error_message": upstream_err.get("message") or "",
            })
            if not continue_on_error:
                break
            continue

        results.append({
            "path": path,
            "ok": True,
            "error_code": None,
            "error_message": None,
        })

    deleted = sum(1 for result in results if result["ok"])
    failed = sum(1 for result in results if not result["ok"])

    return _wrap_tool_result(req_id, {
        "ok": failed == 0,
        "total": len(paths),
        "deleted": deleted,
        "failed": failed,
        "results": results,
    })


def synthetic_bulk_move_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: move multiple assets into a single destination
    folder by dispatching `move_asset` per path.

    Mirrors `synthetic_bulk_delete_assets`'s validation + result shape so
    client code can swap one tool name for the other with no envelope-shape
    surprises. The same defensive path-shape checks apply (NUL byte and
    `..`-segment rejection from PR #115).

    Unlike bulk_delete_assets, `dest_folder` is REQUIRED at the schema
    level: a "move with no destination" is meaningless. Per-path
    destinations aren't supported in the bulk shape; callers needing
    that should drive `move_asset` directly. UE's standard move semantics
    apply (a redirector is left at each source path).

    Synthetic rather than C++ for the same reasons bulk_delete_assets is:
    the bulk loop is pure protocol-level composition over the existing
    `move_asset` handler. Bridge-side keeps each move as a discrete UE
    round-trip so in-editor events fire per-asset and the caller can
    watch progress via the event bus.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: invalid_arguments: arguments must be an object",
        })

    if "paths" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: missing_required_field: 'paths' must be supplied as a list of non-empty strings",
        })

    if "dest_folder" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: missing_required_field: 'dest_folder' must be supplied as a non-empty string",
        })

    paths = args.get("paths")
    if not isinstance(paths, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: invalid_field: 'paths' must be a list of non-empty strings",
        })

    dest_folder = args.get("dest_folder")
    if not isinstance(dest_folder, str) or not dest_folder:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: invalid_field: 'dest_folder' must be a non-empty string",
        })
    # Same defensive shape checks on dest_folder as on source paths.
    if "\x00" in dest_folder:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: invalid_dest_folder: contains a NUL byte",
        })
    if any(segment == ".." for segment in dest_folder.split("/")):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: invalid_dest_folder: contains a '..' segment",
        })

    for i, path in enumerate(paths):
        if not isinstance(path, str) or not path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_move_assets: invalid_path: paths[{i}] must be a non-empty string",
            })
        if "\x00" in path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_move_assets: invalid_path: paths[{i}] contains a NUL byte",
            })
        if any(segment == ".." for segment in path.split("/")):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_move_assets: invalid_path: paths[{i}] contains a '..' segment",
            })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_move_assets: invalid_field: 'continue_on_error' must be a boolean",
        })

    results = []
    for path in paths:
        move_resp = call_ue("move_asset", {"path": path, "dest_folder": dest_folder})
        if "error" in move_resp:
            upstream_err = move_resp.get("error", {}) or {}
            error_code = upstream_err.get("code", -32603)
            if error_code is None:
                error_code = -32603
            results.append({
                "path": path,
                "ok": False,
                "error_code": error_code,
                "error_message": upstream_err.get("message") or "",
            })
            if not continue_on_error:
                break
            continue

        results.append({
            "path": path,
            "ok": True,
            "error_code": None,
            "error_message": None,
        })

    moved = sum(1 for result in results if result["ok"])
    failed = sum(1 for result in results if not result["ok"])

    return _wrap_tool_result(req_id, {
        "ok": failed == 0,
        "total": len(paths),
        "moved": moved,
        "failed": failed,
        "dest_folder": dest_folder,
        "results": results,
    })


def synthetic_bulk_rename_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: rename multiple assets in one call by
    dispatching `rename_asset` per pair.

    Schema differs from `bulk_delete_assets` / `bulk_move_assets` because
    rename needs a per-asset new leaf name (the destination doesn't
    factor): `renames` is a list of `{path, new_name}` objects, not a
    flat `paths` list. Mirrors the result-shape convention so client code
    that already consumes `bulk_delete_assets` / `bulk_move_assets`
    responses can read the per-entry `path` / `ok` / `error_code` /
    `error_message` fields uniformly.

    UE's standard rename semantics apply: each successful rename leaves
    a redirector at the source path. Callers wanting redirector cleanup
    should follow up with `fix_up_redirectors` per affected folder.

    Synthetic rather than C++ for the same reasons bulk_delete/move are:
    the bulk loop is pure protocol-level composition over the existing
    `rename_asset` handler.

    Validation reuses the defensive shape-checks from PR #115:
    NUL byte and `..` segment rejected in `path`. `new_name` is
    separately validated: must be a non-empty string with no '/' or '.'
    (per rename_asset's leaf-name contract) and no NUL byte.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_rename_assets: invalid_arguments: arguments must be an object",
        })

    if "renames" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_rename_assets: missing_required_field: 'renames' must be supplied as a list of {path, new_name} objects",
        })

    renames = args.get("renames")
    if not isinstance(renames, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_rename_assets: invalid_field: 'renames' must be a list of {path, new_name} objects",
        })

    for i, entry in enumerate(renames):
        if not isinstance(entry, dict):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_entry: renames[{i}] must be an object with 'path' and 'new_name'",
            })
        path = entry.get("path")
        new_name = entry.get("new_name")
        if not isinstance(path, str) or not path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_path: renames[{i}].path must be a non-empty string",
            })
        if "\x00" in path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_path: renames[{i}].path contains a NUL byte",
            })
        if any(segment == ".." for segment in path.split("/")):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_path: renames[{i}].path contains a '..' segment",
            })
        if not isinstance(new_name, str) or not new_name:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_new_name: renames[{i}].new_name must be a non-empty string",
            })
        # new_name is a leaf name. UE rejects '/' (path separator) and '.'
        # (used to separate package path from object name); reject at the
        # validator with a caller-actionable message rather than forwarding
        # to rename_asset and surfacing a less clear UE-side error.
        if "/" in new_name or "." in new_name:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_new_name: renames[{i}].new_name must not contain '/' or '.' (it is a leaf name, not a path)",
            })
        if "\x00" in new_name:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_rename_assets: invalid_new_name: renames[{i}].new_name contains a NUL byte",
            })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_rename_assets: invalid_field: 'continue_on_error' must be a boolean",
        })

    results = []
    for entry in renames:
        path = entry["path"]
        new_name = entry["new_name"]
        rename_resp = call_ue("rename_asset", {"path": path, "new_name": new_name})
        if "error" in rename_resp:
            upstream_err = rename_resp.get("error", {}) or {}
            error_code = upstream_err.get("code", -32603)
            if error_code is None:
                error_code = -32603
            results.append({
                "path": path,
                "new_name": new_name,
                "ok": False,
                "error_code": error_code,
                "error_message": upstream_err.get("message") or "",
            })
            if not continue_on_error:
                break
            continue

        results.append({
            "path": path,
            "new_name": new_name,
            "ok": True,
            "error_code": None,
            "error_message": None,
        })

    renamed = sum(1 for result in results if result["ok"])
    failed = sum(1 for result in results if not result["ok"])

    return _wrap_tool_result(req_id, {
        "ok": failed == 0,
        "total": len(renames),
        "renamed": renamed,
        "failed": failed,
        "results": results,
    })


def synthetic_bulk_duplicate_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: duplicate multiple assets in one call by
    dispatching `duplicate_asset` per pair.

    Fourth member of the bulk_*_assets family (after delete + move +
    rename). Schema mirrors bulk_rename's per-entry mapping shape but
    with `dest_path` (full destination path) instead of `new_name`
    (leaf name only), because `duplicate_asset` takes a full destination
    path -- not a folder + name split.

    Unlike rename/move, duplicate does NOT leave a redirector at the
    source -- the source asset is preserved AT its current path and a
    new copy is created at `dest_path`. Callers can reference both the
    original and the duplicate after this call.

    Validation reuses PR #115's defensive shape-checks on BOTH path
    AND dest_path (NUL byte + `..` segment rejected). dest_path gets
    the same checks as path because it's a full asset path, not a leaf
    name like bulk_rename's new_name.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_duplicate_assets: invalid_arguments: arguments must be an object",
        })

    if "duplicates" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_duplicate_assets: missing_required_field: 'duplicates' must be supplied as a list of {path, dest_path} objects",
        })

    duplicates = args.get("duplicates")
    if not isinstance(duplicates, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_duplicate_assets: invalid_field: 'duplicates' must be a list of {path, dest_path} objects",
        })

    for i, entry in enumerate(duplicates):
        if not isinstance(entry, dict):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_entry: duplicates[{i}] must be an object with 'path' and 'dest_path'",
            })
        path = entry.get("path")
        dest_path = entry.get("dest_path")
        # Validate source path.
        if not isinstance(path, str) or not path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_path: duplicates[{i}].path must be a non-empty string",
            })
        if "\x00" in path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_path: duplicates[{i}].path contains a NUL byte",
            })
        if any(segment == ".." for segment in path.split("/")):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_path: duplicates[{i}].path contains a '..' segment",
            })
        # Validate destination path (same rules: it's a full asset path).
        if not isinstance(dest_path, str) or not dest_path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_dest_path: duplicates[{i}].dest_path must be a non-empty string",
            })
        if "\x00" in dest_path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_dest_path: duplicates[{i}].dest_path contains a NUL byte",
            })
        if any(segment == ".." for segment in dest_path.split("/")):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_duplicate_assets: invalid_dest_path: duplicates[{i}].dest_path contains a '..' segment",
            })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_duplicate_assets: invalid_field: 'continue_on_error' must be a boolean",
        })

    results = []
    for entry in duplicates:
        path = entry["path"]
        dest_path = entry["dest_path"]
        dup_resp = call_ue("duplicate_asset", {"path": path, "dest_path": dest_path})
        if "error" in dup_resp:
            upstream_err = dup_resp.get("error", {}) or {}
            error_code = upstream_err.get("code", -32603)
            if error_code is None:
                error_code = -32603
            results.append({
                "path": path,
                "dest_path": dest_path,
                "ok": False,
                "error_code": error_code,
                "error_message": upstream_err.get("message") or "",
            })
            if not continue_on_error:
                break
            continue

        results.append({
            "path": path,
            "dest_path": dest_path,
            "ok": True,
            "error_code": None,
            "error_message": None,
        })

    duplicated = sum(1 for result in results if result["ok"])
    failed = sum(1 for result in results if not result["ok"])

    return _wrap_tool_result(req_id, {
        "ok": failed == 0,
        "total": len(duplicates),
        "duplicated": duplicated,
        "failed": failed,
        "results": results,
    })


def synthetic_inspect_data_asset(req_id, args: dict) -> dict:
    """Bridge-side shim: shallow-reflect a UDataAsset by package path.

    Canonical Python-shim pattern (per PR #46 + LANGUAGE-CHOICE-RETROSPECTIVE.md
    addendum), mirroring `synthetic_get_camera_transform` exactly:

      1. Generate a per-call UUID marker token (collision-proofs against
         concurrent inspects + log buffer overlaps).
      2. Build embedded Python that calls `unreal.EditorAssetLibrary.load_asset`
         and emits the JSON result via `unreal.log('__DATA_<marker>__' + ... + '__END__')`.
      3. Run via `execute_unreal_python` (round-trip 1).
      4. Read recent `LogPython` lines via `get_log_lines` (round-trip 2).
      5. Find the marker, parse the JSON payload, return.

    Why synthetic, not C++: generic UDataAsset reflection is well-served by
    UE's Python `get_editor_property` introspection; a C++ handler would
    have to enumerate FProperty fields manually and stringify them, while
    `dir(obj)` + Python's type-aware repr does the same with less code.

    Logical errors (asset not found, marker not found, invalid JSON in
    payload) are wrapped as `{ok: False, error_code, error_message}`
    success-envelope returns -- callers can retry without distinguishing
    them from transport-level errors (which return as JSON-RPC errors).

    Originally a Copilot CLI single-stream dispatch test (PR #92,
    2026-05-11): retry of the failed PR #90 Copilot stream after the
    prompt was hardened with the literal-template-from-source-file recipe.
    See HANDOFF.md "Session 2026-05-11 (fifth micro-session)" for the
    prompt-discipline transfer outcome.
    """
    path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_data_asset: missing_required_field: 'path' must be a non-empty string",
        })

    marker = uuid.uuid4().hex[:12]
    # Embed path via json.dumps so quotes/backslashes are correctly escaped.
    py_code = (
        "import unreal, json\n"
        f"path = {json.dumps(path)}\n"
        "obj = unreal.EditorAssetLibrary.load_asset(path)\n"
        "if not obj:\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'asset_not_found',\n"
        "        'error_message': 'inspect_data_asset: asset_not_found: ' + path,\n"
        "    }\n"
        f"    unreal.log('__DATA_{marker}__' + json.dumps(_out) + '__END__')\n"
        "else:\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else None\n"
        "    parent = cls.get_super_class() if cls else None\n"
        "    parent_name = parent.get_name() if parent else None\n"
        "    package_path = obj.get_path_name()\n"
        "    props = []\n"
        "    # Heuristic enumeration: dir() filtered to non-underscore names,\n"
        "    # then try get_editor_property; UE returns the value for real\n"
        "    # UPROPERTYs and raises for everything else (methods, transient\n"
        "    # attrs, parent-class slots that aren't editor-exposed).\n"
        "    for n in [x for x in dir(obj) if not x.startswith('_')]:\n"
        "        try:\n"
        "            v = obj.get_editor_property(n)\n"
        "        except Exception:\n"
        "            continue\n"
        "        tname = type(v).__name__\n"
        "        try:\n"
        "            if isinstance(v, bool):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (int, float, str)):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (list, tuple, dict)):\n"
        "                vstr = '<container:' + tname + '>'\n"
        "            else:\n"
        "                vstr = '<unsupported>'\n"
        "        except Exception:\n"
        "            vstr = '<unsupported>'\n"
        "        props.append({'name': n, 'type': tname, 'value': vstr})\n"
        "    _out = {\n"
        "        'ok': True,\n"
        "        'path': path,\n"
        "        'class': cls_name,\n"
        "        'parent_class': parent_name,\n"
        "        'package_path': package_path,\n"
        "        'properties': props,\n"
        "    }\n"
        f"    unreal.log('__DATA_{marker}__' + json.dumps(_out) + '__END__')\n"
    )

    return _run_marker_pattern(req_id, "inspect_data_asset", f"__DATA_{marker}__", py_code, context=path)


def synthetic_inspect_sound_class(req_id, args: dict) -> dict:
    """Bridge-side shim: inspect a USoundClass by package path.

    Same canonical marker pattern as `synthetic_inspect_data_asset` (PR #92):
    UUID marker -> execute_unreal_python round-trip -> get_log_lines
    round-trip -> reverse-scan for marker -> JSON-parse. See lines 1004-1076
    (`synthetic_get_camera_transform`) for the originating pattern.

    Reads the canonical SoundClass shape:
      - leaf class name + package path
      - parent USoundClass as an asset package path (NOT C++ class name);
        callers can chain to `inspect_sound_class { path: parent_class }`
      - child USoundClasses (same shape)
      - FSoundClassProperties values (Volume, Pitch, low-pass filter,
        attenuation distance scale, voice-center-channel volume, radio-
        filter volume, eight boolean flags, OutputTarget enum stringified)

    UE Python field names are snake_case (`volume`, `pitch`,
    `b_apply_ambient_volumes`); the JSON output remaps to the C++ PascalCase
    names per UE's native FSoundClassProperties layout so callers can
    cross-reference UE editor / docs without translation.

    Logical errors (asset_not_found, wrong_asset_type when the path resolves
    to a non-SoundClass, marker_not_found if the LogPython buffer overflowed,
    invalid_json) are wrapped as `{ok: False, error_code, error_message}`
    success envelopes. Transport-level errors return as JSON-RPC errors.

    Originally a Codex parallel-dispatch test (PR #98, 2026-05-11): paired
    with a Copilot stream for `inspect_audio_bus` that regressed (invented
    parameter names `script` and `contains`/`reverse`). Codex's discipline
    held - second consecutive flawless dispatch under the hardened prompt
    recipe.
    """
    path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_sound_class: missing_required_field: 'path' must be a non-empty string",
        })

    marker = uuid.uuid4().hex[:12]
    py_code = (
        "import unreal, json\n"
        "path = " + json.dumps(path) + "\n"
        "def _asset_package_path(asset):\n"
        "    if not asset:\n"
        "        return None\n"
        "    try:\n"
        "        name = asset.get_path_name()\n"
        "    except Exception:\n"
        "        return None\n"
        "    if isinstance(name, str) and '.' in name:\n"
        "        return name.rsplit('.', 1)[0]\n"
        "    return name\n"
        "def _enum_name(v):\n"
        "    try:\n"
        "        return v.name\n"
        "    except Exception:\n"
        "        try:\n"
        "            text = str(v)\n"
        "            if '.' in text:\n"
        "                return text.rsplit('.', 1)[-1]\n"
        "            return text\n"
        "        except Exception:\n"
        "            return None\n"
        "def _read_prop(struct_obj, prop_name):\n"
        "    try:\n"
        "        return struct_obj.get_editor_property(prop_name)\n"
        "    except Exception:\n"
        "        return None\n"
        "obj = unreal.EditorAssetLibrary.load_asset(path)\n"
        "if not obj:\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'asset_not_found',\n"
        "        'error_message': 'inspect_sound_class: asset_not_found: ' + path,\n"
        "    }\n"
        "    unreal.log('__SOUNDCLASS_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "elif not isinstance(obj, unreal.SoundClass):\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else type(obj).__name__\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'wrong_asset_type',\n"
        "        'error_message': 'Asset is not a USoundClass: ' + path,\n"
        "        'actual_class': cls_name,\n"
        "    }\n"
        "    unreal.log('__SOUNDCLASS_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "else:\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else None\n"
        "    package_path = obj.get_path_name()\n"
        "    parent = obj.get_editor_property('parent_class')\n"
        "    child_classes = obj.get_editor_property('child_classes') or []\n"
        "    props_struct = obj.get_editor_property('properties')\n"
        "    properties = {\n"
        "        'Volume': _read_prop(props_struct, 'volume'),\n"
        "        'Pitch': _read_prop(props_struct, 'pitch'),\n"
        "        'LowPassFilterFrequency': _read_prop(props_struct, 'low_pass_filter_frequency'),\n"
        "        'AttenuationDistanceScale': _read_prop(props_struct, 'attenuation_distance_scale'),\n"
        "        'VoiceCenterChannelVolume': _read_prop(props_struct, 'voice_center_channel_volume'),\n"
        "        'RadioFilterVolume': _read_prop(props_struct, 'radio_filter_volume'),\n"
        "        'bApplyAmbientVolumes': _read_prop(props_struct, 'b_apply_ambient_volumes'),\n"
        "        'bApplyEffects': _read_prop(props_struct, 'b_apply_effects'),\n"
        "        'bAlwaysPlay': _read_prop(props_struct, 'b_always_play'),\n"
        "        'bIsUISound': _read_prop(props_struct, 'b_is_ui_sound'),\n"
        "        'bIsMusic': _read_prop(props_struct, 'b_is_music'),\n"
        "        'bReverb': _read_prop(props_struct, 'b_reverb'),\n"
        "        'bCenterChannelOnly': _read_prop(props_struct, 'b_center_channel_only'),\n"
        "        'bApplyDoppler': _read_prop(props_struct, 'b_apply_doppler'),\n"
        "        'bApplyMixerOverrides': _read_prop(props_struct, 'b_apply_mixer_overrides'),\n"
        "        'OutputTarget': _enum_name(_read_prop(props_struct, 'output_target')),\n"
        "    }\n"
        "    _out = {\n"
        "        'ok': True,\n"
        "        'path': path,\n"
        "        'class': cls_name,\n"
        "        'package_path': package_path,\n"
        "        'parent_class': _asset_package_path(parent),\n"
        "        'child_classes': [_asset_package_path(child) for child in child_classes if child],\n"
        "        'properties': properties,\n"
        "    }\n"
        "    unreal.log('__SOUNDCLASS_" + marker + "__' + json.dumps(_out) + '__END__')\n"
    )

    return _run_marker_pattern(req_id, "inspect_sound_class", "__SOUNDCLASS_" + marker + "__", py_code, context=path)


def synthetic_inspect_sound_submix(req_id, args: dict) -> dict:
    """Bridge-side shim: inspect a USoundSubmix by package path.

    Same canonical marker pattern as `synthetic_inspect_sound_class` (PR #98).
    Returns leaf class + package path + parent_submix asset path (chainable)
    + child_submixes asset paths + additional editor-accessible properties
    via the `dir(obj)` permissive enumeration (skipping the curated names
    to avoid duplication).

    Originally a Codex parallel-dispatch test (PR #99, 2026-05-11): paired
    with a Copilot retry stream for `inspect_audio_bus` that recovered
    from the PR #98 regression once the prompt explicitly called out the
    three previous wrongs (`{"script": ...}` vs `{"code": ...}`,
    `{"contains": ..., "reverse": ...}` vs `category_filter`+`count`,
    manifest-style vs bridge-style schema shape).
    """
    path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_sound_submix: missing_required_field: 'path' must be a non-empty string",
        })

    marker = uuid.uuid4().hex[:12]
    py_code = (
        "import unreal, json\n"
        "path = " + json.dumps(path) + "\n"
        "def _asset_package_path(asset):\n"
        "    if not asset:\n"
        "        return None\n"
        "    try:\n"
        "        name = asset.get_path_name()\n"
        "    except Exception:\n"
        "        return None\n"
        "    if isinstance(name, str) and '.' in name:\n"
        "        return name.rsplit('.', 1)[0]\n"
        "    return name\n"
        "obj = unreal.EditorAssetLibrary.load_asset(path)\n"
        "if not obj:\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'asset_not_found',\n"
        "        'error_message': 'inspect_sound_submix: asset_not_found: ' + path,\n"
        "    }\n"
        "    unreal.log('__SOUNDSUBMIX_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "elif not isinstance(obj, unreal.SoundSubmix):\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else type(obj).__name__\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'wrong_asset_type',\n"
        "        'error_message': 'inspect_sound_submix: wrong_asset_type: Asset is not a USoundSubmix: ' + path,\n"
        "        'actual_class': cls_name,\n"
        "    }\n"
        "    unreal.log('__SOUNDSUBMIX_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "else:\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else None\n"
        "    package_path = obj.get_path_name()\n"
        "    try:\n"
        "        parent = obj.get_editor_property('parent_submix')\n"
        "    except Exception:\n"
        "        parent = None\n"
        "    try:\n"
        "        child_submixes = obj.get_editor_property('child_submixes') or []\n"
        "    except Exception:\n"
        "        child_submixes = []\n"
        "    child_paths = []\n"
        "    for child in child_submixes:\n"
        "        child_path = _asset_package_path(child)\n"
        "        if child_path:\n"
        "            child_paths.append(child_path)\n"
        "    skip_names = {'parent_submix', 'child_submixes'}\n"
        "    additional_properties = []\n"
        "    for n in [x for x in dir(obj) if not x.startswith('_') and x not in skip_names]:\n"
        "        try:\n"
        "            v = obj.get_editor_property(n)\n"
        "        except Exception:\n"
        "            continue\n"
        "        tname = type(v).__name__\n"
        "        try:\n"
        "            if isinstance(v, bool):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (int, float, str)):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (list, tuple, dict)):\n"
        "                vstr = '<container:' + tname + '>'\n"
        "            else:\n"
        "                vstr = '<unsupported>'\n"
        "        except Exception:\n"
        "            vstr = '<unsupported>'\n"
        "        additional_properties.append({'name': n, 'type': tname, 'value': vstr})\n"
        "    _out = {\n"
        "        'ok': True,\n"
        "        'path': path,\n"
        "        'class': cls_name,\n"
        "        'package_path': package_path,\n"
        "        'parent_submix': _asset_package_path(parent),\n"
        "        'child_submixes': child_paths,\n"
        "        'additional_properties': additional_properties,\n"
        "    }\n"
        "    unreal.log('__SOUNDSUBMIX_" + marker + "__' + json.dumps(_out) + '__END__')\n"
    )

    return _run_marker_pattern(req_id, "inspect_sound_submix", "__SOUNDSUBMIX_" + marker + "__", py_code, context=path)


def synthetic_inspect_audio_bus(req_id, args: dict) -> dict:
    """Bridge-side shim: inspect a UAudioBus by package path.

    Same canonical marker pattern as `synthetic_inspect_sound_class`.
    Returns leaf class + package path + audio_bus_channels enum stringified
    via `.name` (Mono | Stereo | Quad | FivePointOne | SevenPointOne) +
    additional editor-accessible properties via permissive `dir(obj)`
    enumeration (skipping the curated `audio_bus_channels`).

    Originally a Copilot CLI retry-dispatch test (PR #99, 2026-05-11):
    recovered from the PR #98 regression after the prompt explicitly
    called out the three previous wrongs (invented `{"script": ...}` arg,
    invented `{"contains": ..., "reverse": ...}` for get_log_lines,
    manifest-style schema shape with both `params` and top-level
    `required`). The recipe holds even after a regression as long as
    the prompt names the specific wrongs to avoid.
    """
    path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_audio_bus: missing_required_field: 'path' must be a non-empty string",
        })

    marker = uuid.uuid4().hex[:12]
    py_code = (
        "import unreal, json\n"
        "path = " + json.dumps(path) + "\n"
        "def _enum_name(v):\n"
        "    try:\n"
        "        return v.name\n"
        "    except Exception:\n"
        "        try:\n"
        "            text = str(v)\n"
        "            if '.' in text:\n"
        "                return text.rsplit('.', 1)[-1]\n"
        "            return text\n"
        "        except Exception:\n"
        "            return None\n"
        "obj = unreal.EditorAssetLibrary.load_asset(path)\n"
        "if not obj:\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'asset_not_found',\n"
        "        'error_message': 'inspect_audio_bus: asset_not_found: ' + path,\n"
        "    }\n"
        "    unreal.log('__AUDIOBUS_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "elif not isinstance(obj, unreal.AudioBus):\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else type(obj).__name__\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'wrong_asset_type',\n"
        "        'error_message': 'inspect_audio_bus: wrong_asset_type: Asset is not a UAudioBus: ' + path,\n"
        "        'actual_class': cls_name,\n"
        "    }\n"
        "    unreal.log('__AUDIOBUS_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "else:\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else None\n"
        "    package_path = obj.get_path_name()\n"
        "    try:\n"
        "        abc = obj.get_editor_property('audio_bus_channels')\n"
        "    except Exception:\n"
        "        abc = None\n"
        "    abc_name = _enum_name(abc)\n"
        "    props = []\n"
        "    for n in [x for x in dir(obj) if not x.startswith('_')]:\n"
        "        if n == 'audio_bus_channels':\n"
        "            continue\n"
        "        try:\n"
        "            v = obj.get_editor_property(n)\n"
        "        except Exception:\n"
        "            continue\n"
        "        tname = type(v).__name__\n"
        "        try:\n"
        "            if isinstance(v, bool):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (int, float, str)):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (list, tuple, dict)):\n"
        "                vstr = '<container:' + tname + '>'\n"
        "            else:\n"
        "                vstr = '<unsupported>'\n"
        "        except Exception:\n"
        "            vstr = '<unsupported>'\n"
        "        props.append({'name': n, 'type': tname, 'value': vstr})\n"
        "    _out = {\n"
        "        'ok': True,\n"
        "        'path': path,\n"
        "        'class': cls_name,\n"
        "        'package_path': package_path,\n"
        "        'audio_bus_channels': abc_name,\n"
        "        'additional_properties': props,\n"
        "    }\n"
        "    unreal.log('__AUDIOBUS_" + marker + "__' + json.dumps(_out) + '__END__')\n"
    )

    return _run_marker_pattern(req_id, "inspect_audio_bus", "__AUDIOBUS_" + marker + "__", py_code, context=path)


def synthetic_inspect_material_function(req_id, args: dict) -> dict:
    """Bridge-side shim: inspect a UMaterialFunction by package path.

    Same canonical marker pattern as the rest of the inspect_* family
    (PR #100 _run_marker_pattern helper). Returns leaf class + package
    path + description + library exposure + library categories + the
    enumerated function inputs/outputs (by walking the function_expressions
    array and isinstance-checking each node for MaterialExpressionFunctionInput
    / MaterialExpressionFunctionOutput) + additional editor-accessible
    UPROPERTYs via the dir() permissive enumeration (skipping the curated
    names).

    This synthetic was Opus-direct after a parallel-dispatch round
    (PR #101 attempt) where Codex looped without converging and Copilot's
    output had three integration defects (wrong marker terminator
    `__MATFUNC_<m>__` instead of `__END__`; invalid `"handler"` key in
    TOOLS schema; broken test import path). Both AI streams failed in
    the same dispatch, so the synthetic was written by hand following
    the literal-template recipe rather than salvaging one broken output.
    """
    path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_material_function: missing_required_field: 'path' must be a non-empty string",
        })

    marker = uuid.uuid4().hex[:12]
    py_code = (
        "import unreal, json\n"
        "path = " + json.dumps(path) + "\n"
        "def _enum_name(v):\n"
        "    try:\n"
        "        return v.name\n"
        "    except Exception:\n"
        "        try:\n"
        "            text = str(v)\n"
        "            if '.' in text:\n"
        "                return text.rsplit('.', 1)[-1]\n"
        "            return text\n"
        "        except Exception:\n"
        "            return None\n"
        "obj = unreal.EditorAssetLibrary.load_asset(path)\n"
        "if not obj:\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'asset_not_found',\n"
        "        'error_message': 'inspect_material_function: asset_not_found: ' + path,\n"
        "    }\n"
        "    unreal.log('__MATFUNC_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "elif not isinstance(obj, unreal.MaterialFunction):\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else type(obj).__name__\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'wrong_asset_type',\n"
        "        'error_message': 'inspect_material_function: wrong_asset_type: Asset is not a UMaterialFunction: ' + path,\n"
        "        'actual_class': cls_name,\n"
        "    }\n"
        "    unreal.log('__MATFUNC_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "else:\n"
        "    cls = obj.get_class()\n"
        "    cls_name = cls.get_name() if cls else None\n"
        "    package_path = obj.get_path_name()\n"
        "    try:\n"
        "        description = obj.get_editor_property('description') or ''\n"
        "    except Exception:\n"
        "        description = ''\n"
        "    try:\n"
        "        exposed = bool(obj.get_editor_property('expose_to_library'))\n"
        "    except Exception:\n"
        "        exposed = False\n"
        "    try:\n"
        "        cats = obj.get_editor_property('library_categories_text') or []\n"
        "        library_categories = [str(t) for t in cats]\n"
        "    except Exception:\n"
        "        library_categories = []\n"
        "    inputs = []\n"
        "    outputs = []\n"
        "    try:\n"
        "        exprs = obj.get_editor_property('function_expressions') or []\n"
        "        for e in exprs:\n"
        "            try:\n"
        "                if isinstance(e, unreal.MaterialExpressionFunctionInput):\n"
        "                    try:\n"
        "                        ename = e.get_editor_property('input_name')\n"
        "                    except Exception:\n"
        "                        ename = ''\n"
        "                    try:\n"
        "                        etype = _enum_name(e.get_editor_property('input_type'))\n"
        "                    except Exception:\n"
        "                        etype = None\n"
        "                    inputs.append({'name': str(ename), 'type': 'FunctionInput', 'input_type': etype})\n"
        "                elif isinstance(e, unreal.MaterialExpressionFunctionOutput):\n"
        "                    try:\n"
        "                        oname = e.get_editor_property('output_name')\n"
        "                    except Exception:\n"
        "                        oname = ''\n"
        "                    outputs.append({'name': str(oname), 'type': 'FunctionOutput'})\n"
        "            except Exception:\n"
        "                continue\n"
        "    except Exception:\n"
        "        pass\n"
        "    skip_names = {'description', 'expose_to_library', 'library_categories_text', 'function_expressions'}\n"
        "    additional_properties = []\n"
        "    for n in [x for x in dir(obj) if not x.startswith('_') and x not in skip_names]:\n"
        "        try:\n"
        "            v = obj.get_editor_property(n)\n"
        "        except Exception:\n"
        "            continue\n"
        "        tname = type(v).__name__\n"
        "        try:\n"
        "            if isinstance(v, bool):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (int, float, str)):\n"
        "                vstr = str(v)\n"
        "            elif isinstance(v, (list, tuple, dict)):\n"
        "                vstr = '<container:' + tname + '>'\n"
        "            else:\n"
        "                vstr = '<unsupported>'\n"
        "        except Exception:\n"
        "            vstr = '<unsupported>'\n"
        "        additional_properties.append({'name': n, 'type': tname, 'value': vstr})\n"
        "    _out = {\n"
        "        'ok': True,\n"
        "        'path': path,\n"
        "        'class': cls_name,\n"
        "        'package_path': package_path,\n"
        "        'description': description,\n"
        "        'exposed_to_library': exposed,\n"
        "        'library_categories': library_categories,\n"
        "        'inputs': inputs,\n"
        "        'outputs': outputs,\n"
        "        'additional_properties': additional_properties,\n"
        "    }\n"
        "    unreal.log('__MATFUNC_" + marker + "__' + json.dumps(_out) + '__END__')\n"
    )

    return _run_marker_pattern(req_id, "inspect_material_function", "__MATFUNC_" + marker + "__", py_code, context=path)


def synthetic_inspect_metasound(req_id, args: dict) -> dict:
    """Bridge-side shim: inspect a MetaSoundSource or MetaSoundPatch by package path.

    Same canonical marker pattern as `synthetic_inspect_sound_class` /
    `_submix` / `_audio_bus`. MetaSound assets in UE 5.7 come in two flavours
    (Source for emitter-attached sound, Patch for reusable subgraph); both are
    accepted by this synthetic and the leaf class name is returned so the
    caller can distinguish.

    Returns leaf class + package path + additional editor-accessible
    UPROPERTYs via `dir(obj)` permissive enumeration. MetaSound's graph
    structure (nodes, connections) is not reflected here -- that's a UE
    Python API that requires a dedicated traversal pass (deferred). For
    surface-level metadata (description, output settings, exposed inputs
    via UPROPERTY) the permissive enumeration covers the common case.

    Logical errors come back as `ok: False` success envelopes:
      - asset_not_found: path doesn't resolve to a loadable asset
      - wrong_asset_type: asset loaded but isn't a MetaSoundSource or Patch
      - marker_not_found / marker_truncated / invalid_json: marker pattern
        failures (post-PR #128 split)
    """
    path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_metasound: missing_required_field: 'path' must be a non-empty string",
        })

    marker = uuid.uuid4().hex[:12]
    py_code = (
        "import unreal, json\n"
        "path = " + json.dumps(path) + "\n"
        "obj = unreal.EditorAssetLibrary.load_asset(path)\n"
        "if not obj:\n"
        "    _out = {\n"
        "        'ok': False,\n"
        "        'error_code': 'asset_not_found',\n"
        "        'error_message': 'inspect_metasound: asset_not_found: ' + path,\n"
        "    }\n"
        "    unreal.log('__METASOUND_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "else:\n"
        "    # Accept either Source (emitter-attached) or Patch (reusable\n"
        "    # subgraph). hasattr check guards against engine variants that\n"
        "    # might drop one of the classes from Python.\n"
        "    accepted = []\n"
        "    if hasattr(unreal, 'MetaSoundSource'):\n"
        "        accepted.append(unreal.MetaSoundSource)\n"
        "    if hasattr(unreal, 'MetaSoundPatch'):\n"
        "        accepted.append(unreal.MetaSoundPatch)\n"
        "    if not accepted:\n"
        "        _out = {\n"
        "            'ok': False,\n"
        "            'error_code': 'metasound_unavailable',\n"
        "            'error_message': 'inspect_metasound: metasound_unavailable: neither MetaSoundSource nor MetaSoundPatch is exposed in this UE Python build (Metasound plugin disabled?)',\n"
        "        }\n"
        "        unreal.log('__METASOUND_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "    elif not isinstance(obj, tuple(accepted)):\n"
        "        cls = obj.get_class()\n"
        "        cls_name = cls.get_name() if cls else type(obj).__name__\n"
        "        _out = {\n"
        "            'ok': False,\n"
        "            'error_code': 'wrong_asset_type',\n"
        "            'error_message': 'inspect_metasound: wrong_asset_type: Asset is not a MetaSoundSource or MetaSoundPatch: ' + path,\n"
        "            'actual_class': cls_name,\n"
        "        }\n"
        "        unreal.log('__METASOUND_" + marker + "__' + json.dumps(_out) + '__END__')\n"
        "    else:\n"
        "        cls = obj.get_class()\n"
        "        cls_name = cls.get_name() if cls else None\n"
        "        package_path = obj.get_path_name()\n"
        "        additional_properties = []\n"
        "        for n in [x for x in dir(obj) if not x.startswith('_')]:\n"
        "            try:\n"
        "                v = obj.get_editor_property(n)\n"
        "            except Exception:\n"
        "                continue\n"
        "            tname = type(v).__name__\n"
        "            try:\n"
        "                if isinstance(v, bool):\n"
        "                    vstr = str(v)\n"
        "                elif isinstance(v, (int, float, str)):\n"
        "                    vstr = str(v)\n"
        "                elif isinstance(v, (list, tuple, dict)):\n"
        "                    vstr = '<container:' + tname + '>'\n"
        "                else:\n"
        "                    vstr = '<unsupported>'\n"
        "            except Exception:\n"
        "                vstr = '<unsupported>'\n"
        "            additional_properties.append({'name': n, 'type': tname, 'value': vstr})\n"
        "        _out = {\n"
        "            'ok': True,\n"
        "            'path': path,\n"
        "            'class': cls_name,\n"
        "            'package_path': package_path,\n"
        "            'additional_properties': additional_properties,\n"
        "        }\n"
        "        unreal.log('__METASOUND_" + marker + "__' + json.dumps(_out) + '__END__')\n"
    )

    return _run_marker_pattern(req_id, "inspect_metasound", "__METASOUND_" + marker + "__", py_code, context=path)


# Map of tool-name -> bridge-side synthetic implementation. These are
# tools that don't have a corresponding UE handler -- the bridge composes
# existing UE handlers (or implements pure-protocol logic) to serve them.
def synthetic_bulk_inspect_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: inspect multiple assets via the existing
    `inspect_asset` C++ handler, returning a per-path partial-success
    structure.

    Loops over `paths` and dispatches one `call_ue("inspect_asset", ...)`
    per entry, collecting result records. Mirrors the partial-failure
    semantics of `bulk_delete_assets` / `bulk_move_assets`: by default
    individual failures do not abort the loop, and partial success is
    surfaced via `ok: False` + non-zero `failed` count.

    Synthetic rather than C++ for the same reasons as the rest of the
    bulk_* family — the loop is pure protocol-level composition over an
    existing handler. For pipeline audits ("inspect 500 textures and
    report which lack a power-of-two source"), one batched MCP call
    replaces 500 individual round-trips.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_inspect_assets: invalid_arguments: arguments must be an object",
        })

    if "paths" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_inspect_assets: missing_required_field: 'paths' must be supplied as a list of non-empty strings",
        })

    paths = args.get("paths")
    if not isinstance(paths, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_inspect_assets: invalid_field: 'paths' must be a list of non-empty strings",
        })

    for i, path in enumerate(paths):
        if not isinstance(path, str) or not path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_inspect_assets: invalid_path: paths[{i}] must be a non-empty string",
            })
        # Same NUL + `..` path-shape guards as the other bulk_* synthetics.
        if "\x00" in path:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_inspect_assets: invalid_path: paths[{i}] contains a NUL byte",
            })
        if any(segment == ".." for segment in path.split("/")):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_inspect_assets: invalid_path: paths[{i}] contains a '..' segment",
            })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_inspect_assets: invalid_field: 'continue_on_error' must be a boolean",
        })

    results = []
    inspected = 0
    failed = 0
    for path in paths:
        ue_resp = call_ue("inspect_asset", {"path": path})
        if "error" in ue_resp:
            failed += 1
            err = ue_resp["error"]
            results.append({
                "path": path,
                "ok": False,
                "data": None,
                "error_code": err.get("code"),
                "error_message": err.get("message"),
            })
            if not continue_on_error:
                break
        else:
            inspected += 1
            results.append({
                "path": path,
                "ok": True,
                "data": ue_resp.get("result", {}),
                "error_code": None,
                "error_message": None,
            })

    body = {
        "ok": failed == 0,
        "total": len(paths),
        "inspected": inspected,
        "failed": failed,
        "results": results,
    }
    return _wrap_tool_result(req_id, body)


SYNTHETIC_TOOLS = {
    "wait_for_events": synthetic_wait_for_events,
    "get_camera_transform": synthetic_get_camera_transform,
    "set_camera_transform": synthetic_set_camera_transform,
    "screenshot_actor": synthetic_screenshot_actor,
    "compile_mod_pak": synthetic_compile_mod_pak,
    "compile_mod_pak_direct": synthetic_compile_mod_pak_direct,
    "bulk_delete_assets": synthetic_bulk_delete_assets,
    "bulk_move_assets": synthetic_bulk_move_assets,
    "bulk_rename_assets": synthetic_bulk_rename_assets,
    "bulk_duplicate_assets": synthetic_bulk_duplicate_assets,
    "bulk_inspect_assets": synthetic_bulk_inspect_assets,
    "inspect_data_asset": synthetic_inspect_data_asset,
    "inspect_sound_class": synthetic_inspect_sound_class,
    "inspect_sound_submix": synthetic_inspect_sound_submix,
    "inspect_audio_bus": synthetic_inspect_audio_bus,
    "inspect_material_function": synthetic_inspect_material_function,
    "inspect_metasound": synthetic_inspect_metasound,
}


def handle(req: dict) -> dict | None:
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


def main() -> None:
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
