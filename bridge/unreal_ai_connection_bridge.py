#!/usr/bin/env python
"""
Unreal AI Connection — MCP client bridge.

Any MCP client speaks the MCP protocol
(initialize / tools/list / tools/call) over stdio. The Unreal AI Connection
plugin speaks raw JSON-RPC over a local TCP socket (default
127.0.0.1:18888). This script translates between the two:

  MCP client (stdin, MCP)  ->  this bridge  ->  TCP 127.0.0.1:18888 (raw JSON-RPC)
  MCP client (stdout, MCP) <-  this bridge  <-  TCP 127.0.0.1:18888

Behaviour:
  - "initialize"             returned synthetically (does NOT hit the UE server)
  - "notifications/*"        consumed silently
  - "tools/list"             returns a static list of all 151 tools (112
                             dispatched to the UE plugin's C++ handlers
                             plus 37 bridge-side synthetic tools served by
                             SYNTHETIC_TOOLS without crossing the wire as
                             a single UE round-trip). With
                             UCMCP_TOOL_MODE=progressive it instead returns a
                             small CORE set + a `search_tools` discovery tool
                             (progressive tool disclosure); every tool stays
                             callable via tools/call in both modes.
  - "tools/call"             unpacks {name, arguments} and forwards to the
                             UE server as the matching method
  - All other methods        proxied as-is

The bridge tolerates the UE server being down: it returns a JSON-RPC error
rather than crashing, so the MCP client can show "MCP server not running -
launch UE editor with the Unreal AI Connection plugin enabled".

Override host/port via env: UCMCP_HOST, UCMCP_PORT.
Tool-advertising mode via env: UCMCP_TOOL_MODE (unset/"all" = expose every
tool, the default; "progressive" = core set + search_tools discovery).
"""

import json
import math
import os
import socket
import sys
import tempfile
import time
import uuid

UE_HOST = os.environ.get("UCMCP_HOST", "127.0.0.1")
UE_PORT = int(os.environ.get("UCMCP_PORT", "18888"))

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "unreal-ai-connection"
SERVER_VERSION = "0.9.1"

