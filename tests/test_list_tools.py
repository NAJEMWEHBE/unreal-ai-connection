"""tools/call list_tools: the live inventory covers editor AND bridge tools.

The C++ `list_tools` handler only knows the editor's registry, so on its own
it leaves out every bridge-side synthetic tool (find_actors_by_class,
screenshot_actor, wait_for_events, ...). The bridge merges the two.
"""

import json
from unittest.mock import patch

from conftest import is_tool_error, tool_error

import unreal_ai_connection_bridge as bridge

_NATIVE = ["get_actors_in_level", "list_tools", "spawn_actor", "create_dmx_patch"]


def _list_tools():
    ue = {"jsonrpc": "2.0", "id": 1, "result": {"tools": list(_NATIVE), "count": len(_NATIVE)}}
    with patch.object(bridge, "call_ue", return_value=ue) as m:
        resp = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_tools", "arguments": {}},
            }
        )
    m.assert_called_once_with("list_tools", {})
    assert not is_tool_error(resp), resp
    return json.loads(resp["result"]["content"][0]["text"])


def test_list_tools_includes_the_bridge_side_tools():
    result = _list_tools()
    for name in ("find_actors_by_class", "screenshot_actor", "wait_for_events", "search_tools"):
        assert name in result["tools"], name


def test_list_tools_keeps_every_live_editor_method():
    result = _list_tools()
    for name in _NATIVE:
        assert name in result["tools"], name


def test_list_tools_tags_where_each_tool_runs():
    result = _list_tools()
    assert result["source"]["spawn_actor"] == "native"
    assert result["source"]["create_dmx_patch"] == "native"
    assert result["source"]["find_actors_by_class"] == "bridge"
    assert result["source"]["search_tools"] == "bridge"
    assert set(result["source"]) == set(result["tools"])


def test_list_tools_counts_match_the_merged_list():
    result = _list_tools()
    assert result["count"] == len(result["tools"]) == len(set(result["tools"]))
    assert result["native_count"] == len(_NATIVE)
    assert result["bridge_count"] == len(bridge.SYNTHETIC_TOOLS) + 1  # + search_tools


def test_list_tools_reports_an_unreachable_editor_as_a_tool_error():
    down = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32099, "message": "UE down"}}
    with patch.object(bridge, "call_ue", return_value=down):
        resp = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_tools", "arguments": {}},
            }
        )
    assert tool_error(resp)["code"] == -32099
