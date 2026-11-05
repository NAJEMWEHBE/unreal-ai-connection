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


# -------- screenshots reach the model as image content ---------------------------

_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg=="


def test_viewport_thumbnail_is_returned_as_mcp_image_content():
    ue_result = {
        "path": "C:/Proj/Saved/AIConnection/Screenshots/viewport.png",
        "width": 1920,
        "height": 1080,
        "thumb_base64": _PNG_B64,
        "thumb_width": 1,
        "thumb_height": 1,
    }
    with patch.object(bridge, "call_ue", return_value={"result": ue_result}):
        resp = _call("get_viewport_screenshot", {"include_thumb": True})

    result = resp["result"]
    assert result["isError"] is False
    text_blocks = [c for c in result["content"] if c["type"] == "text"]
    image_blocks = [c for c in result["content"] if c["type"] == "image"]
    assert image_blocks == [{"type": "image", "mimeType": "image/png", "data": _PNG_B64}]
    # The JSON text keeps every other field but no longer repeats the base64.
    text = json.loads(text_blocks[0]["text"])
    assert text["path"] == ue_result["path"]
    assert text["thumb_width"] == 1
    assert _PNG_B64 not in text_blocks[0]["text"]


def test_results_without_a_thumbnail_stay_a_single_text_block():
    with patch.object(bridge, "call_ue", return_value={"result": {"path": "x.png"}}):
        resp = _call("get_viewport_screenshot", {})
    assert [c["type"] for c in resp["result"]["content"]] == ["text"]


@pytest.mark.parametrize("bad_params", [[1, 2], "text"])
def test_non_object_params_get_invalid_params(bad_params):
    resp = bridge.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": bad_params})
    assert resp["error"]["code"] == -32602