# Mirror of UnrealAIConnection/Resources/mcp_manifest.json - kept in sync manually.
# 96 tool entries total. 71 are dispatched straight to UE C++ handlers
# (see UnrealAIConnectionModule.cpp's Reg.Register(...) block). The remaining
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
        "description": "Run arbitrary unreal.* Python in the editor's embedded interpreter (universal escape hatch). Multi-line scripts allowed. The result includes a 'stdout' field with anything your code print()ed (and any traceback). Note: unreal.log()/log_warning() write to UE's LogPython category, not Python stdout, so those still surface via get_log_lines.",
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
        "name": "undo_transaction",
        "description": "Step the editor undo stack backward — the programmatic Ctrl+Z. Each mutating tool call (spawn_actor, delete_actor, set_actor_transform, set_actor_property, add_component) is wrapped by the dispatcher as one editor transaction, so this reverts the last such MCP edit (or the last N via count). Returns undone (how many steps actually reverted), descriptions (their titles), and can_undo / can_redo. 'Nothing to undo' is ok=true, undone=0 — not an error.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of undo steps to apply (default 1, max 50). Stops early if the stack is exhausted.", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "redo_transaction",
        "description": "Step the editor undo stack forward — the programmatic Ctrl+Y. Re-applies transactions previously reverted by undo_transaction (or Ctrl+Z), in order, up to count steps. Returns redone (how many steps re-applied), descriptions (their titles), and can_undo / can_redo. 'Nothing to redo' is ok=true, redone=0 — not an error.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of redo steps to apply (default 1, max 50). Stops early when nothing remains to redo.", "minimum": 1, "maximum": 50},
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
                "action": {"type": "string", "enum": ["start", "stop", "query"], "description": "One of: start, stop, query."},
                "mode": {"type": "string", "enum": ["play", "simulate"], "default": "play", "description": "Only used when action=start. 'play' (default) launches a full PIE session in the active viewport; 'simulate' ticks the world without spawning a Player Controller."},
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
        "description": "Inspect multiple assets in one MCP call by composing the inspect_asset C++ handler bridge-side. By default each result is a SUMMARY (path, class, dependency_count, referencer_count); pass verbose=true for the full per-asset inspect_asset blob under `data`. Aggregate counts always present; partial failures isolated per result. Mirrors the bulk_*_assets family shape. Use for pipeline audits (e.g. enumerate 500 textures and report which lack a power-of-two source).",
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
                "verbose": {
                    "type": "boolean",
                    "description": "Default false. When false, each successful result is a trimmed summary (path, class, dependency_count, referencer_count). When true, each result carries the full inspect_asset blob under `data` (backward-compatible)."
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
                    "type": "integer", "minimum": 1, "maximum": 500, "default": 100,
                    "description": "Max unused assets to return (default 100, max 500 — the underlying find_assets scan is capped at 500 candidates engine-side). Scan halts once this many unused are found OR the scan exhausts."
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
                    "type": "integer", "minimum": 1, "maximum": 8, "default": 3,
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
                    "type": "integer", "minimum": 0, "maximum": 10000, "default": 500,
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
                    "type": "integer", "minimum": 0, "maximum": 10000, "default": 500,
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
        "description": "Walk the asset dependency graph BFS from a root (dependencies, downward by default). Composes inspect_asset recursively; optionally also follows referencers (upward) for a bidirectional sweep. Distinct from get_reference_chain in that it defaults to direction=down (dependencies, packaging-audit framing) and supports a single bidirectional pass instead of forcing two separate calls. De-duplicates visited nodes across both directions. Bounded by max_nodes (default 100) so a large graph returns a truncated subgraph rather than thousands of nodes; raise it for an exhaustive sweep.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Root asset path to walk from."
                },
                "depth": {
                    "type": "integer", "minimum": 1, "maximum": 8, "default": 2,
                    "description": "BFS depth bound. Default 2, range 1..8 (the bidirectional sweep can produce a vast subgraph past depth 4 in any non-trivial project)."
                },
                "include_referencers": {
                    "type": "boolean",
                    "description": "Default false. When true, also follow referencers upward in the same BFS; edges record direction ('up' for referencer edges, 'down' for dependency edges)."
                },
                "max_nodes": {
                    "type": "integer", "minimum": 1, "maximum": 100000, "default": 100,
                    "description": "Cap on distinct nodes visited. Default 100, range 1..100000. Once hit, frontier expansion halts and `truncated`=true. The root counts as node 1. Raise for an exhaustive sweep."
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
        "description": "Project name/id/version, company, engine version, asset_count, and plugin counts (plugin_count + enabled_plugin_count). By default the per-plugin list is OMITTED (a full editor has 200+ plugins); pass verbose=true to include the full plugins[] array (name/version/category/enabled_by_default).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "verbose": {
                    "type": "boolean",
                    "description": "Default false. When true, include the full per-plugin plugins[] array; when false, return plugin counts only."
                },
            },
        },
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
            "properties": {"path": {"type": "string", "description": "Widget Blueprint asset path (UWidgetBlueprint or UEditorUtilityWidgetBlueprint)."}},
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
                "op": {"type": "string", "enum": ["set_root", "add_child", "set_property"], "description": "Mutation to perform: 'set_root' sets the tree root, 'add_child' nests a widget under a parent panel, 'set_property' assigns a UProperty on an existing widget."},
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
        "description": "Capture the active editor viewport as a PNG written to DISK (project-confined), returning the file path + dimensions. Forces a fresh frame first, so the capture is correct even when the editor is backgrounded/Slate-throttled. Optionally returns a small base64 thumbnail for quick inline look checks. (v0.10: no longer returns the full image base64-inline -- that produced multi-MB tool results.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_path": {"type": "string", "description": "Optional output .png path; relative paths resolve against the project dir and the result MUST stay under the project dir. Default: Saved/AIConnection/Screenshots/viewport_<utc>.png."},
                "include_thumb": {"type": "boolean", "description": "When true, also return thumb_base64 -- a small PNG thumbnail (default false)."},
                "thumb_max_dim": {"type": "integer", "minimum": 64, "maximum": 1024, "description": "Max thumbnail dimension in pixels (64..1024, default 320). Only used with include_thumb."},
            },
        },
    },
    {
        "name": "render_camera_to_png",
        "description": "Force a synchronous render of the level-editor viewport (or an off-screen SceneCapture2D at arbitrary resolution) and write it to an absolute path as a PNG. Works headless/backgrounded where deferred screenshots fail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_path": {"type": "string", "description": "Absolute filesystem path for the .png to write."},
                "width": {"type": "integer", "description": "Optional output width in pixels. When width and height are both > 0 (or camera_label is set), an off-screen SceneCapture2D is used instead of the live viewport."},
                "height": {"type": "integer", "description": "Optional output height in pixels (see width)."},
                "camera_label": {"type": "string", "description": "Optional level-actor label; render from that actor's world transform instead of the current viewport camera."},
                "fov": {"type": "number", "description": "Optional horizontal field of view in degrees; overrides the viewport / capture FOV for this render only."},
            },
            "required": ["out_path"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Capture the active level-editor viewport as a PNG and write it to a path that MUST resolve UNDER the UE project directory (paths escaping the project — e.g. via '..' — are rejected). Unlike render_camera_to_png (writes anywhere via an absolute path), this is the project-confined, size-capped variant for the see-the-result loop. When width and height are both supplied they cap the output via an off-screen SceneCapture2D matching the viewport camera (each clamped to a hard 7680 px ceiling); otherwise the live viewport is captured at its current size. Synchronous redraw, so it works headless/backgrounded where deferred screenshots return blank.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "out_path": {"type": "string", "description": "Destination .png path. Relative paths resolve against the project dir; absolute paths must already be under it. A '.png' extension is appended if missing. Parent dirs are created as needed."},
                "width": {"type": "integer", "minimum": 1, "description": "Optional output width in pixels (capped at 7680; clamped, not rejected). Triggers an off-screen capture only when BOTH width and height are > 0; otherwise ignored and the live viewport size is used."},
                "height": {"type": "integer", "minimum": 1, "description": "Optional output height in pixels (capped at 7680; clamped, not rejected). See width."},
                "fov": {"type": "number", "exclusiveMinimum": 0, "description": "Optional horizontal field of view in degrees; overrides the viewport / capture FOV for this capture only."},
            },
            "required": ["out_path"],
        },
    },
    {
        "name": "focus_viewport",
        "description": "Aim the active level-editor viewport. Supply EXACTLY ONE of: 'actor' (frame that named actor — label or unique name — like focus_actor) OR 'location' (snap the camera to an explicit world location, with optional 'rotation' and 'fov'). Supplying both, or neither, is an error. Use this to frame a freshly spawned or edited actor before take_screenshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string", "description": "Actor label or unique object name to select and frame. Mutually exclusive with 'location'."},
                "location": {"type": "object", "description": "World-space camera location {x, y, z}. Mutually exclusive with 'actor'.", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}},
                "rotation": {"type": "object", "description": "Optional camera orientation {pitch, yaw, roll} in degrees, used only with 'location' (defaults to identity-forward when omitted).", "properties": {"pitch": {"type": "number"}, "yaw": {"type": "number"}, "roll": {"type": "number"}}},
                "fov": {"type": "number", "exclusiveMinimum": 0, "description": "Optional horizontal field of view in degrees, used only with 'location'; overrides the viewport FOV."},
            },
        },
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
            "properties": {"name": {"type": "string", "description": "Actor label or unique object name to select and frame in the viewport."}},
            "required": ["name"],
        },
    },
    {
        "name": "load_level_by_path",
        "description": "Load a UE level by package path, e.g. /Game/Maps/MyMap.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Level package path to load, e.g. /Game/Maps/MyMap."}},
            "required": ["path"],
        },
    },
    {
        "name": "take_high_res_screenshot",
        "description": "Trigger UE's HighResShot. Output -> Saved/Screenshots/<Platform>Editor/ (Windows/Mac/Linux). Optional multiplier (1..8). v0.10: forces a viewport redraw after dispatch (throttle-proof when backgrounded) and scans once for the new file -- response includes found + path when the PNG already landed; the write can be async, so poll the dir when found=false.",
        "inputSchema": {
            "type": "object",
            "properties": {"multiplier": {"type": "number", "default": 1, "description": "Resolution multiplier applied to the viewport size (1..8). Default 1."}},
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
                "limit": {"type": "integer", "default": 100, "description": "Cap result count. Default 100, max 500."},
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
        "name": "duplicate_actor",
        "description": "Clone an existing level actor (label or FName), optionally offset and relabel. Returns the new actor's FName + label. Wrapped in an editor transaction (single Ctrl+Z). Ambiguous label returns ambiguous_actor with candidate FNames.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Source actor label OR FName."},
                "offset": {"type": "object", "description": "Optional world-space offset {x,y,z} for the clone (default same location)."},
                "label": {"type": "string", "description": "Optional World Outliner label for the clone (default UE auto-name)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "set_actor_folder",
        "description": "Set an actor's World Outliner folder path (organization), e.g. 'Lighting/Key'. Pass an empty string to move it to the outliner root. Label or FName. Undoable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label OR FName."},
                "folder": {"type": "string", "description": "Slash-delimited outliner folder path (e.g. 'Lighting/Key'); \"\" = root. Required."},
            },
            "required": ["name", "folder"],
        },
    },
    {
        "name": "rename_actor",
        "description": "Change an actor's World Outliner display label (SetActorLabel). The stable FName is unchanged. Returns old_label + new_label. Label or FName to target. Undoable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Actor label OR FName to target."},
                "label": {"type": "string", "description": "New display label. Required."},
            },
            "required": ["name", "label"],
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
                "count": {"type": "integer", "default": 100, "description": "Max lines to return (default 100, max 1000)."},
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
        "name": "set_sequence_playback_range",
        "description": "Set a Level Sequence MovieScene's playback range start/end. Inputs are display-rate frames; the stored range (returned) is in tick units, matching inspect_sequence. Write counterpart to inspect_sequence's playback_range read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path (object path or package path)."},
                "start_frame": {"type": "integer", "description": "Range start in display-rate frames (default 0)."},
                "end_frame": {"type": "integer", "description": "Range end in display-rate frames; must be greater than start_frame."},
            },
            "required": ["sequence_path", "end_frame"],
        },
    },
    {
        "name": "add_cine_camera_to_sequence",
        "description": "Spawn an ACineCameraActor into the editor world, add it as a possessable binding on a Level Sequence, and return the binding GUID (feed it into add_camera_cut_track). The spawned camera is a persistent level actor (not sequence-owned). Spawnable mode is not offered (needs an active Sequencer UI).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path."},
                "label": {"type": "string", "description": "Actor World Outliner label + binding name (default 'CineCameraActor')."},
                "location": {"type": "object", "description": "World location {x,y,z} (default 0,0,0)."},
                "rotation": {"type": "object", "description": "World rotation {pitch,yaw,roll} (default 0,0,0)."},
            },
            "required": ["sequence_path"],
        },
    },
    {
        "name": "add_camera_cut_track",
        "description": "Add (or reuse) the single camera cut track on a Level Sequence and append a section that points the viewer at an existing camera binding (by GUID) over a [start_frame, end_frame] range. Frames are display-rate; the returned range is in ticks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path."},
                "camera_binding_guid": {"type": "string", "description": "GUID of an existing possessable/spawnable in the sequence (e.g. from add_cine_camera_to_sequence or bind_actor_to_sequence)."},
                "start_frame": {"type": "integer", "description": "Cut start in display-rate frames (default 0)."},
                "end_frame": {"type": "integer", "description": "Cut end in display-rate frames (default = playback range end); must be greater than start_frame."},
            },
            "required": ["sequence_path", "camera_binding_guid"],
        },
    },
    {
        "name": "add_audio_track",
        "description": "Add a root-level (master) audio track to a Level Sequence and append a sound section playing a USoundBase (SoundWave / SoundCue / MetaSound source) starting at a given frame.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path."},
                "sound_path": {"type": "string", "description": "Path to a USoundBase asset (SoundWave / SoundCue / MetaSound source)."},
                "start_frame": {"type": "integer", "description": "Section start in display-rate frames (default 0)."},
                "row_index": {"type": "integer", "description": "Track row to place the section on (default -1 = next free row)."},
                "volume": {"type": "number", "description": "Constant volume; sets the section's sound-volume channel default."},
                "looping": {"type": "boolean", "description": "Whether the sound section loops."},
            },
            "required": ["sequence_path", "sound_path"],
        },
    },
    {
        "name": "add_visibility_track",
        "description": "Add a visibility track to an existing binding (possessable/spawnable) on a Level Sequence and key the actor's visibility on/off at given frames. 'visible' is keyed in user terms (the inverted hidden-flag handling is done internally). Frames are display-rate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path."},
                "binding_guid": {"type": "string", "description": "GUID of an existing binding to attach the visibility track to."},
                "keys": {"type": "array", "description": "Array of {frame: int (display-rate), visible: bool}; at least one entry.", "items": {"type": "object"}},
                "visible_at_start": {"type": "boolean", "description": "Convenience: a single visibility key at start_frame instead of 'keys'."},
                "start_frame": {"type": "integer", "description": "Frame for the visible_at_start convenience key (default 0)."},
            },
            "required": ["sequence_path", "binding_guid"],
        },
    },
    {
        "name": "render_sequence_mrq",
        "description": "Render a Level Sequence to an image sequence (PNG/JPG/BMP/EXR) on disk via the Movie Render Queue. ASYNC: builds an MRQ queue+job, kicks off a PIE-based (or out-of-process) render on the game thread, and returns a task_id immediately. Poll with poll_task; the completed result carries {success, output_dir, files_written, frame_count}. An optional map_path also covers the render-a-level case (defaults to the current editor world). Requires the MovieRenderPipeline engine plugin. cancel_task is not wired for this task type in v1.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "Level Sequence asset path (object or package path)."},
                "output_dir": {"type": "string", "description": "Absolute filesystem directory for the output image sequence (written verbatim into the MRQ OutputDirectory)."},
                "map_path": {"type": "string", "description": "UWorld asset path to render the sequence against. Defaults to the currently-loaded editor world."},
                "format": {"type": "string", "enum": ["png", "jpg", "bmp", "exr"], "description": "Output image container (default 'png')."},
                "file_name_format": {"type": "string", "description": "MRQ file-name format (default '{sequence_name}.{frame_number}')."},
                "resolution": {"type": "object", "description": "Output resolution {width:int, height:int} (default 1920x1080)."},
                "output_frame_rate": {"type": "number", "description": "Output frame rate; if >0 enables a custom frame rate (else uses the sequence's display rate)."},
                "use_custom_playback_range": {"type": "object", "description": "Optional {start_frame:int, end_frame:int} (display-rate frames) to override the rendered range; supply both keys together."},
                "overwrite_existing": {"type": "boolean", "description": "Overwrite existing output files (default true)."},
                "render_pass": {"type": "string", "enum": ["lit", "unlit", "detail_lighting", "lighting_only", "reflections_only", "path_tracer"], "description": "Deferred render pass (default 'lit')."},
                "use_new_process": {"type": "boolean", "description": "false (default) = in-editor PIE executor; true = out-of-process executor (no per-file enumeration)."},
                "render_offscreen": {"type": "boolean", "description": "PIE executor only: render without a progress window (default true)."},
            },
            "required": ["sequence_path", "output_dir"],
        },
    },
    {
        "name": "add_blueprint_node",
        "description": "Add one K2 graph node (call_function | variable_get | variable_set | branch) into a UBlueprint's event or function graph at an optional X/Y position. Returns the new node's GUID + name + a list of its pins (name/direction/category) so connect_blueprint_pins and set_blueprint_node_pin_default can address it without guessing pin names.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "blueprint": {"type": "string", "description": "/Game path to the UBlueprint asset."},
                "graph": {"type": "string", "description": "Target graph name; resolved across UbergraphPages then FunctionGraphs. The event graph is usually 'EventGraph'."},
                "node_type": {"type": "string", "enum": ["call_function", "variable_get", "variable_set", "branch"], "description": "Kind of K2 node to create."},
                "function_name": {"type": "string", "description": "UFunction name (required when node_type='call_function')."},
                "function_class": {"type": "string", "description": "Optional /Script or /Game class path that owns the function; defaults to the Blueprint's own generated class (self-context call)."},
                "var_name": {"type": "string", "description": "Variable name (required when node_type='variable_get' or 'variable_set'); resolved as a self-member."},
                "node_pos_x": {"type": "number", "description": "X position of the node in the graph (default 0)."},
                "node_pos_y": {"type": "number", "description": "Y position of the node in the graph (default 0)."},
            },
            "required": ["blueprint", "graph", "node_type"],
        },
    },
    {
        "name": "connect_blueprint_pins",
        "description": "Wire two pins (exec or data) between two existing K2 nodes in the same graph of a UBlueprint, using the schema-validated path (UEdGraphSchema_K2::TryCreateConnection) so type-incompatible or illegal links are rejected with the schema's reason rather than silently corrupting the graph. Nodes are addressed by the NodeGuid returned from add_blueprint_node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "blueprint": {"type": "string", "description": "/Game path to the UBlueprint asset."},
                "graph": {"type": "string", "description": "Graph name (same resolution as add_blueprint_node)."},
                "from_node": {"type": "string", "description": "Source node GUID (as returned by add_blueprint_node)."},
                "from_pin": {"type": "string", "description": "Output pin name on from_node."},
                "to_node": {"type": "string", "description": "Target node GUID."},
                "to_pin": {"type": "string", "description": "Input pin name on to_node."},
            },
            "required": ["blueprint", "graph", "from_node", "from_pin", "to_node", "to_pin"],
        },
    },
    {
        "name": "set_blueprint_node_pin_default",
        "description": "Set a literal default value on an unconnected input pin of an existing K2 node, via the schema's validated setter so the value is parsed/validated for the pin's type. Pass 'value' (a literal string, e.g. '42', 'true', '1.5') for most pins, or 'object_value' (an object/asset path) for object-reference pins. A connected pin is rejected (its default is ignored by the compiler).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "blueprint": {"type": "string", "description": "/Game path to the UBlueprint asset."},
                "graph": {"type": "string", "description": "Graph name."},
                "node": {"type": "string", "description": "Node GUID (as returned by add_blueprint_node)."},
                "pin": {"type": "string", "description": "Input pin name."},
                "value": {"type": "string", "description": "Literal default (numbers/bools passed as their string form). Use this for non-object pins."},
                "object_value": {"type": "string", "description": "Optional /Game or /Script object path; resolved via LoadObject and applied to PC_Object reference pins instead of 'value'."},
            },
            "required": ["blueprint", "graph", "node", "pin"],
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
                "value": {"type": ["number", "object", "string"], "description": "Value shape varies by type: scalar -> number, vector -> {r,g,b,a}, texture -> string asset path."},
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
        "name": "create_level",
        "description": "Create a new empty level (UWorld) asset under /Game/ and open it as the active level.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination level asset path; must start with /Game/ (no '..' or backslash)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "build_lighting",
        "description": "Invoke a static-lighting build on the active editor world. Non-interactive; may take time for large levels.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "create_data_table",
        "description": "Create a new UDataTable asset whose rows conform to a given row UScriptStruct.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination folder under /Game/."},
                "name": {"type": "string", "description": "Leaf asset name."},
                "row_struct": {"type": "string", "description": "Full path to a UScriptStruct, e.g. /Script/Engine.Vector or a /Game user struct."},
            },
            "required": ["path", "name", "row_struct"],
        },
    },
    {
        "name": "create_data_asset",
        "description": "Create a new UDataAsset (or subclass) asset from a UDataAsset subclass path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination folder under /Game/."},
                "name": {"type": "string", "description": "Leaf asset name."},
                "class_path": {"type": "string", "description": "Full path to a UDataAsset subclass, e.g. /Script/Engine.PrimaryDataAsset or a /Game BP-based DataAsset class. Abstract classes are rejected."},
            },
            "required": ["path", "name", "class_path"],
        },
    },
    {
        "name": "create_blueprint",
        "description": "Create a new UBlueprint asset under /Game/ from a parent class (default /Script/Engine.Actor).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Destination folder under /Game/."},
                "name": {"type": "string", "description": "Leaf asset name."},
                "parent_class": {"type": "string", "description": "Full path to the parent UClass, e.g. /Script/Engine.Pawn or a /Game BP generated class. Defaults to /Script/Engine.Actor. Abstract classes are rejected."},
            },
            "required": ["path", "name"],
        },
    },
    {
        "name": "add_blueprint_variable",
        "description": "Add a typed member variable to an existing UBlueprint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "blueprint": {"type": "string", "description": "/Game path to the UBlueprint asset."},
                "var_name": {"type": "string", "description": "New variable name."},
                "type": {"type": "string", "enum": ["bool", "int", "float", "string", "name", "vector", "rotator", "transform", "object"], "description": "Variable type."},
                "object_class": {"type": "string", "description": "Full path to a UClass. Required only when type is 'object'."},
                "default_value": {"type": "string", "description": "Optional default value as a string."},
            },
            "required": ["blueprint", "var_name", "type"],
        },
    },
    {
        "name": "add_blueprint_function",
        "description": "Add a new empty function graph to an existing UBlueprint.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "blueprint": {"type": "string", "description": "/Game path to the UBlueprint asset."},
                "function_name": {"type": "string", "description": "New function name. Must not collide with an existing function graph."},
            },
            "required": ["blueprint", "function_name"],
        },
    },
    {
        "name": "add_material_expression",
        "description": "Create a UMaterialExpression node inside an existing UMaterial's graph, then recompile the material.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "material": {"type": "string", "description": "/Game path to the UMaterial asset."},
                "expression_class": {"type": "string", "description": "Full path to a UMaterialExpression subclass, e.g. /Script/Engine.MaterialExpressionConstant3Vector or /Script/Engine.MaterialExpressionTextureSample. Abstract classes are rejected."},
                "node_pos_x": {"type": "integer", "description": "Optional X position of the node in the graph. Default 0."},
                "node_pos_y": {"type": "integer", "description": "Optional Y position of the node in the graph. Default 0."},
            },
            "required": ["material", "expression_class"],
        },
    },
    {
        "name": "connect_material_expression",
        "description": "Wire a material expression's output to a material property input (e.g. property:BaseColor) or another expression's input (node:<ExprName>:<InputName>), then recompile.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "material": {"type": "string", "description": "/Game path to the UMaterial asset."},
                "from_expression": {"type": "string", "description": "Node name returned by add_material_expression (the source expression)."},
                "from_output": {"type": "string", "description": "Optional name of the source expression's output. Empty = the expression's default/first output."},
                "to": {"type": "string", "description": "Connection target. Either 'property:<Name>' (BaseColor, Metallic, Specular, Roughness, EmissiveColor, Opacity, OpacityMask, Normal, AmbientOcclusion, WorldPositionOffset) or 'node:<ExprName>:<InputName>' to wire into another expression's input."},
            },
            "required": ["material", "from_expression", "to"],
        },
    },
    {
        "name": "spawn_niagara_at_location",
        "description": "Place an editor-persistent Niagara system actor (ANiagaraActor) in the current editor world and assign a UNiagaraSystem asset to its embedded UNiagaraComponent. Persistent = appears in the World Outliner, saved with the level, on the undo stack. Distinct from the transient PIE-only UNiagaraFunctionLibrary spawn path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "system": {"type": "string", "description": "/Game path to the UNiagaraSystem asset, e.g. /Game/FX/NS_Fire."},
                "location": {"type": "object", "description": "World-space {x, y, z}. Defaults to {0,0,0}."},
                "rotation": {"type": "object", "description": "{pitch, yaw, roll} in degrees. Defaults to {0,0,0}."},
                "scale": {"type": "object", "description": "{x, y, z} scale multiplier. Defaults to {1,1,1}."},
                "label": {"type": "string", "description": "Visible name in World Outliner; defaults to UE auto-naming."},
                "auto_activate": {"type": "boolean", "description": "Activate the component after spawn so the FX previews in-viewport. Default true."},
            },
            "required": ["system"],
        },
    },
    {
        "name": "spawn_niagara_attached",
        "description": "Attach an editor-persistent UNiagaraComponent (running a given UNiagaraSystem) to an existing actor (label or FName), optionally to a named socket on a named parent component, with an optional relative transform. Persistent instance component (saved with the actor, undoable). Distinct from the transient UNiagaraFunctionLibrary attach path.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor_name": {"type": "string", "description": "Host actor label or FName."},
                "system": {"type": "string", "description": "/Game path to the UNiagaraSystem asset."},
                "attach_to": {"type": "string", "description": "Existing component name to attach as child of; defaults to the root component."},
                "socket": {"type": "string", "description": "Socket name on the parent component."},
                "component_name": {"type": "string", "description": "FName for the new UNiagaraComponent; defaults to UE auto-naming."},
                "relative_transform": {"type": "object", "description": "{location, rotation, scale} relative to the parent component."},
                "auto_activate": {"type": "boolean", "description": "Activate the component after attach. Default true."},
            },
            "required": ["actor_name", "system"],
        },
    },
    {
        "name": "set_niagara_user_param",
        "description": "Set a user-exposed (override) parameter on a placed/attached UNiagaraComponent, resolved via its owning actor (label or FName), for the value types float, vec3, linearcolor, bool. Writes the component's override-parameter store so the value persists on the level instance. Pre-checks the param exists on the system and errors with invalid_field if absent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor_name": {"type": "string", "description": "Label or FName of the actor owning the Niagara component."},
                "param_name": {"type": "string", "description": "User parameter name. The bare name (e.g. 'Color') and the 'User.'-prefixed name both resolve."},
                "type": {"type": "string", "enum": ["float", "vec3", "linearcolor", "bool"], "description": "Value type selecting the SetVariable* overload."},
                "value": {"type": ["number", "object", "boolean"], "description": "Shape per type: float -> number; vec3 -> {x,y,z}; linearcolor -> {r,g,b,a} (a defaults to 1.0); bool -> boolean."},
                "component_name": {"type": "string", "description": "FName of the target UNiagaraComponent when the actor has more than one; defaults to the first/only one."},
            },
            "required": ["actor_name", "param_name", "type", "value"],
        },
    },
    {
        "name": "run_python_file",
        "description": "Execute a .py file from disk via the editor's embedded Python. Complement to execute_unreal_python -- avoids escaping pain for non-trivial scripts. SYNCHRONOUS: blocks the dispatch tick until the script finishes; for long ops (FBX imports, builds, bakes) use start_python_file_task instead or the call will hit the 30s RPC timeout. Output capture caveat: ExecuteFile mode does not return stdout/eval-result; use unreal.log marker + get_log_lines to round-trip results.",
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
                "duration_ms": {"type": "integer", "minimum": 1, "description": "How long the task should sleep (1 to 3600000 ms / 1 hour). Required."},
            },
            "required": ["duration_ms"],
        },
    },
    {
        "name": "start_python_task",
        "description": "Execute inline Python code as an ASYNC task: returns a task_id immediately (no 30s RPC timeout risk), the code runs on the game thread on a later tick. Use for long ops (FBX imports, builds, bakes) instead of execute_unreal_python. The editor is busy while the script runs. Poll with poll_task; result fields: ok, output, log_output (capped 64KB). Output caveat: emit results via unreal.log markers -- they land in log_output. cancel_task only works before execution starts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source code to execute."},
            },
            "required": ["code"],
        },
    },
    {
        "name": "start_python_file_task",
        "description": "Execute a .py file as an ASYNC task: returns a task_id immediately (no 30s RPC timeout risk), the script runs on the game thread on a later tick. Use for long ops (FBX imports, builds, bakes) instead of run_python_file. The editor is busy while the script runs. Poll with poll_task; result fields: ok, output, log_output (capped 64KB). Output caveat: emit results via unreal.log markers -- they land in log_output. cancel_task only works before execution starts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative path to a .py file (must exist)."},
            },
            "required": ["path"],
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
        "description": "Request COOPERATIVE cancellation of a running task. Sets the task's atomic flag; how fast it takes effect depends on the task type: sleep tasks poll every ~50ms; python tasks (start_python_task / start_python_file_task) check ONLY before execution starts - a python script already running cannot be interrupted; MRQ renders don't observe the flag at all (v1). UE has no safe forced-thread-termination, so workers that don't poll the flag run to completion regardless. Idempotent: returns ok=true with accepted=false for unknown ids and already-terminal tasks.",
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
                "limit": {"type": "integer", "default": 100, "description": "Optional. Max items to return. Default 100; clamped to [1, 500]."},
            },
        },
    },
    {
        "name": "exec_python_persistent",
        "description": "Tier 2 PR #45: like execute_unreal_python but state PERSISTS across calls. Variables, imports, and function/class definitions defined in one call are visible in the next -- letting Claude build up state across turns without re-loading every time. Implemented via UE's FPythonCommandEx with FileExecutionScope=Public (shared globals dict with the editor's Python console). Pairs with reset_python_state. The result includes a 'stdout' field with anything your code print()ed (and any traceback); persistence across calls is preserved (the capture wrapper runs against the shared globals dict). unreal.log()/log_warning() still go to UE's LogPython category, surfaced via get_log_lines.",
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
        "description": "Read structural properties of a UDataTable: RowStruct asset path + name, row count, per-property name+type for each FProperty on the RowStruct (TFieldIterator with EFieldIterationFlags::None to skip super fields), client-strip flag, missing/extra-field tolerance flags, optional ImportKeyField. The sorted row-name list (rows[]) is OMITTED by default (tables can hold thousands of rows); row_count is always present. Pass verbose=true to include rows[]. C++ handler; no new Build.cs deps (Engine + CoreUObject cover UDataTable / UScriptStruct / FProperty). Null-guards RowStruct (freshly-created DataTables can have no struct assigned).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE asset path of a UDataTable, e.g. /Game/Data/DT_Items."},
                "verbose": {"type": "boolean", "description": "Default false. When true, include the full sorted rows[] array of row names; when false, return row_count only."},
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
        "min_engine_version": "5.0",
        "max_engine_version": None,
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
        "min_engine_version": "5.0",
        "max_engine_version": None,
    },
    {
        "name": "screenshot_actor",
        "description": "Frame the editor viewport on an actor (by label or unique name) and capture a focused PNG screenshot. SYNTHETIC bridge-side handler: composes focus_actor + get_viewport_screenshot. Returns the written PNG's disk path + size plus the focused actor's identity and world location.",
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
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10, "description": "Max results to return (default 10, max 50)."},
            },
        },
    },
    {
        "name": "marketplace_import",
        "description": "Download a CC0 asset from a marketplace (Polyhaven or AmbientCG) and import it into the project as a UTexture2D via the native import_texture handler. SYNTHETIC bridge-side handler. Polyhaven path: /files/{slug} catalog lookup -> direct download. AmbientCG path: /api/v2/full_json?id={slug}&include=downloadData -> downloads the per-resolution zip -> extracts the Color map (textures) or sole EXR/HDR (hdris) -> hands the file to import_texture. Supports texture (Color/Diffuse map) and hdri (EXR/HDR); model import is parked for a later PR (native handler has no mesh-import wrapper today). When multi_map=true is passed (texture only), the handler additionally pulls Normal/Roughness/AO/Displacement/Metalness when the source ships them — each map lands as a separate UTexture2D named `<dest_name>_<map>` (Color stays at `<dest_name>` for back-compat). Asset files: both sources are CC0 (public domain, no attribution required). API access: the Polyhaven public API is licensed for non-commercial and academic use only — commercial integrations require a custom license from Poly Haven (https://polyhaven.com/our-api). AmbientCG's public API and asset files are both CC0.",
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
                "multi_map": {"type": "boolean", "description": "When true and asset_type='texture', pull every canonical PBR map (color, normal, roughness, ao, displacement, metalness) the source ships and import each as a separate UTexture2D. Color is required; other maps are best-effort. Naming: Color -> <dest_name>; others -> <dest_name>_<map>. Default false (diffuse only, back-compat)."},
            },
            "required": ["slug"],
        },
    },
    {
        "name": "convert_hdri_to_cubemap",
        "description": "Convert a longlat-projection HDRI (UTexture2D) into a UTextureCube so it can drive a SkyLight's SpecifiedCubemap slot. SYNTHETIC bridge-side handler. Pipeline: spawn an inside-out sphere with the HDRI as an unlit emissive material, capture it with a SceneCaptureCube into a TextureRenderTargetCube, then materialize the static UTextureCube via RenderingLibrary.render_target_create_static_texture_cube_editor_only. Temp render-target + temp material + temp actors are cleaned up before return. Closes the 23rd HANDOFF note's HDRI cubemap parked item.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hdri_path": {"type": "string", "description": "UE asset path of the source longlat UTexture2D (must start with /Game/), e.g. /Game/Marketplace/HDRI_Venice_Sunset."},
                "dest_path": {"type": "string", "description": "UE package path for the new cube. Defaults to the source HDRI's folder."},
                "dest_name": {"type": "string", "description": "Asset name override for the new UTextureCube. Defaults to <source_basename>_Cube."},
                "cube_size": {"type": "integer", "minimum": 16, "maximum": 8192, "default": 1024, "description": "Square face size in pixels for the render target. Range [16, 8192]; powers of two recommended. Default 1024."},
                "compression": {"type": "string", "enum": ["TC_HDR", "TC_HDR_COMPRESSED", "TC_HDR_F32", "TC_DEFAULT"], "default": "TC_HDR", "description": "Texture compression preset. One of TC_HDR (default), TC_HDR_COMPRESSED, TC_HDR_F32, TC_DEFAULT."},
            },
            "required": ["hdri_path"],
        },
        "min_engine_version": "5.0",
        "max_engine_version": None,
    },
    {
        "name": "sequencer_add_transform_keyframe",
        "description": "Add a single keyframe on a Level Sequence's 3D Transform Track for a previously-bound actor. SYNTHETIC bridge-side handler. Closes the keyframe-authoring half of the 21st HANDOFF note's Sequencer parked item: create_sequence + bind_actor_to_sequence already exist; this tool wires up MovieSceneSequenceExtensions.find_binding_by_id + MovieSceneBindingProxy.add_track + MovieSceneScriptingDoubleChannel.add_key. Caller passes location/rotation/scale as optional 3-element triples; missing triples skip those channels. Rotation order is [pitch, yaw, roll] (unreal.Rotator convention) — mapped internally to the channel layout (Roll=X, Pitch=Y, Yaw=Z). Movie Render Queue remains parked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sequence_path": {"type": "string", "description": "UE asset path of the LevelSequence; must start with /Game/."},
                "binding_id": {"type": "string", "description": "GUID string returned by bind_actor_to_sequence. Accepts the bare 32-hex form (no dashes) UE produces by default; dashed/braced forms also parse."},
                "time_seconds": {"type": "number", "minimum": 0, "description": "Time in seconds (display rate) at which to place the keyframe. Must be >= 0; converted internally to a tick-resolution FrameNumber."},
                "location": {"type": "array", "description": "Optional [x, y, z] translation. Omit to skip Location channels.", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "rotation": {"type": "array", "description": "Optional [pitch, yaw, roll] in degrees (unreal.Rotator convention). Omit to skip Rotation channels.", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "scale": {"type": "array", "description": "Optional [x, y, z] scale. Omit to skip Scale channels.", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "interpolation": {"type": "string", "enum": ["linear", "constant", "auto", "smart_auto", "cubic"], "default": "linear", "description": "Key interpolation. One of 'linear' (default), 'constant', 'auto', 'smart_auto', 'cubic'. 'cubic' is an alias for SMART_AUTO."},
                "auto_extend_section": {"type": "boolean", "description": "If true (default), extends the track section's seconds-range to cover time_seconds when needed."},
            },
            "required": ["sequence_path", "binding_id", "time_seconds"],
        },
        "min_engine_version": "5.0",
        "max_engine_version": None,
    },
    {
        "name": "import_mesh",
        "description": "Import a 3D mesh file (.glb/.gltf/.fbx/.obj) from disk into the project as StaticMesh asset(s). SYNTHETIC bridge-side handler. Fills the gap left by import_texture (which only handles images): drives UE's Interchange import to a target /Game/ path and returns the EXACT created StaticMesh asset paths, so the caller never has to guess Interchange's sub-folder nesting before binding the mesh to an actor. Diffs the destination folder before/after import to report precisely what was created. Materials embedded in the source (e.g. glTF PBR) import by default. Pairs with spawn_actor / set_actor_property for placement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Absolute filesystem path to the mesh file. Extension must be one of .glb, .gltf, .fbx, .obj (case-insensitive)."},
                "dest_path": {"type": "string", "description": "UE content path to import into; must be '/Game' or start with '/Game/'. Interchange may create sub-folders under it; the returned static_meshes paths reflect the actual locations."},
                "import_materials": {"type": "boolean", "description": "Import materials embedded in the source file. Default true."},
            },
            "required": ["source_path", "dest_path"],
        },
        "min_engine_version": "5.0",
        "max_engine_version": None,
    },
    {
        "name": "material_auto_remap",
        "description": "Build a PBR Material from a set of UE texture assets (base color / normal / roughness / metallic / ambient occlusion) and assign it to a level actor's StaticMesh, in one call. SYNTHETIC bridge-side handler. Automates the otherwise-tedious dance of creating a Material, adding a TextureSample per map with the correct sampler type, wiring each to the matching material output, and pushing it onto every material slot of the target actor — the natural finisher for import_mesh / import_texture when dressing an imported asset. Correct sampler types are applied automatically (base color sRGB, normal as normal map, roughness/metallic/AO linear grayscale). Optional UV tiling.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor_label": {"type": "string", "description": "Label of the level actor whose StaticMesh component gets the new material (assigned to all slots)."},
                "textures": {"type": "object", "description": "Map of PBR slot -> UE texture asset path (each must start with /Game/). Recognized keys: base_color (required), normal, roughness, metallic, ambient_occlusion. Unknown keys are ignored."},
                "dest_material": {"type": "string", "description": "UE package path for the created Material, e.g. /Game/Mats/M_MyAsset. Must start with /Game/. Defaults to /Game/AutoMaterials/M_<actor_label>."},
                "tiling": {"type": "number", "exclusiveMinimum": 0, "default": 1.0, "description": "UV tiling applied to all maps (U=V). Default 1.0. Must be > 0."},
            },
            "required": ["actor_label", "textures"],
        },
        "min_engine_version": "5.0",
        "max_engine_version": None,
    },
    {
        "name": "place_actors_raycast",
        "description": "Raycast straight down onto level geometry at a set of XY targets and spawn one actor of a given class at each surface hit. Targets are either an explicit 'points' list or a generated 'grid'. Native C++ handler — traces from (x, y, trace_start_z) down through the world (ECC_Visibility) and spawns at the hit's ImpactPoint + z_offset; with align_to_normal the actor's up-axis is rotated onto the surface normal. The studio-builder use case is 'drop a book into every arched niche / scatter props onto a shelf' without pre-computing surface heights. Tip: a Nanite source mesh traces against its coarse fallback — run nanite_collision_toggle to hit real geometry first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "class_path": {"type": "string", "description": "Actor class to spawn at each hit (e.g. '/Script/Engine.StaticMeshActor' or a Blueprint class path)."},
                "points": {"type": "array", "items": {"type": "object"}, "description": "Explicit XY targets [{x, y}, ...]; each is traced from trace_start_z downward. Ignored when 'grid' is supplied."},
                "grid": {"type": "object", "description": "Generate targets on a grid instead of 'points': {min_x, min_y, max_x, max_y, count_x, count_y}. count_x/count_y must be >= 1; samples are spread evenly across each span (midpoint when count==1)."},
                "trace_start_z": {"type": "number", "description": "Z height the downward trace starts from (and the negative of which it ends at). Default 100000. Must be > 0."},
                "align_to_normal": {"type": "boolean", "description": "When true, rotate each spawned actor's up-axis onto the surface normal. Default false."},
                "z_offset": {"type": "number", "description": "Added to the hit point's Z before spawning (lift the actor off the surface). Default 0."},
                "label_prefix": {"type": "string", "description": "When set, each spawned actor is labelled '<prefix><index>'."},
            },
            "required": ["class_path"],
        },
    },
    {
        "name": "batch_material_assign",
        "description": "Assign one Material (UMaterial or UMaterialInstance) to the mesh-component material slots of many actors in a single call. Native C++ handler — resolves the target actor set via exactly one selector (by_label list, by_folder World-Outliner path prefix, or by_name_regex), then for each actor walks its mesh components and calls SetMaterial. The studio-builder use case is 'retexture every wall in the /Set/Walls folder to the new marble material' in one shot — the bulk extension of the single-actor material_auto_remap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "material": {"type": "string", "description": "Path to the UMaterial / UMaterialInstance to assign (e.g. '/Game/Mats/M_Marble')."},
                "targets": {"type": "object", "description": "Actor selector — set exactly one of: by_label (list of actor labels), by_folder (World-Outliner folder path; matched as a prefix so '/Set/Walls' also catches '/Set/Walls/Sub'), or by_name_regex (regex matched against each actor's label and FName)."},
                "slot": {"type": "integer", "description": "Material slot index to assign. Default -1 = assign every slot on each matched mesh component."},
            },
            "required": ["material", "targets"],
        },
    },
    {
        "name": "light_raycast_placement",
        "description": "Spawn and configure lights along a surface raycast sweep. Native C++ handler — sample points come from either a 'sweep' {start, end, count} (spaced evenly, inclusive) or an explicit 'points' list; each sample is traced downward onto level geometry and a light is placed at the hit's ImpactPoint pushed out along the surface normal by surface_offset, then configured (intensity / color / attenuation radius). light_type selects APointLight / ARectLight / ASpotLight. The studio-builder use case is 'run a row of rect lights along this wall / shelf' without hand-placing each fixture.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "light_type": {"type": "string", "enum": ["point", "rect", "spot"], "description": "Which light actor to spawn: 'point' (APointLight), 'rect' (ARectLight), or 'spot' (ASpotLight)."},
                "sweep": {"type": "object", "description": "Sample line: {start{x,y,z}, end{x,y,z}, count}. count points are spaced evenly from start to end (inclusive); count must be >= 1. Each sample is traced straight down onto geometry."},
                "points": {"type": "array", "items": {"type": "object"}, "description": "Explicit sample points [{x,y,z}, ...] instead of a sweep; each is traced straight down onto geometry."},
                "surface_offset": {"type": "number", "description": "Distance the light is pushed out from the hit surface along its normal. Default 50."},
                "intensity": {"type": "number", "description": "Light intensity. When omitted, the light's class default is left untouched."},
                "light_color": {"type": "object", "description": "Linear RGB color {r, g, b} (0..1). When omitted, the light's default color is left untouched."},
                "attenuation_radius": {"type": "number", "description": "Attenuation radius (applies to point/spot/rect — all are local lights). When omitted, the class default is left untouched."},
                "label_prefix": {"type": "string", "description": "When set, each spawned light is labelled '<prefix><index>'."},
            },
            "required": ["light_type"],
        },
    },
    {
        "name": "batch_capture_cameras",
        "description": "Render every CineCamera in the level to disk in one call. SYNTHETIC bridge-side handler — composes get_actors_in_level (filtered to CineCameraActor) plus render_camera_to_png per camera. Optionally restrict to named cameras and set a per-render resolution. The studio-builder use case is a thumbnail / contact-sheet pass over a set's coverage cameras without one render_camera_to_png call per camera by hand.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_dir": {"type": "string", "description": "Directory the .png files are written to (one per camera)."},
                "camera_names": {"type": "array", "items": {"type": "string"}, "description": "Optional list of camera labels (or names) to restrict the render to. Default = every ACineCameraActor in the level."},
                "resolution": {"type": "object", "description": "Optional per-render pixel size {width, height} (both positive integers). When omitted, the live viewport resolution is used."},
                "file_name_format": {"type": "string", "description": "Output file-name template; '{camera}' is replaced with the sanitized camera label. Default '{camera}.png'."},
            },
            "required": ["output_dir"],
        },
    },
    {
        "name": "batch_spawn_from_csv",
        "description": "Spawn N actors from a CSV file or an inline list of row objects. SYNTHETIC bridge-side handler — parses the table, then dispatches spawn_actor once per row. CSV columns / row keys: class, x, y, z, pitch, yaw, roll, label, properties (a JSON object — a JSON-string cell in CSV, a nested object inline). Rows that omit 'class' fall back to default_class. The studio-builder use case is data-driven set-dressing scatter (read a table, spawn a row each).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "csv_path": {"type": "string", "description": "Filesystem path to a .csv with a header row. Supply this OR 'rows' (exactly one)."},
                "rows": {"type": "array", "items": {"type": "object"}, "description": "Inline list of row objects [{class, x, y, z, pitch, yaw, roll, label, properties}, ...]. Supply this OR 'csv_path' (exactly one)."},
                "default_class": {"type": "string", "description": "Actor class used for any row that omits 'class' (e.g. '/Script/Engine.StaticMeshActor')."},
            },
        },
    },
    {
        "name": "nanite_collision_toggle",
        "description": "Toggle a StaticMesh's Nanite-enabled flag so line traces hit the full source geometry instead of the coarse Nanite fallback. Native C++ handler — resolves the mesh from either an asset_path (/Game StaticMesh) or an actor (whose StaticMeshComponent's mesh is used), flips FMeshNaniteSettings.bEnabled via the UE 5.7 accessor, and fires the settings-changed rebuild. The documented gotcha: a raycast-placement pass (place_actors_raycast / light_raycast_placement) against a Nanite mesh lands props on the low-detail fallback; turn Nanite off, place, then turn it back on. Edits asset content — run save_dirty_assets to persist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string", "description": "A /Game StaticMesh path to toggle directly. Supply this OR 'actor'."},
                "actor": {"type": "string", "description": "An actor label/FName whose StaticMeshComponent's mesh is toggled. Supply this OR 'asset_path'."},
                "enabled": {"type": "boolean", "description": "Target Nanite-enabled state: true to enable Nanite, false to disable (so raycasts hit real geometry)."},
            },
            "required": ["enabled"],
        },
    },
    {
        "name": "decal_scatter",
        "description": "Scatter ADecalActors across level geometry by raycasting at a set of XY targets and projecting a decal material onto each surface hit, with deterministic per-decal scale/rotation jitter. Native C++ handler — targets come from an explicit 'points' list, a generated 'grid', or a 'bounds'+'count' random scatter (seeded by 'seed'); each is traced straight down (ECC_Visibility) and a decal is spawned at the hit oriented to project INTO the surface (decals project along +X, unlike upright mesh actors). The studio-builder use case is 'spray grime / posters / footprints / wear across this floor or wall' without hand-placing each decal.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "decal_material": {"type": "string", "description": "Path to the decal UMaterialInterface to project (e.g. '/Game/Decals/M_Grime')."},
                "points": {"type": "array", "items": {"type": "object"}, "description": "Explicit XY targets [{x, y}, ...]. Use this OR 'grid' OR 'bounds'."},
                "grid": {"type": "object", "description": "Generate targets on a grid: {min_x, min_y, max_x, max_y, count_x, count_y} (count_x/count_y >= 1, spread evenly). Preferred over 'points'/'bounds' when present."},
                "bounds": {"type": "object", "description": "Random scatter: {min_x, min_y, max_x, max_y, count} — 'count' XY points drawn uniformly inside the box via the seeded stream."},
                "trace_start_z": {"type": "number", "description": "Z height the downward trace starts from. Default 100000. Must be > 0."},
                "decal_size": {"type": "object", "description": "Base decal half-extents {x, y, z}: x = projection depth, y/z = surface footprint. Default {16, 64, 64}."},
                "scale_min": {"type": "number", "description": "Min uniform scale applied to decal_size per decal. Default 1.0; require 0 < scale_min <= scale_max."},
                "scale_max": {"type": "number", "description": "Max uniform scale applied to decal_size per decal. Default 1.0."},
                "rotation_jitter_deg": {"type": "number", "description": "Max random spin (degrees) about each decal's projection axis (roll). Default 0."},
                "sort_order": {"type": "integer", "description": "Decal SortOrder (higher draws on top). Default 0."},
                "seed": {"type": "integer", "description": "Seed for the deterministic scale/rotation/bounds RNG. Default 0."},
                "label_prefix": {"type": "string", "description": "When set, each spawned decal is labelled '<prefix><index>'."},
            },
            "required": ["decal_material"],
        },
    },
    {
        "name": "inspect_ocio_config",
        "description": "Inspect an OpenColorIO configuration asset (UOpenColorIOConfiguration): list every color space, display, and view defined in the underlying .ocio file, plus the asset's configured 'desired' color-space / display-view subset and OCIO context. Read-only. Provided by the OPTIONAL UnrealAIConnectionOCIO companion plugin (needs the OpenColorIO engine plugin enabled). Broadcast/VP color-pipeline use: see what transforms a stage's OCIO config exposes before wiring a viewport or media look.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Package path to a UOpenColorIOConfiguration asset (e.g. '/Game/Color/OCIO_Studio')."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "inspect_ndisplay_config",
        "description": "Inspect an nDisplay (DisplayCluster) configuration asset: read the cluster topology — nodes (host, window rect), per-node viewports (region rect, projection policy type + parameters, view-point camera, GPU index), and the primary node. Read-only. Provided by the OPTIONAL UnrealAIConnectionNDisplay companion plugin (needs the nDisplay engine plugin enabled). LED-wall / virtual-production use: audit a stage's cluster layout — the single most VP-defining asset — without opening the nDisplay editor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Package path to an nDisplay config blueprint asset (e.g. '/Game/nDisplay/NDC_Stage')."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "mesh_bake_ao_to_vertex_color",
        "description": "Bake self-occlusion ambient occlusion into a Static Mesh asset's vertex colors, in place. Native C++ handler (optional UnrealAIConnectionGeometry companion; needs the GeometryScripting engine plugin enabled). Copies the mesh out via Geometry Scripting, bakes AO to the RGBA vertex-color channel (self-occlusion), optionally blurs, writes back into the chosen SourceModel LOD and saves. Look/optimization use: cheap baked occlusion in vertex colors for real-time shading. Edits + saves the asset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "static_mesh": {"type": "string", "description": "A /Game StaticMesh asset path to bake AO into (required)."},
                "occlusion_rays": {"type": "integer", "description": "Number of occlusion rays cast per vertex. Default 16, clamped to 1..256."},
                "max_distance": {"type": "number", "description": "Max ray distance for occlusion. Default 0 = infinite."},
                "spread_angle": {"type": "number", "description": "Hemisphere spread angle (degrees) the rays sample over. Default 180."},
                "bias_angle": {"type": "number", "description": "Bias angle (degrees) away from the surface to avoid self-shadowing artifacts. Default 15."},
                "lod_index": {"type": "integer", "description": "SourceModel LOD index to bake into and write back. Default 0."},
                "blur_iterations": {"type": "integer", "description": "Number of smoothing passes over the baked AO. Default 0 = no blur."},
                "blur_strength": {"type": "number", "description": "Strength of each blur iteration. Default 0.5."},
                "split_at_uv_seams": {"type": "boolean", "description": "Split occlusion at UV seams when baking. Default false."},
                "split_at_normal_seams": {"type": "boolean", "description": "Split occlusion at hard-normal seams when baking. Default false."},
                "save": {"type": "boolean", "description": "Save the edited asset to disk after baking. Default true."},
            },
            "required": ["static_mesh"],
        },
    },
    {
        "name": "post_process_grade_preset",
        "description": "Save a PostProcessVolume's color grade to a JSON file, or load one back onto a volume. Native C++ handler — captures the 8 global color-grade fields (white temp/tint, the saturation/contrast/gamma/gain/offset wheels, and auto-exposure bias) plus each field's bOverride flag, so a saved look reapplies exactly. Look-dev use: save a 'golden hour' or 'warm interview' grade once, then reapply it across shots or push the same look onto another volume without re-dialling the wheels. On load it edits the volume — run save_dirty_assets to persist the level.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "load"], "description": "'save' writes the target volume's grade to preset_path; 'load' applies the preset_path JSON onto the target volume."},
                "target": {"type": "string", "description": "Label/FName of the PostProcessVolume actor."},
                "preset_path": {"type": "string", "description": "Absolute filesystem path to the .json preset file (written on save, read on load)."},
            },
            "required": ["action", "target", "preset_path"],
        },
    },
    {
        "name": "sequence_snapshot",
        "description": "Crash-safety checkpoint  -  duplicate the current editor level and optionally named Level Sequence assets into a timestamped folder under /Game/_Snapshots/ so a risky edit can be rolled back. Call before bulk destructive edits (material reassign, scene restructure, animation bake). Returns snapshot_folder, level_snapshot path, per-sequence {source, snapshot, ok} pairs, and a note to run save_dirty_assets to flush the duplicates to disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short name embedded in the snapshot folder (default 'snapshot'). Alphanumeric, hyphens, underscores only; other characters are replaced with '_'.",
                },
                "sequence_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of /Game Level Sequence asset paths to also snapshot alongside the level.",
                },
            },
        },
    },
    {
        "name": "material_blend_override",
        "description": "Set or override a named material parameter (scalar or color) across many actors' mesh materials via dynamic material instances  -  time-of-day / look-dev use. Native C++ handler  -  resolves the target actor set via exactly one selector (by_label, by_folder, or by_name_regex), then for each actor walks its UMeshComponent(s) and calls CreateDynamicMaterialInstance per slot, followed by SetScalarParameterValue or SetVectorParameterValue. Use case: push 'GoldenHour_Intensity' or 'SkyColor' across every prop in /Set/Exterior in one call.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "targets": {"type": "object", "description": "Actor selector  -  set exactly one of: by_label (string or list of strings), by_folder (World-Outliner folder path, matched as a prefix so '/Set/Walls' also catches '/Set/Walls/Sub'), or by_name_regex (regex matched against each actor's label and FName)."},
                "parameter": {"type": "string", "description": "The material parameter name to set (e.g. 'GoldenHour_Intensity' or 'SkyColor')."},
                "scalar": {"type": "number", "description": "Scalar float value to set. Supply this OR 'color' (exactly one)."},
                "color": {"type": "object", "description": "Linear color value {r, g, b, a} (floats 0..1, 'a' defaults to 1.0). Supply this OR 'scalar' (exactly one)."},
                "slot": {"type": "integer", "description": "Material slot index to target. Default -1 = all slots on each matched mesh component. Out-of-range slots on a component are silently skipped."},
            },
            "required": ["targets", "parameter"],
        },
    },
    {
        "name": "export_actor_as_gltf",
        "description": "Export selected or named actor(s) to a .gltf or .glb file for handoff to external DCC / glTF-compatible tools. Native C++ handler backed by Epic's GLTFExporter plugin. Supply 'actors' (array of labels) to export specific actors, 'selected_only'=true to export the current editor selection, or omit both to export the entire visible level. GLTFExporter is an optional cascade-enabled dependency (no manual setup); material baking is disabled for stability.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "output_path": {"type": "string", "description": "Absolute filesystem path for the output file. Must end in .gltf (separate JSON + textures) or .glb (self-contained binary)."},
                "actors": {"type": "array", "items": {"type": "string"}, "description": "Optional list of actor labels/FNames to export. If omitted and selected_only is false, the entire visible level is exported."},
                "selected_only": {"type": "boolean", "description": "If true, export only the actors currently selected in the editor viewport. Ignored when 'actors' is supplied."},
            },
            "required": ["output_path"],
        },
    },
]


