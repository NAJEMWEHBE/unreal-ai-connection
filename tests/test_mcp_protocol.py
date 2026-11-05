"""MCP protocol conformance of the bridge's stdio surface (no UE needed).

Covers what an MCP client sees on the wire: ping, malformed requests,
tool failures reported as `isError` results, and image content.
"""

import io
import json
from unittest.mock import patch

import pytest

import unreal_ai_connection_bridge as bridge


def _call(name, arguments=None, req_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return bridge.handle({"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params})


def _run_main(monkeypatch, capsys, stdin_text):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    bridge.main()
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


# -------- ping ---------------------------------------------------------------


def test_ping_gets_an_empty_result():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert resp == {"jsonrpc": "2.0", "id": 7, "result": {}}


# -------- malformed input ------------------------------------------------------


def test_invalid_json_line_gets_a_parse_error_with_null_id(monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "{not json\n")
    assert len(out) == 1
    assert out[0]["id"] is None
    assert out[0]["error"]["code"] == -32700
    assert "Parse error" in out[0]["error"]["message"]


def test_non_object_request_gets_invalid_request(monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "[1, 2]\n")
    assert out[0]["id"] is None
    assert out[0]["error"]["code"] == -32600


@pytest.mark.parametrize("bad_args", [[1, 2], "text", 5])
def test_non_object_arguments_get_invalid_params(bad_args):
    with patch.object(bridge, "call_ue") as m:
        resp = _call("list_levels", bad_args)
    m.assert_not_called()
    assert resp["error"]["code"] == -32602
    assert "arguments" in resp["error"]["message"]


# -------- tool failures are results with isError, not protocol errors ----------


def _error_payload(resp):
    """The structured error a failed tool call carries in its text block."""
    assert "error" not in resp, f"expected a tool result, got a protocol error: {resp}"
    result = resp["result"]
    assert result["isError"] is True
    return json.loads(result["content"][0]["text"])


def test_editor_side_failure_is_an_iserror_result():
    ue_error = {"code": -32000, "message": "spawn_actor: class_not_found: Nope"}
    with patch.object(bridge, "call_ue", return_value={"error": ue_error}):
        resp = _call("spawn_actor", {"class": "Nope"})
    assert _error_payload(resp) == ue_error


def test_editor_unreachable_is_an_iserror_result_the_model_can_read():
    ue_error = {"code": -32099, "message": "UE server not reachable on 127.0.0.1:18888"}
    with patch.object(bridge, "call_ue", return_value={"error": ue_error}):
        resp = _call("list_levels", {})
    assert _error_payload(resp)["code"] == -32099


def test_synthetic_validation_failure_is_an_iserror_result():
    with patch.object(bridge, "call_ue") as m:
        resp = _call("bulk_delete_assets", {"paths": "not-a-list"})
    m.assert_not_called()
    payload = _error_payload(resp)
    assert payload["code"] == -32602
    assert "bulk_delete_assets" in payload["message"]


def test_unknown_tool_stays_a_protocol_error():
    ue_error = {"code": -32601, "message": "Method not found: no_such_tool"}
    with patch.object(bridge, "call_ue", return_value={"error": ue_error}):
        resp = _call("no_such_tool", {})
    assert resp["error"]["code"] == -32601


def test_missing_tool_name_stays_a_protocol_error():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}})
    assert resp["error"]["code"] == -32602
