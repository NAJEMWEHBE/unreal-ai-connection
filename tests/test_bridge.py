"""
Unit tests for `bridge/unreal_claude_mcp_bridge.py`.

These run with NO Unreal Engine instance — `socket.socket` is mocked. They
cover the MCP protocol surface (initialize / tools/list / tools/call /
notifications / unknown methods) and the TCP layer's error paths.

Run from repo root:    pytest tests/
"""

import json
import socket
from unittest.mock import MagicMock, patch

import pytest

import unreal_claude_mcp_bridge as bridge
from conftest import EXPECTED_TOOL_COUNT


# -------- TOOLS schema --------------------------------------------------------

def test_tools_list_size():
    # Cross-checked against the manifest in test_manifest_sync.py; the absolute
    # number bumps with each new tool. Function name kept count-agnostic so
    # it doesn't drift behind the assertion (per Sonnet pre-review on PR #50).
    # The expected count lives in tests/conftest.py so a tool bump is a
    # one-line edit (PR #87 cleanup of the "two count assertions" trap).
    assert len(bridge.TOOLS) == EXPECTED_TOOL_COUNT


def test_each_tool_has_required_mcp_fields():
    for tool in bridge.TOOLS:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"
        assert isinstance(tool["name"], str) and tool["name"]
        assert isinstance(tool["description"], str) and tool["description"]


def test_tool_names_are_unique_and_match_handlers():
    names = [t["name"] for t in bridge.TOOLS]
    assert len(names) == len(set(names)), "duplicate tool names"
    expected = {
        "execute_unreal_python", "get_project_summary", "inspect_blueprint",
        "inspect_widget_tree", "edit_widget_tree", "get_viewport_screenshot",
        "list_tools", "get_actors_in_level", "focus_actor",
        "load_level_by_path", "take_high_res_screenshot",
        "import_texture", "configure_texture",
        "find_assets", "spawn_actor", "set_actor_transform", "delete_actor",
        "set_actor_property", "add_component",
        "get_log_lines", "execute_console_command",
        "inspect_asset", "move_asset", "rename_asset", "duplicate_asset", "delete_asset",
        "inspect_sequence", "create_sequence", "bind_actor_to_sequence",
        "create_material_instance", "set_mi_parameter", "inspect_material",
        "inspect_material_instance",
        "run_python_file",
        "fix_up_redirectors",
        "apply_python_to_selection",
        "compile_blueprint",
        "get_console_variable",
        "set_console_variable",
        "poll_events",
        "wait_for_events",
        "register_subscription",
        "unsubscribe",
        "poll_subscription",
        "start_sleep_task",
        "poll_task",
        "cancel_task",
        "list_tasks",
        "exec_python_persistent",
        "reset_python_state",
        "find_console_variables",
        "inspect_static_mesh",
        "inspect_niagara_system",
        "inspect_anim_blueprint",
        "inspect_landscape",
        "inspect_skeletal_mesh",
        "inspect_anim_montage",
        "inspect_widget_blueprint",
        "inspect_data_table",
        "inspect_texture",
        "inspect_curve",
        "inspect_physics_asset",
        "inspect_sound_cue",
        "inspect_sound_wave",
        "inspect_sound_attenuation",
        "get_camera_transform",
        "set_camera_transform",
        "screenshot_actor",
        "compile_mod_pak",
        "compile_mod_pak_direct",
        "bulk_delete_assets",
        "bulk_move_assets",
        "bulk_rename_assets",
        "bulk_duplicate_assets",
        "inspect_data_asset",
        "inspect_sound_class",
        "inspect_sound_submix",
        "inspect_audio_bus",
        "inspect_material_function",
        "inspect_metasound",
        "get_engine_version",
        "list_levels",
        "save_dirty_assets",
        "get_selected_actors",
        "inspect_input_mappings",
        "bulk_inspect_assets",
        "pie_control",
        "inspect_project_setting",
        "find_unused_assets",
        "get_reference_chain",
        "bulk_compile_blueprints",
        "audit_blueprint_compile_status",
    }
    assert set(names) == expected


def test_edit_widget_tree_schema_includes_compile_flag():
    tool = next(t for t in bridge.TOOLS if t["name"] == "edit_widget_tree")
    assert "compile" in tool["inputSchema"]["properties"]
    assert tool["inputSchema"]["properties"]["compile"]["type"] == "boolean"


def test_find_assets_schema_includes_tags_and_include_tags():
    """v0.7.0: find_assets gains optional tags + include_tags fields."""
    find_assets = next(t for t in bridge.TOOLS if t["name"] == "find_assets")
    props = find_assets["inputSchema"]["properties"]
    assert "tags" in props, "find_assets schema must declare 'tags'"
    assert props["tags"]["type"] == "object"
    assert "include_tags" in props, "find_assets schema must declare 'include_tags'"
    assert props["include_tags"]["type"] == "boolean"
    # Required list unchanged — both new fields are optional.
    assert find_assets["inputSchema"]["required"] == ["class_path"]


def test_inspect_asset_in_tools_catalog():
    """v0.7.0: inspect_asset is a new handler with required 'path' param."""
    inspect = next((t for t in bridge.TOOLS if t["name"] == "inspect_asset"), None)
    assert inspect is not None, "inspect_asset must be in TOOLS catalog"
    assert "path" in inspect["inputSchema"]["properties"]
    assert inspect["inputSchema"]["required"] == ["path"]


def test_get_engine_version_in_tools_catalog():
    """Wave A: get_engine_version is a new C++ handler with no params.
    Returns structured engine version components separately so callers can
    branch on (major, minor) without parsing the single 'engine_version'
    string get_project_summary already emits."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "get_engine_version"), None)
    assert tool is not None, "get_engine_version must be in TOOLS catalog"
    # Mirrors get_project_summary / list_tools shape (no params).
    assert tool["inputSchema"]["type"] == "object"
    assert tool["inputSchema"]["properties"] == {}
    # NOT a synthetic — dispatches straight to the UE C++ handler.
    assert "get_engine_version" not in bridge.SYNTHETIC_TOOLS


def test_list_levels_in_tools_catalog():
    """Wave A: list_levels is a new C++ handler. Optional path_under +
    name_contains (no required params). Closes the gap where
    load_level_by_path required pre-knowledge of the package path."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "list_levels"), None)
    assert tool is not None, "list_levels must be in TOOLS catalog"
    props = tool["inputSchema"]["properties"]
    assert "path_under" in props
    assert props["path_under"]["type"] == "string"
    assert "name_contains" in props
    assert props["name_contains"]["type"] == "string"
    # All params optional — no required field.
    assert "required" not in tool["inputSchema"] or tool["inputSchema"]["required"] == []
    # NOT a synthetic — dispatches straight to the UE C++ handler.
    assert "list_levels" not in bridge.SYNTHETIC_TOOLS


def test_save_dirty_assets_in_tools_catalog():
    """Wave A: save_dirty_assets is a new C++ handler closing the
    persistence loop after every edit. Optional include_levels +
    include_content (both default true) — mirrors editor 'Save All'."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "save_dirty_assets"), None)
    assert tool is not None, "save_dirty_assets must be in TOOLS catalog"
    props = tool["inputSchema"]["properties"]
    assert "include_levels" in props
    assert props["include_levels"]["type"] == "boolean"
    assert "include_content" in props
    assert props["include_content"]["type"] == "boolean"
    # All params optional — no required field.
    assert "required" not in tool["inputSchema"] or tool["inputSchema"]["required"] == []
    # NOT a synthetic.
    assert "save_dirty_assets" not in bridge.SYNTHETIC_TOOLS


def test_get_selected_actors_in_tools_catalog():
    """Wave A: get_selected_actors is a new C++ handler. Companion to
    apply_python_to_selection — lets the LLM observe what the user has
    selected before running code against it. No params, no error paths
    for empty selection (returns count:0)."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "get_selected_actors"), None)
    assert tool is not None, "get_selected_actors must be in TOOLS catalog"
    assert tool["inputSchema"]["type"] == "object"
    assert tool["inputSchema"]["properties"] == {}
    assert "get_selected_actors" not in bridge.SYNTHETIC_TOOLS


def test_inspect_input_mappings_in_tools_catalog():
    """Wave A: inspect_input_mappings is a new C++ handler. Returns the
    project's legacy UInputSettings (action_mappings + axis_mappings) plus
    a uses_enhanced_input flag. No params, no required field."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "inspect_input_mappings"), None)
    assert tool is not None, "inspect_input_mappings must be in TOOLS catalog"
    assert tool["inputSchema"]["type"] == "object"
    assert tool["inputSchema"]["properties"] == {}
    assert "inspect_input_mappings" not in bridge.SYNTHETIC_TOOLS


def test_pie_control_in_tools_catalog():
    """Wave A.5: pie_control is a new C++ handler. Required 'action' field;
    optional 'mode' for start. Closes the validation feedback loop —
    the LLM can now trigger PIE to test its edits."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "pie_control"), None)
    assert tool is not None, "pie_control must be in TOOLS catalog"
    assert tool["inputSchema"]["required"] == ["action"]
    props = tool["inputSchema"]["properties"]
    assert "action" in props and props["action"]["type"] == "string"
    assert "mode" in props and props["mode"]["type"] == "string"
    assert "pie_control" not in bridge.SYNTHETIC_TOOLS


def test_inspect_project_setting_in_tools_catalog():
    """Wave A.5: inspect_project_setting is a new C++ handler. Reflects
    any UDeveloperSettings subclass. Required 'settings_class' (full
    class path); optional 'property' for single-property mode."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "inspect_project_setting"), None)
    assert tool is not None, "inspect_project_setting must be in TOOLS catalog"
    assert tool["inputSchema"]["required"] == ["settings_class"]
    props = tool["inputSchema"]["properties"]
    assert "settings_class" in props and props["settings_class"]["type"] == "string"
    assert "property" in props and props["property"]["type"] == "string"
    assert "inspect_project_setting" not in bridge.SYNTHETIC_TOOLS


def test_bulk_inspect_assets_is_synthetic():
    """Wave A: bulk_inspect_assets is a SYNTHETIC bridge-side composition
    over inspect_asset. Required 'paths' (list of strings); optional
    continue_on_error (default true). Mirrors bulk_delete_assets shape."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "bulk_inspect_assets"), None)
    assert tool is not None, "bulk_inspect_assets must be in TOOLS catalog"
    assert tool["inputSchema"]["required"] == ["paths"]
    props = tool["inputSchema"]["properties"]
    assert props["paths"]["type"] == "array"
    assert props["paths"]["items"]["type"] == "string"
    assert "continue_on_error" in props
    assert props["continue_on_error"]["type"] == "boolean"
    assert "bulk_inspect_assets" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["bulk_inspect_assets"] is bridge.synthetic_bulk_inspect_assets


def test_bulk_inspect_assets_happy_path_composes_inspect_asset():
    """Loop over paths, dispatch one call_ue('inspect_asset', ...) per entry,
    accumulate per-path results with full inspection data on success."""
    inspect_responses = [
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "path": "/Game/A", "class": "Texture2D"}},
        {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "path": "/Game/B", "class": "StaticMesh"}},
    ]
    with patch.object(bridge, "call_ue", side_effect=inspect_responses) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 300, "method": "tools/call",
            "params": {
                "name": "bulk_inspect_assets",
                "arguments": {"paths": ["/Game/A", "/Game/B"]},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("inspect_asset", {"path": "/Game/A"})
    assert m.call_args_list[1].args == ("inspect_asset", {"path": "/Game/B"})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["total"] == 2
    assert body["inspected"] == 2
    assert body["failed"] == 0
    assert body["results"][0]["data"]["class"] == "Texture2D"
    assert body["results"][1]["data"]["class"] == "StaticMesh"


def test_bulk_inspect_assets_partial_failure_continues_when_continue_on_error_true():
    """Default partial-failure path. Second inspect fails; loop keeps going,
    third still attempted. Per-path error_code/error_message preserved."""
    ok_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "path": "/Game/A", "class": "Texture2D"}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "inspect_asset: asset_not_found: /Game/Missing"}}
    ok_resp_2 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "path": "/Game/C", "class": "Material"}}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp, err_resp, ok_resp_2]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 301, "method": "tools/call",
            "params": {
                "name": "bulk_inspect_assets",
                "arguments": {"paths": ["/Game/A", "/Game/Missing", "/Game/C"]},
            },
        })

    assert m.call_count == 3
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["inspected"] == 2
    assert body["failed"] == 1
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error_code"] == -32000


def test_bulk_inspect_assets_rejects_missing_paths():
    """Required 'paths' field absent -> -32602 with the canonical
    missing_required_field shape."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 302, "method": "tools/call",
        "params": {"name": "bulk_inspect_assets", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "bulk_inspect_assets" in resp["error"]["message"]
    assert "paths" in resp["error"]["message"]


# ============================================================================
# Wave B asset-hygiene synthetic tools
# (find_unused_assets, get_reference_chain, bulk_compile_blueprints,
# audit_blueprint_compile_status)
# ============================================================================


# ---- find_unused_assets ----------------------------------------------------

def test_find_unused_assets_is_synthetic():
    """Wave B: find_unused_assets is a SYNTHETIC bridge-side composition over
    find_assets + inspect_asset. All params optional (path_under default
    /Game; limit default 100)."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "find_unused_assets"), None)
    assert tool is not None, "find_unused_assets must be in TOOLS catalog"
    # No required fields -- everything has a default.
    assert tool["inputSchema"].get("required", []) == []
    props = tool["inputSchema"]["properties"]
    assert props["path_under"]["type"] == "string"
    assert props["class_filter"]["type"] == "string"
    assert props["limit"]["type"] == "integer"
    assert "find_unused_assets" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["find_unused_assets"] is bridge.synthetic_find_unused_assets


def test_find_unused_assets_happy_path():
    """find_assets returns 3 candidates; per-asset inspect_asset reports
    referencer arrays. Empty referencers list -> 'unused'."""
    find_resp = {
        "jsonrpc": "2.0", "id": 1, "result": {
            "ok": True, "matched": 3, "returned": 3,
            "assets": [
                {"name": "T_Unused", "package_path": "/Game/Tex/T_Unused", "class": "Texture2D"},
                {"name": "T_Used", "package_path": "/Game/Tex/T_Used", "class": "Texture2D"},
                {"name": "T_AlsoUnused", "package_path": "/Game/Tex/T_AlsoUnused", "class": "Texture2D"},
            ],
        },
    }
    inspect_unused = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "referencers": []}}
    inspect_used = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "referencers": ["/Game/Maps/M_Demo"]}}
    inspect_unused_2 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "referencers": []}}
    with patch.object(bridge, "call_ue", side_effect=[find_resp, inspect_unused, inspect_used, inspect_unused_2]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 400, "method": "tools/call",
            "params": {"name": "find_unused_assets", "arguments": {"path_under": "/Game/Tex"}},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["scanned"] == 3
    assert body["unused_count"] == 2
    paths = [u["path"] for u in body["unused"]]
    assert "/Game/Tex/T_Unused" in paths
    assert "/Game/Tex/T_AlsoUnused" in paths
    assert "/Game/Tex/T_Used" not in paths
    assert body["truncated"] is False


def test_find_unused_assets_swallows_individual_inspect_failures():
    """When SOME inspect_asset calls fail, the loop continues; only when
    EVERY inspect fails does the synthetic surface inspect_failed."""
    find_resp = {
        "jsonrpc": "2.0", "id": 1, "result": {
            "ok": True, "matched": 2, "returned": 2,
            "assets": [
                {"name": "A", "package_path": "/Game/A", "class": "Texture2D"},
                {"name": "B", "package_path": "/Game/B", "class": "Texture2D"},
            ],
        },
    }
    inspect_ok_unused = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "referencers": []}}
    inspect_err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "asset_not_found"}}
    with patch.object(bridge, "call_ue", side_effect=[find_resp, inspect_ok_unused, inspect_err]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 401, "method": "tools/call",
            "params": {"name": "find_unused_assets", "arguments": {}},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True  # individual failure swallowed
    assert body["scanned"] == 2
    assert body["unused_count"] == 1
    assert body["unused"][0]["path"] == "/Game/A"


def test_find_unused_assets_all_inspects_fail_surfaces_error():
    """When EVERY inspect fails, an inspect_failed envelope-level error
    surfaces -- a confusing '0 unused found' would otherwise mask the
    underlying issue."""
    find_resp = {
        "jsonrpc": "2.0", "id": 1, "result": {
            "ok": True, "matched": 2, "returned": 2,
            "assets": [
                {"name": "A", "package_path": "/Game/A", "class": "Texture2D"},
                {"name": "B", "package_path": "/Game/B", "class": "Texture2D"},
            ],
        },
    }
    inspect_err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "asset_not_found"}}
    with patch.object(bridge, "call_ue", side_effect=[find_resp, inspect_err, inspect_err]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 402, "method": "tools/call",
            "params": {"name": "find_unused_assets", "arguments": {}},
        })

    assert "error" in resp
    assert resp["error"]["code"] == -32603
    assert "inspect_failed" in resp["error"]["message"]