# ===========================================================================
# Progressive tool disclosure  (opt-in, off by default)
#
# State-of-the-art MCP servers no longer dump every tool schema into the
# model's context up front. Past ~30-50 tools, tool-selection accuracy and
# token cost both blow up (Anthropic "Tool Search Tool", arXiv 2603.20313
# semantic discovery, modelcontextprotocol discussion #532). We advertise
# ~147 tools; that is 3-5x over the documented safe ceiling.
#
# This block adds an OPT-IN progressive mode. When enabled, `tools/list`
# returns only a small always-on CORE set plus one bridge-side `search_tools`
# discovery tool. The model calls `search_tools(query=..., category=...)` to
# pull the full schemas of just the tools it needs, on demand.
#
# Backward compatibility (default OFF):
#   - UCMCP_TOOL_MODE unset / "all" / "full"  -> tools/list returns every
#     tool exactly as before. No behaviour change for existing clients.
#   - UCMCP_TOOL_MODE = "progressive"          -> tools/list returns CORE +
#     search_tools; everything else is discoverable via search_tools.
#
# Crucially, EVERY tool in TOOLS remains directly callable via tools/call in
# BOTH modes. Progressive mode only changes what is *advertised*, never what
# is *dispatchable* -- a client that already knows a tool name keeps working.
# `search_tools` is a bridge-only synthetic: it never reaches the UE server,
# is not part of the C++ manifest, and is not counted in EXPECTED_TOOL_COUNT.
# ===========================================================================

