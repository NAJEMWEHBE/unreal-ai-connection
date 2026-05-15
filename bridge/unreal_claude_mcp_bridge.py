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
  - "tools/list"             returns a static list of all 102 tools (71
                             dispatched to the UE plugin's C++ handlers
                             plus 31 bridge-side synthetic tools served by
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
# 96 tool entries total. 71 are dispatched straight to UE C++ handlers
# (see UnrealClaudeMCPModule.cpp's Reg.Register(...) block). The remaining
# 25 -- wait_for_events, get_camera_transform, set_camera_transform,
# screenshot_actor, compile_mod_pak, compile_mod_pak_direct,
# bulk_delete_assets, bulk_move_assets, bulk_rename_assets,
# bulk_duplicate_assets, bulk_inspect_assets, inspect_data_asset,
# inspect_sound_class, inspect_sound_submix, inspect_audio_bus,
# inspect_material_function, inspect_metasound, find_unused_assets,
# get_reference_chain, bulk_compile_blueprints,
# audit_blueprint_compile_status, find_actors_by_class,
# bulk_focus_actors, bulk_screenshot_actors, bulk_set_actor_property
# -- are bridge-side synthetic tools served by SYNTHETIC_TOOLS (see
# below) without a dedicated UE handler: they either compose existing
# handlers (focus + screenshot, repeated poll, loop over delete_asset /
# move_asset / rename_asset / duplicate_asset / inspect_asset /
# inspect_blueprint / compile_blueprint / find_assets / focus_actor /
# set_actor_property), run the matching unreal.* Python via
# execute_unreal_python with the marker pattern (most inspect_*
# shims), or (compile_mod_pak / compile_mod_pak_direct) shell out to
# RunUAT.bat entirely outside the UE process.
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
        "name": "pie_control",
        "description": "Start / stop / query Play-In-Editor sessions. Closes the 'did my edit actually work?' loop — LLM can scaffold a gameplay change, trigger PIE, observe the running state, then stop. action=start with mode=play|simulate; action=stop tears down current session; action=query returns is_playing + is_simulating booleans.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "One of: start, stop, query."},
                "mode": {"type": "string", "description": "Only used when action=start. 'play' (default) launches a full PIE session in the active viewport; 'simulate' ticks the world without spawning a Player Controller."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "inspect_project_setting",
        "description": "Reflect any UDeveloperSettings subclass (RendererSettings, PhysicsSettings, InputSettings, etc.) and dump editable UPROPERTY values as JSON. Bulk mode (omit 'property') returns every editable property; single mode (pass 'property') returns just that one. Closes the gap where the LLM had no access to per-system Project Settings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "settings_class": {"type": "string", "description": "Full class path of a UDeveloperSettings subclass (e.g. '/Script/Engine.RendererSettings')."},
                "property": {"type": "string", "description": "Optional. When supplied, return just this property's name/type/value instead of the full bulk dump."},
            },
            "required": ["settings_class"],
        },
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
        "name": "find_unused_assets",
        "description": "Enumerate assets under a content path and report which have zero referencers (i.e. nothing in the project references them). Composes find_assets + inspect_asset bridge-side. Useful for content cleanup audits before shipping. Returns the first `limit` unused assets and a `truncated` flag when more remain.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_under": {
                    "type": "string",
                    "description": "Folder to scan. Default /Game. Recursive."
                },
                "class_filter": {
                    "type": "string",
                    "description": "Optional UE class path filter (e.g. /Script/Engine.Texture2D) to scan only assets of one type."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max unused assets to return (default 100, max 10000). Scan halts once this many unused are found OR the scan exhausts."
                },
            },
        },
    },
    {
        "name": "get_reference_chain",
        "description": "Walk the asset reference graph BFS from a root, returning every node and edge up to a depth bound. Composes inspect_asset recursively. `direction=up` follows referencers (who references me) — useful for impact-of-change analysis before deleting/renaming. `direction=down` follows dependencies (what I reference) — useful for dependency audits before packaging.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Root asset path to walk from. Required."
                },
                "depth": {
                    "type": "integer",
                    "description": "BFS depth bound. Default 3, max 8 (8 hops is already a vast subgraph in any non-trivial project)."
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Default 'up'. 'up' follows referencers; 'down' follows dependencies."
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "bulk_compile_blueprints",
        "description": "Recompile multiple Blueprints in one MCP call by composing the compile_blueprint C++ handler bridge-side. Returns per-path success/failure plus aggregate counts. Mirrors the bulk_*_assets family shape (paths list + continue_on_error). Useful after batch-mutating BPs via execute_unreal_python or other tooling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Blueprint asset paths to compile (each non-empty, NUL + '..' segments rejected, max 1000 entries)."
                },
                "continue_on_error": {
                    "type": "boolean",
                    "description": "Default true. When false, stop at first per-path compile failure and return the partial results."
                },
            },
            "required": ["paths"],
        },
    },
    {
        "name": "audit_blueprint_compile_status",
        "description": "Enumerate every Blueprint under a content path and report its compile-status bucket (UpToDate/Dirty/Error/Unknown/BeingCreated). Composes find_assets + inspect_blueprint bridge-side. This is a READ-ONLY audit (no recompile triggered); pair with bulk_compile_blueprints to actually fix anything found.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_under": {
                    "type": "string",
                    "description": "Content path to scan. Default /Game. Recursive."
                },
                "compile_failures_only": {
                    "type": "boolean",
                    "description": "Default true. When true, problem_assets only lists Blueprints whose status is Error or Unknown. When false, problem_assets lists every scanned Blueprint."
                },
            },
        },
    },
    {
        "name": "find_actors_by_class",
        "description": "Filter the current level's actors by class. Composes get_actors_in_level bridge-side and matches each actor's short class name against the supplied class_name (accepts either a short name like 'StaticMeshActor' or a class path like '/Script/Engine.StaticMeshActor' — the synthetic strips the path prefix and matches case-insensitively). Useful for 'find every light' / 'find every spawn point' walkthroughs without forcing the LLM to grep through a thousand-actor get_actors_in_level dump.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_name": {
                    "type": "string",
                    "description": "Short class name (e.g. 'StaticMeshActor') or full class path (e.g. '/Script/Engine.StaticMeshActor'). Match is case-insensitive against the actor's short class name; class-path inputs have everything up to and including the final '.' stripped before comparison."
                },
                "level": {
                    "type": "string",
                    "description": "Optional UWorld package path to load before enumerating (e.g. '/Game/Maps/MyMap'). When omitted, the active editor level is scanned in place."
                },
            },
            "required": ["class_name"],
        },
    },
    {
        "name": "bulk_focus_actors",
        "description": "Frame the viewport on each actor in a sequence, optionally capturing a screenshot after each focus settles. Composes focus_actor (plus, when screenshot_each=true, get_viewport_screenshot) per name. Useful for 'show me each enemy / spawn / light in turn' walkthroughs where one screenshot_actor at a time would force the LLM into a polling loop.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Actor labels or unique names; each non-empty, max 100 entries."
                },
                "delay_ms": {
                    "type": "integer",
                    "description": "Settle delay between focus calls in milliseconds (default 500, max 10000). Sleeps BETWEEN calls, not after the last."
                },
                "screenshot_each": {
                    "type": "boolean",
                    "description": "Default false. When true, capture a viewport PNG after each focus settles and emit a parallel 'screenshots' array."
                },
            },
            "required": ["names"],
        },
    },
    {
        "name": "bulk_screenshot_actors",
        "description": "Frame and screenshot each actor in a sequence. Composes screenshot_actor (which itself composes focus_actor + get_viewport_screenshot) per name. Same shape as bulk_focus_actors but always captures a PNG — convenient for thumbnail-pipeline runs where every actor in a list needs a deterministic centered shot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Actor labels or unique names; each non-empty, max 50 entries (smaller cap than bulk_focus_actors because each entry yields a PNG)."
                },
                "delay_ms": {
                    "type": "integer",
                    "description": "Settle delay between actors in milliseconds (default 500, max 10000). Sleeps BETWEEN actors, not after the last."
                },
            },
            "required": ["names"],
        },
    },
    {
        "name": "bulk_set_actor_property",
        "description": "Apply many UPROPERTY mutations across many actors in one MCP call. Composes set_actor_property bridge-side; mirrors the bulk_*_assets family shape (assignments list + continue_on_error). Each assignment specifies its own {actor, property, value} so this is NOT 'set the same property on N actors' — it's 'run N individual sets'. Useful after batch-spawning to push initial-state mutations without N round-trips.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "actor": {"type": "string", "description": "Actor label or FName."},
                            "property": {"type": "string", "description": "UPROPERTY name (case-sensitive)."},
                            "value": {"description": "JSON value coerced based on the FProperty type."},
                        },
                    },
                    "description": "List of {actor, property, value} triples; each actor and property non-empty, max 200 entries."
                },
                "continue_on_error": {
                    "type": "boolean",
                    "description": "Default true. When false, stop at the first per-assignment failure and return the partial results plus halted_at_index."
                },
            },
            "required": ["assignments"],
        },
    },
    {
        "name": "compare_assets",
        "description": "Symmetric diff between two assets' inspect_asset outputs. Composes inspect_asset bridge-side on both paths and returns the fields that differ. Useful for 'what changed between these two versions of the same blueprint?' walkthroughs and for cross-checking duplicated assets that should be identical. The `path` field is excluded from comparison (trivially different between the two inputs).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path_a": {
                    "type": "string",
                    "description": "First asset path (e.g. /Game/Blueprints/BP_A.BP_A)."
                },
                "path_b": {
                    "type": "string",
                    "description": "Second asset path; same shape as path_a."
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional whitelist of inspect_asset field names to compare. When omitted, the synthetic diffs the union of both responses' keys (minus 'path'). Use this to scope the diff to a known-volatile subset (e.g. ['dependencies', 'referencers'])."
                },
            },
            "required": ["path_a", "path_b"],
        },
    },
    {
        "name": "bulk_set_console_variables",
        "description": "Set multiple Console Variables in one MCP call with optional atomic rollback. Composes get_console_variable (to capture each pre-value) plus set_console_variable (to apply each new value); on any per-cvar failure when rollback_on_error=true, the synthetic walks back every applied change to its captured pre-value. Mirrors the editor's 'apply scalability set then revert if any fail' pattern, with an explicit rollback failure list so callers know which restores themselves failed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "assignments": {
                    "type": "object",
                    "description": "Mapping of {cvar_name: new_value}. Each name must be a non-empty string; each value must be string, number, or boolean (matching set_console_variable's polymorphic value). Max 50 entries per call."
                },
                "rollback_on_error": {
                    "type": "boolean",
                    "description": "Default true. When true, any failure halts the loop, then every already-applied change is restored to its captured pre-value. When false, failures are recorded but applied changes are NOT restored."
                },
            },
            "required": ["assignments"],
        },
    },
    {
        "name": "inspect_dependency_graph",
        "description": "Walk the asset dependency graph BFS from a root (dependencies, downward by default). Composes inspect_asset recursively; optionally also follows referencers (upward) for a bidirectional sweep. Distinct from get_reference_chain in that it defaults to direction=down (dependencies, packaging-audit framing) and supports a single bidirectional pass instead of forcing two separate calls. De-duplicates visited nodes across both directions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Root asset path to walk from."
                },
                "depth": {
                    "type": "integer",
                    "description": "BFS depth bound. Default 2, range 1..8 (the bidirectional sweep can produce a vast subgraph past depth 4 in any non-trivial project)."
                },
                "include_referencers": {
                    "type": "boolean",
                    "description": "Default false. When true, also follow referencers upward in the same BFS; edges record direction ('up' for referencer edges, 'down' for dependency edges)."
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "bulk_fix_redirectors",
        "description": "Resolve UObjectRedirector stubs across multiple content folders in one MCP call. Composes fix_up_redirectors per folder. Useful as a follow-up to a sweep of bulk_move_assets / bulk_rename_assets calls (each of which leaves redirectors at the source paths) so the LLM does not have to issue one fix_up_redirectors per touched folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folders": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Content folder paths under which to fix up redirectors (e.g. ['/Game/Materials', '/Game/Textures']). Each non-empty, NUL + '..' segments rejected, max 100 entries."
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Default true. Echoed back in the response for clarity; fix_up_redirectors itself always operates recursively under the supplied path -- this field exists so callers can capture intent without having to track it separately."
                },
                "continue_on_error": {
                    "type": "boolean",
                    "description": "Default true. When false, stop at the first per-folder fix-up failure and emit halted_at_index."
                },
            },
            "required": ["folders"],
        },
    },
    {
        "name": "get_project_summary",
        "description": "Project name, engine version, enabled plugins, asset counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_blueprint",
        "description": "Read parent class, declared variables, function/event graph names, and compile status (UpToDate/Dirty/Error/Unknown/BeingCreated) of a Blueprint asset.",
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
                "value": {"type": ["string", "number", "boolean", "array", "object", "null"], "description": "JSON value coerced based on the FProperty type. Polymorphic: primitives for scalar UPROPERTYs, JSON arrays for TArray / TSet (e.g. OverrideMaterials), JSON objects for FVector / FRotator / FLinearColor / FInstancedStruct / TMap, and null for explicit clear on nullable properties. Declaring the typed union (instead of leaving value untyped) prevents strict MCP clients from coercing array values to JSON strings before wire transport. JSON Schema `number` validates integers; `integer` omitted to mirror set_console_variable. See docs/TOOLS.md for the full supported-types table."},
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
    {
        "name": "marketplace_search",
        "description": "Search free CC0 asset marketplaces (Polyhaven, AmbientCG) for textures / HDRIs / models matching a keyword and return a normalised list of matches. SYNTHETIC bridge-side handler — fetches the source's public JSON catalog via plain HTTPS (no auth, no API key). Asset files are CC0 (public domain, free for any use including commercial). API-access terms differ from asset terms: the Polyhaven public API at api.polyhaven.com is licensed for non-commercial and academic use only — commercial integrations require a custom license from Poly Haven (https://polyhaven.com/our-api). AmbientCG asset terms are similarly CC0 with their own API ToS. Pair with marketplace_import to actually download and import a chosen result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword(s). Matched against name, tags, and categories on the source side. Empty string = list popular assets."},
                "source": {"type": "string", "description": "Marketplace to query. 'polyhaven' (default), 'ambientcg', or 'all' to fan out across both.", "enum": ["polyhaven", "ambientcg", "all"]},
                "asset_type": {"type": "string", "description": "Asset class filter. 'texture' (default), 'hdri', 'model', or 'all'.", "enum": ["texture", "hdri", "model", "all"]},
                "limit": {"type": "integer", "description": "Max results to return (default 10, max 50)."},
            },
        },
    },
    {
        "name": "marketplace_import",
        "description": "Download a CC0 asset from a marketplace (Polyhaven or AmbientCG) and import it into the project as a UTexture2D via the native import_texture handler. SYNTHETIC bridge-side handler. Polyhaven path: /files/{slug} catalog lookup -> direct download. AmbientCG path: /api/v2/full_json?id={slug}&include=downloadData -> downloads the per-resolution zip -> extracts the Color map (textures) or sole EXR/HDR (hdris) -> hands the file to import_texture. Supports texture (Color/Diffuse map) and hdri (EXR/HDR); model import is parked for a later PR (native handler has no mesh-import wrapper today). Asset files: both sources are CC0 (public domain, no attribution required). API access: the Polyhaven public API is licensed for non-commercial and academic use only — commercial integrations require a custom license from Poly Haven (https://polyhaven.com/our-api). AmbientCG's public API and asset files are both CC0.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Marketplace to import from. 'polyhaven' (default) or 'ambientcg'.", "enum": ["polyhaven", "ambientcg"]},
                "slug": {"type": "string", "description": "Source-specific asset identifier (e.g. 'aerial_beach_01'). Obtain via marketplace_search."},
                "asset_type": {"type": "string", "description": "'texture' (diffuse map only in v1), 'hdri' (EXR/HDR sky), or 'model' (not yet implemented).", "enum": ["texture", "hdri", "model"]},
                "resolution": {"type": "string", "description": "Asset resolution. Common values: '1k', '2k' (default), '4k', '8k'. Available set depends on the asset; the error message lists what the source actually offers when the request is invalid."},
                "format": {"type": "string", "description": "File format. Defaults to 'png' for textures and 'exr' for HDRIs. Other accepted values fall back to the source's default if the requested format isn't published."},
                "dest_path": {"type": "string", "description": "UE package path. Must start with /Game/. Default /Game/Marketplace."},
                "dest_name": {"type": "string", "description": "Asset name override. Defaults to the slug."},
                "replace_existing": {"type": "boolean", "description": "Overwrite an existing asset at dest_path/dest_name (default false)."},
            },
            "required": ["slug"],
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