def test_find_unused_assets_rejects_invalid_limit():
    """limit must be 1..10000; out-of-range -> -32602."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 403, "method": "tools/call",
        "params": {"name": "find_unused_assets", "arguments": {"limit": 20000}},
    })
    assert resp["error"]["code"] == -32602
    assert "limit" in resp["error"]["message"]


def test_find_unused_assets_find_failed_propagates():
    """When the underlying find_assets call errors, the synthetic surfaces
    find_failed verbatim."""
    find_err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32099, "message": "UE unreachable"}}
    with patch.object(bridge, "call_ue", side_effect=[find_err]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 404, "method": "tools/call",
            "params": {"name": "find_unused_assets", "arguments": {}},
        })
    assert "error" in resp
    assert "find_failed" in resp["error"]["message"]


# ---- get_reference_chain ---------------------------------------------------

def test_get_reference_chain_is_synthetic():
    """Wave B: get_reference_chain is a SYNTHETIC bridge-side composition
    over inspect_asset (BFS recursion). path is REQUIRED; depth + direction
    optional."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "get_reference_chain"), None)
    assert tool is not None, "get_reference_chain must be in TOOLS catalog"
    assert tool["inputSchema"]["required"] == ["path"]
    props = tool["inputSchema"]["properties"]
    assert props["path"]["type"] == "string"
    assert props["depth"]["type"] == "integer"
    assert props["direction"]["enum"] == ["up", "down"]
    assert "get_reference_chain" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["get_reference_chain"] is bridge.synthetic_get_reference_chain


def test_get_reference_chain_happy_path_up_direction():
    """BFS up from /Game/M_Stone: 1 referencer at depth 1 (/Game/BP_Block);
    /Game/BP_Block itself has no further referencers. node_count=2,
    edge_count=1, truncated=False."""
    root_resp = {"jsonrpc": "2.0", "id": 1, "result": {
        "referencers": ["/Game/BP_Block"], "dependencies": []}}
    bp_resp = {"jsonrpc": "2.0", "id": 1, "result": {"referencers": [], "dependencies": []}}
    with patch.object(bridge, "call_ue", side_effect=[root_resp, bp_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 410, "method": "tools/call",
            "params": {"name": "get_reference_chain", "arguments": {
                "path": "/Game/M_Stone.M_Stone", "depth": 3, "direction": "up",
            }},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["root"] == "/Game/M_Stone.M_Stone"
    assert body["direction"] == "up"
    assert body["node_count"] == 2
    assert body["edge_count"] == 1
    # For up direction, edges flow neighbor -> node.
    assert body["edges"][0] == {"from": "/Game/BP_Block", "to": "/Game/M_Stone.M_Stone"}
    assert body["truncated"] is False
    assert m.call_count == 2


def test_get_reference_chain_happy_path_down_direction():
    """BFS down from /Game/BP_Block: 2 deps at depth 1; each has no deps.
    edges flow node -> neighbor."""
    root_resp = {"jsonrpc": "2.0", "id": 1, "result": {
        "referencers": [], "dependencies": ["/Game/M_Stone", "/Game/T_Stone"]}}
    leaf1 = {"jsonrpc": "2.0", "id": 1, "result": {"referencers": [], "dependencies": []}}
    leaf2 = {"jsonrpc": "2.0", "id": 1, "result": {"referencers": [], "dependencies": []}}
    with patch.object(bridge, "call_ue", side_effect=[root_resp, leaf1, leaf2]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 411, "method": "tools/call",
            "params": {"name": "get_reference_chain", "arguments": {
                "path": "/Game/BP_Block", "direction": "down",
            }},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["direction"] == "down"
    assert body["node_count"] == 3  # root + 2 deps
    assert body["edge_count"] == 2
    for edge in body["edges"]:
        assert edge["from"] == "/Game/BP_Block"
        assert edge["to"] in ("/Game/M_Stone", "/Game/T_Stone")


def test_get_reference_chain_dedupes_visited_nodes_on_cycle():
    """A cycle in the asset graph (A refs B, B refs A) must NOT loop
    infinitely. Each node inspected once; second sighting is de-duped."""
    a_resp = {"jsonrpc": "2.0", "id": 1, "result": {"referencers": ["/Game/B"], "dependencies": []}}
    b_resp = {"jsonrpc": "2.0", "id": 1, "result": {"referencers": ["/Game/A"], "dependencies": []}}
    # If de-dup is broken, call_ue would keep getting called past 2.
    with patch.object(bridge, "call_ue", side_effect=[a_resp, b_resp, a_resp, b_resp, a_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 412, "method": "tools/call",
            "params": {"name": "get_reference_chain", "arguments": {
                "path": "/Game/A", "depth": 5, "direction": "up",
            }},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    # Exactly 2 distinct nodes visited (A + B). Each inspected at most once
    # in the de-duped BFS.
    assert body["node_count"] == 2
    assert m.call_count <= 3  # A at depth 0, B at depth 1, possibly A re-checked once via cycle


def test_get_reference_chain_rejects_root_not_found():
    """When the ROOT's inspect_asset returns asset_not_found, the synthetic
    surfaces -32602 asset_not_found at envelope-level."""
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000, "message": "inspect_asset: asset_not_found: /Game/Missing"}}
    with patch.object(bridge, "call_ue", side_effect=[err_resp]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 413, "method": "tools/call",
            "params": {"name": "get_reference_chain", "arguments": {
                "path": "/Game/Missing",
            }},
        })

    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "asset_not_found" in resp["error"]["message"]


def test_get_reference_chain_rejects_invalid_direction():
    """direction must be 'up' or 'down'."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 414, "method": "tools/call",
        "params": {"name": "get_reference_chain", "arguments": {
            "path": "/Game/Foo", "direction": "sideways",
        }},
    })
    assert resp["error"]["code"] == -32602
    assert "invalid_direction" in resp["error"]["message"]


def test_get_reference_chain_rejects_invalid_depth():
    """depth must be 1..8."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 415, "method": "tools/call",
        "params": {"name": "get_reference_chain", "arguments": {
            "path": "/Game/Foo", "depth": 99,
        }},
    })
    assert resp["error"]["code"] == -32602
    assert "invalid_depth" in resp["error"]["message"]


def test_get_reference_chain_rejects_path_with_nul_byte():
    """NUL byte rejected at path validation before any call_ue."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 416, "method": "tools/call",
            "params": {"name": "get_reference_chain", "arguments": {
                "path": "/Game/Bad\x00Path",
            }},
        })
    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "NUL" in resp["error"]["message"]


# ---- bulk_compile_blueprints -----------------------------------------------

def test_bulk_compile_blueprints_is_synthetic():
    """Wave B: bulk_compile_blueprints is a SYNTHETIC bridge-side composition
    over compile_blueprint. paths REQUIRED; continue_on_error optional
    (default true)."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "bulk_compile_blueprints"), None)
    assert tool is not None
    assert tool["inputSchema"]["required"] == ["paths"]
    props = tool["inputSchema"]["properties"]
    assert props["paths"]["type"] == "array"
    assert props["paths"]["items"]["type"] == "string"
    assert props["continue_on_error"]["type"] == "boolean"
    assert "bulk_compile_blueprints" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["bulk_compile_blueprints"] is bridge.synthetic_bulk_compile_blueprints


def test_bulk_compile_blueprints_happy_path():
    """All compiles succeed -> ok=true, succeeded==total, failed==0."""
    compile_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    with patch.object(bridge, "call_ue", side_effect=[compile_resp, compile_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 420, "method": "tools/call",
            "params": {"name": "bulk_compile_blueprints", "arguments": {
                "paths": ["/Game/BP_A", "/Game/BP_B"],
            }},
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("compile_blueprint", {"path": "/Game/BP_A"})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["total"] == 2
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert all(r["ok"] for r in body["results"])


def test_bulk_compile_blueprints_partial_failure_continues_by_default():
    """continue_on_error=true (default): second compile fails, third still
    attempted; per-path error preserved."""
    ok_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "compile error"}}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp, err_resp, ok_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 421, "method": "tools/call",
            "params": {"name": "bulk_compile_blueprints", "arguments": {
                "paths": ["/Game/BP_A", "/Game/BP_B", "/Game/BP_C"],
            }},
        })

    assert m.call_count == 3
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["succeeded"] == 2
    assert body["failed"] == 1
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error"]["code"] == -32000


def test_bulk_compile_blueprints_continue_on_error_false_halts():
    """continue_on_error=false: first failure aborts; third path never
    attempted."""
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "compile error"}}
    with patch.object(bridge, "call_ue", side_effect=[err_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 422, "method": "tools/call",
            "params": {"name": "bulk_compile_blueprints", "arguments": {
                "paths": ["/Game/BP_A", "/Game/BP_B", "/Game/BP_C"],
                "continue_on_error": False,
            }},
        })

    assert m.call_count == 1
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["succeeded"] == 0
    assert body["failed"] == 1
    assert len(body["results"]) == 1


def test_bulk_compile_blueprints_rejects_missing_paths():
    """paths is required at schema level."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 423, "method": "tools/call",
        "params": {"name": "bulk_compile_blueprints", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "missing_required_field" in resp["error"]["message"]


def test_bulk_compile_blueprints_rejects_path_with_nul_byte():
    """NUL byte in any path -> -32602 + path_invalid before any compile call."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 424, "method": "tools/call",
            "params": {"name": "bulk_compile_blueprints", "arguments": {
                "paths": ["/Game/Good", "/Game/Bad\x00Path"],
            }},
        })
    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "paths[1]" in resp["error"]["message"]
    assert "NUL" in resp["error"]["message"]


def test_bulk_compile_blueprints_rejects_path_with_dotdot_segment():
    """`..` as a path SEGMENT -> -32602 + path_invalid before any compile."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 425, "method": "tools/call",
            "params": {"name": "bulk_compile_blueprints", "arguments": {
                "paths": ["/Game/Foo/../Bar"],
            }},
        })
    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert ".." in resp["error"]["message"]


def test_bulk_compile_blueprints_rejects_too_many_paths():
    """More than 1000 paths -> -32602 (invalid_paths_shape)."""
    paths = [f"/Game/BP_{i}" for i in range(1001)]
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 426, "method": "tools/call",
            "params": {"name": "bulk_compile_blueprints", "arguments": {"paths": paths}},
        })
    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "invalid_paths_shape" in resp["error"]["message"]


# ---- audit_blueprint_compile_status ---------------------------------------

def test_audit_blueprint_compile_status_is_synthetic():
    """Wave B: audit_blueprint_compile_status is a SYNTHETIC bridge-side
    composition over find_assets + inspect_blueprint. All params optional."""
    tool = next((t for t in bridge.TOOLS if t["name"] == "audit_blueprint_compile_status"), None)
    assert tool is not None
    assert tool["inputSchema"].get("required", []) == []
    props = tool["inputSchema"]["properties"]
    assert props["path_under"]["type"] == "string"
    assert props["compile_failures_only"]["type"] == "boolean"
    assert "audit_blueprint_compile_status" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["audit_blueprint_compile_status"] is bridge.synthetic_audit_blueprint_compile_status


def test_audit_blueprint_compile_status_happy_path_failures_only():
    """find_assets returns 2 BPs; inspect_blueprint returns Error for one
    and UpToDate for the other. compile_failures_only=true (default) ->
    problem_assets only includes the Error BP."""
    find_resp = {
        "jsonrpc": "2.0", "id": 1, "result": {
            "ok": True, "matched": 2, "returned": 2,
            "assets": [
                {"name": "BP_Broken", "package_path": "/Game/BP_Broken", "class": "Blueprint"},
                {"name": "BP_Good", "package_path": "/Game/BP_Good", "class": "Blueprint"},
            ],
        },
    }
    broken = {"jsonrpc": "2.0", "id": 1, "result": {"blueprint_status": "Error"}}
    good = {"jsonrpc": "2.0", "id": 1, "result": {"blueprint_status": "UpToDate"}}
    with patch.object(bridge, "call_ue", side_effect=[find_resp, broken, good]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 430, "method": "tools/call",
            "params": {"name": "audit_blueprint_compile_status", "arguments": {}},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["scanned"] == 2
    assert body["by_status"]["Error"] == 1
    assert body["by_status"]["UpToDate"] == 1
    assert len(body["problem_assets"]) == 1
    assert body["problem_assets"][0]["path"] == "/Game/BP_Broken"
    assert body["problem_assets"][0]["status"] == "Error"


def test_audit_blueprint_compile_status_compile_failures_only_false_lists_all():
    """compile_failures_only=false -> problem_assets includes every scanned
    BP regardless of status."""
    find_resp = {
        "jsonrpc": "2.0", "id": 1, "result": {
            "ok": True, "matched": 2, "returned": 2,
            "assets": [
                {"name": "BP_A", "package_path": "/Game/BP_A", "class": "Blueprint"},
                {"name": "BP_B", "package_path": "/Game/BP_B", "class": "Blueprint"},
            ],
        },
    }
    up_to_date = {"jsonrpc": "2.0", "id": 1, "result": {"blueprint_status": "UpToDate"}}
    with patch.object(bridge, "call_ue", side_effect=[find_resp, up_to_date, up_to_date]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 431, "method": "tools/call",
            "params": {"name": "audit_blueprint_compile_status", "arguments": {
                "compile_failures_only": False,
            }},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert len(body["problem_assets"]) == 2


def test_audit_blueprint_compile_status_missing_status_field_buckets_unknown():
    """When inspect_blueprint doesn't return blueprint_status (current UE 5.7
    state), every BP buckets as Unknown. The audit shape stays stable for
    when the C++ side eventually adds the field."""
    find_resp = {
        "jsonrpc": "2.0", "id": 1, "result": {
            "ok": True, "matched": 1, "returned": 1,
            "assets": [{"name": "BP_X", "package_path": "/Game/BP_X", "class": "Blueprint"}],
        },
    }
    # inspect_blueprint result without blueprint_status field (matches
    # current Handler_InspectBlueprint.cpp output as of UE 5.7).
    inspect_no_status = {"jsonrpc": "2.0", "id": 1, "result": {
        "parent_class": "Actor", "variables": [], "function_graphs": [], "event_graphs": []}}
    with patch.object(bridge, "call_ue", side_effect=[find_resp, inspect_no_status]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 432, "method": "tools/call",
            "params": {"name": "audit_blueprint_compile_status", "arguments": {}},
        })

    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["by_status"]["Unknown"] == 1
    assert len(body["problem_assets"]) == 1  # Unknown counts as a "problem" under failures_only=true


def test_audit_blueprint_compile_status_find_failed_propagates():
    """When find_assets fails, the synthetic surfaces find_failed."""
    find_err = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32099, "message": "UE unreachable"}}
    with patch.object(bridge, "call_ue", side_effect=[find_err]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 433, "method": "tools/call",
            "params": {"name": "audit_blueprint_compile_status", "arguments": {}},
        })
    assert "error" in resp
    assert "find_failed" in resp["error"]["message"]


def test_audit_blueprint_compile_status_rejects_non_bool_compile_failures_only():
    """compile_failures_only must be a bool."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 434, "method": "tools/call",
        "params": {"name": "audit_blueprint_compile_status", "arguments": {
            "compile_failures_only": "yes",
        }},
    })
    assert resp["error"]["code"] == -32602
    assert "compile_failures_only" in resp["error"]["message"]


# ---- shared _validate_asset_path helper -----------------------------------

def test_validate_asset_path_helper_accepts_valid_path():
    """The hoisted shared helper returns None on valid paths."""
    assert bridge._validate_asset_path("tool", "/Game/Foo/Bar", "path") is None


def test_validate_asset_path_helper_rejects_non_string():
    msg = bridge._validate_asset_path("tool", 42, "path")
    assert msg is not None
    assert "path_must_be_string" in msg


def test_validate_asset_path_helper_rejects_nul_byte():
    msg = bridge._validate_asset_path("tool", "/Game/A\x00B", "paths[0]")
    assert msg is not None
    assert "NUL" in msg
    assert "paths[0]" in msg


def test_validate_asset_path_helper_rejects_dotdot_segment():
    msg = bridge._validate_asset_path("tool", "/Game/Foo/../Bar", "path")
    assert msg is not None
    assert ".." in msg


def test_validate_asset_path_helper_allows_consecutive_dots_inside_segment():
    """`/Game/My..Asset` is a legitimate asset name shape; segment-aware
    check must let it through (the same allowance as bulk_delete_assets)."""
    assert bridge._validate_asset_path("tool", "/Game/My..Asset", "path") is None


def test_move_asset_in_tools_catalog():
    """v0.7.0: move_asset takes path + dest_folder, both required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "move_asset"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"path", "dest_folder"}


def test_rename_asset_in_tools_catalog():
    """v0.7.0: rename_asset takes path + new_name, both required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "rename_asset"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"path", "new_name"}


def test_duplicate_asset_in_tools_catalog():
    """duplicate_asset takes path + dest_path, both required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "duplicate_asset"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"path", "dest_path"}


def test_delete_asset_in_tools_catalog():
    """v0.7.0: delete_asset takes path required + optional force flag."""
    t = next((t for t in bridge.TOOLS if t["name"] == "delete_asset"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert "force" in t["inputSchema"]["properties"]
    assert t["inputSchema"]["properties"]["force"]["type"] == "boolean"


def test_inspect_sequence_in_tools_catalog():
    """v0.8.0: inspect_sequence requires 'path'."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_sequence"), None)
    assert t is not None, "inspect_sequence must be in TOOLS catalog"
    assert "path" in t["inputSchema"]["properties"]
    assert t["inputSchema"]["required"] == ["path"]