# Always-advertised tools in progressive mode. Deliberately tiny: the few
# observe/orient tools an agent needs before it knows what else to search for,
# plus the universal Python escape hatch. Keep this list small -- every entry
# here is permanent context cost.
CORE_TOOL_NAMES = (
    "get_project_summary",       # orient: what project / engine / maps
    "list_tools",                # enumerate registered UE methods
    "get_actors_in_level",       # observe current world
    "execute_unreal_python",     # universal escape hatch
    "take_high_res_screenshot",  # see the result
    "get_viewport_screenshot",   # see the result (inline)
    "poll_events",               # async/event awareness
)

# Curated daily-driver profile, measured from real production sessions
# (HDM rebuild-v2 benchmark, 2026-06): the tools actually used to assemble,
# light, texture and capture a full broadcast set, plus the async-task
# family that replaces sync python for long ops. Sits between CORE (7) and
# the full catalog (149): big enough to drive a session without
# search_tools round-trips, small enough to cut the advertised-schema
# weight ~75% for clients that don't defer tool schemas.
LEAN_TOOL_NAMES = (
    # orient
    "get_project_summary", "get_engine_version", "list_levels", "load_level_by_path",
    # observe
    "get_actors_in_level", "find_actors_by_class",
    # actors
    "spawn_actor", "delete_actor", "set_actor_transform", "set_actor_property", "focus_actor",
    # camera
    "get_camera_transform", "set_camera_transform",
    # python
    "execute_unreal_python", "exec_python_persistent", "reset_python_state", "run_python_file",
    # async tasks
    "start_python_task", "start_python_file_task", "poll_task", "list_tasks", "cancel_task",
    # console + logs
    "execute_console_command", "get_log_lines",
    # see the result
    "get_viewport_screenshot", "take_screenshot", "take_high_res_screenshot", "render_camera_to_png",
    # materials + assets
    "import_texture", "create_material_instance", "set_mi_parameter", "save_dirty_assets",
)

# Coarse workflow categories, matched by keyword against each tool's name +
# description. Used only to let `search_tools(category=...)` filter; a tool
# may match several. Ordering is intentional (more specific first) so the
# single "primary" category reported per tool is the best fit.
_TOOL_CATEGORIES = (
    ("sequencer", ("sequence", "sequencer", "keyframe", "camera_cut", "cine_camera", "mrq", "playback")),
    ("material", ("material", "_mi_", "mi_parameter", "shader", "texture", "ocio", "pbr")),
    ("blueprint", ("blueprint", "_bp_", "widget", "umg", "anim_blueprint", "node", "pin")),
    ("niagara", ("niagara", "vfx", "particle")),
    ("lighting", ("light", "lumen", "nanite", "build_lighting", "post_process", "hdri", "cubemap", "grade")),
    ("actor", ("actor", "spawn", "transform", "component", "focus", "outliner", "folder")),
    ("asset", ("asset", "import", "redirector", "data_table", "data_asset", "static_mesh", "skeletal_mesh", "metasound", "sound", "audio")),
    ("level", ("level", "world", "map", "umap")),
    ("rendering", ("screenshot", "render", "viewport", "capture", "screen", "png", "gltf")),
    ("async", ("event", "subscription", "subscribe", "task", "poll", "sleep", "wait")),
    ("python", ("python", "console", "command", "cvar", "console_variable")),
    ("inspect", ("inspect", "audit", "find_", "list_", "get_", "compare", "dependency", "reference")),
)

# Hard caps so a hostile or buggy caller cannot make search_tools do
# unbounded work or echo back the entire catalog as a single huge result.
_SEARCH_QUERY_MAX_LEN = 256
_SEARCH_RESULT_HARD_CAP = 25
_SEARCH_RESULT_DEFAULT_LIMIT = 8

# The descriptor advertised for the bridge-side discovery tool. Mirrors the
# MCP tool shape of the entries in TOOLS so clients render it identically.
SEARCH_TOOLS_DESCRIPTOR = {
    "name": "search_tools",
    "description": (
        "Progressive tool discovery. This server exposes ~147 tools but only a "
        "small core set is advertised up front to keep context lean. Call this "
        "to find the full input schemas of the tools you need on demand. Pass a "
        "free-text `query` (e.g. 'add a camera keyframe', 'import a PBR texture') "
        "and/or a `category`. Returns the matching tools' name + description + "
        "inputSchema, ranked by relevance. Every returned tool is callable "
        "directly via tools/call by its name. Categories: "
        + ", ".join(c for c, _ in _TOOL_CATEGORIES) + "."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Free-text intent to match against tool names + descriptions.",
            },
            "category": {
                "type": "string",
                "description": "Optional workflow category filter (see description for the list).",
                "enum": [c for c, _ in _TOOL_CATEGORIES],
            },
            "limit": {
                "type": "integer",
                "description": f"Max tools to return (1..{_SEARCH_RESULT_HARD_CAP}, default {_SEARCH_RESULT_DEFAULT_LIMIT}).",
                "minimum": 1,
                "maximum": _SEARCH_RESULT_HARD_CAP,
            },
        },
    },
}


def tool_mode() -> str:
    """Resolve the advertised-tool mode from the environment, at call time.

    Read on every tools/list so the mode can be flipped without restarting
    the bridge (and so tests can monkeypatch os.environ). Unknown / unset
    values fail safe to "all" -- the legacy expose-everything behaviour --
    so a typo never silently hides tools from a client.
    """
    raw = (os.environ.get("UCMCP_TOOL_MODE") or "").strip().lower()
    if raw in ("progressive", "search", "deferred"):
        return "progressive"
    if raw == "lean":
        return "lean"
    return "all"  # default + any unrecognised value: backward-compatible


def _primary_category(tool: dict) -> str | None:
    """Best-fit single category for a tool, or None if nothing matches."""
    haystack = (str(tool.get("name", "")) + " " + str(tool.get("description", ""))).lower()
    for cat, keywords in _TOOL_CATEGORIES:
        if any(kw in haystack for kw in keywords):
            return cat
    return None


def core_tools() -> list:
    """The CORE tools advertised in progressive mode, in CORE_TOOL_NAMES order.

    Resolved against the live TOOLS catalog so a renamed/removed core tool
    simply drops out rather than advertising a phantom. Always followed by
    the search_tools descriptor at the call site.
    """
    by_name = {t.get("name"): t for t in TOOLS}
    return [by_name[n] for n in CORE_TOOL_NAMES if n in by_name]


def lean_tools() -> list:
    """The curated LEAN profile, in LEAN_TOOL_NAMES order.

    Same resolution discipline as core_tools(): names missing from the live
    TOOLS catalog drop out silently rather than advertising a phantom.
    """
    by_name = {t.get("name"): t for t in TOOLS}
    return [by_name[n] for n in LEAN_TOOL_NAMES if n in by_name]


def advertised_tools() -> list:
    """Tools to return from tools/list, honouring the current tool_mode().

    - "all" (default): the full TOOLS catalog, byte-for-byte as before.
    - "progressive": CORE tools + the search_tools discovery descriptor.
    - "lean": curated LEAN profile + the search_tools discovery descriptor.
    """
    mode = tool_mode()
    if mode == "progressive":
        return core_tools() + [SEARCH_TOOLS_DESCRIPTOR]
    if mode == "lean":
        return lean_tools() + [SEARCH_TOOLS_DESCRIPTOR]
    return TOOLS


def _score_tool(tool: dict, query_tokens: list, raw_query: str) -> int:
    """Cheap, deterministic relevance score for a tool against a query.

    No external deps (no embeddings) so the bridge stays a single zero-install
    file. Scoring is purely lexical over the static catalog text:
      - exact tool-name substring of the whole query    -> strong boost
      - each query token found in the tool name          -> medium
      - each query token found in the description        -> small
    Returns 0 when nothing matches (caller drops it).
    """
    name = str(tool.get("name", "")).lower()
    desc = str(tool.get("description", "")).lower()
    score = 0
    if raw_query and raw_query in name:
        score += 50
    for tok in query_tokens:
        if tok in name:
            score += 10
        elif tok in desc:
            score += 3
    return score