def _validate_asset_path(tool_name: str, path, label: str) -> str | None:
    """Shape-check a single UE asset path; return an error message or None.

    Hoisted out of the bulk_*_assets family of synthetics in the
    feat/wave-b-asset-hygiene-synthetics branch. The four older synthetics
    (bulk_delete_assets / bulk_move_assets / bulk_rename_assets /
    bulk_duplicate_assets / bulk_inspect_assets) duplicate the same NUL +
    '..' segment guard; rather than refactor those tested-green call sites
    in this branch, the new wave-B synthetics (`find_unused_assets`,
    `get_reference_chain`, `bulk_compile_blueprints`,
    `audit_blueprint_compile_status`) consume this helper. Existing
    synthetics may be migrated in a future cleanup pass.

    UE asset paths look like `/Game/...`, `/Engine/...`, or
    `/<MountPoint>/...`. Embedded NUL bytes or `..` segments are never
    legitimate and almost always indicate either input corruption or
    path-traversal intent; reject early with a caller-actionable -32602
    error_code rather than forwarding malformed paths to the C++ handler.

    Args:
        tool_name: synthetic tool's name (used as error-message prefix).
        path: the value to check (string or otherwise).
        label: caller-supplied context for the error message
            (e.g. "paths[0]" or "path"). Goes into the message verbatim
            so the caller can pinpoint which input field failed.

    Returns:
        None when `path` passes all checks; an error message string
        otherwise. The message follows the canonical
        `<tool>: <error_code>: <detail>` shape with `path_invalid` as
        the error code (matching the wave-B spec). Caller wraps the
        returned string in `make_response(req_id, error={...})`.
    """
    if not isinstance(path, str) or not path:
        return f"{tool_name}: path_must_be_string: {label} must be a non-empty string"
    if "\x00" in path:
        return f"{tool_name}: path_invalid: {label} contains a NUL byte"
    # Block `..` as a path SEGMENT (between slashes or at ends), not as a
    # substring -- legitimate asset names like `My..Asset` should still pass.
    if any(segment == ".." for segment in path.split("/")):
        return f"{tool_name}: path_invalid: {label} contains a '..' segment"
    return None


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


def synthetic_find_unused_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: list assets under a path that have ZERO
    referencers (nothing in the project links to them).

    Pipeline:
      1. call_ue("find_assets", {class_path?, path_under, limit}) -- one
         round-trip to enumerate candidate assets in the scan range.
         class_path defaults to /Script/Engine.Object (effectively
         "all asset classes") when no filter is supplied; find_assets'
         own schema requires class_path so the synthetic injects this
         catch-all default and lets the C++ handler filter by path.
      2. For each candidate, call_ue("inspect_asset", {path}) -- the
         per-asset round-trip reads the `referencers` array. An empty
         referencers list means the asset is unused.
      3. Stop early once `limit` unused assets have been found OR the
         scan exhausts. `truncated` indicates whether more candidates
         existed beyond what was scanned.

    Per-asset inspect failures are SWALLOWED unless every inspect fails
    -- this preserves the "soft audit" semantic. If the scan returned
    candidates but every inspect_asset returned an error, the synthetic
    surfaces `inspect_failed` so the caller knows to investigate (a
    confusing "0 unused found" would otherwise hide the issue).

    Synthetic rather than C++ because the loop is pure protocol-level
    composition over find_assets + inspect_asset. A native C++ handler
    would have to duplicate find_assets' AssetRegistry query plus
    inspect_asset's referencer lookup -- needlessly so.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_unused_assets: invalid_arguments: arguments must be an object",
        })

    path_under = args.get("path_under", "/Game")
    if not isinstance(path_under, str) or not path_under:
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_unused_assets: invalid_field: 'path_under' must be a non-empty string when supplied",
        })

    class_filter = args.get("class_filter")
    if class_filter is not None and (not isinstance(class_filter, str) or not class_filter):
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_unused_assets: invalid_field: 'class_filter' must be a non-empty string when supplied",
        })

    limit = args.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 10000:
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_unused_assets: invalid_field: 'limit' must be an integer between 1 and 10000",
        })

    # find_assets requires class_path. Default to UObject (root of every
    # UE asset class) when caller didn't pin a class_filter; the path_under
    # narrows the scan range.
    find_params: dict = {
        "class_path": class_filter if class_filter else "/Script/CoreUObject.Object",
        "path_under": path_under,
        # Pull a wider candidate window than `limit` so transient inspect
        # failures don't starve the result. Cap at find_assets' upper bound
        # (500 per its schema) which is the safe maximum for one
        # round-trip.
        "limit": min(500, max(100, limit * 5)),
    }
    find_resp = call_ue("find_assets", find_params)
    if "error" in find_resp:
        upstream = find_resp.get("error", {}) or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"find_unused_assets: find_failed: {upstream.get('message') or 'find_assets returned an error'}",
        })

    candidates = (find_resp.get("result") or {}).get("assets") or []
    scanned = 0
    inspect_failures = 0
    unused: list[dict] = []
    for asset in candidates:
        if not isinstance(asset, dict):
            continue
        pkg = asset.get("package_path")
        if not isinstance(pkg, str) or not pkg:
            continue
        # Reconstruct the object path inspect_asset wants: /Game/Foo/Bar.Bar.
        name = asset.get("name")
        object_path = f"{pkg}.{name}" if isinstance(name, str) and name else pkg
        inspect_resp = call_ue("inspect_asset", {"path": object_path})
        scanned += 1
        if "error" in inspect_resp:
            inspect_failures += 1
            continue
        result = inspect_resp.get("result") or {}
        referencers = result.get("referencers")
        if isinstance(referencers, list) and len(referencers) == 0:
            unused.append({
                "path": pkg,
                "class": asset.get("class") or "",
            })
            if len(unused) >= limit:
                break

    # All inspects failed -> bubble the error up. A "0 unused, 0 scanned"
    # response would otherwise be confused with a clean codebase.
    if scanned > 0 and inspect_failures == scanned:
        return make_response(req_id, error={
            "code": -32603,
            "message": "find_unused_assets: inspect_failed: every candidate's inspect_asset call failed; cannot determine unused set",
        })

    truncated = len(unused) >= limit and scanned < len(candidates)
    return _wrap_tool_result(req_id, {
        "ok": True,
        "scanned": scanned,
        "unused_count": len(unused),
        "unused": unused,
        "truncated": truncated,
    })