def test_create_sequence_in_tools_catalog():
    """v0.8.0: create_sequence requires path + name."""
    t = next((t for t in bridge.TOOLS if t["name"] == "create_sequence"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"path", "name"}


def test_bind_actor_to_sequence_in_tools_catalog():
    """v0.8.0: bind_actor_to_sequence requires sequence_path + actor_name."""
    t = next((t for t in bridge.TOOLS if t["name"] == "bind_actor_to_sequence"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"sequence_path", "actor_name"}


def test_create_material_instance_in_tools_catalog():
    """v0.9.0: create_material_instance requires parent_path + path + name."""
    t = next((t for t in bridge.TOOLS if t["name"] == "create_material_instance"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"parent_path", "path", "name"}


def test_set_mi_parameter_in_tools_catalog():
    """v0.9.0: set_mi_parameter requires path + parameter + type + value."""
    t = next((t for t in bridge.TOOLS if t["name"] == "set_mi_parameter"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"path", "parameter", "type", "value"}
    assert t["inputSchema"]["properties"]["type"]["enum"] == ["scalar", "vector", "texture"]


def test_inspect_material_in_tools_catalog():
    """v0.9.0: inspect_material requires 'path' only."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_material"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]


def test_inspect_material_instance_in_tools_catalog():
    """v0.9.0: inspect_material_instance requires 'path' only."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_material_instance"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]


def test_run_python_file_in_tools_catalog():
    """v0.10.0: run_python_file takes a 'path' to a .py file on disk."""
    t = next((t for t in bridge.TOOLS if t["name"] == "run_python_file"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    props = t["inputSchema"]["properties"]
    assert props["path"]["type"] == "string"


def test_fix_up_redirectors_in_tools_catalog():
    """v0.10.0: fix_up_redirectors takes a folder 'path' (required, no default
    -- avoid accidentally rewriting an entire project)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "fix_up_redirectors"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    props = t["inputSchema"]["properties"]
    assert props["path"]["type"] == "string"


def test_apply_python_to_selection_in_tools_catalog():
    """v0.10.0: apply_python_to_selection takes 'code'; injects `selection` and
    `selected_assets` locals before exec."""
    t = next((t for t in bridge.TOOLS if t["name"] == "apply_python_to_selection"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["code"]
    props = t["inputSchema"]["properties"]
    assert props["code"]["type"] == "string"


def test_compile_blueprint_in_tools_catalog():
    """v0.10.0: compile_blueprint takes path (required) + skip_save (optional)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "compile_blueprint"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    props = t["inputSchema"]["properties"]
    assert props["path"]["type"] == "string"
    assert "skip_save" in props
    assert props["skip_save"]["type"] == "boolean"


def test_get_console_variable_in_tools_catalog():
    """v0.10.1: get_console_variable takes a single required 'name'."""
    t = next((t for t in bridge.TOOLS if t["name"] == "get_console_variable"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["name"]
    props = t["inputSchema"]["properties"]
    assert props["name"]["type"] == "string"


def test_set_console_variable_in_tools_catalog():
    """v0.10.1: set_console_variable takes 'name' + polymorphic 'value'
    (string|number|bool); both required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "set_console_variable"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"name", "value"}
    props = t["inputSchema"]["properties"]
    assert props["name"]["type"] == "string"
    # The polymorphic value field uses JSON Schema's union-type list.
    assert set(props["value"]["type"]) == {"string", "number", "boolean"}


def test_poll_events_in_tools_catalog():
    """v0.11.0 (Tier 2 tracer bullet): poll_events takes no required params;
    optional since_seq (int), max_count (int), event_filter (array of string)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "poll_events"), None)
    assert t is not None
    # All fields optional -- "required" should be absent or empty.
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    assert props["since_seq"]["type"] == "integer"
    assert props["max_count"]["type"] == "integer"
    assert props["event_filter"]["type"] == "array"
    assert props["event_filter"]["items"]["type"] == "string"


def test_find_console_variables_in_tools_catalog():
    """v0.12.0 (language-shim experiment, PR #46): find_console_variables
    is a C++ handler with optional prefix + limit."""
    t = next((t for t in bridge.TOOLS if t["name"] == "find_console_variables"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    assert props["prefix"]["type"] == "string"
    assert props["limit"]["type"] == "integer"


def test_inspect_static_mesh_in_tools_catalog():
    """v0.12.0 (language-shim experiment, PR #46): inspect_static_mesh
    requires path."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_static_mesh"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"


def test_inspect_niagara_system_in_tools_catalog():
    """Tier 3: inspect_niagara_system requires path. C++ handler with
    EnsureFullyLoaded() discipline (UNiagaraSystem is LazyOnDemand)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_niagara_system"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"


def test_inspect_anim_blueprint_in_tools_catalog():
    """Tier 3: inspect_anim_blueprint requires path. C++ handler that
    guards UAnimBlueprintGeneratedClass for null pre-compile."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_anim_blueprint"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"


def test_inspect_landscape_in_tools_catalog():
    """Tier 3: inspect_landscape diverges from siblings -- takes optional
    name AND/OR guid (NOT a 'path'), since landscapes are scene actors not
    assets. With both omitted, the handler returns the sole landscape if
    exactly one exists."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_landscape"), None)
    assert t is not None
    # No required fields -- both name and guid are optional
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    assert props["name"]["type"] == "string"
    assert props["guid"]["type"] == "string"
    # Critical: this handler does NOT take a 'path' field (diverges from siblings)
    assert "path" not in props


def test_inspect_skeletal_mesh_in_tools_catalog():
    """Tier 3: inspect_skeletal_mesh requires path. Returns LOD geometry
    (via GetResourceForRendering->LODRenderData), bones, materials, morphs,
    clothing, physics asset. Bounds shape matches sibling Inspect* handlers."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_skeletal_mesh"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"


def test_inspect_anim_montage_in_tools_catalog():
    """Tier 3: inspect_anim_montage requires path. Completes the animation
    introspection trio (anim_blueprint + skeletal_mesh + anim_montage)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_anim_montage"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"


def test_inspect_sound_attenuation_in_tools_catalog():
    """Tier 3: inspect_sound_attenuation requires path. C++ handler that
    reads USoundAttenuation 3D-playback rules organized into gated feature
    sub-objects (distance / spatialization / air_absorption / listener_focus
    / occlusion / reverb_send / priority_attenuation / feature_flags). Each
    sub-object collapses to {\"enabled\": false} when its master bool is
    off, keeping default-asset JSON compact. Completes the audio
    introspection trio (cue + wave + attenuation)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_sound_attenuation"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_sound_attenuation" not in bridge.SYNTHETIC_TOOLS


def test_inspect_sound_wave_in_tools_catalog():
    """Tier 3: inspect_sound_wave requires path. C++ handler that reads
    USoundWave structural surface (sample rate, channels, frames, duration,
    compression, sound group, looping/streaming flags, subtitle + cue-point
    counts). Pairs with inspect_sound_cue + (forthcoming) inspect_sound_
    attenuation as the audio introspection trio. LazyOnDemand caveat
    handled by reading only declarative fields."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_sound_wave"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_sound_wave" not in bridge.SYNTHETIC_TOOLS


def test_inspect_sound_cue_in_tools_catalog():
    """Tier 3: inspect_sound_cue requires path. C++ handler that reads
    USoundCue base + node-graph surface (FirstNode class taxonomy + full
    AllNodes list, null-skipped). Cross-links to USoundAttenuation via
    attenuation_settings field. Multi-agent dispatch with literal-template
    Codex prompt + extra-high reasoning per memory directive."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_sound_cue"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_sound_cue" not in bridge.SYNTHETIC_TOOLS


def test_inspect_physics_asset_in_tools_catalog():
    """Tier 3: inspect_physics_asset requires path. C++ handler that reads
    UPhysicsAsset body setups (one per simulated bone), constraint setups
    (joints between bodies), bounds-bodies subset, and named profiles.
    Cross-links to USkeletalMesh via preview_skeletal_mesh path. Null-skips
    TObjectPtr<USkeletalBodySetup> + TObjectPtr<UPhysicsConstraintTemplate>
    entries (PR #55->#57 lesson)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_physics_asset"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_physics_asset" not in bridge.SYNTHETIC_TOOLS


def test_inspect_curve_in_tools_catalog():
    """Tier 3: inspect_curve requires path. C++ handler that reads
    UCurveBase channel layout (1 for UCurveFloat, 4 for UCurveLinearColor,
    3 for UCurveVector), per-channel key count + ranges, global time +
    value range across all channels."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_curve"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_curve" not in bridge.SYNTHETIC_TOOLS


def test_inspect_texture_in_tools_catalog():
    """Tier 3: inspect_texture requires path. C++ handler that reads UTexture
    base + UTexture2D-specific surface (size/mips/pixel_format/imported_size
    emitted conditionally only for UTexture2D). Pairs with configure_texture
    sibling (which mutates these fields)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_texture"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_texture" not in bridge.SYNTHETIC_TOOLS


def test_inspect_data_table_in_tools_catalog():
    """Tier 3: inspect_data_table requires path. C++ handler that reads
    UDataTable RowStruct identity, sorted row names, and per-property
    name+type via TFieldIterator<FProperty> with EFieldIterationFlags::None
    (skipping super fields). Null-guards RowStruct (freshly-created
    DataTables can have no struct assigned)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_data_table"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    # Real C++ handler, not synthetic
    assert "inspect_data_table" not in bridge.SYNTHETIC_TOOLS


def test_inspect_widget_blueprint_in_tools_catalog():
    """Tier 3: inspect_widget_blueprint requires path. C++ handler that
    reads Widget-BP-specific surface (animations, delegate bindings,
    inherited named slots, palette category) NOT covered by sibling
    inspect_blueprint (UBlueprint vars/graphs) or inspect_widget_tree
    (widget hierarchy). Cross-link via shared asset path."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_widget_blueprint"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    # Must NOT be synthetic (this is a real C++ handler)
    assert "inspect_widget_blueprint" not in bridge.SYNTHETIC_TOOLS


def test_get_camera_transform_is_synthetic():
    """v0.12.0 (language-shim experiment, PR #46): get_camera_transform
    is a SYNTHETIC bridge-side handler that composes execute_unreal_python
    + get_log_lines via marker pattern."""
    t = next((t for t in bridge.TOOLS if t["name"] == "get_camera_transform"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    assert "get_camera_transform" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["get_camera_transform"] is bridge.synthetic_get_camera_transform


def test_get_camera_transform_happy_path_omits_ok_true_wrapper():
    """Post-refactor (2026-05-12 deferred bridge-audit #3): get_camera_transform
    delegates to `_run_marker_pattern`, which returns the parsed JSON
    payload DIRECTLY as the success envelope. The hand-rolled form used to
    wrap the payload in `{ok: True, **data}` -- this test pins that the
    `ok: True` key is intentionally GONE so a future refactor doesn't
    accidentally reintroduce it (which would silently expand the envelope
    surface back to where it was)."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "location": {"x": 100.0, "y": 200.0, "z": 300.0},
        "rotation": {"pitch": -15.0, "yaw": 90.0, "roll": 0.0},
    }
    marker_hex = "abcdef012345"
    log_line = f"__CAM_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": log_line}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 60, "method": "tools/call",
                "params": {"name": "get_camera_transform", "arguments": {}},
            })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    # Post-refactor: location and rotation are present, no `ok: True` wrapper.
    assert got["location"] == {"x": 100.0, "y": 200.0, "z": 300.0}
    assert got["rotation"] == {"pitch": -15.0, "yaw": 90.0, "roll": 0.0}
    assert "ok" not in got, (
        "post-refactor envelope must NOT include `ok: True` -- the helper "
        "returns the parsed JSON payload directly, no wrapper."
    )


def test_get_camera_transform_marker_not_found_returns_logical_error_envelope():
    """Post-refactor: marker_not_found is now a logical-error envelope (the
    helper's standard shape) -- NOT a JSON-RPC `-32603` transport error like
    the hand-rolled form used to return. Callers that retry on transport
    errors only would otherwise miss the retry-friendly logical-error.
    """
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "unrelated log line, no marker"}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex="deadbeefcaf1")
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 61, "method": "tools/call",
                "params": {"name": "get_camera_transform", "arguments": {}},
            })

    # Logical errors come back as success envelopes (isError=False) with
    # ok:False + error_code inside the inner payload.
    assert "error" not in resp, (
        f"marker_not_found must NOT be a JSON-RPC transport error post-refactor; "
        f"got envelope={resp}"
    )
    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_not_found"


def test_set_camera_transform_rejects_partial_update_when_get_returns_logical_error():
    """If a partial-update call needs to read the current camera state but
    get_camera_transform returns a logical-error envelope (e.g.
    marker_not_found from a flooded LogPython ring), set_camera_transform
    MUST refuse cleanly with -32603 rather than silently snap the omitted
    side to (0, 0, 0).

    This is the second-order failure that the pre-refactor set code didn't
    handle: it checked only for JSON-RPC transport errors. Post-refactor of
    get_camera_transform, marker_not_found is no longer a transport error --
    it's a logical-error envelope -- and the set code now has an explicit
    layer 3 check to catch it.

    Without this guard, a caller running partial-update during a busy
    LogPython burst could see their omitted side silently zero out. This
    test pins the explicit refusal."""
    # First call_ue: get_camera_transform's inner execute_unreal_python.
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    # Second call_ue: get_camera_transform's inner get_log_lines. No marker
    # -> _run_marker_pattern returns marker_not_found logical-error envelope.
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "no marker here, simulated buffer overflow"}
    ]}}
    # set_camera_transform invokes get_camera_transform, so we feed two
    # responses to call_ue in order.
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex="cafebabe1234")
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 62, "method": "tools/call",
                "params": {
                    "name": "set_camera_transform",
                    # Partial update: location supplied, rotation omitted.
                    # Forces set to read current rotation -> hits the
                    # marker_not_found path on get.
                    "arguments": {"location": {"x": 1, "y": 2, "z": 3}},
                },
            })

    assert "error" in resp, (
        "set_camera_transform must refuse a partial update when get returns "
        "a logical-error envelope -- silent (0,0,0) fallback is a regression."
    )
    assert resp["error"]["code"] == -32603
    msg = resp["error"]["message"]
    assert "marker_not_found" in msg, (
        f"refusal message must surface the upstream error_code so a caller "
        f"can triage; got: {msg!r}"
    )


def test_set_camera_transform_is_synthetic():
    """v0.12.0 (language-shim experiment, PR #46): set_camera_transform
    is a SYNTHETIC bridge-side handler with optional location + rotation."""
    t = next((t for t in bridge.TOOLS if t["name"] == "set_camera_transform"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    assert props["location"]["type"] == "object"
    assert props["rotation"]["type"] == "object"
    assert "set_camera_transform" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["set_camera_transform"] is bridge.synthetic_set_camera_transform


def test_set_camera_transform_no_op_read_when_both_location_and_rotation_omitted():
    """Calling set_camera_transform with neither location nor rotation forwards
    to synthetic_get_camera_transform (no-op read). The caller gets the
    current camera state back without mutating anything; the embedded UE
    Python for the SET branch is never compiled or sent.

    This is a deliberate edge-case in the API: omit both fields and the call
    is harmless. Tested here because the no-op-read branch had no coverage
    despite being the safest call pattern callers might lean on for
    "what's the current camera?" introspection."""
    captured = []

    def fake_call_ue(method, args):
        captured.append((method, args))
        if method == "execute_unreal_python":
            return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
        if method == "get_log_lines":
            body = {
                "location": {"x": 10.0, "y": 20.0, "z": 30.0},
                "rotation": {"pitch": 5.0, "yaw": -45.0, "roll": 0.0},
            }
            log_line = f"__CAM_abc123def456__{json.dumps(body)}__END__"
            return {"jsonrpc": "2.0", "id": 1, "result": {
                "lines": [{"category": "LogPython", "message": log_line}],
            }}
        return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}

    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex="abc123def456")
    with patch.object(bridge, "call_ue", side_effect=fake_call_ue), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 100, "method": "tools/call",
            "params": {"name": "set_camera_transform", "arguments": {}},
        })

    # The no-op-read branch forwards to get_camera_transform, which runs
    # the marker pattern: execute_unreal_python + get_log_lines.
    assert any(method == "execute_unreal_python" for method, _ in captured)
    assert any(method == "get_log_lines" for method, _ in captured)

    # No SET-side py_code emitted (no `unreal.UnrealEditorSubsystem` mention
    # in the exec call, which is the SET-branch signature).
    set_branch_calls = [
        args for method, args in captured
        if method == "execute_unreal_python"
        and "set_level_viewport_camera_info" in args.get("code", "")
    ]
    assert not set_branch_calls, "no-op-read must not run the SET-side py_code"

    # Response surfaces the read-back camera state directly.
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["location"] == {"x": 10.0, "y": 20.0, "z": 30.0}
    assert body["rotation"] == {"pitch": 5.0, "yaw": -45.0, "roll": 0.0}


def test_set_camera_transform_uses_property_set_not_positional_rotator() -> None:
    """The embedded Python that set_camera_transform sends to UE must use
    property-set assignment (`_r.pitch = ...; _r.yaw = ...; _r.roll = ...`)
    rather than the positional `unreal.Rotator(rp, ry, rr)` form.

    Live MCP probe on 2026-05-12 confirmed UE 5.7's Python wrapper takes
    Rotator(roll, pitch, yaw) POSITIONALLY -- the args follow FRotator's
    struct-memory order, not the named-property order. The positional form
    silently scrambles rotation: a caller asking for pitch=-20/yaw=45/roll=0
    used to get back pitch=45/yaw=0/roll=-20 from the next
    get_camera_transform. Property-set sidesteps the trap by binding values
    to named slots directly, so the round-trip is lossless regardless of
    UE's constructor convention.

    The test inspects the py_code that the synthetic sends to call_ue and
    asserts the property-set form is used and the positional form is not.
    """
    captured: dict[str, str] = {}

    def fake_call_ue(method: str, args: dict):
        if method == "execute_unreal_python":
            captured["code"] = args.get("code", "")
        # Mimic a successful round-trip so set_camera_transform finishes.
        return {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}

    with patch.object(bridge, "call_ue", side_effect=fake_call_ue):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 99, "method": "tools/call",
            "params": {
                "name": "set_camera_transform",
                "arguments": {
                    "location": {"x": 1, "y": 2, "z": 3},
                    "rotation": {"pitch": -20, "yaw": 45, "roll": 7},
                },
            },
        })

    assert resp["result"]["isError"] is False
    code = captured.get("code", "")
    assert code, "no execute_unreal_python call was captured"

    # The fix uses an empty constructor + named-property assignment.
    assert "unreal.Rotator()" in code, (
        "set_camera_transform must build the Rotator via the empty "
        "constructor + property assignment; the positional form silently "
        "scrambles rotation in UE 5.7 Python."
    )
    assert "_r.pitch = -20" in code
    assert "_r.yaw = 45" in code
    assert "_r.roll = 7" in code

    # And critically: the broken positional form -- Rotator(<num>, <num>, <num>)
    # with three numeric args -- must NOT be present. Build the regex at
    # runtime so this test file's own source can never accidentally match
    # itself when string-searched.
    import re
    broken_form = re.compile(
        r"unreal\." + "Rotator\\(\\s*-?[\\d.]+\\s*,\\s*-?[\\d.]+\\s*,\\s*-?[\\d.]+\\s*\\)"
    )
    assert not broken_form.search(code), (
        "set_camera_transform regressed to positional unreal.Rotator(a,b,c) "
        "form -- UE 5.7 Python interprets those args as (roll, pitch, yaw), "
        "not (pitch, yaw, roll), and the rotation will be silently scrambled."
    )


def test_screenshot_actor_is_synthetic():
    """screenshot_actor is a SYNTHETIC bridge-side handler that composes
    focus_actor + get_viewport_screenshot. Requires only 'name'."""
    t = next((t for t in bridge.TOOLS if t["name"] == "screenshot_actor"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["name"]
    assert t["inputSchema"]["properties"]["name"]["type"] == "string"
    assert "screenshot_actor" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["screenshot_actor"] is bridge.synthetic_screenshot_actor


def test_compile_mod_pak_is_synthetic():
    """compile_mod_pak is a SYNTHETIC bridge-side handler (David's PR #84 +
    integration cleanup): shells RunUAT.bat BuildMod / BuildPlugin headless
    to produce a .pak (BuildMod) or redistributable plugin package
    (BuildPlugin). project_path AND output_dir are both required at the
    schema level so the success-verification step has a known dir to scan."""
    t = next((t for t in bridge.TOOLS if t["name"] == "compile_mod_pak"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"project_path", "output_dir"}
    assert t["inputSchema"]["properties"]["project_path"]["type"] == "string"
    assert t["inputSchema"]["properties"]["output_dir"]["type"] == "string"
    assert t["inputSchema"]["properties"]["uat_command"]["enum"] == ["BuildMod", "BuildPlugin"]
    assert "compile_mod_pak" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["compile_mod_pak"] is bridge.synthetic_compile_mod_pak


def test_compile_mod_pak_direct_is_synthetic():
    """compile_mod_pak_direct is a SYNTHETIC bridge-side handler that
    bypasses RunUAT entirely by invoking UnrealPak.exe directly with a
    response file. Complements compile_mod_pak for Dev Kits where RunUAT
    BuildMod is broken (Funcom Conan Exiles Enhanced ScriptModules bug).
    Requires unreal_pak_path, response_file, and output_pak at the schema
    level so success verification has known artefact paths."""
    t = next((t for t in bridge.TOOLS if t["name"] == "compile_mod_pak_direct"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"unreal_pak_path", "response_file", "output_pak"}
    assert t["inputSchema"]["properties"]["unreal_pak_path"]["type"] == "string"
    assert t["inputSchema"]["properties"]["response_file"]["type"] == "string"
    assert t["inputSchema"]["properties"]["output_pak"]["type"] == "string"
    assert t["inputSchema"]["properties"]["compression"]["enum"] == ["Zlib", "Gzip", "Oodle", "None"]
    assert t["inputSchema"]["properties"]["compression"]["default"] == "Zlib"
    assert "compile_mod_pak_direct" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["compile_mod_pak_direct"] is bridge.synthetic_compile_mod_pak_direct


def test_compile_mod_pak_rejects_non_positive_timeout():
    """timeout_sec <= 0 must short-circuit with -32602 BEFORE subprocess.run
    is reached. subprocess.run(timeout=0) raises TimeoutExpired immediately,
    which would surface as a misleading "compile timed out" error envelope
    even though no compile ever started. This is a tools/call boundary
    contract: positive timeout in, positive timeout enforced."""
    for bad in (0, -1, -3600, "0", "-5", 0.0, -0.5):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {
                "name": "compile_mod_pak",
                "arguments": {
                    "project_path": "/nonexistent.uproject",
                    "output_dir": "/tmp/out",
                    "mod_name": "X",
                    "timeout_sec": bad,
                },
            },
        })
        assert "error" in resp, f"expected error envelope for timeout_sec={bad!r}, got {resp}"
        assert resp["error"]["code"] == -32602, f"expected -32602 for timeout_sec={bad!r}"
        assert "timeout_sec" in resp["error"]["message"], (
            f"error message must mention timeout_sec; got {resp['error']['message']!r}"
        )


def test_compile_mod_pak_accepts_float_timeout():
    """timeout_sec accepts int, float, and numeric string forms uniformly via
    int(float(x)). JSON callers that stringify numbers ("3"), JS clients
    that pass floats (3.0), and Python clients that pass ints (3) all
    converge on the same int value. Truncating floats is documented behavior."""
    for good in (3, 3.9, "3", "3.9", 1800):
        # project_path check fires before subprocess.run so we can probe
        # validation order without mocking subprocess. Expect project_path
        # error, NOT timeout_sec error -> proves timeout parse succeeded.
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 43, "method": "tools/call",
            "params": {
                "name": "compile_mod_pak",
                "arguments": {
                    "project_path": "/nonexistent.uproject",
                    "output_dir": "/tmp/out",
                    "mod_name": "X",
                    "timeout_sec": good,
                },
            },
        })
        assert "error" in resp
        assert "project_path" in resp["error"]["message"], (
            f"expected project_path error (proving timeout parsed OK) for "
            f"timeout_sec={good!r}; got {resp['error']['message']!r}"
        )


def test_compile_mod_pak_rejects_wrong_type_extra_args():
    """extra_args of wrong type (string, number, dict, bool) must short-circuit
    with -32602 instead of silently coercing to []. Silent coercion would
    mask client bugs at a JSON-RPC boundary where the schema declares
    extra_args as 'array'. Omitting extra_args entirely (None) is still OK."""
    for bad in ("not-a-list", 42, {"foo": "bar"}, True, 3.14):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 44, "method": "tools/call",
            "params": {
                "name": "compile_mod_pak",
                "arguments": {
                    "project_path": "/nonexistent.uproject",
                    "output_dir": "/tmp/out",
                    "mod_name": "X",
                    "extra_args": bad,
                },
            },
        })
        assert "error" in resp, f"expected error envelope for extra_args={bad!r}, got {resp}"
        assert resp["error"]["code"] == -32602, f"expected -32602 for extra_args={bad!r}"
        assert "extra_args" in resp["error"]["message"], (
            f"error message must mention extra_args; got {resp['error']['message']!r}"
        )


def test_compile_mod_pak_rejects_unparseable_timeout():
    """timeout_sec of un-parseable type (dict, list, arbitrary string) must
    short-circuit with -32602 instead of silently defaulting to 1800. Silent
    default would mask client bugs (e.g. caller passes {minutes:30} expecting
    1800s but actually gets default 1800 — looks correct, isn't)."""
    for bad in ("thirty-minutes", "1800s", {"minutes": 30}, [1800], object()):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 45, "method": "tools/call",
            "params": {
                "name": "compile_mod_pak",
                "arguments": {
                    "project_path": "/nonexistent.uproject",
                    "output_dir": "/tmp/out",
                    "mod_name": "X",
                    "timeout_sec": bad,
                },
            },
        })
        assert "error" in resp, f"expected error envelope for timeout_sec={bad!r}, got {resp}"
        assert resp["error"]["code"] == -32602, f"expected -32602 for timeout_sec={bad!r}"
        assert "timeout_sec" in resp["error"]["message"], (
            f"error message must mention timeout_sec; got {resp['error']['message']!r}"
        )


def test_compile_mod_pak_omitted_extra_args_defaults_empty():
    """extra_args omitted (None / absent from args) is the documented happy
    path — defaults to [] without error. This is the difference between
    'omitted optional field' and 'wrong-type field'."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 46, "method": "tools/call",
        "params": {
            "name": "compile_mod_pak",
            "arguments": {
                "project_path": "/nonexistent.uproject",
                "output_dir": "/tmp/out",
                "mod_name": "X",
                # extra_args omitted on purpose
            },
        },
    })
    assert "error" in resp
    # Should error on project_path (validation downstream), NOT on extra_args.
    assert "project_path" in resp["error"]["message"]


def test_bulk_delete_assets_is_synthetic():
    """bulk_delete_assets is a SYNTHETIC bridge-side handler (PR #90 — Codex
    parallel-dispatch test): loops over delete_asset calls and aggregates
    partial-success results. paths is required; continue_on_error is
    optional and defaults to true."""
    t = next((t for t in bridge.TOOLS if t["name"] == "bulk_delete_assets"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"paths"}
    assert t["inputSchema"]["properties"]["paths"]["type"] == "array"
    assert t["inputSchema"]["properties"]["paths"]["items"]["type"] == "string"
    assert t["inputSchema"]["properties"]["continue_on_error"]["type"] == "boolean"
    assert t["inputSchema"]["properties"]["continue_on_error"]["default"] is True
    assert "bulk_delete_assets" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["bulk_delete_assets"] is bridge.synthetic_bulk_delete_assets


def test_bulk_delete_assets_happy_path():
    """All deletes succeed -> ok=True, deleted == total, failed == 0,
    per-path results carry ok=True + null error fields."""
    with patch.object(bridge, "call_ue", return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {
                "name": "bulk_delete_assets",
                "arguments": {"paths": ["/Game/Foo", "/Game/Bar"]},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("delete_asset", {"path": "/Game/Foo"})
    assert m.call_args_list[1].args == ("delete_asset", {"path": "/Game/Bar"})
    assert resp["result"]["isError"] is False
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body == {
        "ok": True,
        "total": 2,
        "deleted": 2,
        "failed": 0,
        "results": [
            {"path": "/Game/Foo", "ok": True, "error_code": None, "error_message": None},
            {"path": "/Game/Bar", "ok": True, "error_code": None, "error_message": None},
        ],
    }


def test_bulk_delete_assets_partial_failure_stops_when_continue_on_error_false():
    """First delete succeeds, second fails, continue_on_error=False -> stop
    after the second call. Upstream error code is preserved in the per-path
    result; the third path is never attempted."""
    ok_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "delete_asset: has_referencers: '/Game/Bar' is referenced",
    }}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp, err_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 22, "method": "tools/call",
            "params": {
                "name": "bulk_delete_assets",
                "arguments": {
                    "paths": ["/Game/Foo", "/Game/Bar", "/Game/Baz"],
                    "continue_on_error": False,
                },
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("delete_asset", {"path": "/Game/Foo"})
    assert m.call_args_list[1].args == ("delete_asset", {"path": "/Game/Bar"})
    assert resp["result"]["isError"] is False
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["deleted"] == 1
    assert body["failed"] == 1
    assert body["results"] == [
        {"path": "/Game/Foo", "ok": True, "error_code": None, "error_message": None},
        {
            "path": "/Game/Bar",
            "ok": False,
            "error_code": -32000,
            "error_message": "delete_asset: has_referencers: '/Game/Bar' is referenced",
        },
    ]


def test_bulk_delete_assets_rejects_missing_paths():
    """Schema enforces paths as required; missing it returns -32602."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 23, "method": "tools/call",
        "params": {"name": "bulk_delete_assets", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "bulk_delete_assets" in resp["error"]["message"]


def test_bulk_delete_assets_rejects_path_with_nul_byte():
    """NUL byte in any path -> -32602 + caller-actionable message; the
    handler never forwards the malformed path to delete_asset. The error
    message identifies the offending index so a caller programmatically
    consuming the response can correct the input."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 24, "method": "tools/call",
            "params": {
                "name": "bulk_delete_assets",
                "arguments": {"paths": ["/Game/Good", "/Game/Bad\x00Asset"]},
            },
        })

    assert m.call_count == 0, "validation must short-circuit before any call_ue"
    assert resp["error"]["code"] == -32602
    assert "paths[1]" in resp["error"]["message"]
    assert "NUL" in resp["error"]["message"]


def test_bulk_delete_assets_rejects_path_with_dotdot_segment():
    """`..` as a path SEGMENT (between slashes or at ends) -> -32602. The
    check is segment-aware so legitimate asset names that happen to contain
    consecutive dots (`/Game/My..Asset`) still pass."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 25, "method": "tools/call",
            "params": {
                "name": "bulk_delete_assets",
                "arguments": {"paths": ["/Game/Maps/../Secrets"]},
            },
        })

    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "paths[0]" in resp["error"]["message"]
    assert ".." in resp["error"]["message"]


def test_bulk_delete_assets_allows_consecutive_dots_inside_segment():
    """A legitimate asset name containing `..` inside a SEGMENT (no slash
    boundary on either side) is still acceptable: `/Game/My..Asset` is a
    real UE asset name shape and must not be rejected by the segment check."""
    with patch.object(bridge, "call_ue", return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 26, "method": "tools/call",
            "params": {
                "name": "bulk_delete_assets",
                "arguments": {"paths": ["/Game/My..Asset"]},
            },
        })

    # Path passes validation -> call_ue is invoked -> happy-path envelope.
    assert m.call_count == 1
    assert m.call_args_list[0].args == ("delete_asset", {"path": "/Game/My..Asset"})
    assert resp["result"]["isError"] is False
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["deleted"] == 1


def test_bulk_move_assets_is_synthetic():
    """bulk_move_assets is a SYNTHETIC bridge-side handler that mirrors
    bulk_delete_assets's shape but composes over move_asset. paths AND
    dest_folder are both required at the schema level (a move with no
    destination is meaningless)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "bulk_move_assets"), None)
    assert t is not None
    assert set(t["inputSchema"]["required"]) == {"paths", "dest_folder"}
    assert t["inputSchema"]["properties"]["paths"]["type"] == "array"
    assert t["inputSchema"]["properties"]["paths"]["items"]["type"] == "string"
    assert t["inputSchema"]["properties"]["dest_folder"]["type"] == "string"
    assert t["inputSchema"]["properties"]["continue_on_error"]["type"] == "boolean"
    assert "bulk_move_assets" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["bulk_move_assets"] is bridge.synthetic_bulk_move_assets


def test_bulk_move_assets_happy_path():
    """All moves succeed -> ok=True, moved == total, failed == 0, per-path
    results carry ok=True + null error fields. The dest_folder field is
    echoed in the response envelope for caller correlation (move can leave
    redirectors at the source paths so the caller may want to track where
    each asset ended up)."""
    with patch.object(bridge, "call_ue", return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 50, "method": "tools/call",
            "params": {
                "name": "bulk_move_assets",
                "arguments": {
                    "paths": ["/Game/Foo", "/Game/Bar"],
                    "dest_folder": "/Game/Archive",
                },
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("move_asset", {"path": "/Game/Foo", "dest_folder": "/Game/Archive"})
    assert m.call_args_list[1].args == ("move_asset", {"path": "/Game/Bar", "dest_folder": "/Game/Archive"})
    assert resp["result"]["isError"] is False
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body == {
        "ok": True,
        "total": 2,
        "moved": 2,
        "failed": 0,
        "dest_folder": "/Game/Archive",
        "results": [
            {"path": "/Game/Foo", "ok": True, "error_code": None, "error_message": None},
            {"path": "/Game/Bar", "ok": True, "error_code": None, "error_message": None},
        ],
    }


def test_bulk_move_assets_partial_failure_stops_when_continue_on_error_false():
    """First move succeeds, second fails, continue_on_error=False -> stop
    after the second call. Upstream error code is preserved in the per-path
    result; the third path is never attempted."""
    ok_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "move_asset: name_collision: '/Game/Archive/Bar' already exists",
    }}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp, err_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 51, "method": "tools/call",
            "params": {
                "name": "bulk_move_assets",
                "arguments": {
                    "paths": ["/Game/Foo", "/Game/Bar", "/Game/Baz"],
                    "dest_folder": "/Game/Archive",
                    "continue_on_error": False,
                },
            },
        })

    assert m.call_count == 2
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["moved"] == 1
    assert body["failed"] == 1
    assert body["dest_folder"] == "/Game/Archive"
    assert body["results"][0] == {"path": "/Game/Foo", "ok": True, "error_code": None, "error_message": None}
    assert body["results"][1]["path"] == "/Game/Bar"
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error_code"] == -32000


def test_bulk_move_assets_partial_failure_continues_when_continue_on_error_true():
    """Second path fails, but continue_on_error=True (default) keeps the loop
    going and surfaces per-path errors in results[]. All three paths attempted;
    the failure does NOT abort the third call. Mirrors the
    _stops_when_continue_on_error_false test but exercises the default-on
    branch that the original partial-failure test never covered."""
    ok_resp_1 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "move_asset: not_found: '/Game/Bar'",
    }}
    ok_resp_2 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp_1, err_resp, ok_resp_2]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 51, "method": "tools/call",
            "params": {
                "name": "bulk_move_assets",
                "arguments": {
                    "paths": ["/Game/Foo", "/Game/Bar", "/Game/Baz"],
                    "dest_folder": "/Game/Archive",
                    "continue_on_error": True,
                },
            },
        })

    assert m.call_count == 3
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["moved"] == 2
    assert body["failed"] == 1
    assert body["dest_folder"] == "/Game/Archive"
    assert body["results"][0]["ok"] is True
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error_code"] == -32000
    assert body["results"][2]["ok"] is True


def test_bulk_move_assets_rejects_missing_paths():
    """Schema enforces paths as required; missing it returns -32602."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 52, "method": "tools/call",
        "params": {"name": "bulk_move_assets", "arguments": {"dest_folder": "/Game/Archive"}},
    })
    assert resp["error"]["code"] == -32602
    assert "paths" in resp["error"]["message"]


def test_bulk_move_assets_rejects_missing_dest_folder():
    """Schema enforces dest_folder as required; missing it returns -32602.
    bulk_move's distinction from bulk_delete is that move needs a target,
    so the validator rejects the missing destination before any call_ue."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 53, "method": "tools/call",
            "params": {"name": "bulk_move_assets", "arguments": {"paths": ["/Game/Foo"]}},
        })

    assert m.call_count == 0, "validation must short-circuit before any call_ue"
    assert resp["error"]["code"] == -32602
    assert "dest_folder" in resp["error"]["message"]


def test_bulk_move_assets_rejects_path_with_nul_byte():
    """Same defensive shape-checks as bulk_delete_assets (PR #115):
    NUL byte in any path -> -32602, no call_ue dispatched."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 54, "method": "tools/call",
            "params": {
                "name": "bulk_move_assets",
                "arguments": {
                    "paths": ["/Game/Good", "/Game/Bad\x00Asset"],
                    "dest_folder": "/Game/Archive",
                },
            },
        })

    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "paths[1]" in resp["error"]["message"]
    assert "NUL" in resp["error"]["message"]


def test_bulk_move_assets_rejects_dotdot_segment_in_dest_folder():
    """dest_folder gets the same defensive shape-checks as source paths
    (NUL byte + `..` segment). A `..`-traversal in the destination is a
    classic mis-input that should fail loud at the validator rather than
    silently move assets to an unintended folder."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 55, "method": "tools/call",
            "params": {
                "name": "bulk_move_assets",
                "arguments": {
                    "paths": ["/Game/Foo"],
                    "dest_folder": "/Game/Archive/../Secrets",
                },
            },
        })

    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "dest_folder" in resp["error"]["message"]
    assert ".." in resp["error"]["message"]


def test_bulk_rename_assets_is_synthetic():
    """bulk_rename_assets is the third member of the bulk_*_assets family
    (after delete + move). Schema differs from its siblings: takes a
    `renames` list of {path, new_name} objects so each asset gets a
    per-entry leaf name."""
    t = next((t for t in bridge.TOOLS if t["name"] == "bulk_rename_assets"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["renames"]
    assert t["inputSchema"]["properties"]["renames"]["type"] == "array"
    assert t["inputSchema"]["properties"]["renames"]["items"]["type"] == "object"
    assert set(t["inputSchema"]["properties"]["renames"]["items"]["required"]) == {"path", "new_name"}
    assert t["inputSchema"]["properties"]["continue_on_error"]["type"] == "boolean"
    assert "bulk_rename_assets" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["bulk_rename_assets"] is bridge.synthetic_bulk_rename_assets


def test_bulk_rename_assets_happy_path():
    """All renames succeed -> ok=True, renamed == total, per-entry results
    include both path AND new_name so the caller can build a rename map
    from the response envelope alone."""
    with patch.object(bridge, "call_ue", return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 60, "method": "tools/call",
            "params": {
                "name": "bulk_rename_assets",
                "arguments": {
                    "renames": [
                        {"path": "/Game/Foo", "new_name": "FooRenamed"},
                        {"path": "/Game/Bar", "new_name": "BarRenamed"},
                    ],
                },
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("rename_asset", {"path": "/Game/Foo", "new_name": "FooRenamed"})
    assert m.call_args_list[1].args == ("rename_asset", {"path": "/Game/Bar", "new_name": "BarRenamed"})
    assert resp["result"]["isError"] is False
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["total"] == 2
    assert body["renamed"] == 2
    assert body["failed"] == 0
    assert body["results"][0] == {
        "path": "/Game/Foo", "new_name": "FooRenamed",
        "ok": True, "error_code": None, "error_message": None,
    }


def test_bulk_rename_assets_partial_failure_stops_when_continue_on_error_false():
    """First rename succeeds, second fails, continue_on_error=False stops
    after the second call. Upstream error code preserved per entry."""
    ok_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "rename_asset: name_collision: '/Game/BarRenamed' already exists",
    }}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp, err_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 61, "method": "tools/call",
            "params": {
                "name": "bulk_rename_assets",
                "arguments": {
                    "renames": [
                        {"path": "/Game/Foo", "new_name": "FooRenamed"},
                        {"path": "/Game/Bar", "new_name": "BarRenamed"},
                        {"path": "/Game/Baz", "new_name": "BazRenamed"},
                    ],
                    "continue_on_error": False,
                },
            },
        })

    assert m.call_count == 2
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["renamed"] == 1
    assert body["failed"] == 1
    assert body["results"][1]["path"] == "/Game/Bar"
    assert body["results"][1]["new_name"] == "BarRenamed"
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error_code"] == -32000


def test_bulk_rename_assets_partial_failure_continues_when_continue_on_error_true():
    """Second rename fails, but continue_on_error=True (default) keeps the
    loop going and surfaces per-entry errors in results[]. All three renames
    attempted; the failure does NOT abort the third call. Pairs with the
    existing _stops_when_continue_on_error_false test to cover both branches."""
    ok_resp_1 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "rename_asset: name_collision: '/Game/BarRenamed' already exists",
    }}
    ok_resp_2 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp_1, err_resp, ok_resp_2]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 61, "method": "tools/call",
            "params": {
                "name": "bulk_rename_assets",
                "arguments": {
                    "renames": [
                        {"path": "/Game/Foo", "new_name": "FooRenamed"},
                        {"path": "/Game/Bar", "new_name": "BarRenamed"},
                        {"path": "/Game/Baz", "new_name": "BazRenamed"},
                    ],
                    "continue_on_error": True,
                },
            },
        })

    assert m.call_count == 3
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["renamed"] == 2
    assert body["failed"] == 1
    assert body["results"][0]["ok"] is True
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error_code"] == -32000
    assert body["results"][2]["ok"] is True


def test_bulk_rename_assets_rejects_missing_renames():
    """Schema enforces renames as required."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 62, "method": "tools/call",
        "params": {"name": "bulk_rename_assets", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "renames" in resp["error"]["message"]


def test_bulk_rename_assets_rejects_new_name_with_slash_or_dot():
    """rename_asset takes a LEAF name -- '/' and '.' are not allowed.
    Reject at the validator with a caller-actionable message rather than
    forwarding to rename_asset and surfacing a less clear UE-side error.
    The check covers both '/' (path separator) and '.' (used to separate
    package path from object name in UE asset references)."""
    with patch.object(bridge, "call_ue") as m:
        # Slash in new_name
        resp1 = bridge.handle({
            "jsonrpc": "2.0", "id": 63, "method": "tools/call",
            "params": {
                "name": "bulk_rename_assets",
                "arguments": {"renames": [{"path": "/Game/Foo", "new_name": "Sub/Folder"}]},
            },
        })
        # Dot in new_name
        resp2 = bridge.handle({
            "jsonrpc": "2.0", "id": 64, "method": "tools/call",
            "params": {
                "name": "bulk_rename_assets",
                "arguments": {"renames": [{"path": "/Game/Foo", "new_name": "Foo.bar"}]},
            },
        })

    assert m.call_count == 0
    for resp in (resp1, resp2):
        assert resp["error"]["code"] == -32602
        assert "new_name" in resp["error"]["message"]
        assert "'/' or '.'" in resp["error"]["message"]


def test_bulk_rename_assets_rejects_path_with_nul_byte():
    """Same defensive shape-checks as bulk_delete/move: NUL byte in path
    -> -32602 with the entry index, no call_ue dispatched."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 65, "method": "tools/call",
            "params": {
                "name": "bulk_rename_assets",
                "arguments": {
                    "renames": [
                        {"path": "/Game/Good", "new_name": "GoodNew"},
                        {"path": "/Game/Bad\x00Asset", "new_name": "BadNew"},
                    ],
                },
            },
        })

    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "renames[1].path" in resp["error"]["message"]
    assert "NUL" in resp["error"]["message"]


def test_bulk_duplicate_assets_is_synthetic():
    """bulk_duplicate_assets is the fourth bulk_*_assets twin. Schema mirrors
    bulk_rename's per-entry mapping but with `dest_path` (full destination)
    instead of `new_name` (leaf name), since duplicate_asset takes a full
    destination path -- not a folder + name split."""
    t = next((t for t in bridge.TOOLS if t["name"] == "bulk_duplicate_assets"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["duplicates"]
    assert t["inputSchema"]["properties"]["duplicates"]["type"] == "array"
    item_schema = t["inputSchema"]["properties"]["duplicates"]["items"]
    assert item_schema["type"] == "object"
    assert set(item_schema["required"]) == {"path", "dest_path"}
    assert "bulk_duplicate_assets" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["bulk_duplicate_assets"] is bridge.synthetic_bulk_duplicate_assets


def test_bulk_duplicate_assets_happy_path():
    """All duplicates succeed -> ok=True, duplicated == total, per-entry
    results include both path AND dest_path so the caller can build a
    source→duplicate mapping from the response envelope alone."""
    with patch.object(bridge, "call_ue", return_value={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 70, "method": "tools/call",
            "params": {
                "name": "bulk_duplicate_assets",
                "arguments": {
                    "duplicates": [
                        {"path": "/Game/Foo", "dest_path": "/Game/Archive/Foo"},
                        {"path": "/Game/Bar", "dest_path": "/Game/Archive/Bar"},
                    ],
                },
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args == ("duplicate_asset", {"path": "/Game/Foo", "dest_path": "/Game/Archive/Foo"})
    assert m.call_args_list[1].args == ("duplicate_asset", {"path": "/Game/Bar", "dest_path": "/Game/Archive/Bar"})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["total"] == 2
    assert body["duplicated"] == 2
    assert body["failed"] == 0
    assert body["results"][0] == {
        "path": "/Game/Foo", "dest_path": "/Game/Archive/Foo",
        "ok": True, "error_code": None, "error_message": None,
    }


def test_bulk_duplicate_assets_partial_failure_stops_when_continue_on_error_false():
    """First duplicate succeeds, second fails (e.g. dest already exists),
    continue_on_error=False stops after #2. Upstream error code preserved."""
    ok_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "duplicate_asset: dest_exists: '/Game/Archive/Bar' already exists",
    }}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp, err_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 71, "method": "tools/call",
            "params": {
                "name": "bulk_duplicate_assets",
                "arguments": {
                    "duplicates": [
                        {"path": "/Game/Foo", "dest_path": "/Game/Archive/Foo"},
                        {"path": "/Game/Bar", "dest_path": "/Game/Archive/Bar"},
                        {"path": "/Game/Baz", "dest_path": "/Game/Archive/Baz"},
                    ],
                    "continue_on_error": False,
                },
            },
        })

    assert m.call_count == 2
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["duplicated"] == 1
    assert body["failed"] == 1
    assert body["results"][1]["error_code"] == -32000
    assert body["results"][1]["dest_path"] == "/Game/Archive/Bar"


def test_bulk_duplicate_assets_partial_failure_continues_when_continue_on_error_true():
    """Second duplicate fails (e.g. dest already exists), but
    continue_on_error=True (default) keeps the loop going. All three
    duplicates attempted; per-entry error surfaced in results[]. Mirrors the
    bulk_move / bulk_rename variants of the same test."""
    ok_resp_1 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32000,
        "message": "duplicate_asset: dest_exists: '/Game/Archive/Bar' already exists",
    }}
    ok_resp_2 = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
    with patch.object(bridge, "call_ue", side_effect=[ok_resp_1, err_resp, ok_resp_2]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 71, "method": "tools/call",
            "params": {
                "name": "bulk_duplicate_assets",
                "arguments": {
                    "duplicates": [
                        {"path": "/Game/Foo", "dest_path": "/Game/Archive/Foo"},
                        {"path": "/Game/Bar", "dest_path": "/Game/Archive/Bar"},
                        {"path": "/Game/Baz", "dest_path": "/Game/Archive/Baz"},
                    ],
                    "continue_on_error": True,
                },
            },
        })

    assert m.call_count == 3
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["total"] == 3
    assert body["duplicated"] == 2
    assert body["failed"] == 1
    assert body["results"][0]["ok"] is True
    assert body["results"][1]["ok"] is False
    assert body["results"][1]["error_code"] == -32000
    assert body["results"][1]["dest_path"] == "/Game/Archive/Bar"
    assert body["results"][2]["ok"] is True