def search_tools_impl(args: dict) -> dict:
    """Pure, side-effect-free tool discovery over the static TOOLS catalog.

    SECURITY / fail-closed contract:
      - Validates every input type; never raises on bad input -- returns a
        structured `{"ok": False, "error_code": ...}` payload instead.
      - Reads ONLY the in-process TOOLS list. It touches no filesystem, runs
        no code, opens no socket, and interpolates nothing into a shell or
        Python string. `query`/`category` are matched as plain lowercased
        substrings -- there is no path, glob, or expression evaluation, so
        path-traversal / injection inputs are inert data, not vectors.
      - Never surfaces a tool that is not already in the publicly-advertised
        TOOLS catalog. There are no hidden/internal handlers to leak: the
        bridge-only `search_tools` descriptor itself is deliberately excluded
        from results (a client already has it).
      - Bounded work: query length is capped and the result count is hard-
        capped (`_SEARCH_RESULT_HARD_CAP`) regardless of the requested limit.

    Returns a plain result dict (NOT a JSON-RPC envelope); the caller wraps it
    via _wrap_tool_result so logical errors ride back as ok:false success
    envelopes, matching every other synthetic tool.
    """
    if not isinstance(args, dict):
        return {"ok": False, "error_code": "invalid_arguments",
                "message": "search_tools: arguments must be an object"}

    query = args.get("query", "")
    category = args.get("category")
    limit = args.get("limit", _SEARCH_RESULT_DEFAULT_LIMIT)

    # --- validate query -----------------------------------------------------
    if query is None:
        query = ""
    if not isinstance(query, str):
        return {"ok": False, "error_code": "invalid_query",
                "message": "search_tools: 'query' must be a string"}
    if len(query) > _SEARCH_QUERY_MAX_LEN:
        return {"ok": False, "error_code": "query_too_long",
                "message": f"search_tools: 'query' exceeds {_SEARCH_QUERY_MAX_LEN} characters"}

    # --- validate category --------------------------------------------------
    valid_categories = {c for c, _ in _TOOL_CATEGORIES}
    if category is not None:
        if not isinstance(category, str):
            return {"ok": False, "error_code": "invalid_category",
                    "message": "search_tools: 'category' must be a string"}
        category = category.strip().lower()
        if category and category not in valid_categories:
            return {"ok": False, "error_code": "unknown_category",
                    "message": f"search_tools: unknown category '{category}'. "
                               f"Valid: {sorted(valid_categories)}"}
        if not category:
            category = None

    # --- validate limit -----------------------------------------------------
    if isinstance(limit, bool) or not isinstance(limit, int):
        return {"ok": False, "error_code": "invalid_limit",
                "message": "search_tools: 'limit' must be an integer"}
    if limit < 1:
        return {"ok": False, "error_code": "invalid_limit",
                "message": "search_tools: 'limit' must be >= 1"}
    limit = min(limit, _SEARCH_RESULT_HARD_CAP)

    raw_query = query.strip().lower()
    query_tokens = [t for t in raw_query.replace("_", " ").split() if t]

    # An empty query with no category would match everything; require at least
    # one of the two so the model gives a real signal (and we never dump the
    # whole catalog through the search path -- that defeats the purpose).
    if not raw_query and not category:
        return {"ok": False, "error_code": "empty_search",
                "message": "search_tools: supply a 'query' and/or a 'category'."}

    # Hoist the category-keyword lookup out of the loop: build the {category: keywords}
    # map ONCE (it was rebuilt up to 147x before, since _TOOL_CATEGORIES is a tuple of
    # pairs) and cache each tool's primary category so it is computed once here, not
    # again during result construction.
    _cat_map = dict(_TOOL_CATEGORIES)
    cat_keywords = _cat_map.get(category, ()) if category is not None else ()
    matches = []
    for tool in TOOLS:
        pc = _primary_category(tool)
        if category is not None and pc != category:
            # Also allow a secondary keyword hit so category isn't too strict.
            haystack = (str(tool.get("name", "")) + " " + str(tool.get("description", ""))).lower()
            if not any(kw in haystack for kw in cat_keywords):
                continue

        if raw_query:
            score = _score_tool(tool, query_tokens, raw_query)
            if score <= 0:
                continue
        else:
            score = 1  # category-only browse: flat score, name-sorted below

        matches.append((score, tool, pc))

    # Sort by score desc, then name asc for stable deterministic output.
    matches.sort(key=lambda st: (-st[0], str(st[1].get("name", ""))))

    results = []
    for _score, tool, pc in matches[:limit]:
        entry = {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "inputSchema": tool.get("inputSchema"),
            "category": pc,
        }
        # Carry through engine gating so the model doesn't pick a tool the
        # connected editor can't run (same metadata the real catalog exposes).
        if tool.get("min_engine_version"):
            entry["min_engine_version"] = tool["min_engine_version"]
        results.append(entry)

    return {
        "ok": True,
        "query": query,
        "category": category,
        "total_matches": len(matches),
        "returned": len(results),
        "tools": results,
        "hint": (
            "Call any returned tool directly via tools/call using its 'name'. "
            "All ~147 tools remain callable even though only a core set is "
            "advertised in progressive mode."
        ),
    }


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
                "message": f"UE server not reachable on {UE_HOST}:{UE_PORT}: {e}. Open the UE editor with the UnrealAIConnection plugin enabled.",
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

    # Phase H engine gate: blocks on UE < 5.0 (get_editor_subsystem is 5.0+).
    gate = check_engine_gate(req_id, "get_camera_transform")
    if gate is not None:
        return gate

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

    # Phase H engine gate: blocks on UE < 5.0 (get_editor_subsystem is 5.0+).
    gate = check_engine_gate(req_id, "set_camera_transform")
    if gate is not None:
        return gate

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
         to a project-confined PNG on disk

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
        "bytes": shot_result.get("bytes"),
        "path": shot_result.get("path"),
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

    Token footprint: each inspect_asset blob carries the full tags dict
    plus the (possibly long) dependencies/referencers arrays. For a batch
    of N assets that full payload dominates the response. By default
    (`verbose=False`) each successful result records a SUMMARY — path,
    class, and dependency/referencer counts — instead of the raw blob.
    Pass `verbose=True` to restore the full per-asset `data` (the
    backward-compatible pre-trim shape). Failed results are unaffected:
    their error_code/error_message are preserved in both modes.
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

    # verbose defaults to False -> per-asset SUMMARY (path/class + dep/ref
    # counts). verbose=True -> full inspect_asset blob under `data`
    # (backward-compatible pre-trim shape).
    verbose = args.get("verbose", False)
    if not isinstance(verbose, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "bulk_inspect_assets: invalid_field: 'verbose' must be a boolean",
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
            data = ue_resp.get("result", {}) or {}
            if verbose:
                # Full per-asset blob, unchanged from the pre-trim contract.
                results.append({
                    "path": path,
                    "ok": True,
                    "data": data,
                    "error_code": None,
                    "error_message": None,
                })
            else:
                # Trimmed summary. Pull the high-signal scalars and collapse the
                # (potentially long) dependencies/referencers arrays to counts.
                deps = data.get("dependencies")
                refs = data.get("referencers")
                results.append({
                    "path": path,
                    "ok": True,
                    "class": data.get("class"),
                    "dependency_count": len(deps) if isinstance(deps, list) else None,
                    "referencer_count": len(refs) if isinstance(refs, list) else None,
                    "error_code": None,
                    "error_message": None,
                })

    body = {
        "ok": failed == 0,
        "total": len(paths),
        "inspected": inspected,
        "failed": failed,
        "verbose": verbose,
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
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 500:
        return make_response(req_id, error={
            "code": -32602,
            "message": "find_unused_assets: invalid_field: 'limit' must be an integer between 1 and 500",
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
         round-trip that reads a `blueprint_status` field. Buckets:
         UpToDate, Dirty, Error, Unknown, BeingCreated.
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
                        "path": shot_result.get("path"),
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
                "path": inner.get("path"),
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

    Token footprint: a bidirectional sweep past depth ~3 can enumerate
    thousands of nodes/edges in a non-trivial project. A `max_nodes` cap
    (default 100) bounds the response by halting frontier expansion once
    that many distinct nodes have been visited — at which point
    `truncated=true`. Raise `max_nodes` (up to 100000) for an exhaustive
    sweep. The depth bound still applies independently.

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

    # max_nodes bounds the visited set (default 100). Once the cap is hit, no
    # further neighbors are queued and `truncated` is set. The root counts as
    # node 1, so a cap of 1 returns just the root node with no edges and no expansion.
    max_nodes = args.get("max_nodes", 100)
    if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or max_nodes < 1 or max_nodes > 100000:
        return make_response(req_id, error={
            "code": -32602,
            "message": "inspect_dependency_graph: invalid_max_nodes: 'max_nodes' must be an integer between 1 and 100000",
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
                    if neighbor in visited:
                        # Already in the graph: keep the edge, no re-queue.
                        edges.append({"from": node, "to": neighbor, "direction": "down"})
                    elif len(visited) < max_nodes:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
                        edges.append({"from": node, "to": neighbor, "direction": "down"})
                    else:
                        # max_nodes cap hit: don't add the node or a dangling
                        # edge to it; flag the graph as truncated.
                        truncated = True

            # Optionally also follow referencers (up).
            if include_referencers:
                refs = result.get("referencers") or []
                if isinstance(refs, list):
                    for neighbor in refs:
                        if not isinstance(neighbor, str) or not neighbor:
                            continue
                        if neighbor in visited:
                            edges.append({"from": neighbor, "to": node, "direction": "up"})
                        elif len(visited) < max_nodes:
                            visited.add(neighbor)
                            next_frontier.append(neighbor)
                            edges.append({"from": neighbor, "to": node, "direction": "up"})
                        else:
                            truncated = True
        if not next_frontier:
            break
        frontier = next_frontier
    else:
        truncated = truncated or bool(frontier)

    return _wrap_tool_result(req_id, {
        "ok": True,
        "root": root,
        "depth": depth,
        "include_referencers": include_referencers,
        "max_nodes": max_nodes,
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


_MARKETPLACE_USER_AGENT = "UnrealAIConnection/0.9.1 (+https://github.com/NAJEMWEHBE/unreal-ai-connection)"
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


# AmbientCG zip filename markers per canonical map. Order inside each tuple
# is preference order — `_NormalGL` is preferred over `_NormalDX` (UE's
# tangent-space convention is OpenGL), but DX variants are kept as a fallback
# so assets that only publish DX-tangent normals still resolve.
_AMBIENTCG_MAP_MARKERS: dict[str, tuple[str, ...]] = {
    "color":        ("_Color.", "_color.", "_Diffuse.", "_diffuse."),
    "normal":       ("_NormalGL.", "_normalgl.", "_NormalDX.", "_normaldx.", "_Normal.", "_normal."),
    "roughness":    ("_Roughness.", "_roughness.", "_Rough.", "_rough."),
    "ao":           ("_AmbientOcclusion.", "_ambientocclusion.", "_AO.", "_ao."),
    "displacement": ("_Displacement.", "_displacement.", "_Disp.", "_disp."),
    "metalness":    ("_Metalness.", "_metalness.", "_Metal.", "_metal."),
}


def _ambientcg_extract_pbr_maps(zip_path: str, dest_dir: str) -> tuple[dict[str, str] | None, dict | None]:
    """Multi-map sibling of `_ambientcg_extract_primary_map`. Extracts every
    canonical PBR map present in the AmbientCG zip and returns a dict of
    `canonical_name -> extracted_file_path`.

    Color is required — its absence is the only condition that produces an
    error. Other maps are best-effort: if the zip doesn't ship Normal, the
    returned dict simply omits the `normal` key.

    Path-traversal safety: every extracted name is flattened via
    `os.path.basename`, mirroring `_ambientcg_extract_primary_map`.
    """
    import zipfile
    import os
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            file_names = [n for n in names if not n.endswith("/") and not os.path.basename(n).startswith(".")]
            if not file_names:
                return None, {"code": -32603, "message": f"ambientcg: bad_zip: {zip_path}: archive contains no files"}
            picks: dict[str, str] = {}
            for canonical, markers in _AMBIENTCG_MAP_MARKERS.items():
                for marker in markers:
                    matches = [n for n in file_names if marker in os.path.basename(n)]
                    if matches:
                        picks[canonical] = sorted(matches)[0]
                        break
            if "color" not in picks:
                return None, {"code": -32603, "message": f"ambientcg: zip_has_no_color_map: files={[os.path.basename(n) for n in file_names]}"}
            extracted: dict[str, str] = {}
            for canonical, pick in picks.items():
                safe_name = os.path.basename(pick)
                dest_path = os.path.join(dest_dir, safe_name)
                with zf.open(pick) as src, open(dest_path, "wb") as out:
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
                extracted[canonical] = dest_path
            return extracted, None
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


# Polyhaven /files/{slug} top-level key -> canonical PBR map name. Preference
# inside each tuple matters: `nor_gl` (OpenGL tangent-space normal) wins over
# `Normal` because UE's tangent-space convention is OpenGL.
_POLYHAVEN_MAP_KEYS: dict[str, tuple[str, ...]] = {
    "color":        ("Diffuse", "diffuse", "Color", "color"),
    "normal":       ("nor_gl", "nor_dx", "NormalDX", "Normal", "normal"),
    "roughness":    ("Rough", "Roughness", "roughness"),
    "ao":           ("AO", "ao"),
    "displacement": ("Displacement", "displacement", "Disp"),
    "metalness":    ("Metal", "metal", "Metalness", "metalness"),
}


def _polyhaven_pick_pbr_files(files: dict, resolution: str, fmt: str) -> tuple[dict[str, str] | None, list[str], dict | None]:
    """Multi-map sibling of `_polyhaven_pick_file`. Walks the Polyhaven
    /files/{slug} payload and returns a dict of `canonical_name -> URL`
    for every PBR map present at the requested resolution. Format-fallback
    (`png <-> jpg`) is applied per-map so a mixed asset (some maps PNG,
    others only JPG) still resolves cleanly.

    Color is required. Other maps are best-effort: missing maps are simply
    absent from the returned dict.

    Returns (urls_by_map, available_resolutions, error). Available is
    derived from the diffuse map's resolution set (same source of truth
    used by the single-map picker).
    """
    def _resolution_sort_key(r: str) -> tuple[int, str]:
        if r.endswith("k") and r[:-1].isdigit():
            return (int(r[:-1]), r)
        return (0, r)

    # Anchor resolution-availability on the diffuse map so multi-map mode
    # surfaces the same error message as the single-map picker when the
    # caller asks for a resolution Polyhaven doesn't publish for this asset.
    diff_block = None
    for key in _POLYHAVEN_MAP_KEYS["color"]:
        candidate = files.get(key)
        if isinstance(candidate, dict):
            diff_block = candidate
            break
    if diff_block is None:
        return None, [], {"code": -32603, "message": "texture_no_diffuse: Polyhaven payload lacks a Diffuse/Color map"}
    resolutions = sorted(diff_block.keys(), key=_resolution_sort_key)
    if not isinstance(diff_block.get(resolution), dict):
        return None, resolutions, {"code": -32603, "message": f"resolution_unavailable: '{resolution}' not in available {resolutions}"}

    urls: dict[str, str] = {}
    for canonical, keys in _POLYHAVEN_MAP_KEYS.items():
        map_block = None
        for key in keys:
            candidate = files.get(key)
            if isinstance(candidate, dict):
                map_block = candidate
                break
        if map_block is None:
            continue
        res_block = map_block.get(resolution)
        if not isinstance(res_block, dict):
            continue
        for f in [fmt, "png", "jpg"]:
            entry = res_block.get(f)
            if isinstance(entry, dict) and "url" in entry:
                urls[canonical] = entry["url"]
                break

    if "color" not in urls:
        return None, resolutions, {"code": -32603, "message": f"format_unavailable: diffuse map present but no {fmt}/png/jpg variant at resolution {resolution}"}
    return urls, resolutions, None


def _marketplace_import_multimap(
    req_id,
    source: str,
    slug: str,
    resolution: str,
    fmt: str,
    dest_path: str,
    dest_name: str,
    replace_existing: bool,
    safe_slug: str,
    safe_resolution: str,
    tmp_dir: str,
) -> dict:
    """Multi-map sibling of `synthetic_marketplace_import`'s diffuse-only
    body. Resolves every canonical PBR map present on the source, downloads
    each (one HTTP per map for Polyhaven; single zip for AmbientCG), and
    calls `import_texture` once per extracted map. Color is required; all
    other canonical maps are best-effort.

    Naming convention in UE:
      - Color  -> {dest_name}              (back-compat with single-map mode)
      - Others -> {dest_name}_{canonical}  e.g. Rocks023_normal, _roughness, …

    Returns the standard tool envelope. Response body adds a `maps` dict
    (canonical_name -> UE asset path), keeps `ue_asset_path` pinned to the
    color map so existing callers don't break.
    """
    import os
    import urllib.parse as _urlparse

    # 1. Resolve a per-map URL/file table.
    urls: dict[str, str] = {}
    available: list = []
    downloaded_from: str
    chosen_fmt: str | None = None
    extracted_paths: dict[str, str] = {}

    if source == "polyhaven":
        files_url = f"https://api.polyhaven.com/files/{_urlparse.quote(slug, safe='')}"
        files, err = _marketplace_http_get_json(files_url)
        if err is not None:
            return make_response(req_id, error=err)
        if not isinstance(files, dict):
            return make_response(req_id, error={
                "code": -32603,
                "message": f"marketplace_import: unexpected_payload: /files/{slug} did not return a JSON object",
            })
        urls, available, pick_err = _polyhaven_pick_pbr_files(files, resolution, fmt)
        if pick_err is not None:
            return make_response(req_id, error=pick_err)
        downloaded_from = files_url
        for canonical, url in (urls or {}).items():
            safe_canonical = "".join(c for c in canonical if c.isalnum()) or "map"
            # Suffix derives from the URL's extension. Polyhaven URLs always
            # end with the actual file extension after the last dot.
            url_ext = url.rsplit(".", 1)[-1].lower()
            safe_ext = "".join(c for c in url_ext if c.isalnum()) or "bin"
            tmp_path = os.path.join(tmp_dir, f"marketplace_{safe_slug}_{safe_resolution}_{safe_canonical}.{safe_ext}")
            dl_err = _marketplace_http_download(url, tmp_path)
            if dl_err is not None:
                return make_response(req_id, error=dl_err)
            extracted_paths[canonical] = tmp_path
            if canonical == "color":
                chosen_fmt = safe_ext
    else:  # source == "ambientcg"
        zip_url, chosen_attr, available, pick_err = _ambientcg_resolve_zip_url(slug, "texture", resolution, fmt)
        if pick_err is not None:
            return make_response(req_id, error=pick_err)
        downloaded_from = zip_url or ""
        chosen_fmt = (chosen_attr or "").split("-")[-1].lower() if chosen_attr else None
        zip_tmp = os.path.join(tmp_dir, f"marketplace_{safe_slug}_{safe_resolution}.zip")
        dl_err = _marketplace_http_download(zip_url, zip_tmp)
        if dl_err is not None:
            return make_response(req_id, error=dl_err)
        extract_dir = os.path.join(tmp_dir, f"marketplace_{safe_slug}_{safe_resolution}_extract")
        try:
            os.makedirs(extract_dir, exist_ok=True)
        except OSError as e:
            return make_response(req_id, error={
                "code": -32603,
                "message": f"marketplace_import: ambientcg_mkdir_failed: {extract_dir}: {e}",
            })
        ex_maps, ex_err = _ambientcg_extract_pbr_maps(zip_tmp, extract_dir)
        if ex_err is not None:
            return make_response(req_id, error=ex_err)
        extracted_paths = ex_maps or {}
        try:
            os.remove(zip_tmp)
        except OSError:
            pass

    # 2. Fan out import_texture calls. Color first so a failure surfaces
    # the most-critical map's error rather than a secondary one.
    # map_order is derived from extracted_paths so every element exists
    # in the dict by construction — no membership check needed in the loop.
    map_order = ["color"] + [m for m in extracted_paths.keys() if m != "color"]
    imported: dict[str, str] = {}
    import_results: dict[str, dict] = {}
    for canonical in map_order:
        per_map_dest_name = dest_name if canonical == "color" else f"{dest_name}_{canonical}"
        import_params = {
            "source_path": extracted_paths[canonical],
            "dest_path": dest_path,
            "dest_name": per_map_dest_name,
            "replace_existing": replace_existing,
            "automated": True,
            "save": True,
        }
        import_resp = call_ue("import_texture", import_params)
        if "error" in import_resp:
            upstream = import_resp.get("error") or {}
            # Partial-import recovery: surface every map that did land in
            # UE so the caller can decide whether to `delete_asset` them
            # or retry the failed map with `replace_existing=true`. Without
            # this, an `replace_existing=false` retry would hit the stale
            # color asset and double-fail.
            return make_response(req_id, error={
                "code": upstream.get("code", -32603) or -32603,
                "message": f"marketplace_import: ue_import_failed: map={canonical}: {upstream.get('message') or 'import_texture returned an error'}",
                "data": {
                    "failed_map": canonical,
                    "imported_so_far": imported,
                    "remaining_maps": [m for m in map_order if m not in imported and m != canonical],
                    "hint": "retry with replace_existing=true, or delete the assets in imported_so_far before retrying with replace_existing=false",
                },
            })
        result = import_resp.get("result") or {}
        imported[canonical] = result.get("asset_path") or f"{dest_path}/{per_map_dest_name}"
        import_results[canonical] = result

    body: dict = {
        "ok": True,
        "source": source,
        "slug": slug,
        "asset_type": "texture",
        "resolution": resolution,
        "format": chosen_fmt or fmt,
        "downloaded_from": downloaded_from,
        "maps": imported,
        "ue_asset_path": imported.get("color", f"{dest_path}/{dest_name}"),
        "available_resolutions": available,
        "import_results": import_results,
        "license": "CC0",
    }
    return _wrap_tool_result(req_id, body)


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
    multi_map = args.get("multi_map", False)
    if not isinstance(multi_map, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": f"marketplace_import: invalid_field: 'multi_map' must be a boolean, got {type(multi_map).__name__}",
        })
    if multi_map and asset_type != "texture":
        return make_response(req_id, error={
            "code": -32602,
            "message": f"marketplace_import: invalid_field: 'multi_map=true' applies to asset_type='texture' only (got '{asset_type}'); HDRIs ship as a single file",
        })
    replace_existing = args.get("replace_existing", False)
    if not isinstance(replace_existing, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": f"marketplace_import: invalid_field: 'replace_existing' must be a boolean, got {type(replace_existing).__name__}",
        })

    import tempfile
    import os
    def _safe_path_token(s: str, default: str) -> str:
        cleaned = "".join(c for c in (s or "") if c.isalnum() or c in "._-")
        return cleaned or default
    safe_slug = _safe_path_token(slug, "slug")
    safe_resolution = _safe_path_token(resolution, "res")
    tmp_dir = tempfile.gettempdir()

    # Multi-map PBR path: fan-out per canonical map name. Color is required;
    # other maps are best-effort. Each map lands as a separate UTexture2D
    # named `<dest_name>_<map>` (Color suffix omitted so the existing
    # diffuse-only dest_name stays the color asset path for back-compat).
    if multi_map:
        return _marketplace_import_multimap(
            req_id, source, slug, resolution, fmt,
            dest_path, dest_name, replace_existing,
            safe_slug, safe_resolution, tmp_dir,
        )

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


# ---------------------------------------------------------------------------
# convert_hdri_to_cubemap (synthetic)
# ---------------------------------------------------------------------------

# Allowed compression presets the conversion script will pass through to
# `unreal.TextureCompressionSettings`. Restricting to this allowlist keeps
# caller input from injecting arbitrary enum names into the generated UE
# Python.
_HDRI_CUBE_COMPRESSION_ALLOWED = {
    "TC_HDR",
    "TC_HDR_COMPRESSED",
    "TC_HDR_F32",
    "TC_DEFAULT",
}


def _render_hdri_to_cubemap_script(hdri_path: str, dest_pkg: str, cube_name: str,
                                   cube_size: int, compression: str,
                                   tag: str) -> str:
    """Return the UE Python script that executes the longlat -> cubemap
    pipeline (SceneCaptureCube against an inside-out unit sphere with the
    HDRI as an unlit emissive material). Inputs MUST already be validated
    by the caller — this helper does no escaping. `tag` is a per-call
    unique suffix used so concurrent invocations don't race over temp
    asset names and never delete unrelated user content."""
    return f"""\
import unreal
HDRI_PATH = {hdri_path!r}
DEST_PKG = {dest_pkg!r}
CUBE_NAME = {cube_name!r}
RT_SIZE = {int(cube_size)}
COMPRESSION = unreal.TextureCompressionSettings.{compression}
TAG = {tag!r}

el = unreal.EditorAssetLibrary
ll = unreal.EditorLevelLibrary
asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
mel = unreal.MaterialEditingLibrary
els = unreal.EditorActorSubsystem()

if not el.does_asset_exist(HDRI_PATH):
    raise RuntimeError(f"hdri_not_found: {{HDRI_PATH}}")

rt_name = f"RT_HDRI_ToCube_Temp_{{TAG}}"
mat_name = f"M_HDRI_Sphere_ToCube_Temp_{{TAG}}"
rt_path = f"{{DEST_PKG}}/{{rt_name}}"
mat_path = f"{{DEST_PKG}}/{{mat_name}}"

rt = None
mat = None
sphere = None
scc = None
cube = None
try:
    rt = asset_tools.create_asset(
        rt_name, DEST_PKG, unreal.TextureRenderTargetCube,
        unreal.TextureRenderTargetCubeFactoryNew(),
    )
    rt.set_editor_property("size_x", RT_SIZE)

    mat = asset_tools.create_asset(
        mat_name, DEST_PKG, unreal.Material, unreal.MaterialFactoryNew(),
    )
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    hdri_tex = el.load_asset(HDRI_PATH)
    ts = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -400, 0)
    ts.texture = hdri_tex
    ts.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR
    mel.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    mel.recompile_material(mat)
    el.save_loaded_asset(mat)

    sphere = ll.spawn_actor_from_object(
        el.load_asset("/Engine/BasicShapes/Sphere"),
        unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    sphere.set_actor_label(f"HDRI_ToCube_Sphere_Temp_{{TAG}}")
    sphere.set_actor_scale3d(unreal.Vector(-50, 50, 50))
    sphere.static_mesh_component.set_material(0, mat)

    scc = ll.spawn_actor_from_class(unreal.SceneCaptureCube, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    scc.set_actor_label(f"HDRI_ToCube_SCC_Temp_{{TAG}}")
    scc_comp = scc.get_component_by_class(unreal.SceneCaptureComponentCube)
    scc_comp.set_editor_property("texture_target", rt)
    # SCS_SCENE_COLOR_HDR_NO_ALPHA preserves the linear HDR range of the
    # source longlat. The LDR variants would tone-map + clamp to 8-bit
    # SDR, defeating the whole point of capturing HDR for a SkyLight
    # ambient cubemap.
    scc_comp.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_SCENE_COLOR_HDR_NO_ALPHA)
    for p, v in (("capture_every_frame", False), ("capture_on_movement", False)):
        try:
            scc_comp.set_editor_property(p, v)
        except Exception:
            pass
    scc_comp.capture_scene()

    cube_full = f"{{DEST_PKG}}/{{CUBE_NAME}}"
    if el.does_asset_exist(cube_full):
        el.delete_asset(cube_full)
    cube = unreal.RenderingLibrary.render_target_create_static_texture_cube_editor_only(
        rt, CUBE_NAME, COMPRESSION,
    )
    if cube is None:
        raise RuntimeError("cube_create_failed: convert returned None")
    el.save_loaded_asset(cube)
    print(f"CUBE_PATH={{cube.get_path_name()}}")
finally:
    # Best-effort cleanup. Each step is independently guarded so one
    # failure doesn't strand the rest of the temp state.
    if sphere is not None:
        try: els.destroy_actor(sphere)
        except Exception: pass
    if scc is not None:
        try: els.destroy_actor(scc)
        except Exception: pass
    if mat is not None and el.does_asset_exist(mat_path):
        try: el.delete_asset(mat_path)
        except Exception: pass
    if rt is not None and el.does_asset_exist(rt_path):
        try: el.delete_asset(rt_path)
        except Exception: pass
"""


def synthetic_convert_hdri_to_cubemap(req_id, args: dict) -> dict:
    """Convert a longlat-projection HDRI (UTexture2D) into a UTextureCube
    so it can drive a SkyLight's SpecifiedCubemap slot. Uses a
    SceneCaptureCube against an inside-out sphere whose unlit emissive
    material samples the HDRI — the canonical UE editor path with no
    Python wrapper in 5.7 vanilla.

    Args (object):
      - hdri_path (str, required): UE asset path of the source longlat
        UTexture2D (e.g. /Game/Marketplace/HDRI_Venice_Sunset).
      - dest_path (str, optional): UE package path for the new cube.
        Defaults to the source HDRI's folder.
      - dest_name (str, optional): Asset name override. Defaults to
        '<source_basename>_Cube'.
      - cube_size (int, optional, default 1024): Square face size in
        pixels for the render target. Powers of two recommended.
      - compression (str, optional, default 'TC_HDR'): One of
        TC_HDR / TC_HDR_COMPRESSED / TC_HDR_F32 / TC_DEFAULT.

    Returns:
      - ok, cube_asset_path, source_hdri, cube_size, compression
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_arguments: arguments must be an object",
        })
    hdri_path = args.get("hdri_path")
    if not isinstance(hdri_path, str) or not hdri_path.startswith("/Game/"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_field: 'hdri_path' must be a non-empty string starting with /Game/",
        })
    # Default dest = source's folder.
    if "/" in hdri_path:
        default_dest = hdri_path.rsplit("/", 1)[0]
        default_basename = hdri_path.rsplit("/", 1)[1]
    else:
        default_dest = "/Game"
        default_basename = hdri_path
    # Strip any trailing `.AssetName` syntax some callers use.
    default_basename = default_basename.split(".")[0]

    dest_path = args.get("dest_path") or default_dest
    if not isinstance(dest_path, str):
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_field: 'dest_path' must be a string",
        })
    # Tight /Game/ guard — must be exactly "/Game" or start with "/Game/".
    # `/GameFoo`, `/Gameplay/x`, `/Engine/...` etc all fail. Also reject
    # backslashes and traversal segments so a malformed path can't push
    # weird state into UE.
    if dest_path != "/Game" and not dest_path.startswith("/Game/"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_field: 'dest_path' must be '/Game' or start with '/Game/'",
        })
    if "\\" in dest_path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_field: 'dest_path' must not contain '\\\\'",
        })
    for seg in dest_path.split("/"):
        if seg in ("", ".", "..") and dest_path != "/Game":
            # leading slash makes the first segment "", which is fine.
            # Only reject empty/.//.. inside the path proper.
            if not (seg == "" and dest_path.startswith("/")):
                return make_response(req_id, error={
                    "code": -32602,
                    "message": f"convert_hdri_to_cubemap: invalid_field: 'dest_path' must not contain '{seg}' segments",
                })
        if seg in (".", ".."):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"convert_hdri_to_cubemap: invalid_field: 'dest_path' must not contain '{seg}' segments",
            })
    dest_name = args.get("dest_name") or f"{default_basename}_Cube"
    if not isinstance(dest_name, str) or not dest_name:
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_field: 'dest_name' must be a non-empty string",
        })
    # Asset names must not contain path separators / dots / quotes / shell
    # metacharacters that could break our generated UE Python literal.
    for ch in "/\\\"'`\n\r\t;":
        if ch in dest_name:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"convert_hdri_to_cubemap: invalid_field: 'dest_name' must not contain {ch!r}",
            })

    cube_size = args.get("cube_size", 1024)
    if not isinstance(cube_size, int) or cube_size < 16 or cube_size > 8192:
        return make_response(req_id, error={
            "code": -32602,
            "message": "convert_hdri_to_cubemap: invalid_field: 'cube_size' must be an int in [16, 8192]",
        })

    compression = args.get("compression", "TC_HDR")
    if compression not in _HDRI_CUBE_COMPRESSION_ALLOWED:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"convert_hdri_to_cubemap: invalid_field: 'compression' must be one of {sorted(_HDRI_CUBE_COMPRESSION_ALLOWED)}",
        })

    # Phase H engine gate: blocks on UE < 5.0 (EditorAssetLibrary subsystem
    # + render-target cube APIs the inner script uses are 5.0+).
    gate = check_engine_gate(req_id, "convert_hdri_to_cubemap")
    if gate is not None:
        return gate

    # Unique per-call suffix for temp asset names so concurrent invocations
    # don't race + the cleanup never targets pre-existing user content.
    import uuid as _uuid
    tag = _uuid.uuid4().hex[:12]
    code = _render_hdri_to_cubemap_script(hdri_path, dest_path, dest_name, cube_size, compression, tag)
    resp = call_ue("execute_unreal_python", {"code": code, "capture_output": True})
    if "error" in resp:
        upstream = resp.get("error") or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"convert_hdri_to_cubemap: ue_exec_failed: {upstream.get('message') or 'execute_unreal_python returned an error'}",
        })
    result = resp.get("result") or {}
    if not result.get("ok"):
        return make_response(req_id, error={
            "code": -32603,
            "message": f"convert_hdri_to_cubemap: ue_python_error: {result.get('output') or result}",
        })

    body = {
        "ok": True,
        "source_hdri": hdri_path,
        "cube_asset_path": f"{dest_path}/{dest_name}.{dest_name}",
        "dest_path": dest_path,
        "dest_name": dest_name,
        "cube_size": cube_size,
        "compression": compression,
    }
    return _wrap_tool_result(req_id, body)


# ---------------------------------------------------------------------------
# sequencer_add_transform_keyframe (synthetic)
# ---------------------------------------------------------------------------

# Map caller-facing interpolation names to the
# unreal.MovieSceneKeyInterpolation enum member to bake into the generated
# script. The allowlist keeps caller input from injecting arbitrary enum
# names — same defence-in-depth posture as
# `_HDRI_CUBE_COMPRESSION_ALLOWED`.
#
# Note: UE 5.7 exposes AUTO / BREAK / CONSTANT / LINEAR / SMART_AUTO / USER.
# We surface a curated subset that maps to the caller-facing API. "cubic"
# is exposed as an ergonomic alias for SMART_AUTO (the smart-tangent cubic
# spline the sequencer UI labels "Cubic (Smart Auto)").
_SEQUENCER_KEY_INTERPOLATION = {
    "linear": "LINEAR",
    "constant": "CONSTANT",
    "auto": "AUTO",
    "smart_auto": "SMART_AUTO",
    "cubic": "SMART_AUTO",  # alias
}


# Caller convention: location = [x, y, z], rotation = [pitch, yaw, roll],
# scale = [x, y, z]. The sequencer transform channels are laid out as
# Roll=X, Pitch=Y, Yaw=Z (UE's Euler convention inside the Movie Scene
# section). This dict drives the per-axis remap so the caller never has
# to know the internal channel layout.
_SEQUENCER_AXIS_MAP = {
    "location": [("Location.X", 0), ("Location.Y", 1), ("Location.Z", 2)],
    # rotation index 0 = pitch -> channel Rotation.Y
    # rotation index 1 = yaw   -> channel Rotation.Z
    # rotation index 2 = roll  -> channel Rotation.X
    "rotation": [("Rotation.Y", 0), ("Rotation.Z", 1), ("Rotation.X", 2)],
    "scale": [("Scale.X", 0), ("Scale.Y", 1), ("Scale.Z", 2)],
}


def _render_sequencer_add_transform_keyframe_script(
    sequence_path: str,
    binding_id: str,
    time_seconds: float,
    location,            # list[float] | None
    rotation,            # list[float] | None  (caller order: [pitch, yaw, roll])
    scale,               # list[float] | None
    interpolation_member: str,
    auto_extend_section: bool,
) -> str:
    """Return the UE Python script that adds a single 3D Transform Track
    keyframe on the given LevelSequence's binding. Inputs MUST already be
    validated by the caller — this helper does no escaping; numeric inputs
    are coerced through float()/bool() and the string inputs are emitted via
    ``!r`` so any embedded quotes round-trip safely.

    The script:
      1. Loads the LevelSequence; aborts with sequence_not_found /
         not_a_sequence if the load returns None or the wrong class.
      2. Parses ``binding_id`` via ``unreal.GuidLibrary.parse_string_to_guid``
         (returns ``(Guid, success)``); aborts with binding_not_found if the
         parse fails or the resulting binding proxy reports an invalid GUID.
      3. Finds or creates a single ``MovieScene3DTransformTrack`` on the
         binding (extension method on the proxy class).
      4. Finds or creates the section. Optionally extends the section's
         seconds-range to cover the requested time.
      5. Walks ``section.get_all_channels()`` and matches each requested
         component (Location.X/Y/Z, Rotation.X/Y/Z mapped from caller
         [pitch, yaw, roll], Scale.X/Y/Z) by ``channel_name``.
      6. Calls ``add_key`` once per channel with the resolved interpolation
         enum, using TICK_RESOLUTION time-unit so the frame number is
         interpreted at the sequence's actual tick resolution.
      7. Saves the loaded sequence.

    The script writes a single marker line ``SEQ_KEYFRAME_OK::<json>``
    before returning so the bridge can extract structured results from the
    captured stdout if it wants to in a future revision; for now the body
    is composed from the caller-side validated inputs.
    """
    # Build the per-component payloads as Python literal lists. Each entry
    # is a (channel_name, value) tuple; the inner script then iterates over
    # the concatenated list. Skipping a component => empty list => zero
    # keys added for that component, no enum branching at runtime.
    def _component_pairs(comp_name, values):
        if values is None:
            return []
        return [(ch, float(values[idx])) for ch, idx in _SEQUENCER_AXIS_MAP[comp_name]]

    loc_pairs = _component_pairs("location", location)
    rot_pairs = _component_pairs("rotation", rotation)
    scl_pairs = _component_pairs("scale", scale)
    all_pairs = loc_pairs + rot_pairs + scl_pairs

    return f"""\
import unreal
import json as _json

SEQ_PATH = {sequence_path!r}
BINDING_ID = {binding_id!r}
T_SECONDS = {float(time_seconds)!r}
PAIRS = {all_pairs!r}
INTERP = unreal.MovieSceneKeyInterpolation.{interpolation_member}
AUTO_EXTEND = {bool(auto_extend_section)!r}

el = unreal.EditorAssetLibrary

try:
    seq = el.load_asset(SEQ_PATH)
    if seq is None:
        raise RuntimeError(f"sequence_not_found: {{SEQ_PATH}}")
    if not isinstance(seq, unreal.LevelSequence):
        raise RuntimeError(f"not_a_sequence: {{SEQ_PATH}}")

    parsed_guid, ok = unreal.GuidLibrary.parse_string_to_guid(BINDING_ID)
    if not ok or not unreal.GuidLibrary.is_valid_guid(parsed_guid):
        raise RuntimeError(f"binding_not_found: {{BINDING_ID}}")
    binding = unreal.MovieSceneSequenceExtensions.find_binding_by_id(seq, parsed_guid)
    # An unmatched GUID still returns a proxy struct, but its inner
    # binding_id stays default (all-zero). Re-validate against the proxy's
    # own get_id() — a stale/garbage GUID surfaces as an invalid Guid here.
    if not unreal.GuidLibrary.is_valid_guid(binding.get_id()):
        raise RuntimeError(f"binding_not_found: {{BINDING_ID}}")

    # Get or create the 3D Transform Track on the binding.
    tracks = binding.find_tracks_by_exact_type(unreal.MovieScene3DTransformTrack)
    if tracks:
        track = tracks[0]
    else:
        track = binding.add_track(unreal.MovieScene3DTransformTrack)

    sections = track.get_sections()
    if sections:
        section = sections[0]
    else:
        section = track.add_section()

    if AUTO_EXTEND:
        try:
            cur_start = section.get_start_frame_seconds()
            cur_end = section.get_end_frame_seconds()
            new_start = min(cur_start, T_SECONDS)
            new_end = max(cur_end, T_SECONDS)
            if new_start != cur_start or new_end != cur_end:
                section.set_range_seconds(new_start, new_end)
        except Exception:
            # If a brand-new section has no resolvable range, just set it
            # to a one-second window centred on the keyframe time. The
            # outer try/except RuntimeError still catches anything fatal.
            section.set_range_seconds(min(0.0, T_SECONDS), max(T_SECONDS + 1.0, T_SECONDS))

    tick_rate = unreal.MovieSceneSequenceExtensions.get_tick_resolution(seq)
    frame_no = unreal.FrameNumber(int(round(T_SECONDS * tick_rate.numerator / tick_rate.denominator)))

    all_channels = section.get_all_channels()
    by_name = {{}}
    for ch in all_channels:
        by_name[str(ch.channel_name)] = ch

    keys_added = 0
    channels_keyed = []
    for ch_name, value in PAIRS:
        ch = by_name.get(ch_name)
        if ch is None:
            raise RuntimeError(f"channel_missing: {{ch_name}} (section returned no channel by that name)")
        ch.add_key(frame_no, value, 0.0, unreal.MovieSceneTimeUnit.TICK_RESOLUTION, INTERP)
        keys_added += 1
        channels_keyed.append(ch_name)

    el.save_loaded_asset(seq)
    # Emit success marker via unreal.log so it lands in the LogPython
    # ring buffer (which get_log_lines reads), not the python-evaluator's
    # CommandResult stdout — UE 5.7's evaluator doesn't reliably flush
    # captured stdout into CommandResult on every execute path.
    # The __END__ sentinel disambiguates the marker line during scrape.
    unreal.log("SEQ_KEYFRAME_OK::" + _json.dumps({{
        "keys_added": keys_added,
        "channels_keyed": channels_keyed,
        "track_path": track.get_path_name(),
    }}) + "__END__")
except Exception as _e:
    raise RuntimeError(str(_e))
"""


def synthetic_sequencer_add_transform_keyframe(req_id, args: dict) -> dict:
    """Add a single keyframe on a Level Sequence's 3D Transform Track.

    Closes the keyframe-authoring half of the 21st HANDOFF note's
    Sequencer parked item: the bridge already exposes
    `create_sequence` + `bind_actor_to_sequence`, but had no way to set
    keys on a binding's transform track from the Python side. UE 5.7's
    Movie Scene scripting surface attaches `find_tracks_by_exact_type` /
    `add_track` directly on `MovieSceneBindingProxy`; the
    `MovieSceneSequenceExtensions.find_binding_by_id(seq, guid)` helper
    resolves a binding by string GUID, and
    `MovieSceneScriptingDoubleChannel.add_key(frame, value, sub, unit,
    interp)` lets us write each component channel directly.

    Args (object):
      - sequence_path (str, required): UE asset path of the LevelSequence;
        must start with /Game/.
      - binding_id (str, required): GUID string returned by
        `bind_actor_to_sequence`. Accepts the bare 32-hex form (no
        dashes) UE produces by default; the dashed form parses too —
        the inner script delegates to GuidLibrary.parse_string_to_guid
        which is tolerant of both.
      - time_seconds (number, required, >= 0): time in seconds (display
        rate) at which to place the keyframe. Internally converted to a
        tick-resolution FrameNumber using the sequence's own
        tick-resolution.
      - location (list[3] of numbers, optional): [x, y, z]. Omit to skip
        Location.X / Y / Z.
      - rotation (list[3] of numbers, optional): [pitch, yaw, roll] in
        degrees — matches `unreal.Rotator`'s constructor convention.
        Internally remapped to channel layout
        (Roll -> Rotation.X, Pitch -> Rotation.Y, Yaw -> Rotation.Z).
        Omit to skip Rotation.X / Y / Z.
      - scale (list[3] of numbers, optional): [x, y, z]. Omit to skip
        Scale.X / Y / Z.
      - interpolation (string, optional, default 'linear'): one of
        'linear' / 'constant' / 'auto' / 'smart_auto' / 'cubic'. Maps to
        MovieSceneKeyInterpolation enum; 'cubic' is an alias for
        SMART_AUTO.
      - auto_extend_section (bool, optional, default true): if the
        section's seconds-range doesn't already cover time_seconds, the
        script extends it. Disable for callers that pre-size their
        sections and want a hard error on out-of-range keys.

    At least one of `location` / `rotation` / `scale` must be present —
    callers passing none get a `nothing_to_key` invalid_arguments error
    rather than a no-op round trip into UE.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_arguments: arguments must be an object",
        })

    sequence_path = args.get("sequence_path")
    if not isinstance(sequence_path, str) or not sequence_path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'sequence_path' must be a non-empty string starting with /Game/",
        })
    # Bare `/Game` is the root content folder, not an asset path. This tool
    # requires an actual LevelSequence asset path, so reject the folder up
    # front and keep the error in -32602 validation territory rather than
    # falling through to a UE-side `sequence_not_found`.
    if not sequence_path.startswith("/Game/"):
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'sequence_path' must start with '/Game/' and name a LevelSequence asset",
        })
    if "\\" in sequence_path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'sequence_path' must not contain '\\\\'",
        })
    for seg in sequence_path.split("/"):
        if seg in (".", ".."):
            return make_response(req_id, error={
                "code": -32602,
                "message": f"sequencer_add_transform_keyframe: invalid_field: 'sequence_path' must not contain '{seg}' segments",
            })

    binding_id = args.get("binding_id")
    if not isinstance(binding_id, str) or not binding_id.strip():
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'binding_id' must be a non-empty string",
        })
    # The GUID string is passed straight into the generated UE Python as a
    # repr()d literal, but defence-in-depth: only allow hex + dashes +
    # braces (UE's FGuid::ToString() variants all stay inside that set).
    # Anything else means we're not looking at a GUID anyway and the
    # caller benefits from a fast-fail.
    binding_id_stripped = binding_id.strip()
    if not all(c in "0123456789abcdefABCDEF-{}" for c in binding_id_stripped):
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'binding_id' must be a hex GUID (with optional dashes/braces)",
        })

    t_raw = args.get("time_seconds")
    if not isinstance(t_raw, (int, float)) or isinstance(t_raw, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'time_seconds' must be a number",
        })
    time_seconds = float(t_raw)
    if time_seconds < 0.0:
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'time_seconds' must be >= 0",
        })

    def _validate_triple(name, value):
        if value is None:
            return None, None
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return None, f"sequencer_add_transform_keyframe: invalid_field: '{name}' must be a 3-element list of numbers"
        coerced = []
        for v in value:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return None, f"sequencer_add_transform_keyframe: invalid_field: '{name}' entries must be numbers"
            coerced.append(float(v))
        return coerced, None

    location, err = _validate_triple("location", args.get("location"))
    if err:
        return make_response(req_id, error={"code": -32602, "message": err})
    rotation, err = _validate_triple("rotation", args.get("rotation"))
    if err:
        return make_response(req_id, error={"code": -32602, "message": err})
    scale, err = _validate_triple("scale", args.get("scale"))
    if err:
        return make_response(req_id, error={"code": -32602, "message": err})

    if location is None and rotation is None and scale is None:
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: nothing_to_key: at least one of 'location' / 'rotation' / 'scale' must be supplied",
        })

    interpolation_raw = args.get("interpolation", "linear")
    if not isinstance(interpolation_raw, str):
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'interpolation' must be a string",
        })
    interpolation_key = interpolation_raw.strip().lower()
    if interpolation_key not in _SEQUENCER_KEY_INTERPOLATION:
        return make_response(req_id, error={
            "code": -32602,
            "message": f"sequencer_add_transform_keyframe: invalid_field: 'interpolation' must be one of {sorted(_SEQUENCER_KEY_INTERPOLATION.keys())}",
        })
    interpolation_member = _SEQUENCER_KEY_INTERPOLATION[interpolation_key]

    auto_extend_section = args.get("auto_extend_section", True)
    if not isinstance(auto_extend_section, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "sequencer_add_transform_keyframe: invalid_field: 'auto_extend_section' must be a bool",
        })

    # Phase H engine gate: blocks on UE < 5.0 (unreal.MovieSceneTimeUnit and
    # the MovieScene scripting channel APIs the inner script uses are 5.0+).
    gate = check_engine_gate(req_id, "sequencer_add_transform_keyframe")
    if gate is not None:
        return gate

    code = _render_sequencer_add_transform_keyframe_script(
        sequence_path,
        binding_id_stripped,
        time_seconds,
        location,
        rotation,
        scale,
        interpolation_member,
        auto_extend_section,
    )

    resp = call_ue("execute_unreal_python", {"code": code, "capture_output": True})
    if "error" in resp:
        upstream = resp.get("error") or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"sequencer_add_transform_keyframe: ue_exec_failed: {upstream.get('message') or 'execute_unreal_python returned an error'}",
        })
    result = resp.get("result") or {}
    if not result.get("ok"):
        inner = result.get("output") or result
        inner_text = str(inner)
        # Surface the structured inner-script raises (sequence_not_found,
        # not_a_sequence, binding_not_found, channel_missing) as
        # ue_python_error so MCP clients can pattern-match on the inner
        # code. The inner script raises RuntimeError(str(e)); the captured
        # traceback string contains "RuntimeError: <code>: ...".
        return make_response(req_id, error={
            "code": -32603,
            "message": f"sequencer_add_transform_keyframe: ue_python_error: {inner_text}",
        })

    # Compose the result body from caller-validated inputs + the per-axis
    # remap so the caller doesn't have to know the internal channel layout.
    channels_keyed = []
    if location is not None:
        channels_keyed.extend(ch for ch, _ in _SEQUENCER_AXIS_MAP["location"])
    if rotation is not None:
        channels_keyed.extend(ch for ch, _ in _SEQUENCER_AXIS_MAP["rotation"])
    if scale is not None:
        channels_keyed.extend(ch for ch, _ in _SEQUENCER_AXIS_MAP["scale"])

    # Pull the success marker from the LogPython ring buffer. UE 5.7's
    # Python evaluator does not reliably flush captured stdout into
    # Cmd.CommandResult (`result["output"]`) on every execute path, so
    # the rendered script writes the marker via unreal.log instead. The
    # __END__ sentinel disambiguates the marker line during scrape.
    track_path = None
    log_resp = call_ue("get_log_lines", {
        "count": 64,
        "category_filter": "LogPython",
        "min_verbosity": "Log",
    })
    log_result = log_resp.get("result") if isinstance(log_resp, dict) else None
    log_lines = (log_result or {}).get("lines") or []
    # Walk newest-last -> scan in reverse so the most recent marker wins.
    for entry in reversed(log_lines):
        msg = entry.get("message", "") if isinstance(entry, dict) else ""
        idx = msg.find("SEQ_KEYFRAME_OK::")
        end = msg.find("__END__", idx) if idx >= 0 else -1
        if idx >= 0 and end > idx:
            try:
                payload = json.loads(msg[idx + len("SEQ_KEYFRAME_OK::"):end])
                track_path = payload.get("track_path")
            except Exception:
                pass
            break
    # Fallback: legacy stdout scrape so tests that mock only call_ue
    # for execute_unreal_python keep passing.
    if track_path is None:
        output = (result.get("output") or "") if isinstance(result, dict) else ""
        for line in output.splitlines():
            idx = line.find("SEQ_KEYFRAME_OK::")
            end = line.find("__END__", idx) if idx >= 0 else -1
            if idx >= 0 and end > idx:
                try:
                    payload = json.loads(line[idx + len("SEQ_KEYFRAME_OK::"):end])
                    track_path = payload.get("track_path")
                except Exception:
                    pass
                break

    body = {
        "ok": True,
        "sequence_path": sequence_path,
        "binding_id": binding_id_stripped,
        "time_seconds": time_seconds,
        "keys_added": len(channels_keyed),
        "channels_keyed": channels_keyed,
        "interpolation": interpolation_member,
        "track_path": track_path,
    }
    return _wrap_tool_result(req_id, body)