def synthetic_get_reference_chain(req_id, args: dict) -> dict:
    """Bridge-side composition: BFS the asset reference graph from a root,
    up to `depth` hops.

    Composes inspect_asset recursively. Each call reads either
    `referencers` (direction=up, "who references me") or `dependencies`
    (direction=down, "what I reference"). De-duplicates visited nodes so
    cycles in the asset graph don't loop infinitely.

    Direction semantics:
      - `up`: starting from `root`, expand to every asset that has
        `root` in its dependencies. Useful for impact analysis ("if I
        delete X, what breaks?").
      - `down`: starting from `root`, expand to every asset listed in
        `root`'s dependencies. Useful for dependency audits ("what does
        X pull in?").

    Returns the BFS as a node + edge list rather than a tree because
    real asset graphs are DAGs and a flat edge representation lets the
    caller render whatever shape they want (tree, graph, table).

    `truncated` is set when the BFS hit the depth bound and there were
    still neighbors to expand at that frontier.

    Per-node inspect failures are SWALLOWED -- the BFS continues from
    whatever neighbors are known. Path validation (NUL + '..') applies
    to the root.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "get_reference_chain: invalid_arguments: arguments must be an object",
        })

    if "path" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "get_reference_chain: missing_required_field: 'path' is required",
        })

    root = args.get("path")
    err = _validate_asset_path("get_reference_chain", root, "path")
    if err is not None:
        return make_response(req_id, error={"code": -32602, "message": err})

    depth = args.get("depth", 3)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1 or depth > 8:
        return make_response(req_id, error={
            "code": -32602,
            "message": "get_reference_chain: invalid_depth: 'depth' must be an integer between 1 and 8",
        })

    direction = args.get("direction", "up")
    if direction not in ("up", "down"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "get_reference_chain: invalid_direction: 'direction' must be 'up' or 'down'",
        })

    # BFS: frontier holds nodes to expand at the current depth.
    visited: set[str] = {root}
    edges: list[dict] = []
    frontier: list[str] = [root]
    root_ok = False
    truncated = False
    neighbor_field = "referencers" if direction == "up" else "dependencies"

    for _ in range(depth):
        next_frontier: list[str] = []
        for node in frontier:
            inspect_resp = call_ue("inspect_asset", {"path": node})
            if "error" in inspect_resp:
                if node == root:
                    upstream = inspect_resp.get("error", {}) or {}
                    msg = upstream.get("message", "") or ""
                    # Surface asset_not_found verbatim when the root doesn't
                    # exist -- nothing useful to walk from.
                    if "asset_not_found" in msg.lower() or "not_found" in msg.lower():
                        return make_response(req_id, error={
                            "code": -32602,
                            "message": f"get_reference_chain: asset_not_found: root path '{root}' not in asset registry",
                        })
                    return make_response(req_id, error={
                        "code": upstream.get("code", -32603) or -32603,
                        "message": f"get_reference_chain: inspect_failed: inspecting root '{root}' failed: {msg}",
                    })
                # Non-root inspect failure: skip the node, continue BFS.
                continue
            if node == root:
                root_ok = True
            neighbors = (inspect_resp.get("result") or {}).get(neighbor_field) or []
            if not isinstance(neighbors, list):
                continue
            for neighbor in neighbors:
                if not isinstance(neighbor, str) or not neighbor:
                    continue
                edge = (
                    {"from": neighbor, "to": node}
                    if direction == "up"
                    else {"from": node, "to": neighbor}
                )
                edges.append(edge)
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.append(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier
    else:
        # Loop completed all `depth` iterations without breaking -- the
        # frontier at the last depth still had neighbors that would have
        # been expanded at depth+1. That counts as truncation.
        truncated = bool(frontier)

    # Root never resolved (unlikely after the validation pass above) but
    # we still return a clean envelope -- node_count counts everything
    # visited, including the root.
    if not root_ok and not edges:
        # Root inspect failed in a non-not_found way already returned above.
        # Reaching here means root inspect returned success with no
        # neighbors; that's a valid "no references" answer.
        pass

    return _wrap_tool_result(req_id, {
        "ok": True,
        "root": root,
        "direction": direction,
        "depth": depth,
        "node_count": len(visited),
        "edge_count": len(edges),
        "edges": edges,
        "truncated": truncated,
    })


def synthetic_bulk_compile_blueprints(req_id, args: dict) -> dict:
    """Bridge-side composition: compile multiple Blueprints in one MCP call
    by dispatching `compile_blueprint` per path.

    Mirrors `bulk_inspect_assets`'s shape (paths list + continue_on_error
    + per-path result envelope). Useful after batch-mutating Blueprint
    properties via execute_unreal_python or other tooling.

    Path validation reuses _validate_asset_path; per-path compile
    failures preserve the upstream JSON-RPC error code so callers can
    distinguish transport errors (-32099) from logical compile errors.

    Synthetic rather than C++ because the loop is pure protocol-level
    composition over the existing compile_blueprint handler.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_compile_blueprints: invalid_arguments: arguments must be an object",
        })

    if "paths" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_compile_blueprints: missing_required_field: 'paths' must be supplied as a list of Blueprint asset paths",
        })

    paths = args.get("paths")
    if not isinstance(paths, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_compile_blueprints: invalid_paths_shape: 'paths' must be a list of strings",
        })

    if len(paths) > 1000:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"bulk_compile_blueprints: invalid_paths_shape: at most 1000 paths per call (got {len(paths)})",
        })

    for i, path in enumerate(paths):
        err = _validate_asset_path("bulk_compile_blueprints", path, f"paths[{i}]")
        if err is not None:
            return make_response(req_id, error={"code": -32602, "message": err})

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_compile_blueprints: invalid_field: 'continue_on_error' must be a boolean",
        })

    results: list[dict] = []
    succeeded = 0
    failed = 0
    for path in paths:
        compile_resp = call_ue("compile_blueprint", {"path": path})
        if "error" in compile_resp:
            failed += 1
            upstream = compile_resp.get("error", {}) or {}
            code = upstream.get("code", -32603)
            if code is None:
                code = -32603
            results.append({
                "path": path,
                "ok": False,
                "error": {
                    "code": code,
                    "message": upstream.get("message") or "",
                },
            })
            if not continue_on_error:
                break
        else:
            succeeded += 1
            results.append({"path": path, "ok": True})

    return _wrap_tool_result(req_id, {
        "ok": failed == 0,
        "total": len(paths),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    })


def synthetic_audit_blueprint_compile_status(req_id, args: dict) -> dict:
    """Bridge-side composition: enumerate every Blueprint under a content
    path and bucket each by its compile-status.

    Pipeline:
      1. call_ue("find_assets", {class_path: /Script/Engine.Blueprint,
         path_under, limit: 500}) -- one round-trip to enumerate
         Blueprint assets in the scan range.
      2. For each, call_ue("inspect_blueprint", {path}) -- per-asset
         round-trip that reads (or will read once the C++ side adds it)
         a `blueprint_status` field. Buckets: UpToDate, Dirty, Error,
         Unknown, BeingCreated.
      3. Aggregate into `by_status` counts plus a `problem_assets`
         filtered list (Error+Unknown when compile_failures_only=true,
         otherwise every scanned BP).

    READ-ONLY: no compile is triggered. Pair with `bulk_compile_blueprints`
    to actually fix anything found.

    NB: Handler_InspectBlueprint.cpp emits `blueprint_status` as of the
    PR that closes scorecard follow-up #4 (mirrors the helper already used
    by Handler_InspectWidgetBlueprint.cpp). Older plugin DLLs that predate
    the fix will still surface every BP as `Unknown` (defensive fallback)
    until the host editor is cold-rebuilt against the new handler.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "audit_blueprint_compile_status: invalid_arguments: arguments must be an object",
        })

    path_under = args.get("path_under", "/Game")
    if not isinstance(path_under, str) or not path_under:
        return make_response(req_id, error={
            "code": -32602,
            "message": "audit_blueprint_compile_status: invalid_field: 'path_under' must be a non-empty string when supplied",
        })
    # Normalize: find_assets's `path_under` validator rejects bare mount points
    # without a trailing slash (e.g. '/Game' errors with invalid_path_filter)
    # but accepts '/Game/'. Append the slash when missing so callers can pass
    # either form. Documented in the PR #174 scorecard follow-up #3.
    if not path_under.endswith("/"):
        path_under = path_under + "/"

    compile_failures_only = args.get("compile_failures_only", True)
    if not isinstance(compile_failures_only, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "audit_blueprint_compile_status: invalid_field: 'compile_failures_only' must be a boolean",
        })

    find_resp = call_ue("find_assets", {
        "class_path": "/Script/Engine.Blueprint",
        "path_under": path_under,
        "limit": 500,
    })
    if "error" in find_resp:
        upstream = find_resp.get("error", {}) or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"audit_blueprint_compile_status: find_failed: {upstream.get('message') or 'find_assets returned an error'}",
        })

    candidates = (find_resp.get("result") or {}).get("assets") or []
    by_status = {
        "UpToDate": 0,
        "Dirty": 0,
        "Error": 0,
        "Unknown": 0,
        "BeingCreated": 0,
    }
    problem_assets: list[dict] = []
    scanned = 0
    inspect_failures = 0
    for asset in candidates:
        if not isinstance(asset, dict):
            continue
        pkg = asset.get("package_path")
        if not isinstance(pkg, str) or not pkg:
            continue
        name = asset.get("name")
        object_path = f"{pkg}.{name}" if isinstance(name, str) and name else pkg
        inspect_resp = call_ue("inspect_blueprint", {"path": object_path})
        scanned += 1
        if "error" in inspect_resp:
            inspect_failures += 1
            # Treat inspect failures as Unknown rather than aborting --
            # the asset registry listed the BP so it exists, even if a
            # transient inspect failed.
            status = "Unknown"
        else:
            result = inspect_resp.get("result") or {}
            raw_status = result.get("blueprint_status")
            if isinstance(raw_status, str) and raw_status in by_status:
                status = raw_status
            else:
                status = "Unknown"
        by_status[status] += 1
        if compile_failures_only:
            if status in ("Error", "Unknown"):
                problem_assets.append({"path": pkg, "status": status})
        else:
            problem_assets.append({"path": pkg, "status": status})

    if scanned > 0 and inspect_failures == scanned:
        return make_response(req_id, error={
            "code": -32603,
            "message": "audit_blueprint_compile_status: inspect_failed: every candidate's inspect_blueprint call failed; audit results meaningless",
        })

    return _wrap_tool_result(req_id, {
        "ok": True,
        "scanned": scanned,
        "by_status": by_status,
        "problem_assets": problem_assets,
    })


def synthetic_find_actors_by_class(req_id, args: dict) -> dict:
    """Bridge-side composition: filter the active level's actors by class.

    Pipeline:
      1. Optionally call_ue("load_level_by_path", {"path": level}) when
         the caller supplied a `level` UWorld path — get_actors_in_level
         only enumerates the active editor world, so the level must be
         current.
      2. call_ue("get_actors_in_level", {}) -- one round-trip to fetch
         every actor's name/label/class/transform.
      3. Filter client-side by class name. Input accepts either a short
         class name (`StaticMeshActor`) or a full class path
         (`/Script/Engine.StaticMeshActor`); the synthetic strips
         everything up to and including the final `.` before
         case-insensitive comparison against each actor's `class` field.

    The UE C++ handler currently emits only `class` (short name); the
    synthetic re-projects the flat `loc_x/y/z` + `yaw/pitch/roll` into a
    structured `transform: {loc, rot}` envelope for caller convenience.
    Scale is not emitted by the handler so it is omitted rather than
    fabricated.

    Synthetic rather than C++ because the loop is pure protocol-level
    composition over get_actors_in_level — adding a class filter
    server-side would only duplicate logic the bridge can do trivially.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_actors_by_class: invalid_arguments: arguments must be an object",
        })

    if "class_name" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_actors_by_class: missing_required_field: 'class_name' must be supplied",
        })

    class_name = args.get("class_name")
    if not isinstance(class_name, str) or not class_name:
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_actors_by_class: missing_required_field: 'class_name' must be a non-empty string",
        })

    level = args.get("level")
    if level is not None and (not isinstance(level, str) or not level):
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_actors_by_class: invalid_field: 'level' must be a non-empty string when supplied",
        })

    if isinstance(level, str):
        load_resp = call_ue("load_level_by_path", {"path": level})
        if "error" in load_resp:
            upstream = load_resp.get("error", {}) or {}
            return make_response(req_id, error={
                "code": upstream.get("code", -32603) or -32603,
                "message": f"find_actors_by_class: get_actors_failed: load_level_by_path for '{level}' failed: {upstream.get('message') or ''}",
            })

    actors_resp = call_ue("get_actors_in_level", {})
    if "error" in actors_resp:
        upstream = actors_resp.get("error", {}) or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"find_actors_by_class: get_actors_failed: {upstream.get('message') or 'get_actors_in_level returned an error'}",
        })

    result = actors_resp.get("result") or {}
    all_actors = result.get("actors") or []
    total_in_level = result.get("total_actors", len(all_actors))

    # Strip everything up to and including the final '.' so a class-path
    # input like '/Script/Engine.StaticMeshActor' matches the C++
    # handler's short-name output 'StaticMeshActor'.
    needle_short = class_name.rsplit(".", 1)[-1].lower()
    # Guard against trailing-dot or dot-only input that strips to empty.
    # Without this, every actor.class would compare unequal to "" and the
    # call silently returns count=0 with no error. Surface as -32602
    # invalid_field so callers know the input shape was malformed.
    if not needle_short:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"find_actors_by_class: invalid_field: 'class_name' resolves to empty after trimming class-path prefix (input was '{class_name}')",
        })

    matched: list[dict] = []
    for actor in all_actors:
        if not isinstance(actor, dict):
            continue
        cls = actor.get("class")
        if not isinstance(cls, str):
            continue
        if cls.lower() != needle_short:
            continue
        matched.append({
            "name": actor.get("name"),
            "label": actor.get("label"),
            "class": cls,
            "class_path": actor.get("class_path"),
            "transform": {
                "loc": {
                    "x": actor.get("loc_x"),
                    "y": actor.get("loc_y"),
                    "z": actor.get("loc_z"),
                },
                "rot": {
                    "pitch": actor.get("pitch"),
                    "yaw": actor.get("yaw"),
                    "roll": actor.get("roll"),
                },
            },
        })

    return _wrap_tool_result(req_id, {
        "ok": True,
        "class_name": class_name,
        "total_in_level": total_in_level,
        "count": len(matched),
        "actors": matched,
    })