def test_bulk_duplicate_assets_rejects_missing_duplicates():
    """Schema enforces duplicates as required."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 72, "method": "tools/call",
        "params": {"name": "bulk_duplicate_assets", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "duplicates" in resp["error"]["message"]


def test_bulk_duplicate_assets_rejects_dotdot_in_dest_path():
    """dest_path gets the same defensive shape-checks as path: `..` segment
    -> -32602 with the entry index. Catches the classic "I'll traverse
    out of /Game" mistake at the validator rather than after dispatch."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 73, "method": "tools/call",
            "params": {
                "name": "bulk_duplicate_assets",
                "arguments": {
                    "duplicates": [
                        {"path": "/Game/Foo", "dest_path": "/Game/Archive/../Hidden"},
                    ],
                },
            },
        })

    assert m.call_count == 0
    assert resp["error"]["code"] == -32602
    assert "duplicates[0].dest_path" in resp["error"]["message"]
    assert ".." in resp["error"]["message"]


def test_bulk_duplicate_assets_rejects_missing_dest_path():
    """Each entry's dest_path is required; missing it returns -32602 with
    'dest_path' in the message. No call_ue dispatched. Parity with
    bulk_move_assets_rejects_missing_dest_folder."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 75, "method": "tools/call",
            "params": {
                "name": "bulk_duplicate_assets",
                "arguments": {
                    "duplicates": [{"path": "/Game/Foo"}],
                },
            },
        })

    assert m.call_count == 0, "validation must short-circuit before any call_ue"
    assert resp["error"]["code"] == -32602
    assert "dest_path" in resp["error"]["message"]


def test_bulk_duplicate_assets_rejects_path_with_nul_byte():
    """Same defensive shape-checks as bulk_move/bulk_rename: NUL byte in any
    entry's path -> -32602, no call_ue dispatched."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 76, "method": "tools/call",
            "params": {
                "name": "bulk_duplicate_assets",
                "arguments": {
                    "duplicates": [
                        {"path": "/Game/Good", "dest_path": "/Game/Archive/Good"},
                        {"path": "/Game/Bad\x00Asset", "dest_path": "/Game/Archive/Bad"},
                    ],
                },
            },
        })

    assert m.call_count == 0, "validation must short-circuit before any call_ue"
    assert resp["error"]["code"] == -32602
    assert "NUL" in resp["error"]["message"] or "nul" in resp["error"]["message"].lower()