# ---------------------------------------------------------------------------
# Phase H -- engine-version gating for synthetic tools (docs/PHASE-H-COMPAT.md)
#
# A handful of bridge-side synthetic tools call `unreal.*` Python APIs that
# only exist on engine 5.0-and-newer (`unreal.get_editor_subsystem`,
# `unreal.EditorActorSubsystem`, `unreal.MovieSceneTimeUnit`). On the 4.27
# line the embedded interpreter raises a raw AttributeError deep inside the
# marker pattern, surfacing as an opaque -32603. The catalog now carries
# `min_engine_version` on those tools (mirrored in mcp_manifest.json); the
# gate below converts "engine too old" into a structured, caller-actionable
# error BEFORE the doomed UE round-trip.
#
# Fail-open contract: if the connected editor's version is genuinely
# undeterminable (UE down, handler missing, unparseable), we PROCEED rather
# than hard-block -- the underlying tool will fail for its own reason with
# its own envelope. We only block when the version is known AND too low.
# ---------------------------------------------------------------------------

# Cached (major, minor) of the connected editor, discovered once via the
# native `get_engine_version` handler. `_ENGINE_VERSION_UNSET` (a distinct
# sentinel) means "not yet looked up"; a cached `None` means "lookup ran but
# was undeterminable -> fail open". Both a successful tuple AND an
# undeterminable `None` are memoised, so repeated gated calls cost exactly
# one round-trip total regardless of outcome.
_ENGINE_VERSION_UNSET = object()
_ENGINE_VERSION_CACHE: tuple[int, int] | None | object = _ENGINE_VERSION_UNSET