def _validate_actor_names(tool_name: str, args: dict, max_names: int) -> tuple[list[str], dict | None]:
    """Shape-check `names` list for the bulk_*_actors family.

    Returns (names, error_envelope). When error_envelope is not None the
    caller should return it immediately; otherwise `names` holds the
    validated list.
    """
    if "names" not in args:
        return [], {
            "code": -32602,
            "message": f"{tool_name}: missing_required_field: 'names' must be supplied as a list of actor names",
        }
    names = args.get("names")
    if not isinstance(names, list):
        return [], {
            "code": -32602,
            "message": f"{tool_name}: invalid_names_shape: 'names' must be a list of strings",
        }
    if len(names) > max_names:
        return [], {
            "code": -32602,
            "message": f"{tool_name}: too_many_names: at most {max_names} names per call (got {len(names)})",
        }
    for i, name in enumerate(names):
        if not isinstance(name, str) or not name:
            return [], {
                "code": -32602,
                "message": f"{tool_name}: name_must_be_string: names[{i}] must be a non-empty string",
            }
    return names, None


def _validate_delay_ms(tool_name: str, args: dict) -> tuple[int, dict | None]:
    """Shape-check optional `delay_ms`; default 500, max 10000.

    Returns (delay_ms, error_envelope). Booleans are rejected (Python's
    bool is an int subclass — accepting True would coerce to 1ms).
    """
    delay_ms = args.get("delay_ms", 500)
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int):
        return 0, {
            "code": -32602,
            "message": f"{tool_name}: invalid_delay: 'delay_ms' must be an integer between 0 and 10000",
        }
    if delay_ms < 0 or delay_ms > 10000:
        return 0, {
            "code": -32602,
            "message": f"{tool_name}: invalid_delay: 'delay_ms' must be an integer between 0 and 10000 (got {delay_ms})",
        }
    return delay_ms, None


def synthetic_bulk_focus_actors(req_id, args: dict) -> dict:
    """Bridge-side composition: visit each actor in sequence, framing the
    viewport on each, and optionally capturing a viewport screenshot
    after each focus.

    Composition:
      For each name in `names`:
        1. focus_actor {name} -- viewport reframe
        2. time.sleep(delay_ms / 1000) -- settle viewport + LOD (skipped
           after the last entry)
        3. if screenshot_each: get_viewport_screenshot {} -- capture PNG

    Useful for `show me each enemy / spawn / light in turn` walkthroughs
    that would otherwise force the LLM into a focus -> screenshot ->
    focus polling loop. `screenshot_each=false` keeps the round-trip
    count down when only the side-effect of moving the viewport matters
    (e.g. preparing a recorded sequence).

    Synthetic rather than C++ because the loop is pure protocol-level
    composition; a C++ handler would have to duplicate focus_actor +
    get_viewport_screenshot internals.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_focus_actors: invalid_arguments: arguments must be an object",
        })

    names, err = _validate_actor_names("bulk_focus_actors", args, max_names=100)
    if err is not None:
        return make_response(req_id, error=err)

    delay_ms, err = _validate_delay_ms("bulk_focus_actors", args)
    if err is not None:
        return make_response(req_id, error=err)

    screenshot_each = args.get("screenshot_each", False)
    if not isinstance(screenshot_each, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_focus_actors: invalid_field: 'screenshot_each' must be a boolean",
        })

    focused = 0
    failed: list[dict] = []
    screenshots: list[dict] = []
    for i, name in enumerate(names):
        focus_resp = call_ue("focus_actor", {"name": name})
        if "error" in focus_resp:
            upstream = focus_resp.get("error", {}) or {}
            failed.append({
                "name": name,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": f"bulk_focus_actors: focus_failed: focus_actor on '{name}' failed: {upstream.get('message') or ''}",
                },
            })
        else:
            focused += 1
            if screenshot_each:
                # Settle window BEFORE the screenshot so the captured
                # frame reflects the post-focus viewport (LODs streamed,
                # camera lerp finished). Without this delay, the
                # screenshot races the focus_actor side-effect and may
                # capture the previous frame. Applied per-iteration
                # rather than between iterations (the original spec was
                # ambiguous; CodeRabbit flagged the race in PR #168).
                if delay_ms > 0:
                    time.sleep(delay_ms / 1000.0)
                shot_resp = call_ue("get_viewport_screenshot", {})
                if "error" in shot_resp:
                    upstream = shot_resp.get("error", {}) or {}
                    failed.append({
                        "name": name,
                        "error": {
                            "code": upstream.get("code", -32603) or -32603,
                            "message": f"bulk_focus_actors: screenshot_failed: get_viewport_screenshot after '{name}' failed: {upstream.get('message') or ''}",
                        },
                    })
                else:
                    shot_result = shot_resp.get("result", {}) or {}
                    screenshots.append({
                        "name": name,
                        "png_base64": shot_result.get("png_base64"),
                    })

        # Settle delay: when screenshot_each=true we sleep BEFORE the
        # screenshot inside the iteration (see block above — moved there
        # for correctness). For the non-screenshot path we still want a
        # delay between focus calls so LODs / streaming have time to
        # update before the next focus_actor call. delay_ms == 0 disables.
        if delay_ms > 0 and not screenshot_each and i < len(names) - 1:
            time.sleep(delay_ms / 1000.0)

    body: dict = {
        "ok": len(failed) == 0,
        "total": len(names),
        "focused": focused,
        "failed": failed,
    }
    if screenshot_each:
        body["screenshots"] = screenshots
    return _wrap_tool_result(req_id, body)


def synthetic_bulk_screenshot_actors(req_id, args: dict) -> dict:
    """Bridge-side composition: focus + screenshot each actor in a
    sequence by dispatching the existing `screenshot_actor` synthetic
    (which itself composes focus_actor + get_viewport_screenshot).

    Composition:
      For each name in `names`:
        1. screenshot_actor {name} -- the existing synthetic handles
           focus + capture in one logical step (separate UE round-trips
           under the hood so the camera-move-then-capture race is
           avoided)
        2. time.sleep(delay_ms / 1000) -- settle delay between actors,
           skipped after the last entry

    Useful for thumbnail-pipeline runs: 'screenshot each StaticMeshActor
    in the level' becomes one MCP call instead of N. The same delay-ms
    knob as bulk_focus_actors lets callers tune for LOD/streaming.

    Synthetic rather than C++ because screenshot_actor itself is a
    bridge-side composition; nesting C++ over Python over C++ would
    double the round-trip cost without functional gain.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_screenshot_actors: invalid_arguments: arguments must be an object",
        })

    names, err = _validate_actor_names("bulk_screenshot_actors", args, max_names=50)
    if err is not None:
        return make_response(req_id, error=err)

    delay_ms, err = _validate_delay_ms("bulk_screenshot_actors", args)
    if err is not None:
        return make_response(req_id, error=err)

    succeeded = 0
    results: list[dict] = []
    for i, name in enumerate(names):
        # Re-enter the synthetic dispatcher rather than calling call_ue
        # directly: screenshot_actor is itself a synthetic so it has no
        # UE handler to dispatch to.
        shot_resp = synthetic_screenshot_actor(req_id, {"name": name})
        if "error" in shot_resp:
            upstream = shot_resp.get("error", {}) or {}
            results.append({
                "name": name,
                "ok": False,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": upstream.get("message") or f"bulk_screenshot_actors: screenshot_failed: screenshot_actor on '{name}' failed",
                },
            })
        else:
            # screenshot_actor wraps its body in {"result": {"content":
            # [{"type": "text", "text": json_blob}], "isError": false}}.
            # Unwrap the inner JSON so the bulk results stay flat.
            content = (shot_resp.get("result") or {}).get("content") or []
            inner_text = content[0].get("text") if content else "{}"
            try:
                inner = json.loads(inner_text) if isinstance(inner_text, str) else {}
            except json.JSONDecodeError as e:
                # Malformed inner payload is a real failure — do NOT count
                # as succeeded. Previously we swallowed JSONDecodeError +
                # marked the actor ok:true with null png_base64, which
                # CodeRabbit flagged as a silent false-positive in PR #168.
                results.append({
                    "name": name,
                    "ok": False,
                    "error": {
                        "code": -32603,
                        "message": f"bulk_screenshot_actors: malformed_screenshot_payload: screenshot_actor on '{name}' returned non-JSON content: {e}",
                    },
                })
                continue
            succeeded += 1
            results.append({
                "name": name,
                "ok": True,
                "png_base64": inner.get("png_base64"),
                "focused": inner.get("focused"),
                "loc": inner.get("loc"),
            })

        if delay_ms > 0 and i < len(names) - 1:
            time.sleep(delay_ms / 1000.0)

    return _wrap_tool_result(req_id, {
        "ok": succeeded == len(names),
        "total": len(names),
        "succeeded": succeeded,
        "results": results,
    })