def test_inspect_data_asset_is_synthetic():
    """inspect_data_asset is a SYNTHETIC bridge-side handler (PR #92 — Copilot
    parallel-dispatch retry after the PR #90 stream's prompt was hardened):
    composes execute_unreal_python + get_log_lines via the marker pattern,
    same as synthetic_get_camera_transform. path is required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_data_asset"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_data_asset" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["inspect_data_asset"] is bridge.synthetic_inspect_data_asset


def test_inspect_data_asset_happy_path():
    """Two-round-trip: exec_python returns ok=True (no payload in result; the
    payload travels through LogPython). Then get_log_lines returns the
    marker-wrapped JSON. The bridge extracts the JSON between the marker and
    __END__ and returns it via _wrap_tool_result."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": True,
        "path": "/Game/Data/DA_PlayerStats",
        "class": "MyDataAsset",
        "parent_class": "PrimaryDataAsset",
        "package_path": "/Game/Data/DA_PlayerStats.DA_PlayerStats",
        "properties": [
            {"name": "MaxHealth", "type": "float", "value": "100.0"},
            {"name": "DisplayName", "type": "str", "value": "Hero"},
        ],
    }
    # Patch uuid.uuid4().hex[:12] to a known marker so the assertion is deterministic.
    marker_hex = "deadbeefcaf0"
    log_line = f"__DATA_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]) as m, \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 71, "method": "tools/call",
            "params": {
                "name": "inspect_data_asset",
                "arguments": {"path": "/Game/Data/DA_PlayerStats"},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args[0] == "execute_unreal_python"
    assert m.call_args_list[1].args == ("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_data_asset_propagates_asset_not_found():
    """UE-side `unreal.EditorAssetLibrary.load_asset` returns None for a
    missing asset; the embedded Python emits a sentinel-wrapped ok=False
    logical-error payload. Bridge must propagate it verbatim as a
    success-envelope (NOT a JSON-RPC error) so the caller sees the
    typed error_code without needing to distinguish from transport errors."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "asset_not_found",
        "error_message": "inspect_data_asset: asset_not_found: /Game/Data/Missing",
    }
    marker_hex = "deadbeefcaf0"
    log_line = f"__DATA_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 72, "method": "tools/call",
            "params": {"name": "inspect_data_asset", "arguments": {"path": "/Game/Data/Missing"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_data_asset_marker_not_found():
    """If the LogPython buffer doesn't contain the marker (log overflowed
    between exec and read, or UE silently dropped log), the bridge returns
    a marker_not_found logical-error success-envelope with a hint that
    retry typically resolves."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "Some other unrelated python log line"}
    ]}}
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 73, "method": "tools/call",
            "params": {"name": "inspect_data_asset", "arguments": {"path": "/Game/Data/Whatever"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_not_found"
    assert "retry typically resolves" in got["error_message"]


def test_marker_pattern_truncated_returns_marker_truncated_error_code():
    """If a LogPython line contains the marker prefix but the __END__ token
    is missing (line truncated by the log ring's per-entry cap, or by
    transient I/O), the bridge must return error_code='marker_truncated'
    -- NOT 'invalid_json' (which the old conflated except clause did).
    The two failure modes are different to triage:
      - marker_truncated: retry the call; the payload was probably fine,
        the log line just got cut off in transit.
      - invalid_json: the payload itself is malformed; retry won't help,
        the embedded Python's json.dumps emitted something wrong.
    Conflating them sent maintainers to debug the wrong layer."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    # Marker present, but no __END__ token after it.
    marker_hex = "deadbeefcaf0"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": f"__DATA_{marker_hex}__{{\"ok\":true,\"truncated"}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 175, "method": "tools/call",
                "params": {"name": "inspect_data_asset", "arguments": {"path": "/Game/Truncated"}},
            })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_truncated", (
        f"expected marker_truncated for truncated log line, got {got['error_code']!r}"
    )
    assert "end token" in got["error_message"]
    assert "/Game/Truncated" in got["error_message"]


def test_marker_pattern_propagates_execute_unreal_python_failure_envelope():
    """If execute_unreal_python returns `ok: False` (the embedded Python
    interpreter raised — syntax error, runtime exception, etc), the marker
    pattern must surface that as a -32603 transport-class error rather than
    proceeding to scan logs for a marker that was never emitted.

    The `output` field of the exec response (which carries the Python
    traceback) must be surfaced in the error message so the caller can debug
    without re-running the call.

    Construction: exec_resp has ok=False + a traceback in output. Bridge
    should short-circuit before the second call_ue (get_log_lines) and return
    the error. No log scan happens; the bridge doesn't try to recover."""
    traceback_text = (
        'Traceback (most recent call last):\n'
        '  File "<embed>", line 5, in <module>\n'
        '    obj.get_editor_property("MissingProperty")\n'
        'AttributeError: ...'
    )
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {
        "ok": False,
        "output": traceback_text,
    }}
    with patch.object(bridge, "call_ue", side_effect=[exec_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 200, "method": "tools/call",
            "params": {"name": "inspect_data_asset", "arguments": {"path": "/Game/Foo"}},
        })

    # Only the exec round-trip happened; the bridge did NOT proceed to fetch
    # logs because there's nothing to scan.
    assert m.call_count == 1, (
        f"bridge made {m.call_count} call_ue calls; expected 1 (exec only — "
        f"no log scan when exec itself failed)"
    )
    assert m.call_args_list[0].args[0] == "execute_unreal_python"

    # Failure surfaces as a JSON-RPC -32603 transport error (not a
    # success-envelope with ok=False), because the caller can't usefully
    # retry — the Python is broken, retry won't help.
    assert "error" in resp, f"expected JSON-RPC error envelope, got: {resp}"
    assert resp["error"]["code"] == -32603


def test_marker_pattern_bad_json_returns_invalid_json_error_code():
    """If a LogPython line contains a full marker block (prefix + __END__)
    but the payload between them isn't valid JSON, the bridge must return
    error_code='invalid_json' -- NOT 'marker_truncated'. This is the half
    the old conflated except clause handled correctly; this test pins the
    behavior so the split can't regress it.
    """
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    marker_hex = "feedfacefeed"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": f"__DATA_{marker_hex}__not-valid-json__END__"}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 176, "method": "tools/call",
                "params": {"name": "inspect_data_asset", "arguments": {"path": "/Game/BadJSON"}},
            })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "invalid_json", (
        f"expected invalid_json for malformed payload, got {got['error_code']!r}"
    )


