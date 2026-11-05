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