def synthetic_bulk_set_actor_property(req_id, args: dict) -> dict:
    """Bridge-side composition: apply many UPROPERTY mutations across
    many actors by dispatching `set_actor_property` per assignment.

    Composition:
      For each {actor, property, value} in `assignments`:
        1. set_actor_property {name=actor, property, value}
        2. on failure: record + continue (continue_on_error=true,
           default) OR halt and record halted_at_index
           (continue_on_error=false)

    Each assignment is independent — this is NOT 'set the same property
    on N actors', it's 'run N individual sets'. Useful after batch-
    spawning to apply initial-state mutations (e.g. paint each enemy's
    AI tag, set per-actor team colors) without N round-trips.

    Mirrors the bulk_compile_blueprints partial-failure semantics:
    ok=true only when failed==0; halted_at_index appears only when
    continue_on_error=false stopped the loop early.

    Synthetic rather than C++ for the same reasons as the rest of the
    bulk_* family.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_actor_property: invalid_arguments: arguments must be an object",
        })

    if "assignments" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_actor_property: missing_required_field: 'assignments' must be supplied as a list of {actor, property, value} objects",
        })

    assignments = args.get("assignments")
    if not isinstance(assignments, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_actor_property: invalid_assignments_shape: 'assignments' must be a list of objects",
        })

    if len(assignments) > 200:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"bulk_set_actor_property: too_many_assignments: at most 200 assignments per call (got {len(assignments)})",
        })

    for i, assignment in enumerate(assignments):
        if not isinstance(assignment, dict):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_set_actor_property: assignment_must_be_object: assignments[{i}] must be an object",
            })
        actor = assignment.get("actor")
        if not isinstance(actor, str) or not actor:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_set_actor_property: assignment_missing_field: assignments[{i}].'actor' must be a non-empty string",
            })
        prop = assignment.get("property")
        if not isinstance(prop, str) or not prop:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_set_actor_property: assignment_missing_field: assignments[{i}].'property' must be a non-empty string",
            })
        if "value" not in assignment:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_set_actor_property: assignment_missing_field: assignments[{i}].'value' is required (use null for explicit-null intent)",
            })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_actor_property: invalid_field: 'continue_on_error' must be a boolean",
        })

    succeeded = 0
    failed: list[dict] = []
    halted_at_index: int | None = None
    for i, assignment in enumerate(assignments):
        actor = assignment["actor"]
        prop = assignment["property"]
        value = assignment["value"]
        set_resp = call_ue("set_actor_property", {
            "name": actor,
            "property": prop,
            "value": value,
        })
        if "error" in set_resp:
            upstream = set_resp.get("error", {}) or {}
            failed.append({
                "actor": actor,
                "property": prop,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": f"bulk_set_actor_property: set_failed: set_actor_property on '{actor}'.'{prop}' failed: {upstream.get('message') or ''}",
                },
            })
            if not continue_on_error:
                halted_at_index = i
                break
        else:
            succeeded += 1

    body: dict = {
        "ok": len(failed) == 0,
        "total": len(assignments),
        "succeeded": succeeded,
        "failed": failed,
    }
    if halted_at_index is not None:
        body["halted_at_index"] = halted_at_index
    return _wrap_tool_result(req_id, body)


def synthetic_compare_assets(req_id, args: dict) -> dict:
    """Bridge-side composition: symmetric diff between two assets' inspect_asset
    outputs.

    Composes two `call_ue("inspect_asset", {path})` requests and returns the
    fields that differ. The 'path' field is excluded from comparison because
    it is trivially different between the two inputs (each response echoes
    its own path).

    When `fields` is supplied, only those names are compared (intersection
    with each response). When omitted, the union of both responses' keys
    (minus 'path') is diffed.

    Synthetic rather than C++ because the diff is pure dict comparison over
    the existing inspect_asset handler -- no UE side-effect, no shared state.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "compare_assets: invalid_arguments: arguments must be an object",
        })

    if "path_a" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compare_assets: missing_required_field: 'path_a' is required",
        })
    if "path_b" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "compare_assets: missing_required_field: 'path_b' is required",
        })

    path_a = args.get("path_a")
    err = _validate_asset_path("compare_assets", path_a, "path_a")
    if err is not None:
        return make_response(req_id, error={"code": -32602, "message": err})

    path_b = args.get("path_b")
    err = _validate_asset_path("compare_assets", path_b, "path_b")
    if err is not None:
        return make_response(req_id, error={"code": -32602, "message": err})

    fields = args.get("fields")
    if fields is not None:
        if not isinstance(fields, list):
            return make_response(req_id, error={
                "code": -32602,
                "message": "compare_assets: invalid_field: 'fields' must be a list of strings",
            })
        for i, f in enumerate(fields):
            if not isinstance(f, str) or not f:
                return make_response(req_id, error={
                    "code": -32602,
                    "message": f"compare_assets: invalid_field: fields[{i}] must be a non-empty string",
                })

    resp_a = call_ue("inspect_asset", {"path": path_a})
    if "error" in resp_a:
        upstream = resp_a.get("error", {}) or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"compare_assets: inspect_failed_a: inspecting '{path_a}' failed: {upstream.get('message') or ''}",
        })

    resp_b = call_ue("inspect_asset", {"path": path_b})
    if "error" in resp_b:
        upstream = resp_b.get("error", {}) or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"compare_assets: inspect_failed_b: inspecting '{path_b}' failed: {upstream.get('message') or ''}",
        })

    result_a = resp_a.get("result") or {}
    result_b = resp_b.get("result") or {}

    # The 'path' field is trivially different (each result echoes its own
    # path) -- exclude it so the diff is meaningful.
    if fields:
        compared = [f for f in fields if f != "path"]
    else:
        union = set(result_a.keys()) | set(result_b.keys())
        union.discard("path")
        compared = sorted(union)

    differences: list[dict] = []
    for field in compared:
        va = result_a.get(field)
        vb = result_b.get(field)
        if va != vb:
            differences.append({
                "field": field,
                "value_a": va,
                "value_b": vb,
            })

    return _wrap_tool_result(req_id, {
        "ok": True,
        "path_a": path_a,
        "path_b": path_b,
        "identical": len(differences) == 0,
        "fields_compared": compared,
        "differences": differences,
    })