def test_inspect_data_asset_rejects_missing_path():
    """Schema enforces path as required; missing it returns -32602."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 74, "method": "tools/call",
        "params": {"name": "inspect_data_asset", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "inspect_data_asset" in resp["error"]["message"]


def test_inspect_sound_class_is_synthetic():
    """inspect_sound_class is a SYNTHETIC bridge-side handler (PR #98 - Codex
    parallel-dispatch test; paired with a Copilot stream for inspect_audio_bus
    that regressed). Composes execute_unreal_python + get_log_lines via the
    marker pattern. path is required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_sound_class"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_sound_class" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["inspect_sound_class"] is bridge.synthetic_inspect_sound_class


def test_inspect_sound_class_happy_path():
    """Two-round-trip happy path. The embedded UE Python emits a sentinel-
    wrapped JSON via unreal.log(); the bridge retrieves it via get_log_lines
    on the LogPython category. JSON output uses PascalCase property names
    (Volume, bApplyAmbientVolumes, OutputTarget) even though UE Python uses
    snake_case internally."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": True,
        "path": "/Game/Audio/SC_Music",
        "class": "SoundClass",
        "package_path": "/Game/Audio/SC_Music.SC_Music",
        "parent_class": "/Game/Audio/SC_Master",
        "child_classes": ["/Game/Audio/SC_MusicLayered", "/Game/Audio/SC_Music3D"],
        "properties": {
            "Volume": 1.0,
            "Pitch": 1.0,
            "LowPassFilterFrequency": 20000.0,
            "AttenuationDistanceScale": 1.0,
            "VoiceCenterChannelVolume": 0.0,
            "RadioFilterVolume": 0.0,
            "bApplyAmbientVolumes": False,
            "bApplyEffects": True,
            "bAlwaysPlay": False,
            "bIsUISound": False,
            "bIsMusic": False,
            "bReverb": True,
            "bCenterChannelOnly": False,
            "bApplyDoppler": True,
            "bApplyMixerOverrides": False,
            "OutputTarget": "Master",
        },
    }
    marker_hex = "abc123def456"
    log_line = f"__SOUNDCLASS_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]) as m, \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 75, "method": "tools/call",
            "params": {
                "name": "inspect_sound_class",
                "arguments": {"path": "/Game/Audio/SC_Music"},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args[0] == "execute_unreal_python"
    assert m.call_args_list[1].args == ("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_sound_class_propagates_wrong_asset_type():
    """When the loaded asset is not a USoundClass, embedded Python emits a
    wrong_asset_type logical-error payload with the actual leaf class name."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "wrong_asset_type",
        "error_message": "Asset is not a USoundClass: /Game/Data/DA_NotASoundClass",
        "actual_class": "MyDataAsset",
    }
    marker_hex = "abc123def456"
    log_line = f"__SOUNDCLASS_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 76, "method": "tools/call",
            "params": {"name": "inspect_sound_class", "arguments": {"path": "/Game/Data/DA_NotASoundClass"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_sound_class_rejects_missing_path():
    """Schema enforces path as required; missing it returns -32602."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 77, "method": "tools/call",
        "params": {"name": "inspect_sound_class", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "inspect_sound_class" in resp["error"]["message"]


def test_inspect_sound_submix_is_synthetic():
    """inspect_sound_submix is a SYNTHETIC bridge-side handler (PR #99 - Codex
    parallel-dispatch). Composes execute_unreal_python + get_log_lines via
    the marker pattern. path is required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_sound_submix"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_sound_submix" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["inspect_sound_submix"] is bridge.synthetic_inspect_sound_submix


def test_inspect_sound_submix_happy_path():
    """Two-round-trip happy path. parent_submix + child_submixes returned as
    asset package paths (chainable to another inspect_sound_submix call)."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": True,
        "path": "/Game/Audio/SX_Music",
        "class": "SoundSubmix",
        "package_path": "/Game/Audio/SX_Music.SX_Music",
        "parent_submix": "/Game/Audio/SX_Master",
        "child_submixes": ["/Game/Audio/SX_MusicReverb"],
        "additional_properties": [
            {"name": "output_volume", "type": "float", "value": "0.8"},
        ],
    }
    marker_hex = "abc123def456"
    log_line = f"__SOUNDSUBMIX_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]) as m, \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 78, "method": "tools/call",
            "params": {
                "name": "inspect_sound_submix",
                "arguments": {"path": "/Game/Audio/SX_Music"},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args[0] == "execute_unreal_python"
    assert m.call_args_list[1].args == ("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_sound_submix_rejects_missing_path():
    """Schema enforces path as required; the rejection happens BEFORE any UE
    round-trip (validated via patched-but-unused call_ue mock)."""
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 79, "method": "tools/call",
            "params": {"name": "inspect_sound_submix", "arguments": {}},
        })

    m.assert_not_called()
    assert resp["error"]["code"] == -32602
    assert "inspect_sound_submix" in resp["error"]["message"]


def test_inspect_sound_submix_propagates_wrong_asset_type():
    """When the loaded asset is not a USoundSubmixBase, embedded Python emits a
    wrong_asset_type logical-error payload with the actual leaf class name. The
    bridge propagates it verbatim as an ok=False success-envelope (NOT a JSON-RPC
    error), so callers can branch on error_code without parsing exception text."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "wrong_asset_type",
        "error_message": "inspect_sound_submix: wrong_asset_type: /Game/Data/DA_NotASubmix",
        "actual_class": "MyDataAsset",
    }
    marker_hex = "abc123def456"
    log_line = f"__SOUNDSUBMIX_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 90, "method": "tools/call",
            "params": {"name": "inspect_sound_submix", "arguments": {"path": "/Game/Data/DA_NotASubmix"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_sound_submix_marker_not_found():
    """If the LogPython buffer doesn't contain the __SUBMIX_ marker after exec
    (log overflowed between exec and read, or UE silently dropped log), the
    bridge returns a marker_not_found logical-error envelope with the
    'retry typically resolves' hint. Mirrors inspect_data_asset's behaviour
    for the same failure mode (see test_inspect_data_asset_marker_not_found)."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "Some other unrelated python log line"}
    ]}}
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 91, "method": "tools/call",
            "params": {"name": "inspect_sound_submix", "arguments": {"path": "/Game/Audio/SX_Whatever"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_not_found"
    assert "retry typically resolves" in got["error_message"]


def test_inspect_audio_bus_is_synthetic():
    """inspect_audio_bus is a SYNTHETIC bridge-side handler (PR #99 - Copilot
    retry that recovered from PR #98 regression after the prompt explicitly
    called out the three previous wrongs). Composes execute_unreal_python +
    get_log_lines via the marker pattern. path is required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_audio_bus"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_audio_bus" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["inspect_audio_bus"] is bridge.synthetic_inspect_audio_bus


def test_inspect_audio_bus_happy_path():
    """Two-round-trip happy path. audio_bus_channels stringified via .name
    (Mono | Stereo | Quad | FivePointOne | SevenPointOne)."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": True,
        "path": "/Game/Audio/AB_Master",
        "class": "AudioBus",
        "package_path": "/Game/Audio/AB_Master.AB_Master",
        "audio_bus_channels": "Stereo",
        "additional_properties": [
            {"name": "sample_rate", "type": "int", "value": "48000"},
        ],
    }
    marker_hex = "abc123def456"
    log_line = f"__AUDIOBUS_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]) as m, \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 80, "method": "tools/call",
            "params": {
                "name": "inspect_audio_bus",
                "arguments": {"path": "/Game/Audio/AB_Master"},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args[0] == "execute_unreal_python"
    assert m.call_args_list[1].args == ("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_audio_bus_rejects_missing_path():
    """Schema enforces path as required."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 81, "method": "tools/call",
        "params": {"name": "inspect_audio_bus", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "inspect_audio_bus" in resp["error"]["message"]


def test_inspect_audio_bus_propagates_wrong_asset_type():
    """When the loaded asset is not a UAudioBus, embedded Python emits a
    wrong_asset_type logical-error payload that the bridge propagates as an
    ok=False success envelope. Same pattern as the other inspect_* synthetics."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "wrong_asset_type",
        "error_message": "inspect_audio_bus: wrong_asset_type: /Game/Data/DA_NotABus",
        "actual_class": "MyDataAsset",
    }
    marker_hex = "abc123def456"
    log_line = f"__AUDIOBUS_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 92, "method": "tools/call",
            "params": {"name": "inspect_audio_bus", "arguments": {"path": "/Game/Data/DA_NotABus"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_audio_bus_marker_not_found():
    """If the LogPython buffer doesn't contain the __AUDIOBUS_ marker after
    exec, the bridge returns a marker_not_found logical-error envelope with
    the canonical 'retry typically resolves' hint."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "Some other unrelated python log line"}
    ]}}
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 93, "method": "tools/call",
            "params": {"name": "inspect_audio_bus", "arguments": {"path": "/Game/Audio/AB_Whatever"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_not_found"
    assert "retry typically resolves" in got["error_message"]


def test_inspect_material_function_is_synthetic():
    """inspect_material_function is a SYNTHETIC bridge-side handler. Opus-
    direct authorship after PR #101 parallel-dispatch round where both
    Codex and Copilot streams failed (Codex looped, Copilot's output had
    wrong marker terminator + invalid TOOLS schema). Composes
    execute_unreal_python + get_log_lines via the marker pattern. path is
    required."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_material_function"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_material_function" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["inspect_material_function"] is bridge.synthetic_inspect_material_function


def test_inspect_material_function_happy_path():
    """Two-round-trip happy path. Returns the enumerated inputs/outputs
    discovered from function_expressions plus the description + library
    categories + permissive additional_properties enumeration."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": True,
        "path": "/Game/Materials/MF_PackedNormal",
        "class": "MaterialFunction",
        "package_path": "/Game/Materials/MF_PackedNormal.MF_PackedNormal",
        "description": "Decodes a packed-format normal map",
        "exposed_to_library": True,
        "library_categories": ["Normal Maps", "Texture Packing"],
        "inputs": [
            {"name": "PackedNormal", "type": "FunctionInput", "input_type": "Vector3"},
        ],
        "outputs": [
            {"name": "Normal", "type": "FunctionOutput"},
        ],
        "additional_properties": [],
    }
    marker_hex = "abc123def456"
    log_line = f"__MATFUNC_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]) as m, \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 82, "method": "tools/call",
            "params": {
                "name": "inspect_material_function",
                "arguments": {"path": "/Game/Materials/MF_PackedNormal"},
            },
        })

    assert m.call_count == 2
    assert m.call_args_list[0].args[0] == "execute_unreal_python"
    assert m.call_args_list[1].args == ("get_log_lines", {"category_filter": "LogPython", "count": 1000})
    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_material_function_rejects_missing_path():
    """Schema enforces path as required."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 83, "method": "tools/call",
        "params": {"name": "inspect_material_function", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "inspect_material_function" in resp["error"]["message"]


def test_inspect_material_function_propagates_wrong_asset_type():
    """When the loaded asset is not a UMaterialFunction (or
    MaterialFunctionMaterialLayer / ...LayerBlend), embedded Python emits
    a wrong_asset_type logical-error payload that the bridge propagates as
    an ok=False success envelope."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "wrong_asset_type",
        "error_message": "inspect_material_function: wrong_asset_type: /Game/Data/DA_NotMF",
        "actual_class": "MyDataAsset",
    }
    marker_hex = "abc123def456"
    log_line = f"__MATFUNC_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [{"category": "LogPython", "message": log_line}]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 94, "method": "tools/call",
            "params": {"name": "inspect_material_function", "arguments": {"path": "/Game/Data/DA_NotMF"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_material_function_propagates_asset_not_found():
    """When EditorAssetLibrary.load_asset(path) returns None, embedded Python
    emits an asset_not_found logical-error envelope; the message follows the
    post-PR-#126 canonical '<tool>: asset_not_found: <path>' shape."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "asset_not_found",
        "error_message": "inspect_material_function: asset_not_found: /Game/Materials/MF_Missing",
    }
    marker_hex = "0011223344aa"
    log_line = f"__MATFUNC_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": log_line}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]), \
         patch.object(bridge, "uuid", fake_uuid):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 95, "method": "tools/call",
            "params": {"name": "inspect_material_function", "arguments": {"path": "/Game/Materials/MF_Missing"}},
        })

    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "asset_not_found"
    assert got["error_message"].startswith("inspect_material_function: asset_not_found:")


def test_inspect_material_function_marker_not_found():
    """If the LogPython buffer doesn't contain the __MATFUNC_ marker after
    exec, the bridge returns a marker_not_found logical-error envelope with
    the canonical 'retry typically resolves' hint."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "Some other unrelated python log line"}
    ]}}
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 96, "method": "tools/call",
            "params": {"name": "inspect_material_function", "arguments": {"path": "/Game/Materials/MF_Whatever"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_not_found"
    assert "retry typically resolves" in got["error_message"]


def test_inspect_metasound_is_synthetic():
    """inspect_metasound is a SYNTHETIC bridge-side handler that accepts
    either MetaSoundSource (emitter-attached) or MetaSoundPatch (reusable
    subgraph) per the Metasound plugin's two-class surface in UE 5.7.
    path is required at the schema level."""
    t = next((t for t in bridge.TOOLS if t["name"] == "inspect_metasound"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["path"]
    assert t["inputSchema"]["properties"]["path"]["type"] == "string"
    assert "inspect_metasound" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["inspect_metasound"] is bridge.synthetic_inspect_metasound


def test_inspect_metasound_happy_path():
    """Two-round-trip marker pattern: exec_python returns ok=True with
    no inline payload; get_log_lines returns the marker-wrapped JSON
    payload that the embedded Python emitted. The bridge extracts the
    JSON between the marker and __END__ and returns it via
    _wrap_tool_result."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": True,
        "path": "/Game/Audio/MS_Test",
        "class": "MetaSoundSource",
        "package_path": "/Game/Audio/MS_Test.MS_Test",
        "additional_properties": [],
    }
    marker_hex = "fedcba987654"
    log_line = f"__METASOUND_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": log_line}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 84, "method": "tools/call",
                "params": {"name": "inspect_metasound", "arguments": {"path": "/Game/Audio/MS_Test"}},
            })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got == body


def test_inspect_metasound_propagates_asset_not_found():
    """asset_not_found logical error envelope (the marker payload's
    `ok: False` branch); the message follows the post-PR-#126 canonical
    `<tool>: asset_not_found: <path>` shape."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    body = {
        "ok": False,
        "error_code": "asset_not_found",
        "error_message": "inspect_metasound: asset_not_found: /Game/Audio/MS_Missing",
    }
    marker_hex = "0011223344aa"
    log_line = f"__METASOUND_{marker_hex}__{json.dumps(body)}__END__"
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": log_line}
    ]}}
    fake_uuid = MagicMock()
    fake_uuid.uuid4.return_value = MagicMock(hex=marker_hex)
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        with patch.object(bridge, "uuid", fake_uuid):
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 85, "method": "tools/call",
                "params": {"name": "inspect_metasound", "arguments": {"path": "/Game/Audio/MS_Missing"}},
            })

    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "asset_not_found"
    assert got["error_message"].startswith("inspect_metasound: asset_not_found:")


