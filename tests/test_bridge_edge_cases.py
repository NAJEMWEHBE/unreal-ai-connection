"""
Edge-case unit tests for the Python bridge. Complements `test_bridge.py`.

Focus: malformed/atypical incoming requests, parameter passthrough across
ALL 13 tools, and current-behaviour documentation for things outside spec
(non-dict params, etc.). Like `test_bridge.py`, runs without UE.
"""

import json
import socket
from unittest.mock import MagicMock, patch

import pytest

import unreal_claude_mcp_bridge as bridge


# -------- Parameterised round-trip across all 13 tools -----------------------

@pytest.mark.parametrize("tool", [t["name"] for t in bridge.TOOLS])
def test_every_tool_routes_through_tools_call(tool):
    """tools/call with each registered tool name forwards to call_ue with
    the exact name and the exact arguments dict."""
    args = {"sentinel": tool}  # unique per tool
    with patch.object(bridge, "call_ue", return_value={"result": {}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
    m.assert_called_once_with(tool, args)
    assert resp["result"]["isError"] is False


# -------- handle() request shape edge cases ----------------------------------

def test_handle_request_with_params_explicitly_null():
    """Some clients send `"params": null`. The bridge should treat it as {}."""
    with patch.object(bridge, "call_ue", return_value={"result": {}}) as m:
        bridge.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": None,
        })
    # params is None -> handle() treats as {} -> tools/call missing 'name' branch
    # so call_ue should NOT have been called
    m.assert_not_called()


def test_handle_tools_call_with_arguments_explicitly_null():
    """`arguments`: null should coerce to {}, not crash."""
    with patch.object(bridge, "call_ue", return_value={"result": {}}) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_tools", "arguments": None},
        })
    m.assert_called_once_with("list_tools", {})
    assert "result" in resp


def test_handle_initialize_with_no_id_returns_response_anyway():
    """`initialize` is special: even if a client wrongly omits id, the bridge
    still returns the synthetic envelope (current behaviour - documented)."""
    resp = bridge.handle({"jsonrpc": "2.0", "method": "initialize"})
    assert resp is not None
    assert resp["result"]["serverInfo"]["name"] == bridge.SERVER_NAME


def test_handle_empty_method_string_with_id_returns_method_not_found():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": ""})
    assert resp["error"]["code"] == -32601


def test_handle_missing_method_field_with_id_returns_method_not_found():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 1})
    assert resp["error"]["code"] == -32601


# -------- Multiple sequential calls share no leaked state --------------------

def test_handle_consecutive_calls_use_supplied_request_ids():
    """Each handle() call returns the request's own id, not a cached one."""
    resp1 = bridge.handle({"jsonrpc": "2.0", "id": 100, "method": "initialize"})
    resp2 = bridge.handle({"jsonrpc": "2.0", "id": 200, "method": "tools/list"})
    resp3 = bridge.handle({"jsonrpc": "2.0", "id": "string-id", "method": "tools/list"})
    assert resp1["id"] == 100
    assert resp2["id"] == 200
    assert resp3["id"] == "string-id"


# -------- call_ue: more transport scenarios ----------------------------------

def _make_fake_socket(recv_chunks):
    sock = MagicMock()
    sock.recv.side_effect = list(recv_chunks) + [b""]
    return sock


def test_call_ue_handles_response_without_terminating_brace_via_eof():
    """Server may close the connection without the buffer ending in '}'
    (e.g. the response is split mid-token); the loop must still exit on EOF
    and return whatever it got. With invalid JSON, that's a parse error."""
    sock = _make_fake_socket([b'{"jsonrpc":"2.0",', b'"id":1,', b'"result":{"ok":true']
                             # note: no closing braces
                             )
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    assert resp["error"]["code"] == -32700  # parse error


def test_call_ue_handles_invalid_utf8_with_replacement():
    """The bridge decodes with errors='replace', so invalid UTF-8 doesn't
    crash; it surfaces as a parse error if the result isn't valid JSON."""
    sock = _make_fake_socket([b'{"jsonrpc":"2.0","id":1,"result":"\xff\xfe"}'])
    with patch.object(socket, "socket", return_value=sock):
        resp = bridge.call_ue("list_tools", {})
    # Replacement chars produce valid JSON string content -> success
    assert "result" in resp


def test_call_ue_default_host_port_from_env(monkeypatch):
    """UCMCP_HOST / UCMCP_PORT are read at import time, but the constants
    are exposed and used at runtime - confirm that overriding the
    module-level constants reroutes the connect() call."""
    sock = _make_fake_socket([b'{"jsonrpc":"2.0","id":1,"result":{}}'])
    monkeypatch.setattr(bridge, "UE_HOST", "10.0.0.1")
    monkeypatch.setattr(bridge, "UE_PORT", 9999)
    with patch.object(socket, "socket", return_value=sock):
        bridge.call_ue("list_tools", {})
    sock.connect.assert_called_once_with(("10.0.0.1", 9999))


def test_call_ue_payload_includes_jsonrpc_version_and_id():
    sock = _make_fake_socket([b'{"jsonrpc":"2.0","id":1,"result":{}}'])
    with patch.object(socket, "socket", return_value=sock):
        bridge.call_ue("list_tools", {"x": 1})
    sent = json.loads(sock.sendall.call_args[0][0].decode("utf-8"))
    assert sent["jsonrpc"] == "2.0"
    assert sent["id"] == 1
    assert sent["method"] == "list_tools"
    assert sent["params"] == {"x": 1}