def synthetic_bulk_set_console_variables(req_id, args: dict) -> dict:
    """Bridge-side composition: set multiple CVars in one MCP call with
    optional atomic rollback.

    Pipeline per assignment:
      1. call_ue("get_console_variable", {name}) -- capture pre-value's
         value_string for rollback.
      2. call_ue("set_console_variable", {name, value}) -- apply new value.

    On any failure when rollback_on_error=true: stop applying further
    assignments AND walk back every already-applied change by issuing
    set_console_variable with its captured pre-value. Per-restore failures
    are surfaced in `rollback_failures` so the caller knows which CVars
    are still in their mutated state.

    Mirrors the bulk_compile_blueprints partial-failure shape but adds the
    rollback ledger because cvar mutations have observable side-effects on
    the running editor (unlike bulk_inspect_assets which is read-only).
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_console_variables: invalid_arguments: arguments must be an object",
        })

    if "assignments" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_console_variables: missing_required_field: 'assignments' must be supplied as an object mapping cvar_name -> new_value",
        })

    assignments = args.get("assignments")
    if not isinstance(assignments, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_console_variables: invalid_assignments_shape: 'assignments' must be an object (mapping cvar_name -> new_value)",
        })

    if len(assignments) > 50:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"bulk_set_console_variables: too_many_assignments: at most 50 assignments per call (got {len(assignments)})",
        })

    for name, value in assignments.items():
        if not isinstance(name, str) or not name:
            return make_response(req_id, error={
                "code": -32602,
                "message": "bulk_set_console_variables: invalid_assignments_shape: cvar names must be non-empty strings",
            })
        # set_console_variable accepts string|number|bool. Mirror that here so
        # we reject mistyped values before any UE round-trip.
        if not isinstance(value, (str, int, float, bool)):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_set_console_variables: assignment_value_invalid_type: assignments['{name}'] must be a string, number, or boolean",
            })

    rollback_on_error = args.get("rollback_on_error", True)
    if not isinstance(rollback_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_set_console_variables: invalid_field: 'rollback_on_error' must be a boolean",
        })

    applied: list[dict] = []
    failed: list[dict] = []
    captured: list[tuple[str, str]] = []  # (name, pre_value_string) for rollback

    for name, value in assignments.items():
        # Capture old value first.
        get_resp = call_ue("get_console_variable", {"name": name})
        if "error" in get_resp:
            upstream = get_resp.get("error", {}) or {}
            failed.append({
                "name": name,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": f"bulk_set_console_variables: get_failed: capturing pre-value for '{name}' failed: {upstream.get('message') or ''}",
                },
            })
            if rollback_on_error:
                break
            continue

        old_value = (get_resp.get("result") or {}).get("value_string", "")

        # Apply new value.
        set_resp = call_ue("set_console_variable", {"name": name, "value": value})
        if "error" in set_resp:
            upstream = set_resp.get("error", {}) or {}
            failed.append({
                "name": name,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": f"bulk_set_console_variables: set_failed: applying '{name}' failed: {upstream.get('message') or ''}",
                },
            })
            if rollback_on_error:
                break
            continue

        captured.append((name, old_value))
        applied.append({"name": name, "old_value": old_value, "new_value": value})

    rolled_back = False
    rollback_failures: list[dict] = []
    if rollback_on_error and failed and captured:
        rolled_back = True
        # Restore in REVERSE order of application so inter-dependent
        # CVars unwind correctly (a later-applied CVar may depend on an
        # earlier one — restoring the dependent first leaves the
        # dependency in an inconsistent intermediate state). Best-
        # practice rollback semantics; flagged by gemini-code-assist
        # on PR #169.
        for name, old_value in reversed(captured):
            restore_resp = call_ue("set_console_variable", {"name": name, "value": old_value})
            if "error" in restore_resp:
                upstream = restore_resp.get("error", {}) or {}
                rollback_failures.append({
                    "name": name,
                    "error": {
                        "code": upstream.get("code", -32603) or -32603,
                        "message": f"bulk_set_console_variables: rollback_failed: restoring '{name}' to pre-value failed: {upstream.get('message') or ''}",
                    },
                })

    body: dict = {
        "ok": len(failed) == 0,
        "total": len(assignments),
        "applied": applied,
        "failed": failed,
        "rolled_back": rolled_back,
    }
    if rolled_back and rollback_failures:
        body["rollback_failures"] = rollback_failures
    return _wrap_tool_result(req_id, body)


def synthetic_inspect_dependency_graph(req_id, args: dict) -> dict:
    """Bridge-side composition: BFS the asset dependency graph from a root,
    optionally bidirectional.

    Mirrors get_reference_chain's BFS shape but:
      - defaults to direction=down (dependencies) -- this synthetic is
        framed for packaging audits, not impact-of-change.
      - when include_referencers=true, also expands referencers in the same
        BFS, recording direction per edge. Visited de-duplication spans
        both directions so a single asset reached via two paths is
        inspected once.
      - edges carry a `direction` field so the caller can render the
        bidirectional graph without losing edge orientation.

    Per-node inspect failures are SWALLOWED (BFS continues from known
    neighbors). Root failure SURFACES (asset_not_found -> -32602,
    other inspect failures -> -32603).
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_dependency_graph: invalid_arguments: arguments must be an object",
        })

    if "path" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_dependency_graph: missing_required_field: 'path' is required",
        })

    root = args.get("path")
    err = _validate_asset_path("inspect_dependency_graph", root, "path")
    if err is not None:
        return make_response(req_id, error={"code": -32602, "message": err})

    depth = args.get("depth", 2)
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1 or depth > 8:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_dependency_graph: invalid_depth: 'depth' must be an integer between 1 and 8",
        })

    include_referencers = args.get("include_referencers", False)
    if not isinstance(include_referencers, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_dependency_graph: invalid_field: 'include_referencers' must be a boolean",
        })

    visited: set[str] = {root}
    edges: list[dict] = []
    frontier: list[str] = [root]
    truncated = False

    for _ in range(depth):
        next_frontier: list[str] = []
        for node in frontier:
            inspect_resp = call_ue("inspect_asset", {"path": node})
            if "error" in inspect_resp:
                if node == root:
                    upstream = inspect_resp.get("error", {}) or {}
                    msg = upstream.get("message", "") or ""
                    if "asset_not_found" in msg.lower() or "not_found" in msg.lower():
                        return make_response(req_id, error={
                            "code": -32602,
                            "message": f"inspect_dependency_graph: asset_not_found: root path '{root}' not in asset registry",
                        })
                    return make_response(req_id, error={
                        "code": upstream.get("code", -32603) or -32603,
                        "message": f"inspect_dependency_graph: inspect_failed: inspecting root '{root}' failed: {msg}",
                    })
                # Non-root inspect failure: skip the node, continue BFS.
                continue

            result = inspect_resp.get("result") or {}

            # Always follow dependencies (down).
            deps = result.get("dependencies") or []
            if isinstance(deps, list):
                for neighbor in deps:
                    if not isinstance(neighbor, str) or not neighbor:
                        continue
                    edges.append({"from": node, "to": neighbor, "direction": "down"})
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)

            # Optionally also follow referencers (up).
            if include_referencers:
                refs = result.get("referencers") or []
                if isinstance(refs, list):
                    for neighbor in refs:
                        if not isinstance(neighbor, str) or not neighbor:
                            continue
                        edges.append({"from": neighbor, "to": node, "direction": "up"})
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_frontier.append(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier
    else:
        truncated = bool(frontier)

    return _wrap_tool_result(req_id, {
        "ok": True,
        "root": root,
        "depth": depth,
        "include_referencers": include_referencers,
        "node_count": len(visited),
        "edge_count": len(edges),
        "nodes": sorted(visited),
        "edges": edges,
        "truncated": truncated,
    })


def synthetic_bulk_fix_redirectors(req_id, args: dict) -> dict:
    """Bridge-side composition: resolve UObjectRedirector stubs across many
    folders by dispatching `fix_up_redirectors` per folder.

    Mirrors bulk_compile_blueprints's partial-failure shape. The optional
    `recursive` flag is informational (fix_up_redirectors itself always
    operates recursively under the supplied path) -- it is echoed back so
    callers can capture intent without tracking it separately.

    Synthetic rather than C++ for the same reason as the rest of the
    bulk_* family.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_fix_redirectors: invalid_arguments: arguments must be an object",
        })

    if "folders" not in args:
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_fix_redirectors: missing_required_field: 'folders' must be supplied as a list of content folder paths",
        })

    folders = args.get("folders")
    if not isinstance(folders, list):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_fix_redirectors: invalid_folders_shape: 'folders' must be a list of strings",
        })

    if len(folders) > 100:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"bulk_fix_redirectors: too_many_folders: at most 100 folders per call (got {len(folders)})",
        })

    for i, folder in enumerate(folders):
        if not isinstance(folder, str) or not folder:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"bulk_fix_redirectors: folder_must_be_string: folders[{i}] must be a non-empty string",
            })
        err = _validate_asset_path("bulk_fix_redirectors", folder, f"folders[{i}]")
        if err is not None:
            # _validate_asset_path emits path_must_be_string / path_invalid;
            # remap to folder_invalid so the error code matches the spec.
            return make_response(req_id, error={
                "code": -32602,
                "message": err.replace("path_must_be_string", "folder_invalid").replace("path_invalid", "folder_invalid"),
            })

    recursive = args.get("recursive", True)
    if not isinstance(recursive, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_fix_redirectors: invalid_field: 'recursive' must be a boolean",
        })

    continue_on_error = args.get("continue_on_error", True)
    if not isinstance(continue_on_error, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_fix_redirectors: invalid_field: 'continue_on_error' must be a boolean",
        })

    succeeded = 0
    failed: list[dict] = []
    halted_at_index: int | None = None
    for i, folder in enumerate(folders):
        fix_resp = call_ue("fix_up_redirectors", {"path": folder})
        if "error" in fix_resp:
            upstream = fix_resp.get("error", {}) or {}
            failed.append({
                "folder": folder,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": f"bulk_fix_redirectors: fix_failed: fix_up_redirectors on '{folder}' failed: {upstream.get('message') or ''}",
                },
            })
            if not continue_on_error:
                halted_at_index = i
                break
        else:
            succeeded += 1

    body: dict = {
        "ok": len(failed) == 0,
        "total": len(folders),
        "succeeded": succeeded,
        "failed": failed,
        "recursive": recursive,
    }
    if halted_at_index is not None:
        body["halted_at_index"] = halted_at_index
    return _wrap_tool_result(req_id, body)


# ---------------------------------------------------------------------------
# Marketplace synthetic tools (PR #2)
#
# Two bridge-side synthetic tools that surface CC0 / free-to-use 3D assets
# from public marketplaces (Polyhaven, AmbientCG) without leaving the
# editor. All endpoints below are public JSON APIs that need no auth and
# no API key. The bridge fetches catalog metadata via urllib (stdlib —
# no extra Python dep), then for `marketplace_import` downloads the chosen
# file to a temp path and calls the native `import_texture` handler to
# round-trip it into the project as a UTexture2D asset.
#
# Licensing:
#   - Polyhaven: every asset on the platform is CC0 (public domain) —
#     no attribution required, free for any use including commercial.
#   - AmbientCG: every asset on the platform is CC0 as well.
#
# Scope of v1:
#   - Textures (color/diffuse map only — full PBR multi-map import is a
#     v2 enhancement) at user-chosen resolution.
#   - HDRIs (sky environments) as EXR.
#   - Models: NOT yet implemented (would need a glTF/FBX import path; the
#     native `import_texture` only handles UTexture2D-class imports). The
#     `asset_type=model` path is parked behind a clear "not_implemented"
#     error so the surface is discoverable for future work.
#
# Failure modes intentionally surfaced rather than masked:
#   - Network unreachable / DNS failure → `network_error` with the
#     underlying urllib exception in the message.
#   - HTTP 4xx/5xx → `http_error` with status code.
#   - Slug not found in source catalog → `not_found`.
#   - Requested resolution not available for asset → `resolution_unavailable`
#     with the list of resolutions the source actually offers.
# ---------------------------------------------------------------------------


_MARKETPLACE_USER_AGENT = "UnrealClaudeMCP/0.9.1 (+https://github.com/NAJEMWEHBE/UnrealClaudeMCP)"
_MARKETPLACE_TIMEOUT_SECS = 30


def _marketplace_http_get_json(url: str) -> tuple[dict | list | None, dict | None]:
    """Plain-HTTPS GET that returns (parsed_json, error_dict).

    On success: (data, None). On any failure: (None, error_dict) shaped for
    `make_response`. urllib is used because the bridge has no `requests` dep.
    """
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, headers={"User-Agent": _MARKETPLACE_USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_MARKETPLACE_TIMEOUT_SECS) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        return None, {"code": -32603, "message": f"http_error: status={e.code} url={url}: {e.reason}"}
    except urllib.error.URLError as e:
        return None, {"code": -32603, "message": f"network_error: url={url}: {e.reason}"}
    except Exception as e:
        return None, {"code": -32603, "message": f"fetch_failed: url={url}: {e}"}
    try:
        return json.loads(body.decode("utf-8", errors="replace")), None
    except Exception as e:
        return None, {"code": -32603, "message": f"json_decode_failed: url={url}: {e}"}


def _marketplace_http_download(url: str, dest_path: str) -> dict | None:
    """Stream a binary URL to dest_path. Returns None on success or an
    error dict suitable for `make_response`. Atomic-ish: writes to
    dest_path + ".part" then renames. On any failure mid-download the
    .part file is removed so it does not orphan in the temp dir."""
    import urllib.request
    import urllib.error
    import os
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return {"code": -32603, "message": f"invalid_download_url: scheme must be https, got '{parsed.scheme or ''}': {url}"}
    tmp = dest_path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": _MARKETPLACE_USER_AGENT})
    err_result: dict | None = None
    try:
        with urllib.request.urlopen(req, timeout=_MARKETPLACE_TIMEOUT_SECS) as resp, open(tmp, "wb") as out:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.HTTPError as e:
        err_result = {"code": -32603, "message": f"http_error: status={e.code} url={url}: {e.reason}"}
    except urllib.error.URLError as e:
        err_result = {"code": -32603, "message": f"network_error: url={url}: {e.reason}"}
    except Exception as e:
        err_result = {"code": -32603, "message": f"download_failed: url={url}: {e}"}
    if err_result is not None:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return err_result
    try:
        os.replace(tmp, dest_path)
    except Exception as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return {"code": -32603, "message": f"rename_failed: {tmp} -> {dest_path}: {e}"}
    return None


def _polyhaven_type_for(asset_type: str) -> str | None:
    """Polyhaven's /assets endpoint expects plural-string type filters:
    hdris / textures / models / all. The response payload encodes the
    type as a 0/1/2 int (kept in the inverse table inside
    `_polyhaven_search` when normalising results)."""
    return {"hdri": "hdris", "texture": "textures", "model": "models"}.get(asset_type)


def _polyhaven_search(query: str, asset_type: str, limit: int) -> tuple[list[dict] | None, dict | None]:
    type_filter = _polyhaven_type_for(asset_type) if asset_type != "all" else None
    # Polyhaven's /assets endpoint returns the full catalog scoped by
    # ?type=<hdris|textures|models|all> (omitted = all types). The
    # ?search= query parameter is documented but the public API ignores
    # it and returns the full catalog regardless, so the query is
    # applied client-side below via AND-token matching across name +
    # tags + categories + slug, then ranked by download_count desc
    # before applying the limit.
    url = "https://api.polyhaven.com/assets"
    if type_filter is not None:
        url = url + "?type=" + type_filter
    data, err = _marketplace_http_get_json(url)
    if err is not None:
        return None, err
    if not isinstance(data, dict):
        return None, {"code": -32603, "message": "polyhaven: unexpected payload (not a JSON object)"}
    inv_type = {0: "hdri", 1: "texture", 2: "model"}
    tokens = [t.lower() for t in (query or "").split() if t]
    candidates: list[dict] = []
    for slug, meta in data.items():
        if not isinstance(meta, dict):
            continue
        t = inv_type.get(meta.get("type"), "unknown")
        entry = {
            "slug": slug,
            "name": meta.get("name") or slug,
            "source": "polyhaven",
            "asset_type": t,
            "thumbnail_url": meta.get("thumbnail_url") or "",
            "tags": meta.get("tags") or [],
            "categories": meta.get("categories") or [],
            "description": meta.get("description") or "",
            "max_resolution": meta.get("max_resolution") or None,
            "download_count": meta.get("download_count") or 0,
        }
        if tokens:
            haystack = " ".join([
                slug,
                str(entry["name"]),
                " ".join(entry["tags"]),
                " ".join(entry["categories"]),
            ]).lower()
            if not all(tok in haystack for tok in tokens):
                continue
        candidates.append(entry)
    candidates.sort(key=lambda e: e["download_count"], reverse=True)
    return candidates[:limit], None


def _ambientcg_search(query: str, asset_type: str, limit: int) -> tuple[list[dict] | None, dict | None]:
    # AmbientCG's /full_json endpoint accepts ?q=keyword and ?type=DataType.
    # DataType values used: "Material" (PBR texture set), "HDRI", "3DModel".
    type_map = {"texture": "Material", "hdri": "HDRI", "model": "3DModel"}
    import urllib.parse
    qparts = [f"limit={min(50, max(1, limit))}", "sort=Popular"]
    if asset_type != "all":
        dt = type_map.get(asset_type)
        if dt:
            qparts.append(f"type={dt}")
    if query:
        qparts.append(f"q={urllib.parse.quote(query)}")
    url = "https://ambientcg.com/api/v2/full_json?" + "&".join(qparts)
    data, err = _marketplace_http_get_json(url)
    if err is not None:
        return None, err
    if not isinstance(data, dict):
        return None, {"code": -32603, "message": "ambientcg: unexpected payload"}
    found = data.get("foundAssets") or []
    inv_type = {"Material": "texture", "HDRI": "hdri", "3DModel": "model"}
    results: list[dict] = []
    for asset in found[:limit]:
        if not isinstance(asset, dict):
            continue
        results.append({
            "slug": asset.get("assetId") or "",
            "name": asset.get("displayName") or asset.get("assetId") or "",
            "source": "ambientcg",
            "asset_type": inv_type.get(asset.get("dataType") or "", "unknown"),
            "thumbnail_url": (asset.get("previewImage") or {}).get("PreviewSphere") or "",
            "tags": asset.get("tags") or [],
            "categories": [asset.get("category") or ""] if asset.get("category") else [],
            "description": asset.get("description") or "",
        })
    return results, None


def _ambientcg_resolve_zip_url(slug: str, asset_type: str, resolution: str, fmt: str) -> tuple[str | None, str | None, list[str], dict | None]:
    """Hit AmbientCG's `/api/v2/full_json?id=<slug>&include=downloadData`
    and pick the per-resolution / per-format zip URL.

    Returns (zip_url, chosen_attribute, available_attributes, error).
    `attribute` is AmbientCG's `<Res>K-<FMT>` token (e.g. `2K-JPG`).
    The caller asks via the same (resolution, fmt) shape used by the
    Polyhaven path; this function maps `2k` -> `2K` and `jpg` -> `JPG`
    and matches against the response's `attribute` strings.
    """
    import urllib.parse as _urlparse
    url = f"https://ambientcg.com/api/v2/full_json?id={_urlparse.quote(slug, safe='')}&include=downloadData"
    data, err = _marketplace_http_get_json(url)
    if err is not None:
        return None, None, [], err
    if not isinstance(data, dict):
        return None, None, [], {"code": -32603, "message": "ambientcg: unexpected payload (not a JSON object)"}
    found = data.get("foundAssets") or []
    if not found or not isinstance(found, list) or not isinstance(found[0], dict):
        return None, None, [], {"code": -32603, "message": f"ambientcg: asset_not_found: id={slug}"}
    asset = found[0]
    folders = asset.get("downloadFolders") or {}
    default = folders.get("default") if isinstance(folders, dict) else None
    if not isinstance(default, dict):
        return None, None, [], {"code": -32603, "message": f"ambientcg: no_download_folder: id={slug}"}
    cats = default.get("downloadFiletypeCategories") or {}
    zip_block = cats.get("zip") if isinstance(cats, dict) else None
    if not isinstance(zip_block, dict):
        return None, None, [], {"code": -32603, "message": f"ambientcg: no_zip_category: id={slug}"}
    downloads = zip_block.get("downloads") or []
    if not isinstance(downloads, list) or not downloads:
        return None, None, [], {"code": -32603, "message": f"ambientcg: no_downloads: id={slug}"}
    # Normalise caller request to AmbientCG attribute syntax.
    req_res = (resolution or "").upper()  # "2k" -> "2K"
    req_fmt = (fmt or "").upper()           # "jpg" -> "JPG"; HDR fmt names match
    want = f"{req_res}-{req_fmt}"
    attrs = [d.get("attribute") for d in downloads if isinstance(d, dict) and d.get("attribute")]
    # Exact match first.
    for d in downloads:
        if not isinstance(d, dict):
            continue
        if d.get("attribute") == want:
            link = d.get("fullDownloadPath") or d.get("downloadLink")
            if isinstance(link, str) and link:
                return link, want, attrs, None
    # Fallback: same resolution, alternate format (JPG <-> PNG for textures,
    # EXR <-> HDR for HDRIs). Preserve the resolution prefix; swap the format.
    swap = {"JPG": "PNG", "PNG": "JPG", "EXR": "HDR", "HDR": "EXR"}.get(req_fmt)
    if swap:
        alt = f"{req_res}-{swap}"
        for d in downloads:
            if not isinstance(d, dict):
                continue
            if d.get("attribute") == alt:
                link = d.get("fullDownloadPath") or d.get("downloadLink")
                if isinstance(link, str) and link:
                    return link, alt, attrs, None
    return None, None, attrs, {"code": -32603, "message": f"ambientcg: resolution_or_format_unavailable: wanted '{want}' not in available {attrs}"}


def _ambientcg_extract_primary_map(zip_path: str, asset_type: str, dest_dir: str) -> tuple[str | None, dict | None]:
    """Extract the AmbientCG zip and return the path of the file the
    marketplace_import handler should hand to `import_texture`.

    For `texture` assets the AmbientCG zip contains a multi-map PBR set
    (`<slug>_<Res>_Color.<ext>`, `_Roughness`, `_NormalGL` etc.); this
    helper imports the Color/Diffuse map only. For `hdri` assets the
    zip contains a single .exr/.hdr file.

    Returns (extracted_file_path, error). The caller is responsible for
    cleanup of `dest_dir` after the downstream `import_texture` call.
    """
    import zipfile
    import os
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            # Skip directories and hidden entries.
            file_names = [n for n in names if not n.endswith("/") and not os.path.basename(n).startswith(".")]
            if not file_names:
                return None, {"code": -32603, "message": f"ambientcg: bad_zip: {zip_path}: archive contains no files"}
            pick: str | None = None
            if asset_type == "texture":
                # Prefer the Color map; AmbientCG's canonical convention is
                # `<slug>_<Res>_Color.<ext>`. Fall back to `_Diffuse`. Both
                # use case-insensitive matching to absorb the rare older
                # asset that ships with a lowercase suffix.
                for marker in ("_Color.", "_color.", "_Diffuse.", "_diffuse."):
                    matches = [n for n in file_names if marker in os.path.basename(n)]
                    if matches:
                        pick = sorted(matches)[0]
                        break
                if pick is None:
                    return None, {"code": -32603, "message": f"ambientcg: zip_has_no_color_map: files={[os.path.basename(n) for n in file_names]}"}
            elif asset_type == "hdri":
                # HDRI zip should ship exactly one .exr or .hdr. Prefer .exr.
                exrs = [n for n in file_names if n.lower().endswith(".exr")]
                hdrs = [n for n in file_names if n.lower().endswith(".hdr")]
                if exrs:
                    pick = sorted(exrs)[0]
                elif hdrs:
                    pick = sorted(hdrs)[0]
                else:
                    return None, {"code": -32603, "message": f"ambientcg: zip_has_no_hdri: files={[os.path.basename(n) for n in file_names]}"}
            else:
                return None, {"code": -32603, "message": f"ambientcg: asset_type_unsupported: '{asset_type}'"}
            # Extract just the picked file. Use a flat path under dest_dir
            # so any directory components inside the zip (including
            # traversal sequences like "../") don't escape it.
            safe_name = os.path.basename(pick)
            dest_path = os.path.join(dest_dir, safe_name)
            with zf.open(pick) as src, open(dest_path, "wb") as out:
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            return dest_path, None
    except zipfile.BadZipFile as e:
        return None, {"code": -32603, "message": f"ambientcg: bad_zip: {zip_path}: {e}"}
    except Exception as e:
        return None, {"code": -32603, "message": f"ambientcg: extract_failed: {zip_path}: {e}"}


def synthetic_marketplace_search(req_id, args: dict) -> dict:
    """Search free CC0 asset marketplaces (Polyhaven, AmbientCG) for
    textures / HDRIs / models matching a keyword. Returns a normalised
    list of asset descriptors so the caller can pick a slug to import
    via `marketplace_import`."""
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_search: invalid_arguments: arguments must be an object",
        })
    query = args.get("query", "")
    if not isinstance(query, str):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_search: invalid_field: 'query' must be a string when supplied",
        })
    source = args.get("source", "polyhaven")
    if source not in ("polyhaven", "ambientcg", "all"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_search: invalid_field: 'source' must be one of polyhaven|ambientcg|all",
        })
    asset_type = args.get("asset_type", "texture")
    if asset_type not in ("texture", "hdri", "model", "all"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_search: invalid_field: 'asset_type' must be one of texture|hdri|model|all",
        })
    limit = args.get("limit", 10)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 50:
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_search: invalid_field: 'limit' must be an integer between 1 and 50",
        })

    results: list[dict] = []
    errors: list[str] = []
    if source == "all":
        polyhaven_limit = max(1, limit - limit // 2)
        ambientcg_limit = max(0, limit // 2)
    elif source == "polyhaven":
        polyhaven_limit = limit
        ambientcg_limit = 0
    elif source == "ambientcg":
        polyhaven_limit = 0
        ambientcg_limit = limit
    else:
        polyhaven_limit = 0
        ambientcg_limit = 0

    if polyhaven_limit > 0:
        ph_results, ph_err = _polyhaven_search(query, asset_type, polyhaven_limit)
        if ph_err is not None:
            errors.append(f"polyhaven: {ph_err.get('message') or 'unknown'}")
        elif ph_results:
            results.extend(ph_results)
    if ambientcg_limit > 0:
        ag_results, ag_err = _ambientcg_search(query, asset_type, ambientcg_limit)
        if ag_err is not None:
            errors.append(f"ambientcg: {ag_err.get('message') or 'unknown'}")
        elif ag_results:
            results.extend(ag_results)

    # If both sources failed AND we have no results, surface the errors.
    if not results and errors:
        return make_response(req_id, error={
            "code": -32603,
            "message": "marketplace_search: all_sources_failed: " + "; ".join(errors),
        })

    body: dict = {
        "ok": True,
        "query": query,
        "source": source,
        "asset_type": asset_type,
        "limit": limit,
        "count": len(results),
        "results": results[:limit],
    }
    if errors:
        body["partial_errors"] = errors
    return _wrap_tool_result(req_id, body)


def _polyhaven_pick_file(files: dict, asset_type: str, resolution: str, fmt: str) -> tuple[str | None, str | None, list[str], dict | None]:
    """Drill into Polyhaven's /files/{slug} response to pull the URL of
    the diffuse/HDRI file at the requested resolution + format.

    Returns (download_url, chosen_format, available_resolutions, error).
    chosen_format is the format actually picked (may differ from the
    requested fmt when a fallback fires — e.g. caller asked 'png' but
    only 'jpg' exists). On failure download_url is None.
    """
    def _resolution_sort_key(r: str) -> tuple[int, str]:
        # Polyhaven resolutions are e.g. "1k","2k","4k","8k","16k". Sort
        # by leading integer so "10k" beats "2k". Fall back to lexical
        # for anything non-conforming.
        if r.endswith("k") and r[:-1].isdigit():
            return (int(r[:-1]), r)
        return (0, r)

    if asset_type == "hdri":
        # HDRI files live under "hdri": {"4k": {"exr": {...}, "hdr": {...}}}
        hdri = files.get("hdri") or {}
        resolutions = sorted(hdri.keys(), key=_resolution_sort_key)
        block = hdri.get(resolution)
        if not isinstance(block, dict):
            return None, None, resolutions, {"code": -32603, "message": f"resolution_unavailable: '{resolution}' not in available {resolutions}"}
        # Prefer EXR for HDRI; fall back to HDR.
        for f in [fmt, "exr", "hdr"]:
            entry = block.get(f)
            if isinstance(entry, dict) and "url" in entry:
                return entry["url"], f, resolutions, None
        return None, None, resolutions, {"code": -32603, "message": f"format_unavailable: tried {fmt}/exr/hdr in resolution {resolution}"}
    if asset_type == "texture":
        # Texture files: top-level keys are map names ("Diffuse", "Normal", etc.)
        # v1 imports diffuse only.
        diff = files.get("Diffuse") or files.get("diffuse") or files.get("Color")
        if not isinstance(diff, dict):
            return None, None, [], {"code": -32603, "message": "texture_no_diffuse: Polyhaven payload lacks a Diffuse/Color map"}
        resolutions = sorted(diff.keys(), key=_resolution_sort_key)
        block = diff.get(resolution)
        if not isinstance(block, dict):
            return None, None, resolutions, {"code": -32603, "message": f"resolution_unavailable: '{resolution}' not in available {resolutions}"}
        for f in [fmt, "png", "jpg"]:
            entry = block.get(f)
            if isinstance(entry, dict) and "url" in entry:
                return entry["url"], f, resolutions, None
        return None, None, resolutions, {"code": -32603, "message": f"format_unavailable: tried {fmt}/png/jpg in resolution {resolution}"}
    return None, None, [], {"code": -32603, "message": f"asset_type_unsupported: '{asset_type}' (marketplace_import v1 supports texture + hdri only)"}


def synthetic_marketplace_import(req_id, args: dict) -> dict:
    """Download an asset from a CC0 marketplace (Polyhaven for now) and
    import it into the project as a UTexture2D via the native
    `import_texture` handler.

    Composes:
      1. GET https://api.polyhaven.com/files/{slug} to resolve the
         per-resolution / per-format download URL.
      2. urllib download to a temp file under the system tempdir.
      3. call_ue("import_texture", {source_path, dest_path, dest_name,
         replace_existing, automated, save}) -- the existing native
         handler does the UE-side import via the canonical asset import
         pipeline.

    Models (glTF / FBX) are not yet implemented; the native side has no
    mesh-import wrapper today. asset_type=model returns a clear
    not_implemented error so the surface is discoverable.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_arguments: arguments must be an object",
        })
    source = args.get("source", "polyhaven")
    if source not in ("polyhaven", "ambientcg"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_field: 'source' must be one of polyhaven|ambientcg",
        })
    slug = args.get("slug")
    if not isinstance(slug, str) or not slug:
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_field: 'slug' must be a non-empty string",
        })
    asset_type = args.get("asset_type", "texture")
    if asset_type not in ("texture", "hdri", "model"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_field: 'asset_type' must be one of texture|hdri|model",
        })
    if asset_type == "model":
        return make_response(req_id, error={
            "code": -32603,
            "message": "marketplace_import: not_implemented: model import is parked for v2 (native handler has no mesh-import wrapper today)",
        })
    resolution = args.get("resolution", "2k")
    if not isinstance(resolution, str) or not resolution:
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_field: 'resolution' must be a non-empty string (e.g. '1k', '2k', '4k', '8k')",
        })
    fmt = args.get("format", "png" if asset_type == "texture" else "exr")
    if not isinstance(fmt, str) or not fmt:
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_field: 'format' must be a non-empty string",
        })
    dest_path = args.get("dest_path", "/Game/Marketplace")
    if not isinstance(dest_path, str) or not dest_path.startswith("/Game"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "marketplace_import: invalid_field: 'dest_path' must start with /Game",
        })
    dest_name = args.get("dest_name") or slug

    import tempfile
    import os
    def _safe_path_token(s: str, default: str) -> str:
        cleaned = "".join(c for c in (s or "") if c.isalnum() or c in "._-")
        return cleaned or default
    safe_slug = _safe_path_token(slug, "slug")
    safe_resolution = _safe_path_token(resolution, "res")
    tmp_dir = tempfile.gettempdir()

    # 1. Resolve download URL + 2. Download to temp. Branches per source.
    download_url: str
    chosen_fmt: str | None
    available: list
    tmp_path: str  # filesystem path of the file `import_texture` will be handed.
    if source == "polyhaven":
        # Polyhaven: per-asset JSON has a flat map of per-resolution / per-
        # format URLs. URL-encode the slug so a value containing '/', '?',
        # or '#' cannot escape the /files/{slug} path.
        import urllib.parse as _urlparse
        files_url = f"https://api.polyhaven.com/files/{_urlparse.quote(slug, safe='')}"
        files, err = _marketplace_http_get_json(files_url)
        if err is not None:
            return make_response(req_id, error=err)
        if not isinstance(files, dict):
            return make_response(req_id, error={
                "code": -32603,
                "message": f"marketplace_import: unexpected_payload: /files/{slug} did not return a JSON object",
            })
        download_url, chosen_fmt, available, pick_err = _polyhaven_pick_file(files, asset_type, resolution, fmt)
        if pick_err is not None:
            return make_response(req_id, error=pick_err)
        # Suffix derives from the chosen format (may differ from the
        # requested fmt when a fallback fires). Each path-component is
        # allowlist-sanitised to block caller-supplied traversal sequences.
        safe_fmt = _safe_path_token(chosen_fmt or fmt, "bin")
        suffix = "." + safe_fmt
        tmp_path = os.path.join(tmp_dir, f"marketplace_{safe_slug}_{safe_resolution}{suffix}")
        dl_err = _marketplace_http_download(download_url, tmp_path)
        if dl_err is not None:
            return make_response(req_id, error=dl_err)
    else:  # source == "ambientcg" (validated above)
        # AmbientCG: zip-archive per resolution/format. Resolve the zip
        # URL, download it, extract the diffuse map (or sole HDRI file),
        # then hand the extracted file to `import_texture`.
        zip_url, chosen_attr, available, pick_err = _ambientcg_resolve_zip_url(slug, asset_type, resolution, fmt)
        if pick_err is not None:
            return make_response(req_id, error=pick_err)
        download_url = zip_url  # type: ignore[assignment]
        chosen_fmt = (chosen_attr or "").split("-")[-1].lower() if chosen_attr else None
        zip_tmp = os.path.join(tmp_dir, f"marketplace_{safe_slug}_{safe_resolution}.zip")
        dl_err = _marketplace_http_download(zip_url, zip_tmp)
        if dl_err is not None:
            return make_response(req_id, error=dl_err)
        # Extract under a per-asset subdir so concurrent imports of
        # different assets don't collide. Mirror the same path-token
        # sanitisation used for the zip filename itself.
        extract_dir = os.path.join(tmp_dir, f"marketplace_{safe_slug}_{safe_resolution}_extract")
        try:
            os.makedirs(extract_dir, exist_ok=True)
        except OSError as e:
            return make_response(req_id, error={
                "code": -32603,
                "message": f"marketplace_import: ambientcg_mkdir_failed: {extract_dir}: {e}",
            })
        extracted_path, ex_err = _ambientcg_extract_primary_map(zip_tmp, asset_type, extract_dir)
        if ex_err is not None:
            return make_response(req_id, error=ex_err)
        tmp_path = extracted_path  # type: ignore[assignment]
        # Best-effort cleanup of the archive (extracted file lives separately).
        # OSError swallowed: stale zip is a leak, not a correctness bug.
        try:
            os.remove(zip_tmp)
        except OSError:
            pass

    # 3. Hand off to native import_texture.
    replace_existing = args.get("replace_existing", False)
    if not isinstance(replace_existing, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": f"marketplace_import: invalid_field: 'replace_existing' must be a boolean, got {type(replace_existing).__name__}",
        })
    import_params = {
        "source_path": tmp_path,
        "dest_path": dest_path,
        "dest_name": dest_name,
        "replace_existing": replace_existing,
        "automated": True,
        "save": True,
    }
    import_resp = call_ue("import_texture", import_params)
    if "error" in import_resp:
        upstream = import_resp.get("error") or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"marketplace_import: ue_import_failed: {upstream.get('message') or 'import_texture returned an error'}",
        })
    import_result = import_resp.get("result") or {}

    body: dict = {
        "ok": True,
        "source": source,
        "slug": slug,
        "asset_type": asset_type,
        "resolution": resolution,
        "format": chosen_fmt or fmt,
        "downloaded_from": download_url,
        "temp_path": tmp_path,
        "ue_asset_path": import_result.get("asset_path") or f"{dest_path}/{dest_name}",
        "available_resolutions": available,
        "import_result": import_result,
        "license": "CC0",
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
    "find_unused_assets": synthetic_find_unused_assets,
    "get_reference_chain": synthetic_get_reference_chain,
    "bulk_compile_blueprints": synthetic_bulk_compile_blueprints,
    "audit_blueprint_compile_status": synthetic_audit_blueprint_compile_status,
    "find_actors_by_class": synthetic_find_actors_by_class,
    "bulk_focus_actors": synthetic_bulk_focus_actors,
    "bulk_screenshot_actors": synthetic_bulk_screenshot_actors,
    "bulk_set_actor_property": synthetic_bulk_set_actor_property,
    "compare_assets": synthetic_compare_assets,
    "bulk_set_console_variables": synthetic_bulk_set_console_variables,
    "inspect_dependency_graph": synthetic_inspect_dependency_graph,
    "bulk_fix_redirectors": synthetic_bulk_fix_redirectors,
    "marketplace_search": synthetic_marketplace_search,
    "marketplace_import": synthetic_marketplace_import,
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