def test_inspect_metasound_rejects_missing_path():
    """Schema enforces path as required; missing it returns -32602."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 86, "method": "tools/call",
        "params": {"name": "inspect_metasound", "arguments": {}},
    })
    assert resp["error"]["code"] == -32602
    assert "inspect_metasound" in resp["error"]["message"]


def test_inspect_metasound_marker_not_found():
    """If the LogPython buffer doesn't contain the __METASOUND_ marker after
    exec, the bridge returns a marker_not_found logical-error envelope with
    the canonical 'retry typically resolves' hint. Closes the last gap in
    inspect_metasound's error-branch coverage."""
    exec_resp = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True, "output": ""}}
    log_resp = {"jsonrpc": "2.0", "id": 1, "result": {"lines": [
        {"category": "LogPython", "message": "Some other unrelated python log line"}
    ]}}
    with patch.object(bridge, "call_ue", side_effect=[exec_resp, log_resp]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 97, "method": "tools/call",
            "params": {"name": "inspect_metasound", "arguments": {"path": "/Game/Audio/MS_Whatever"}},
        })

    assert resp["result"]["isError"] is False
    got = json.loads(resp["result"]["content"][0]["text"])
    assert got["ok"] is False
    assert got["error_code"] == "marker_not_found"
    assert "retry typically resolves" in got["error_message"]


def test_screenshot_actor_happy_path():
    """When focus_actor and get_viewport_screenshot both succeed, the
    synthetic must compose their results: focus identity + screenshot bytes."""
    focus_resp = {"jsonrpc": "2.0", "id": 1, "result": {
        "focused": "MyCube_Label", "name": "StaticMeshActor_3",
        "loc_x": 100.0, "loc_y": 200.0, "loc_z": 50.0,
    }}
    shot_resp = {"jsonrpc": "2.0", "id": 1, "result": {
        "width": 1920, "height": 1080,
        "png_bytes": 4242, "png_base64": "iVBORw0KGgo=",
    }}
    with patch.object(bridge, "call_ue", side_effect=[focus_resp, shot_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "screenshot_actor", "arguments": {"name": "MyCube_Label"}},
        })
    # Composition: focus_actor first, then get_viewport_screenshot
    assert m.call_count == 2
    assert m.call_args_list[0].args == ("focus_actor", {"name": "MyCube_Label"})
    assert m.call_args_list[1].args == ("get_viewport_screenshot", {})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["ok"] is True
    assert body["focused"] == "MyCube_Label"
    assert body["name"] == "StaticMeshActor_3"
    assert body["loc"] == {"x": 100.0, "y": 200.0, "z": 50.0}
    assert body["width"] == 1920 and body["height"] == 1080
    assert body["png_bytes"] == 4242
    assert body["png_base64"] == "iVBORw0KGgo="


def test_screenshot_actor_propagates_focus_error():
    """If focus_actor fails (actor not found, no GEditor, etc.), the synthetic
    must surface a focus_failed error and NOT call get_viewport_screenshot."""
    err_resp = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32603, "message": "Actor not found: BadName",
    }}
    with patch.object(bridge, "call_ue", side_effect=[err_resp]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "screenshot_actor", "arguments": {"name": "BadName"}},
        })
    # Only focus_actor was called; screenshot was not attempted
    assert m.call_count == 1
    assert m.call_args_list[0].args == ("focus_actor", {"name": "BadName"})
    assert "error" in resp
    assert "focus_failed" in resp["error"]["message"]
    assert "Actor not found: BadName" in resp["error"]["message"]


def test_screenshot_actor_propagates_screenshot_error():
    """If focus succeeds but the screenshot fails (no active viewport, ReadPixels
    returns false, etc.), the synthetic must surface a screenshot_failed error."""
    focus_resp = {"jsonrpc": "2.0", "id": 1, "result": {
        "focused": "Cube", "name": "Cube_0",
        "loc_x": 0.0, "loc_y": 0.0, "loc_z": 0.0,
    }}
    shot_err = {"jsonrpc": "2.0", "id": 1, "error": {
        "code": -32603, "message": "No active viewport (open a level)",
    }}
    with patch.object(bridge, "call_ue", side_effect=[focus_resp, shot_err]) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 13, "method": "tools/call",
            "params": {"name": "screenshot_actor", "arguments": {"name": "Cube"}},
        })
    assert m.call_count == 2
    assert "error" in resp
    assert "screenshot_failed" in resp["error"]["message"]
    assert "No active viewport" in resp["error"]["message"]


def test_screenshot_actor_rejects_missing_name():
    """Validation runs BEFORE any UE round-trip (cheaper failure path).
    Missing/empty/non-string 'name' must be caught upfront."""
    for bad_args in [{}, {"name": ""}, {"name": None}, {"name": 42}]:
        with patch.object(bridge, "call_ue") as m:
            resp = bridge.handle({
                "jsonrpc": "2.0", "id": 14, "method": "tools/call",
                "params": {"name": "screenshot_actor", "arguments": bad_args},
            })
        # No call_ue invocation — validation rejected the request immediately
        m.assert_not_called()
        assert "error" in resp
        assert "missing_required_field" in resp["error"]["message"]


def test_exec_python_persistent_in_tools_catalog():
    """v0.11.x (Tier 2 PR #45): exec_python_persistent requires 'code'."""
    t = next((t for t in bridge.TOOLS if t["name"] == "exec_python_persistent"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["code"]
    assert t["inputSchema"]["properties"]["code"]["type"] == "string"


def test_reset_python_state_in_tools_catalog():
    """v0.11.x (Tier 2 PR #45): reset_python_state takes no params."""
    t = next((t for t in bridge.TOOLS if t["name"] == "reset_python_state"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    assert t["inputSchema"]["properties"] == {}


def test_start_sleep_task_in_tools_catalog():
    """v0.11.x (Tier 2 PR #44): start_sleep_task requires duration_ms."""
    t = next((t for t in bridge.TOOLS if t["name"] == "start_sleep_task"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["duration_ms"]
    assert t["inputSchema"]["properties"]["duration_ms"]["type"] == "integer"


def test_poll_task_in_tools_catalog():
    """v0.11.x (Tier 2 PR #44): poll_task requires task_id."""
    t = next((t for t in bridge.TOOLS if t["name"] == "poll_task"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["task_id"]
    assert t["inputSchema"]["properties"]["task_id"]["type"] == "string"


def test_cancel_task_in_tools_catalog():
    """v0.11.x (Tier 2 PR #44): cancel_task requires task_id."""
    t = next((t for t in bridge.TOOLS if t["name"] == "cancel_task"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["task_id"]
    assert t["inputSchema"]["properties"]["task_id"]["type"] == "string"


def test_list_tasks_in_tools_catalog():
    """Tier 3: list_tasks enumerates the FUCMCPTaskRegistry. All params
    optional; status_filter is enum-constrained; limit is integer-typed."""
    t = next((t for t in bridge.TOOLS if t["name"] == "list_tasks"), None)
    assert t is not None
    # No required fields — every param is optional
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    # status_filter is an enum locked to the 5 documented status strings
    assert props["status_filter"]["type"] == "string"
    assert set(props["status_filter"]["enum"]) == {
        "pending", "running", "completed", "cancelled", "failed"
    }
    # type_filter is a free-form string (no enum — task types are open-ended)
    assert props["type_filter"]["type"] == "string"
    assert "enum" not in props["type_filter"]
    # limit is integer-typed in the JSON schema (the C++ side still defends
    # against fractional doubles via cast-after-clamp; this just shapes the
    # client expectation)
    assert props["limit"]["type"] == "integer"


def test_register_subscription_in_tools_catalog():
    """v0.11.x (Tier 2 PR #43): register_subscription takes optional
    event_filter (array of string)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "register_subscription"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    assert props["event_filter"]["type"] == "array"
    assert props["event_filter"]["items"]["type"] == "string"


def test_unsubscribe_in_tools_catalog():
    """v0.11.x (Tier 2 PR #43): unsubscribe requires subscription_id."""
    t = next((t for t in bridge.TOOLS if t["name"] == "unsubscribe"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["subscription_id"]
    assert t["inputSchema"]["properties"]["subscription_id"]["type"] == "string"


def test_poll_subscription_in_tools_catalog():
    """v0.11.x (Tier 2 PR #43): poll_subscription requires subscription_id;
    optional max_count (int)."""
    t = next((t for t in bridge.TOOLS if t["name"] == "poll_subscription"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["subscription_id"]
    props = t["inputSchema"]["properties"]
    assert props["subscription_id"]["type"] == "string"
    assert props["max_count"]["type"] == "integer"


def test_wait_for_events_in_tools_catalog():
    """v0.11.x (Tier 2 PR #42): wait_for_events is a SYNTHETIC tool served
    bridge-side (composes poll_events); shares poll_events's cursor + filter
    shape and adds optional timeout_ms (int) + poll_interval_ms (int). All
    params optional."""
    t = next((t for t in bridge.TOOLS if t["name"] == "wait_for_events"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    props = t["inputSchema"]["properties"]
    assert props["timeout_ms"]["type"] == "integer"
    assert props["poll_interval_ms"]["type"] == "integer"
    assert props["since_seq"]["type"] == "integer"
    assert props["max_count"]["type"] == "integer"
    assert props["event_filter"]["type"] == "array"
    assert props["event_filter"]["items"]["type"] == "string"


def test_wait_for_events_is_synthetic():
    """The handler is bridge-side (no UE round-trip per outer call) -- it
    must be registered in SYNTHETIC_TOOLS and bound to the corresponding
    function so tools/call hits the bridge path, not call_ue."""
    assert "wait_for_events" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["wait_for_events"] is bridge.synthetic_wait_for_events


def test_wait_for_events_returns_immediately_on_first_match():
    """When poll_events returns events on the very first call, wait_for_events
    must NOT sleep -- return immediately. Validates the no-stall fast path."""
    fake_ue_resp = {
        "jsonrpc": "2.0", "id": 1,
        "result": {
            "ok": True, "next_seq": 5, "first_seq_in_buffer": 0,
            "returned": 1, "dropped": False,
            "events": [{"seq": 4, "event": "actor_spawned", "ts": "x", "data": {}}],
        },
    }
    with patch.object(bridge, "call_ue", return_value=fake_ue_resp) as m, \
         patch.object(bridge.time, "sleep") as sleep_mock:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 99, "method": "tools/call",
            "params": {"name": "wait_for_events", "arguments": {"timeout_ms": 1000}},
        })
    # call_ue called exactly once; no sleeps (events available immediately)
    m.assert_called_once_with("poll_events", {})
    sleep_mock.assert_not_called()
    # Response is wrapped MCP tools/call, with timed_out=false
    assert resp["id"] == 99
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["timed_out"] is False
    assert body["events"][0]["event"] == "actor_spawned"


def test_wait_for_events_polls_until_match():
    """Two empty polls then a match -- caller should see the matched events,
    timed_out=false, and exactly 3 call_ue invocations."""
    empty = {"jsonrpc": "2.0", "id": 1,
             "result": {"ok": True, "next_seq": 0, "first_seq_in_buffer": -1,
                        "returned": 0, "dropped": False, "events": []}}
    matched = {"jsonrpc": "2.0", "id": 1,
               "result": {"ok": True, "next_seq": 1, "first_seq_in_buffer": 0,
                          "returned": 1, "dropped": False,
                          "events": [{"seq": 0, "event": "asset_added", "ts": "x", "data": {}}]}}
    with patch.object(bridge, "call_ue", side_effect=[empty, empty, matched]) as m, \
         patch.object(bridge.time, "sleep") as sleep_mock, \
         patch.object(bridge.time, "monotonic", side_effect=[0.0, 0.05, 0.10, 0.15, 0.20]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "wait_for_events",
                       "arguments": {"timeout_ms": 5000, "poll_interval_ms": 50}},
        })
    assert m.call_count == 3
    # Two sleeps between three polls
    assert sleep_mock.call_count == 2
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["timed_out"] is False
    assert body["events"][0]["event"] == "asset_added"


def test_wait_for_events_times_out_returns_timed_out_true():
    """When deadline passes with no events, response.timed_out must be true
    and events must be empty."""
    empty = {"jsonrpc": "2.0", "id": 1,
             "result": {"ok": True, "next_seq": 5, "first_seq_in_buffer": 0,
                        "returned": 0, "dropped": False, "events": []}}
    # monotonic sequence: start=0, after 1st poll=0.6 (past 500ms deadline)
    with patch.object(bridge, "call_ue", return_value=empty), \
         patch.object(bridge.time, "sleep"), \
         patch.object(bridge.time, "monotonic", side_effect=[0.0, 0.6, 0.7]):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 8, "method": "tools/call",
            "params": {"name": "wait_for_events", "arguments": {"timeout_ms": 500}},
        })
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["timed_out"] is True
    assert body["events"] == []


def test_wait_for_events_dropped_short_circuits():
    """If poll_events returns dropped=true, the wait should return immediately
    (don't keep polling -- the caller needs to re-sync regardless)."""
    dropped_resp = {"jsonrpc": "2.0", "id": 1,
                    "result": {"ok": True, "next_seq": 100, "first_seq_in_buffer": 50,
                               "returned": 0, "dropped": True, "events": [],
                               "note": "..."}}
    with patch.object(bridge, "call_ue", return_value=dropped_resp) as m, \
         patch.object(bridge.time, "sleep") as sleep_mock:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "wait_for_events",
                       "arguments": {"since_seq": 5, "timeout_ms": 5000}},
        })
    m.assert_called_once()
    sleep_mock.assert_not_called()
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["dropped"] is True
    assert body["timed_out"] is False  # dropped takes precedence over timed_out


def test_wait_for_events_clamps_out_of_range_params():
    """timeout_ms > 30000 and poll_interval_ms < 25 are silently clamped to
    the bracket (not rejected). Verified by checking the deadline math: the
    handler must use the clamped values internally."""
    matched = {"jsonrpc": "2.0", "id": 1,
               "result": {"ok": True, "next_seq": 1, "first_seq_in_buffer": 0,
                          "returned": 1, "dropped": False,
                          "events": [{"seq": 0, "event": "x", "ts": "x", "data": {}}]}}
    with patch.object(bridge, "call_ue", return_value=matched), \
         patch.object(bridge.time, "sleep"):
        # Should not raise -- clamping is silent
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "wait_for_events",
                       "arguments": {"timeout_ms": 999999, "poll_interval_ms": 5}},
        })
    assert "result" in resp
    body = json.loads(resp["result"]["content"][0]["text"])
    assert body["timed_out"] is False


def test_wait_for_events_rejects_non_integer_timeout():
    """Non-integer timeout_ms must produce an error (not silently truncate)."""
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "wait_for_events", "arguments": {"timeout_ms": 1.5}},
    })
    assert "error" in resp
    assert resp["error"]["code"] == -32602


def test_wait_for_events_propagates_ue_error():
    """If poll_events errors out, the bridge must surface it (not swallow it
    or keep polling)."""
    err_resp = {"jsonrpc": "2.0", "id": 1,
                "error": {"code": -32099, "message": "UE down"}}
    with patch.object(bridge, "call_ue", return_value=err_resp) as m, \
         patch.object(bridge.time, "sleep") as sleep_mock:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "wait_for_events", "arguments": {}},
        })
    m.assert_called_once()
    sleep_mock.assert_not_called()
    assert resp["error"]["code"] == -32099


