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


# -------- TOOLS schema --------------------------------------------------------

def test_tools_list_has_fiftytwo_entries():
    assert len(bridge.TOOLS) == 54


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
        "exec_python_persistent",
        "reset_python_state",
        "find_console_variables",
        "inspect_static_mesh",
        "get_camera_transform",
        "set_camera_transform",
        "screenshot_actor",
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


def test_get_camera_transform_is_synthetic():
    """v0.12.0 (language-shim experiment, PR #46): get_camera_transform
    is a SYNTHETIC bridge-side handler that composes execute_unreal_python
    + get_log_lines via marker pattern."""
    t = next((t for t in bridge.TOOLS if t["name"] == "get_camera_transform"), None)
    assert t is not None
    assert "required" not in t["inputSchema"] or t["inputSchema"].get("required") == []
    assert "get_camera_transform" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["get_camera_transform"] is bridge.synthetic_get_camera_transform


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


def test_screenshot_actor_is_synthetic():
    """screenshot_actor is a SYNTHETIC bridge-side handler that composes
    focus_actor + get_viewport_screenshot. Requires only 'name'."""
    t = next((t for t in bridge.TOOLS if t["name"] == "screenshot_actor"), None)
    assert t is not None
    assert t["inputSchema"]["required"] == ["name"]
    assert t["inputSchema"]["properties"]["name"]["type"] == "string"
    assert "screenshot_actor" in bridge.SYNTHETIC_TOOLS
    assert bridge.SYNTHETIC_TOOLS["screenshot_actor"] is bridge.synthetic_screenshot_actor


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
    assert len(resp["result"]["tools"]) == 54


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