def _parse_engine_minor(version: str) -> tuple[int, int] | None:
    """Parse a 'MAJOR.MINOR[.PATCH]' string into an (int, int) tuple.

    Returns None when the string is missing/garbled so callers can fail open.
    """
    if not isinstance(version, str):
        return None
    parts = version.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except (ValueError, TypeError):
        return None


def _get_connected_engine_version() -> tuple[int, int] | None:
    """Discover the connected editor's (major, minor) via the native
    `get_engine_version` handler, memoised in `_ENGINE_VERSION_CACHE`.

    Reuses the same `call_ue` plumbing every synthetic already uses -- the
    handler emits `minor_dotted` (e.g. "5.7") plus separate major/minor
    integer fields. We prefer the integer fields and fall back to parsing
    `minor_dotted`. Any transport error / missing field -> None (fail open).
    No new UE round-trip type is introduced; this is the existing
    get_engine_version handler the catalog already documents.
    """
    global _ENGINE_VERSION_CACHE
    if _ENGINE_VERSION_CACHE is not _ENGINE_VERSION_UNSET:
        return _ENGINE_VERSION_CACHE

    def _memoise(value: tuple[int, int] | None) -> tuple[int, int] | None:
        # Always write the looked-up result -- including `None`
        # (undeterminable) -- so the lookup happens exactly once. A cached
        # `None` still means "fail open / proceed".
        global _ENGINE_VERSION_CACHE
        _ENGINE_VERSION_CACHE = value
        return value

    resp = call_ue("get_engine_version", {})
    if not isinstance(resp, dict) or "error" in resp:
        return _memoise(None)
    result = resp.get("result")
    if not isinstance(result, dict):
        return _memoise(None)

    major = result.get("major")
    minor = result.get("minor")
    if isinstance(major, (int, float)) and isinstance(minor, (int, float)) \
            and not isinstance(major, bool) and not isinstance(minor, bool):
        return _memoise((int(major), int(minor)))

    parsed = _parse_engine_minor(result.get("minor_dotted") or result.get("full") or "")
    return _memoise(parsed)


def _tool_catalog_entry(tool_name: str) -> dict | None:
    """Look up a tool's static catalog entry in TOOLS by name."""
    for t in TOOLS:
        if t.get("name") == tool_name:
            return t
    return None


def check_engine_gate(req_id, tool_name: str) -> dict | None:
    """Engine-version preflight for a synthetic tool.

    Returns None when the tool is allowed to run (no min_engine_version,
    version unknown -> fail open, or connected engine new enough). Returns a
    fully-formed structured-error response envelope when the connected engine
    is KNOWN and below the tool's declared `min_engine_version`.

    Structured-error shape (carried inside the JSON-RPC `error` object so it
    rides the bridge's existing flat error-envelope path verbatim -- `code`
    is an integer per JSON-RPC 2.0, with the string identifier in a sibling
    `error_code` field):

        {"code": -32001,
         "error_code": "unsupported_on_engine_version",
         "message": "<tool>: ...",
         "tool": "<tool>",
         "min_engine_version": "5.0",
         "engine_version": "4.27"}
    """
    entry = _tool_catalog_entry(tool_name)
    if entry is None:
        return None
    min_ver = entry.get("min_engine_version")
    if not min_ver:
        return None

    required = _parse_engine_minor(min_ver)
    if required is None:
        # Catalog metadata itself unparseable -- don't punish the caller.
        return None

    actual = _get_connected_engine_version()
    if actual is None:
        # Version undeterminable -> fail OPEN (proceed). The underlying tool
        # will surface its own error if the API genuinely isn't there.
        return None

    if actual >= required:
        return None

    actual_str = f"{actual[0]}.{actual[1]}"
    return make_response(req_id, error={
        "code": -32001,
        "error_code": "unsupported_on_engine_version",
        "message": (
            f"{tool_name}: unsupported_on_engine_version: requires Unreal "
            f"Engine {min_ver}+ (uses a {min_ver}+ unreal.* Python API); "
            f"connected editor is {actual_str}. See docs/PHASE-H-COMPAT.md."
        ),
        "tool": tool_name,
        "min_engine_version": min_ver,
        "engine_version": actual_str,
    })


_IMPORT_MESH_EXTS = (".glb", ".gltf", ".fbx", ".obj")
_IMPORT_MESH_MARKER = "IMPORT_MESH_RESULT:"


def _build_import_mesh_script(source_path: str, dest_path: str, import_materials: bool) -> str:
    """Generate the UE Python that imports `source_path` into `dest_path` via
    Interchange and prints a marker line with the created StaticMesh asset
    paths. Paths are baked via repr() so they cannot break the literal or
    inject code. Diffs the destination folder before/after so the reported
    paths reflect Interchange's actual (possibly nested) output locations.
    """
    return (
        "import unreal, json\n"
        "DEST = " + repr(dest_path) + "\n"
        "SRC = " + repr(source_path) + "\n"
        "WANT_MATS = " + ("True" if import_materials else "False") + "\n"
        # list_assets raises on a non-existent directory; guard both diffs so a
        # failed import (dir never created) doesn't mask the real error.
        "def _list(d):\n"
        "    return set(unreal.EditorAssetLibrary.list_assets(d, recursive=True, include_folder=False)) if unreal.EditorAssetLibrary.does_directory_exist(d) else set()\n"
        # asset-registry class lookup (no load_asset -> no full asset load into memory)\n"
        "def _cls(a):\n"
        "    try:\n"
        "        return str(unreal.EditorAssetLibrary.find_asset_data(a).asset_class_path.asset_name)\n"
        "    except Exception:\n"
        "        return ''\n"
        "before = _list(DEST)\n"
        "mgr = unreal.InterchangeManager.get_interchange_manager_scripted()\n"
        "src = mgr.create_source_data(SRC)\n"
        "p = unreal.ImportAssetParameters()\n"
        "p.is_automated = True\n"
        "try:\n"
        "    p.import_level = False\n"
        "except Exception:\n"
        "    pass\n"
        "mgr.import_asset(DEST, src, p)\n"
        "after = _list(DEST)\n"
        "new = sorted(after - before)\n"
        # honor import_materials=False: drop the material assets Interchange made
        # (meshes fall back to the engine default material). EditorAssetLibrary
        # .delete_asset is the scripting API -- it does not raise a modal.\n"
        "if not WANT_MATS:\n"
        "    for a in list(new):\n"
        "        if _cls(a) in ('Material', 'MaterialInstanceConstant'):\n"
        "            unreal.EditorAssetLibrary.delete_asset(a)\n"
        "    after = _list(DEST); new = sorted(after - before)\n"
        "sms = [a for a in new if _cls(a) == 'StaticMesh']\n"
        "print(" + repr(_IMPORT_MESH_MARKER) + " + json.dumps({'static_meshes': sms, 'created': new, 'materials_imported': bool(WANT_MATS)}))\n"
    )


def synthetic_import_mesh(req_id, args: dict) -> dict:
    """Import a mesh file from disk into the project as StaticMesh asset(s),
    returning the exact created asset paths.

    Args (object):
      - source_path (str, required): absolute filesystem path to the mesh
        (.glb/.gltf/.fbx/.obj).
      - dest_path (str, required): '/Game' or a '/Game/...' content path.
      - import_materials (bool, optional, default True).

    Returns: ok, source_path, dest_path, static_meshes[], created[], count.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "import_mesh: invalid_arguments: arguments must be an object",
        })
    source_path = args.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        return make_response(req_id, error={
            "code": -32602,
            "message": "import_mesh: invalid_field: 'source_path' must be a non-empty string",
        })
    if not source_path.lower().endswith(_IMPORT_MESH_EXTS):
        return make_response(req_id, error={
            "code": -32602,
            "message": f"import_mesh: invalid_field: 'source_path' must end with one of {list(_IMPORT_MESH_EXTS)}",
        })
    if not os.path.isfile(source_path):
        return make_response(req_id, error={
            "code": -32602,
            "message": f"import_mesh: file_not_found: no file at source_path {source_path!r}",
        })
    dest_path = args.get("dest_path")
    if not isinstance(dest_path, str) or (dest_path != "/Game" and not dest_path.startswith("/Game/")):
        return make_response(req_id, error={
            "code": -32602,
            "message": "import_mesh: invalid_field: 'dest_path' must be '/Game' or start with '/Game/'",
        })
    if "\\" in dest_path or any(seg in (".", "..") for seg in dest_path.split("/")):
        return make_response(req_id, error={
            "code": -32602,
            "message": "import_mesh: invalid_field: 'dest_path' must not contain '\\\\' or '.'/'..' segments",
        })
    import_materials = args.get("import_materials", True)
    if not isinstance(import_materials, bool):
        return make_response(req_id, error={
            "code": -32602,
            "message": "import_mesh: invalid_field: 'import_materials' must be a boolean",
        })

    gate = check_engine_gate(req_id, "import_mesh")
    if gate is not None:
        return gate

    code = _build_import_mesh_script(source_path, dest_path, import_materials)
    resp = call_ue("execute_unreal_python", {"code": code, "capture_output": True})
    if "error" in resp:
        upstream = resp.get("error") or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"import_mesh: ue_exec_failed: {upstream.get('message') or 'execute_unreal_python returned an error'}",
        })
    result = resp.get("result") or {}
    if not result.get("ok"):
        return make_response(req_id, error={
            "code": -32603,
            "message": f"import_mesh: ue_python_error: {result.get('output') or result}",
        })
    # Parse the marker line out of stdout/output.
    blob = ""
    for key in ("stdout", "output"):
        v = result.get(key)
        if isinstance(v, str) and _IMPORT_MESH_MARKER in v:
            blob = v
            break
    # Fail closed: the inner script always prints the marker on success, so a
    # missing marker (or unparseable payload) means the import did not complete
    # as expected -- surface an error instead of a hollow ok with empty lists.
    if not blob:
        snippet = ""
        for key in ("stdout", "output"):
            v = result.get(key)
            if isinstance(v, str) and v:
                snippet = v[:300]
                break
        return make_response(req_id, error={
            "code": -32603,
            "message": f"import_mesh: missing_result_marker: import ran but emitted no result marker (output: {snippet!r})",
        })
    line = blob.split(_IMPORT_MESH_MARKER, 1)[1].splitlines()[0].strip()
    try:
        parsed = json.loads(line)
    except (ValueError, TypeError):
        return make_response(req_id, error={
            "code": -32603,
            "message": f"import_mesh: bad_result_marker: could not parse result payload {line[:200]!r}",
        })
    body = {
        "ok": True,
        "source_path": source_path,
        "dest_path": dest_path,
        "import_materials": import_materials,
        "static_meshes": parsed.get("static_meshes", []),
        "created": parsed.get("created", []),
        "materials_imported": parsed.get("materials_imported", import_materials),
        "count": len(parsed.get("static_meshes", [])),
    }
    return _wrap_tool_result(req_id, body)


_MATREMAP_MARKER = "MATERIAL_REMAP_RESULT:"
# slot -> (sampler-type enum member name, MaterialProperty enum member name, output pin)
_MATREMAP_SLOTS = {
    "base_color": ("SAMPLERTYPE_COLOR", "MP_BASE_COLOR", "RGB"),
    "normal": ("SAMPLERTYPE_NORMAL", "MP_NORMAL", "RGB"),
    "roughness": ("SAMPLERTYPE_GRAYSCALE", "MP_ROUGHNESS", "R"),
    "metallic": ("SAMPLERTYPE_GRAYSCALE", "MP_METALLIC", "R"),
    "ambient_occlusion": ("SAMPLERTYPE_GRAYSCALE", "MP_AMBIENT_OCCLUSION", "R"),
}


def _build_material_remap_script(actor_label: str, textures: dict, dest_material: str, tiling: float) -> str:
    """Generate the UE Python that builds a PBR Material from `textures`, assigns it
    to the named actor's StaticMesh slots, and prints a marker line with the result.
    All caller values are baked via repr() (injection-safe). Only recognized slots are
    emitted, each with its correct sampler type + output pin."""
    pkg = dest_material.rsplit("/", 1)[0] if "/" in dest_material else "/Game"
    name = dest_material.rsplit("/", 1)[1] if "/" in dest_material else dest_material
    # emit only recognized slots that were supplied
    slot_lines = []
    for slot, (st, prop, out) in _MATREMAP_SLOTS.items():
        if slot in textures:
            slot_lines.append((textures[slot], st, prop, out))
    # actor-first: resolve the target actor BEFORE creating/deleting any material asset,
    # so a missing/typo'd actor_label never mutates or destroys dest_material (cubic P1).
    head = (
        "import unreal, json\n"
        "ACTOR = " + repr(actor_label) + "\n"
        "MATPKG = " + repr(pkg) + "\n"
        "MATNAME = " + repr(name) + "\n"
        "MATPATH = MATPKG + '/' + MATNAME\n"
        "TILING = " + repr(float(tiling)) + "\n"
        "MARKER = " + repr(_MATREMAP_MARKER) + "\n"
        "mel = unreal.MaterialEditingLibrary\n"
        "eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)\n"
        "target = None\n"
        "for _a in eas.get_all_level_actors():\n"
        "    if _a.get_actor_label() == ACTOR:\n"
        "        target = _a; break\n"
        "if target is None:\n"
        "    print(MARKER + json.dumps({'material_path': MATPATH, 'actor_found': False, 'slots_assigned': 0}))\n"
        "else:\n"
        "    for _p in " + repr([t[0] for t in slot_lines]) + ":\n"
        "        if not unreal.EditorAssetLibrary.does_asset_exist(_p):\n"
        "            raise RuntimeError('texture_not_found: ' + _p)\n"
        "    if unreal.EditorAssetLibrary.does_asset_exist(MATPATH):\n"
        "        unreal.EditorAssetLibrary.delete_asset(MATPATH)\n"
        "    m = unreal.AssetToolsHelpers.get_asset_tools().create_asset(MATNAME, MATPKG, unreal.Material, unreal.MaterialFactoryNew())\n"
        "    if m is None:\n"
        "        raise RuntimeError('material_create_failed: ' + MATPATH)\n"
        "    tc = mel.create_material_expression(m, unreal.MaterialExpressionTextureCoordinate, -900, 0)\n"
        "    tc.set_editor_property('u_tiling', TILING); tc.set_editor_property('v_tiling', TILING)\n"
        "    _y = -300\n"
    )
    mid = ""
    for path, st, prop, out in slot_lines:
        mid += (
            "    _y += 300\n"
            "    _s = mel.create_material_expression(m, unreal.MaterialExpressionTextureSample, -500, _y)\n"
            "    _s.set_editor_property('texture', unreal.EditorAssetLibrary.load_asset(" + repr(path) + "))\n"
            "    _s.set_editor_property('sampler_type', unreal.MaterialSamplerType." + st + ")\n"
            "    mel.connect_material_expressions(tc, '', _s, 'UVs')\n"
            "    mel.connect_material_property(_s, " + repr(out) + ", unreal.MaterialProperty." + prop + ")\n"
        )
    tail = (
        "    mel.recompile_material(m)\n"
        "    unreal.EditorAssetLibrary.save_asset(MATPATH)\n"
        "    smc = target.get_component_by_class(unreal.StaticMeshComponent)\n"
        "    slots = 0\n"
        "    if smc:\n"
        "        slots = smc.get_num_materials()\n"
        "        for _i in range(slots):\n"
        "            smc.set_material(_i, m)\n"
        "    print(MARKER + json.dumps({'material_path': MATPATH, 'actor_found': True, 'slots_assigned': slots}))\n"
    )
    return head + mid + tail


def synthetic_material_auto_remap(req_id, args: dict) -> dict:
    """Build a PBR material from a texture set and assign it to a level actor.

    Args (object): actor_label (str, required); textures (object slot->/Game/ path,
    base_color required); dest_material (str, optional /Game/ path); tiling (number,
    optional default 1.0). Returns: ok, material_path, actor_label, actor_found,
    slots_assigned, slots_used.
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_arguments: arguments must be an object"})
    actor_label = args.get("actor_label")
    if not isinstance(actor_label, str) or not actor_label:
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_field: 'actor_label' must be a non-empty string"})
    textures = args.get("textures")
    if not isinstance(textures, dict) or not textures:
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_field: 'textures' must be a non-empty object"})
    recognized = {k: v for k, v in textures.items() if k in _MATREMAP_SLOTS}
    if "base_color" not in recognized:
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_field: 'textures' must include a 'base_color' entry"})
    for slot, path in recognized.items():
        if not isinstance(path, str) or not path.startswith("/Game/"):
            return make_response(req_id, error={
                "code": -32602, "message": f"material_auto_remap: invalid_field: textures['{slot}'] must be a string starting with /Game/"})
        if "\\" in path or any(seg in (".", "..") for seg in path.split("/")):
            return make_response(req_id, error={
                "code": -32602, "message": f"material_auto_remap: invalid_field: textures['{slot}'] must not contain '\\\\' or '.'/'..' segments"})
    dest_material = args.get("dest_material")
    if dest_material is None:
        safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in actor_label)
        dest_material = f"/Game/AutoMaterials/M_{safe}"
    if not isinstance(dest_material, str) or not dest_material.startswith("/Game/"):
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_field: 'dest_material' must be a string starting with /Game/"})
    if "\\" in dest_material or any(seg in (".", "..") for seg in dest_material.split("/")):
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_field: 'dest_material' must not contain '\\\\' or '.'/'..' segments"})
    tiling = args.get("tiling", 1.0)
    if not isinstance(tiling, (int, float)) or isinstance(tiling, bool) or not math.isfinite(tiling) or tiling <= 0:
        return make_response(req_id, error={
            "code": -32602, "message": "material_auto_remap: invalid_field: 'tiling' must be a finite number > 0"})

    gate = check_engine_gate(req_id, "material_auto_remap")
    if gate is not None:
        return gate

    code = _build_material_remap_script(actor_label, recognized, dest_material, tiling)
    resp = call_ue("execute_unreal_python", {"code": code, "capture_output": True})
    if "error" in resp:
        upstream = resp.get("error") or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"material_auto_remap: ue_exec_failed: {upstream.get('message') or 'execute_unreal_python returned an error'}"})
    result = resp.get("result") or {}
    if not result.get("ok"):
        return make_response(req_id, error={
            "code": -32603, "message": f"material_auto_remap: ue_python_error: {result.get('output') or result}"})
    blob = ""
    for key in ("stdout", "output"):
        v = result.get(key)
        if isinstance(v, str) and _MATREMAP_MARKER in v:
            blob = v
            break
    if not blob:
        snippet = ""
        for key in ("stdout", "output"):
            v = result.get(key)
            if isinstance(v, str) and v:
                snippet = v[:300]
                break
        return make_response(req_id, error={
            "code": -32603,
            "message": f"material_auto_remap: missing_result_marker: ran but emitted no result marker (output: {snippet!r})"})
    line = blob.split(_MATREMAP_MARKER, 1)[1].splitlines()[0].strip()
    try:
        parsed = json.loads(line)
    except (ValueError, TypeError):
        return make_response(req_id, error={
            "code": -32603, "message": f"material_auto_remap: bad_result_marker: could not parse {line[:200]!r}"})
    body = {
        "ok": True,
        "actor_label": actor_label,
        "material_path": parsed.get("material_path", dest_material),
        "actor_found": parsed.get("actor_found", False),
        "slots_assigned": parsed.get("slots_assigned", 0),
        "slots_used": sorted(recognized.keys()),
    }
    return _wrap_tool_result(req_id, body)