def test_required_params_match_handler_contract():
    by_name = {t["name"]: t for t in bridge.TOOLS}
    assert by_name["execute_unreal_python"]["inputSchema"]["required"] == ["code"]
    assert by_name["inspect_blueprint"]["inputSchema"]["required"] == ["path"]
    assert by_name["inspect_widget_tree"]["inputSchema"]["required"] == ["path"]
    assert by_name["edit_widget_tree"]["inputSchema"]["required"] == ["path", "op"]
    assert by_name["focus_actor"]["inputSchema"]["required"] == ["name"]
    assert by_name["load_level_by_path"]["inputSchema"]["required"] == ["path"]


def test_import_texture_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "import_texture")
    schema = tool["inputSchema"]
    assert schema["required"] == ["source_path", "dest_path"]
    props = schema["properties"]
    assert props["source_path"]["type"] == "string"
    assert props["dest_path"]["type"] == "string"
    assert props["dest_name"]["type"] == "string"
    assert props["replace_existing"]["type"] == "boolean"
    assert props["automated"]["type"] == "boolean"
    assert props["save"]["type"] == "boolean"


def test_configure_texture_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "configure_texture")
    schema = tool["inputSchema"]
    assert schema["required"] == ["path"]
    props = schema["properties"]
    assert props["path"]["type"] == "string"
    assert props["srgb"]["type"] == "boolean"
    assert props["compression"]["type"] == "string"
    assert props["lod_group"]["type"] == "string"
    assert props["filter"]["type"] == "string"
    assert props["compress"]["type"] == "boolean"
    assert props["filter"].get("enum") == ["Nearest", "Bilinear", "Trilinear", "Default"]


def test_find_assets_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "find_assets")
    schema = tool["inputSchema"]
    assert schema["required"] == ["class_path"]
    props = schema["properties"]
    assert props["class_path"]["type"] == "string"
    assert props["path_under"]["type"] == "string"
    assert props["name_contains"]["type"] == "string"
    assert props["limit"]["type"] == "integer"


def test_spawn_actor_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "spawn_actor")
    schema = tool["inputSchema"]
    assert schema["required"] == ["class_path"]
    props = schema["properties"]
    assert props["class_path"]["type"] == "string"
    assert props["location"]["type"] == "object"
    assert props["rotation"]["type"] == "object"
    assert props["label"]["type"] == "string"
    assert props["properties"]["type"] == "object"


def test_set_actor_transform_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "set_actor_transform")
    schema = tool["inputSchema"]
    assert schema["required"] == ["name"]
    props = schema["properties"]
    assert props["name"]["type"] == "string"
    assert props["location"]["type"] == "object"
    assert props["rotation"]["type"] == "object"
    assert props["scale"]["type"] == "object"
    assert props["relative"]["type"] == "boolean"


def test_delete_actor_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "delete_actor")
    schema = tool["inputSchema"]
    assert schema["required"] == ["name"]
    props = schema["properties"]
    assert props["name"]["type"] == "string"
    assert props["force"]["type"] == "boolean"


def test_set_actor_property_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "set_actor_property")
    schema = tool["inputSchema"]
    assert schema["required"] == ["name", "property", "value"]
    props = schema["properties"]
    assert props["name"]["type"] == "string"
    assert props["property"]["type"] == "string"


def test_add_component_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "add_component")
    schema = tool["inputSchema"]
    assert schema["required"] == ["actor_name", "class_path"]
    props = schema["properties"]
    assert props["actor_name"]["type"] == "string"
    assert props["class_path"]["type"] == "string"
    assert props["component_name"]["type"] == "string"
    assert props["attach_to"]["type"] == "string"
    assert props["socket"]["type"] == "string"
    assert props["relative_transform"]["type"] == "object"


def test_get_log_lines_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "get_log_lines")
    schema = tool["inputSchema"]
    # No required fields — all params are optional.
    assert "required" not in schema or schema.get("required") == []
    props = schema["properties"]
    assert props["count"]["type"] == "integer"
    assert props["category_filter"]["type"] == "string"
    assert props["min_verbosity"]["type"] == "string"
    assert set(props["min_verbosity"]["enum"]) == {
        "Fatal", "Error", "Warning", "Display", "Log", "Verbose", "VeryVerbose"
    }


def test_execute_console_command_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "execute_console_command")
    schema = tool["inputSchema"]
    assert schema["required"] == ["command"]
    props = schema["properties"]
    assert props["command"]["type"] == "string"
    assert props["capture_output"]["type"] == "boolean"


# -------- make_response -------------------------------------------------------

@pytest.mark.parametrize("synth_name", [
    "wait_for_events",
    "get_camera_transform",
    "set_camera_transform",
    "screenshot_actor",
    "compile_mod_pak",
    "compile_mod_pak_direct",
])
@pytest.mark.parametrize("bad_args", [None, [], "string", 42])
def test_synthetic_returns_invalid_arguments_for_non_dict_args(synth_name, bad_args):
    """PR6 added isinstance(args, dict) guards to 6 synthetics that previously
    AttributeError'd on the first args.get() if a client sent params.arguments
    as a list/null/string/int. The guard upgrades the failure to a clean
    -32602 invalid_arguments envelope.

    This test exercises the guard for each of the 6 newly-protected synthetics
    across 4 bad-args shapes: None, list, string, int.

    24 parameter combinations total — covers every (synthetic × bad-shape) pair.
    """
    synth = bridge.SYNTHETIC_TOOLS[synth_name]
    resp = synth(req_id=99, args=bad_args)
    assert "error" in resp, f"expected -32602 error envelope, got result instead: {resp}"
    assert resp["error"]["code"] == -32602
    msg = resp["error"]["message"]
    assert msg.startswith(f"{synth_name}: invalid_arguments:"), (
        f"error message does not follow '{synth_name}: invalid_arguments:' prefix: {msg}"
    )


def test_make_response_with_result():
    r = bridge.make_response(7, result={"ok": True})
    assert r == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_make_response_with_error():
    r = bridge.make_response(7, error={"code": -32601, "message": "nope"})
    assert r == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32601, "message": "nope"}}


def test_make_response_error_overrides_result():
    """If both result and error are passed, error wins (matches current code)."""
    r = bridge.make_response(7, result={"x": 1}, error={"code": -1, "message": "e"})
    assert "result" not in r
    assert r["error"]["code"] == -1


def test_make_response_round_trips_non_integer_req_ids():
    """JSON-RPC 2.0 / MCP allow request ids of type string, integer, OR null.
    make_response must round-trip whatever was passed in -- the wire-side
    ID is what the client used to correlate request to response, so munging
    it (e.g. coercing string -> int, or dropping null) would silently break
    correlation for clients that use any non-integer convention.

    Covers: string id, null id (rare but legal for notifications-as-RPC
    edge cases), and a very large int near JSON-RPC's recommended ceiling.
    """
    for req_id in ("call-42-uuid-7c5e3", None, 9007199254740991):
        r = bridge.make_response(req_id, result={"ok": True})
        assert r["id"] == req_id, f"id was mutated for input {req_id!r}: got {r['id']!r}"
        assert r["jsonrpc"] == "2.0", "jsonrpc literal must always be the string '2.0'"
        assert r["result"] == {"ok": True}


# -------- handle: initialize / notifications / unknown -----------------------

def test_handle_initialize_returns_protocol_envelope():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == bridge.PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == bridge.SERVER_NAME
    assert resp["result"]["serverInfo"]["version"] == bridge.SERVER_VERSION
    assert "tools" in resp["result"]["capabilities"]


def test_handle_notification_returns_none():
    resp = bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None


def test_handle_notification_with_id_is_not_dropped():
    """A 'notification' that wrongly has an id should still produce a response
    (per JSON-RPC, only id-less requests are notifications)."""
    resp = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "notifications/foo"})
    assert resp is not None
    assert resp["id"] == 1


def test_handle_unknown_method_returns_method_not_found():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 99, "method": "does_not_exist"})
    assert resp["error"]["code"] == -32601
    assert "does_not_exist" in resp["error"]["message"]


def test_handle_unknown_method_without_id_is_silent():
    resp = bridge.handle({"jsonrpc": "2.0", "method": "does_not_exist"})
    assert resp is None


# -------- handle: tools/list --------------------------------------------------

def test_handle_tools_list_returns_all_tools():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert resp["id"] == 2
    assert "tools" in resp["result"]
    # Cross-check against bridge.TOOLS rather than re-asserting the absolute
    # count — `test_tools_list_size` already pins the count, this guards the
    # tools/list handler shape.
    assert len(resp["result"]["tools"]) == len(bridge.TOOLS)


# -------- handle: tools/call --------------------------------------------------

def test_handle_tools_call_missing_name_returns_invalid_params():
    resp = bridge.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"arguments": {}},
    })
    assert resp["error"]["code"] == -32602


def test_handle_tools_call_forwards_to_ue_and_wraps_result():
    fake_ue_resp = {"jsonrpc": "2.0", "id": 1, "result": {"hello": "world"}}
    with patch.object(bridge, "call_ue", return_value=fake_ue_resp) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "list_tools", "arguments": {}},
        })
    m.assert_called_once_with("list_tools", {})
    assert resp["id"] == 4
    assert resp["result"]["isError"] is False
    assert resp["result"]["content"][0]["type"] == "text"
    # The UE result is JSON-encoded into the text block
    assert json.loads(resp["result"]["content"][0]["text"]) == {"hello": "world"}


def test_handle_tools_call_propagates_ue_error():
    fake_err = {"jsonrpc": "2.0", "id": 1,
                "error": {"code": -32099, "message": "UE down"}}
    with patch.object(bridge, "call_ue", return_value=fake_err):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "list_tools", "arguments": {}},
        })
    assert "error" in resp
    assert resp["error"]["code"] == -32099


def test_handle_tools_call_passes_arguments_through():
    with patch.object(bridge, "call_ue", return_value={"result": {}}) as m:
        bridge.handle({
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {
                "name": "execute_unreal_python",
                "arguments": {"code": "import unreal\nunreal.log('x')"},
            },
        })
    m.assert_called_once_with(
        "execute_unreal_python",
        {"code": "import unreal\nunreal.log('x')"},
    )


def test_handle_tools_call_default_empty_arguments():
    """tools/call with no `arguments` key should send {} to UE, not None."""
    with patch.object(bridge, "call_ue", return_value={"result": {}}) as m:
        bridge.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": "list_tools"},
        })
    m.assert_called_once_with("list_tools", {})


# -------- call_ue: socket-level error paths ----------------------------------

def _make_fake_socket(recv_chunks):
    """Build a mock socket whose recv() yields the supplied byte chunks then b''.

    For the v0.5.0 framed protocol the chunks must include the 8-byte big-endian
    length prefix followed by the body.  Use _framed(body_bytes) to build them.
    """
    sock = MagicMock()
    sock.recv.side_effect = list(recv_chunks) + [b""]
    return sock


def _framed(body: bytes) -> list:
    """Return [prefix_bytes, body_bytes] — the two recv() chunks for one framed message."""
    prefix = len(body).to_bytes(8, byteorder="big", signed=False)
    return [prefix, body]


def test_call_ue_sends_method_and_params_and_returns_result():
    body = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    sock = _make_fake_socket(_framed(body))
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("focus_actor", {"name": "Cube"})

    # Verify what was put on the wire (sendall receives prefix+body as one call)
    sock.sendall.assert_called_once()
    sent_bytes = sock.sendall.call_args[0][0]
    # The first 8 bytes are the length prefix; the rest is the JSON body
    sent_body = sent_bytes[8:]
    sent = json.loads(sent_body.decode("utf-8"))
    assert sent["jsonrpc"] == "2.0"
    assert sent["method"] == "focus_actor"
    assert sent["params"] == {"name": "Cube"}

    assert resp["result"] == {"ok": True}


def test_call_ue_omits_params_when_empty():
    """Per the bridge contract, an empty params dict should NOT be sent."""
    body = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    sock = _make_fake_socket(_framed(body))
    with patch.object(socket, "socket", return_value=sock):
        bridge.call_ue("list_tools", {})
    sent_bytes = sock.sendall.call_args[0][0]
    sent = json.loads(sent_bytes[8:].decode("utf-8"))
    assert "params" not in sent


def test_call_ue_returns_error_on_connection_refused():
    sock = MagicMock()
    sock.connect.side_effect = ConnectionRefusedError("nope")
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    assert resp["error"]["code"] == -32099
    assert "not reachable" in resp["error"]["message"]


def test_call_ue_returns_error_on_socket_timeout():
    sock = MagicMock()
    sock.connect.side_effect = socket.timeout("slow")
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    assert resp["error"]["code"] == -32099


def test_call_ue_returns_error_on_oserror():
    sock = MagicMock()
    sock.connect.side_effect = OSError("network unreachable")
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    assert resp["error"]["code"] == -32099


def test_call_ue_returns_parse_error_on_non_json_response():
    body = b"not json at all }"
    sock = _make_fake_socket(_framed(body))
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    assert resp["error"]["code"] == -32700


def test_call_ue_handles_chunked_response():
    """Server may split the body across multiple recv() calls; recv_exact loops
    until all bytes arrive."""
    body = b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    prefix = len(body).to_bytes(8, byteorder="big", signed=False)
    # Deliver prefix in one chunk, body in three fragments
    chunks = [prefix, body[:17], body[17:25], body[25:]]
    sock = _make_fake_socket(chunks)
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    assert resp["result"] == {"ok": True}


# -------- main loop fault tolerance ------------------------------------------

def test_handle_internal_exception_is_caught_in_main(monkeypatch, capsys):
    """If handle() raises, main() should emit a -32603 error, not crash."""
    monkeypatch.setattr(bridge, "handle",
                        lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("sys.stdin",
                        iter(['{"jsonrpc":"2.0","id":42,"method":"initialize"}\n']))
    bridge.main()
    out = capsys.readouterr().out.strip()
    assert out, "main() produced no output"
    resp = json.loads(out)
    assert resp["id"] == 42
    assert resp["error"]["code"] == -32603
    assert "boom" in resp["error"]["message"]


def test_main_skips_blank_and_malformed_lines(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin",
        iter(["\n", "not-json\n", '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n']),
    )
    bridge.main()
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 1


def test_synthetic_inspect_asset_not_found_messages_use_canonical_prefix() -> None:
    """Every synthetic_inspect_* tool that emits an `asset_not_found` logical
    error must shape the error message as `'<tool>: asset_not_found: <path>'`.

    This guard exists because the convention drifted in practice: two of the
    five inspectors used bare `'Asset not found: <path>'` while three used
    `'<tool>: asset_not_found: Asset not found: <path>'` (double-labelled,
    redundant). Live MCP testing on 2026-05-12 surfaced the inconsistency,
    not any unit test, because each inspector's pytest happy-path mocks the
    error message it expects to receive -- so per-tool tests can't catch
    cross-tool drift.

    This test reads bridge.py source and asserts that no `synthetic_inspect_*`
    function contains a bare `'Asset not found:'` literal or the redundant
    `'asset_not_found: Asset not found:'` chain. If a future inspector
    regresses to either pattern, this test fails fast.
    """
    import re
    import pathlib

    bridge_src = (
        pathlib.Path(bridge.__file__).read_text(encoding="utf-8", errors="replace")
    )

    # Build pattern at runtime so the literal we're guarding against can never
    # accidentally match this test file itself when the personal-leaks scanner
    # walks the repo. (Same defensive trick the leak detector uses.)
    bare_literal = "'Asset" + " not found:"
    redundant = "asset_not_found: Asset" + " not found:"

    # Find all synthetic_inspect_* function blocks and check each.
    # The optional `\s*->\s*\w+` allows for a return-type annotation
    # (PR7 added `-> dict` to every synthetic; pattern must still match).
    blocks = re.findall(
        r"def\s+synthetic_inspect_\w+\([^)]*\)(?:\s*->\s*\w+)?:.*?(?=\ndef\s|\Z)",
        bridge_src,
        flags=re.DOTALL,
    )
    assert blocks, "no synthetic_inspect_* functions found -- did the bridge module move?"

    offenders: list[str] = []
    for block in blocks:
        # First line of the block is the def signature -- pull the function name
        # out for the error message.
        match = re.match(r"def\s+(synthetic_inspect_\w+)", block)
        name = match.group(1) if match else "<unknown>"
        if bare_literal in block:
            offenders.append(
                f"{name}: contains bare {bare_literal!r} -- use "
                f"'<tool>: asset_not_found: <path>' canonical form"
            )
        if redundant in block:
            offenders.append(
                f"{name}: contains redundant {redundant!r} -- drop the "
                f"second 'Asset not found:' segment"
            )

    assert not offenders, (
        "synthetic_inspect_* error-message shape drift detected:\n  "
        + "\n  ".join(offenders)
    )