def synthetic_batch_capture_cameras(req_id, args: dict) -> dict:
    """Render every CineCamera in the level to disk in one call.

    Composition (bridge-side, no dedicated C++ handler):
      1. call_ue("get_actors_in_level", {}) -- one round-trip to enumerate
         every actor. Filter to CineCameraActor client-side (matching the
         find_actors_by_class needle logic). When the caller supplies
         `camera_names`, intersect with that list so only the requested
         cameras render.
      2. For each surviving camera, call_ue("render_camera_to_png",
         {out_path, camera_label, [width, height]}) -- the native handler
         renders an off-screen SceneCapture2D from that actor's transform.

    Synthetic rather than C++ because it is pure protocol-level composition
    over get_actors_in_level + render_camera_to_png; a C++ handler would
    only duplicate logic the bridge already has.

    Args (object):
      output_dir (str, required) -- directory the .png files are written to.
      camera_names (list[str], optional) -- restrict to these camera labels;
        default = every ACineCameraActor found.
      resolution {width, height} (object, optional) -- per-render pixel size;
        when omitted the native handler uses the live viewport resolution.
      file_name_format (str, optional, default "{camera}.png") -- output file
        name template; "{camera}" is replaced with the (sanitized) label.

    Returns: ok, output_dir, total, succeeded, results[{camera, ok,
      out_path, error?}].
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_capture_cameras: invalid_arguments: arguments must be an object",
        })

    output_dir = args.get("output_dir")
    if not isinstance(output_dir, str) or not output_dir:
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_capture_cameras: missing_required_field: 'output_dir' must be a non-empty string",
        })
    output_dir = os.path.normpath(output_dir)

    camera_names = args.get("camera_names")
    if camera_names is not None:
        if not isinstance(camera_names, list) or not all(
            isinstance(n, str) and n for n in camera_names
        ):
            return make_response(req_id, error={
                "code": -32602,
                "message": "batch_capture_cameras: invalid_field: 'camera_names' must be a list of non-empty strings when supplied",
            })

    file_name_format = args.get("file_name_format", "{camera}.png")
    if not isinstance(file_name_format, str) or "{camera}" not in file_name_format:
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_capture_cameras: invalid_field: 'file_name_format' must be a string containing '{camera}'",
        })

    width = height = None
    resolution = args.get("resolution")
    if resolution is not None:
        if not isinstance(resolution, dict):
            return make_response(req_id, error={
                "code": -32602,
                "message": "batch_capture_cameras: invalid_field: 'resolution' must be an object with width and height",
            })
        width = resolution.get("width")
        height = resolution.get("height")
        for label, val in (("width", width), ("height", height)):
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                return make_response(req_id, error={
                    "code": -32602,
                    "message": f"batch_capture_cameras: invalid_field: resolution['{label}'] must be a positive integer",
                })

    actors_resp = call_ue("get_actors_in_level", {})
    if "error" in actors_resp:
        upstream = actors_resp.get("error", {}) or {}
        return make_response(req_id, error={
            "code": upstream.get("code", -32603) or -32603,
            "message": f"batch_capture_cameras: get_actors_failed: {upstream.get('message') or 'get_actors_in_level returned an error'}",
        })

    all_actors = (actors_resp.get("result") or {}).get("actors") or []
    # CineCameraActor short-class match (case-insensitive), mirroring the
    # find_actors_by_class needle logic.
    cine_cameras = [
        a for a in all_actors
        if isinstance(a, dict) and isinstance(a.get("class"), str)
        and a["class"].lower() == "cinecameraactor"
    ]

    # Restrict to requested camera labels when supplied. render_camera_to_png
    # consumes camera_label, not the actor's unstable internal FName.
    if camera_names is not None:
        wanted = set(camera_names)
        cine_cameras = [
            a for a in cine_cameras
            if a.get("label") in wanted
        ]

    if not cine_cameras:
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_capture_cameras: no_cameras_matched: no CineCameraActor found in the level (or none matched 'camera_names')",
        })

    succeeded = 0
    results: list[dict] = []
    for cam in cine_cameras:
        label = cam.get("label") or cam.get("name") or "camera"
        safe = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in str(label))
        out_path = os.path.join(output_dir, file_name_format.replace("{camera}", safe))

        render_args = {"out_path": out_path, "camera_label": label}
        if width and height:
            render_args["width"] = width
            render_args["height"] = height

        render_resp = call_ue("render_camera_to_png", render_args)
        if "error" in render_resp:
            upstream = render_resp.get("error", {}) or {}
            results.append({
                "camera": label,
                "ok": False,
                "out_path": out_path,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": upstream.get("message") or f"batch_capture_cameras: render_failed: render_camera_to_png on '{label}' failed",
                },
            })
        else:
            succeeded += 1
            results.append({"camera": label, "ok": True, "out_path": out_path})

    return _wrap_tool_result(req_id, {
        "ok": succeeded == len(cine_cameras),
        "output_dir": output_dir,
        "total": len(cine_cameras),
        "succeeded": succeeded,
        "results": results,
    })


# Column / row keys understood by batch_spawn_from_csv. `class` and `label`
# are strings; the six transform keys are floats; `properties` is a JSON
# object string (CSV) or a nested object (inline rows) forwarded verbatim to
# spawn_actor's `properties`.
_CSV_SPAWN_TRANSFORM_KEYS = ("x", "y", "z", "pitch", "yaw", "roll")


def _parse_spawn_rows_from_csv(csv_text: str) -> tuple[list[dict] | None, str | None]:
    """Parse CSV text into a list of row dicts using the stdlib csv module.

    Returns (rows, error_message). On success error_message is None. The
    `properties` column, when present and non-empty, is JSON-decoded into an
    object so it can be forwarded to spawn_actor; a malformed properties cell
    aborts the whole parse with a row-numbered error.
    """
    import csv
    import io as _csv_io

    reader = csv.DictReader(_csv_io.StringIO(csv_text))
    if reader.fieldnames is None:
        return None, "csv has no header row"

    rows: list[dict] = []
    for lineno, raw in enumerate(reader, start=2):  # header is line 1
        row: dict = {}
        for key, val in raw.items():
            if key is None or val is None:
                continue
            val = val.strip()
            if val == "":
                continue
            if key in _CSV_SPAWN_TRANSFORM_KEYS:
                try:
                    row[key] = float(val)
                except ValueError:
                    return None, f"row {lineno}: column '{key}' value {val!r} is not a number"
            elif key == "properties":
                try:
                    parsed = json.loads(val)
                except (ValueError, TypeError):
                    return None, f"row {lineno}: column 'properties' is not valid JSON: {val!r}"
                if not isinstance(parsed, dict):
                    return None, f"row {lineno}: column 'properties' must be a JSON object"
                row["properties"] = parsed
            else:
                row[key] = val
        rows.append(row)
    return rows, None


def synthetic_batch_spawn_from_csv(req_id, args: dict) -> dict:
    """Spawn N actors from a CSV file or an inline list of row objects.

    Composition (bridge-side, no dedicated C++ handler):
      For each row, call_ue("spawn_actor", {class_path, location, rotation,
      label, properties}) -- one round-trip per row. Rows that omit `class`
      fall back to `default_class`.

    CSV columns / row keys: class, x, y, z, pitch, yaw, roll, label,
    properties (a JSON object — a JSON-string cell in CSV, a nested object in
    inline rows). Missing transform keys default to 0; missing label is left
    to spawn_actor (auto-named).

    Synthetic rather than C++ because it is a thin parse-then-loop over the
    existing spawn_actor handler — the set-dressing scatter use case
    (04_dress_set.py) is exactly "read a table, spawn a row each".

    Args (object): exactly one of csv_path (filesystem path to a .csv) OR
      rows (inline list of objects); default_class (str, optional) supplies
      the class for rows that omit one.

    Returns: ok, total, succeeded, results[{row, ok, name?, label?, error?}].
    """
    if not isinstance(args, dict):
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_spawn_from_csv: invalid_arguments: arguments must be an object",
        })

    csv_path = args.get("csv_path")
    rows = args.get("rows")
    if (csv_path is None) == (rows is None):
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_spawn_from_csv: invalid_field: supply exactly one of 'csv_path' or 'rows'",
        })

    default_class = args.get("default_class")
    if default_class is not None and (not isinstance(default_class, str) or not default_class):
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_spawn_from_csv: invalid_field: 'default_class' must be a non-empty string when supplied",
        })

    # Resolve the row list from either source.
    if csv_path is not None:
        if not isinstance(csv_path, str) or not csv_path:
            return make_response(req_id, error={
                "code": -32602,
                "message": "batch_spawn_from_csv: invalid_field: 'csv_path' must be a non-empty string",
            })
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                csv_text = f.read()
        except OSError as e:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"batch_spawn_from_csv: csv_read_failed: could not read '{csv_path}': {e}",
            })
        rows, parse_err = _parse_spawn_rows_from_csv(csv_text)
        if parse_err is not None:
            return make_response(req_id, error={
                "code": -32602,
                "message": f"batch_spawn_from_csv: csv_parse_failed: {parse_err}",
            })
    else:
        if not isinstance(rows, list):
            return make_response(req_id, error={
                "code": -32602,
                "message": "batch_spawn_from_csv: invalid_field: 'rows' must be a list of objects",
            })

    if not rows:
        return make_response(req_id, error={
            "code": -32602,
            "message": "batch_spawn_from_csv: no_rows: the CSV / rows list produced zero spawn rows",
        })

    succeeded = 0
    results: list[dict] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            results.append({
                "row": i,
                "ok": False,
                "error": {"code": -32602, "message": f"batch_spawn_from_csv: bad_row: rows[{i}] must be an object"},
            })
            continue

        class_path = row.get("class") or default_class
        if not isinstance(class_path, str) or not class_path:
            results.append({
                "row": i,
                "ok": False,
                "error": {
                    "code": -32602,
                    "message": f"batch_spawn_from_csv: missing_class: rows[{i}] has no 'class' and no 'default_class' was supplied",
                },
            })
            continue

        numbers: dict[str, float] = {}
        bad_number = None
        for key in _CSV_SPAWN_TRANSFORM_KEYS:
            raw_value = row.get(key, 0) or 0
            try:
                numbers[key] = float(raw_value)
            except (TypeError, ValueError):
                bad_number = (key, raw_value)
                break
        if bad_number is not None:
            key, raw_value = bad_number
            results.append({
                "row": i,
                "ok": False,
                "error": {
                    "code": -32602,
                    "message": f"batch_spawn_from_csv: invalid_field: rows[{i}]['{key}'] value {raw_value!r} is not a number",
                },
            })
            continue

        props = row.get("properties")
        if "properties" in row and props is not None and not isinstance(props, dict):
            results.append({
                "row": i,
                "ok": False,
                "error": {
                    "code": -32602,
                    "message": f"batch_spawn_from_csv: invalid_field: rows[{i}]['properties'] must be an object when supplied",
                },
            })
            continue

        spawn_args: dict = {
            "class_path": class_path,
            "location": {
                "x": numbers["x"],
                "y": numbers["y"],
                "z": numbers["z"],
            },
            "rotation": {
                "pitch": numbers["pitch"],
                "yaw": numbers["yaw"],
                "roll": numbers["roll"],
            },
        }
        label = row.get("label")
        if isinstance(label, str) and label:
            spawn_args["label"] = label
        if isinstance(props, dict) and props:
            spawn_args["properties"] = props

        spawn_resp = call_ue("spawn_actor", spawn_args)
        if "error" in spawn_resp:
            upstream = spawn_resp.get("error", {}) or {}
            results.append({
                "row": i,
                "ok": False,
                "error": {
                    "code": upstream.get("code", -32603) or -32603,
                    "message": upstream.get("message") or f"batch_spawn_from_csv: spawn_failed: spawn_actor for rows[{i}] failed",
                },
            })
        else:
            succeeded += 1
            result = spawn_resp.get("result") or {}
            results.append({
                "row": i,
                "ok": True,
                "name": result.get("name"),
                "label": result.get("label"),
            })

    return _wrap_tool_result(req_id, {
        "ok": succeeded == len(rows),
        "total": len(rows),
        "succeeded": succeeded,
        "results": results,
    })


SYNTHETIC_TOOLS = {
    "import_mesh": synthetic_import_mesh,
    "material_auto_remap": synthetic_material_auto_remap,
    "batch_capture_cameras": synthetic_batch_capture_cameras,
    "batch_spawn_from_csv": synthetic_batch_spawn_from_csv,
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
    "convert_hdri_to_cubemap": synthetic_convert_hdri_to_cubemap,
    "sequencer_add_transform_keyframe": synthetic_sequencer_add_transform_keyframe,
}


# Tools that run raw user Python via UE's ExecuteFile mode. UE's
# FPythonCommandEx::CommandResult does NOT surface stdout for file
# execution -- it returns the last-evaluated expression's repr (almost
# always "None"), which is why every result historically came back with
# `"output": "None"` and callers had to round-trip prints through
# unreal.log("__MARKER__...") + get_log_lines. We close that gap
# bridge-side (no C++ recompile) by wrapping the user's code so its
# stdout/stderr is written to a temp file the bridge reads back after the
# round-trip and folds into a new `stdout` result field. The legacy
# `output` field is left untouched for backward compatibility.
#
# Note: this captures Python-level stdout/stderr (print, tracebacks) only.
# unreal.log()/log_warning() write to UE's LogPython category, not Python
# stdout, so those still surface via get_log_lines as before.
_STDOUT_CAPTURE_TOOLS = {"execute_unreal_python", "exec_python_persistent"}


def _wrap_code_for_stdout_capture(user_code: str, capture_path: str) -> str:
    """Wrap `user_code` so its stdout + stderr (and any traceback) is written
    to `capture_path`, then return the wrapped source.

    The user source is embedded as a string literal (via repr) and run with
    the builtin code-runner against globals() rather than being
    inlined/indented, so that:
      * multi-line string literals inside the user code are never corrupted
        by re-indentation, and
      * scope semantics are preserved exactly -- the runner uses globals(),
        which is whatever dict UE hands the wrapper file: a fresh per-call
        dict for execute_unreal_python (Private scope) or the shared console
        dict for exec_python_persistent (Public scope). Persistence behaves
        as before.

    The source is compiled with the filename '<execute_unreal_python>' so any
    traceback line numbers map back to the user's own code, not this wrapper.
    If the user code raises, the captured output (including the traceback) is
    flushed to the file and the exception is re-raised so UE's
    ExecPythonCommandEx still reports ok=False.
    """
    return (
        "import sys as __ucm_sys, io as __ucm_io, traceback as __ucm_tb, builtins as __ucm_bi\n"
        "__ucm_src = " + repr(user_code) + "\n"
        "__ucm_run = getattr(__ucm_bi, 'exec')\n"
        "__ucm_buf = __ucm_io.StringIO()\n"
        "__ucm_old_out, __ucm_old_err = __ucm_sys.stdout, __ucm_sys.stderr\n"
        "__ucm_sys.stdout = __ucm_sys.stderr = __ucm_buf\n"
        "__ucm_exc = None\n"
        "try:\n"
        "    __ucm_run(compile(__ucm_src, '<execute_unreal_python>', 'exec'), globals())\n"
        "except BaseException:\n"
        "    __ucm_exc = __ucm_tb.format_exc()\n"
        "finally:\n"
        "    __ucm_sys.stdout, __ucm_sys.stderr = __ucm_old_out, __ucm_old_err\n"
        "    try:\n"
        # Use builtins.open via the captured module ref: an unqualified `open`
        # could be shadowed by a name in the user's globals (e.g. a var named
        # `open`), which would silently drop the captured stdout.
        "        with __ucm_bi.open(" + repr(capture_path) + ", 'w', encoding='utf-8') as __ucm_f:\n"
        "            __ucm_f.write(__ucm_buf.getvalue())\n"
        "            if __ucm_exc:\n"
        "                __ucm_f.write(__ucm_exc)\n"
        "    except Exception:\n"
        "        pass\n"
        "    for __ucm_n in ['__ucm_sys', '__ucm_io', '__ucm_tb', '__ucm_bi',\n"
        "                    '__ucm_src', '__ucm_run', '__ucm_buf',\n"
        "                    '__ucm_old_out', '__ucm_old_err', '__ucm_f',\n"
        "                    '__ucm_n']:\n"
        "        globals().pop(__ucm_n, None)\n"
        # pop-in-raise: never bind a separate __ucm_reraise global (it would
        # leak into exec_python_persistent's shared namespace). globals().pop
        # returns the value AND removes the key in one step.
        "if __ucm_exc:\n"
        "    raise RuntimeError('user code raised (see stdout field):\\n' + globals().pop('__ucm_exc'))\n"
        "globals().pop('__ucm_exc', None)\n"
    )


def _call_ue_capturing_stdout(tool_name: str, tool_args: dict) -> dict:
    """call_ue wrapper for the raw-Python exec tools: inject stdout capture,
    forward to UE, then fold the captured text into the result as `stdout`.

    Falls back to a plain call_ue (no wrapping) when `code` is missing or not
    a string, so the C++ handler still emits its own canonical missing-'code'
    error rather than the bridge masking it.
    """
    code = tool_args.get("code")
    if not isinstance(code, str):
        return call_ue(tool_name, tool_args)

    fd, capture_path = tempfile.mkstemp(prefix="ucm_stdout_", suffix=".txt")
    os.close(fd)
    try:
        forwarded = dict(tool_args)
        forwarded["code"] = _wrap_code_for_stdout_capture(code, capture_path)
        resp = call_ue(tool_name, forwarded)

        captured = ""
        try:
            with open(capture_path, "r", encoding="utf-8") as f:
                captured = f.read()
        except OSError:
            captured = ""

        result = resp.get("result") if isinstance(resp, dict) else None
        if isinstance(result, dict):
            result["stdout"] = captured
        return resp
    finally:
        try:
            os.remove(capture_path)
        except OSError:
            pass


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
        # Honour progressive disclosure when UCMCP_TOOL_MODE=progressive;
        # defaults to the full catalog for backward compatibility.
        return make_response(req_id, {"tools": advertised_tools()})

    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {}) or {}
        if not tool_name:
            return make_response(req_id, error={"code": -32602, "message": "tools/call missing 'name'"})

        # Bridge-only progressive-disclosure discovery tool. Served entirely
        # in-process: it never crosses the wire to UE and is always callable
        # regardless of tool_mode() (so a client that found it in progressive
        # mode can keep using it even if the mode flag changes). Pure, fail-
        # closed: see search_tools_impl's security contract.
        if tool_name == "search_tools":
            return _wrap_tool_result(req_id, search_tools_impl(tool_args))

        # Synthetic tools are served bridge-side without a UE round-trip
        # (or, in wait_for_events's case, with multiple UE round-trips
        # composed into one logical operation).
        if tool_name in SYNTHETIC_TOOLS:
            return SYNTHETIC_TOOLS[tool_name](req_id, tool_args)

        if tool_name in _STDOUT_CAPTURE_TOOLS:
            ue_resp = _call_ue_capturing_stdout(tool_name, tool_args)
        else:
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
