"""
Phase H -- bridge-side engine-version gating (docs/PHASE-H-COMPAT.md
"Bridge / Manifest Metadata Plan").

Four synthetic tools call unreal.* Python APIs that only exist on UE 5.0+.
When the connected editor is older than a tool's declared
`min_engine_version`, the bridge must return a STRUCTURED error envelope
BEFORE the doomed UE round-trip -- rather than letting the embedded
interpreter raise a raw AttributeError that surfaces as an opaque -32603.

These run with NO Unreal Engine instance -- the native `get_engine_version`
round-trip is mocked via `bridge.call_ue`, exactly like the existing
synthetic-tool tests in test_bridge.py.

Run from repo root:    pytest tests/
"""

import json
from unittest.mock import patch

import pytest

import unreal_ai_connection_bridge as bridge


GATED_TOOLS = [
    "convert_hdri_to_cubemap",
    "sequencer_add_transform_keyframe",
    "get_camera_transform",
    "set_camera_transform",
]


def _engine_version_resp(major: int, minor: int) -> dict:
    """Shape of the native get_engine_version handler's JSON-RPC response."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "major": major,
            "minor": minor,
            "patch": 0,
            "changelist": 0,
            "full": f"{major}.{minor}.0-0+++UE5+Release-{major}.{minor}",
            "branch": f"++UE5+Release-{major}.{minor}",
            "minor_dotted": f"{major}.{minor}",
            "is_licensee_version": False,
        },
    }


@pytest.fixture(autouse=True)
def _reset_engine_cache():
    """The discovered (major, minor) is memoised module-side; reset it
    around every test so cases don't leak a cached version into each other."""
    bridge._ENGINE_VERSION_CACHE = None
    yield
    bridge._ENGINE_VERSION_CACHE = None


# Minimal VALID arguments per gated tool, so the synthetic's own input
# validation passes and execution reaches the engine gate (the gate is
# placed AFTER argument validation so genuinely-bad input still
# short-circuits with -32602 and zero round-trips -- preserving the
# bridge's long-standing "validate before network I/O" invariant).
_VALID_ARGS = {
    "convert_hdri_to_cubemap": {"hdri_path": "/Game/HDRI/test"},
    "sequencer_add_transform_keyframe": {
        "sequence_path": "/Game/Seq",
        "binding_id": "0123456789abcdef0123456789abcdef",
        "time_seconds": 1.0,
        "location": [0.0, 0.0, 0.0],
    },
    "get_camera_transform": {},
    "set_camera_transform": {"location": {"x": 1.0, "y": 2.0, "z": 3.0}},
}


@pytest.mark.parametrize("tool_name", GATED_TOOLS)
def test_gate_blocks_each_gated_tool_on_engine_4_27(tool_name):
    """Connected editor is UE 4.27 -> every gated tool must be refused with
    the structured error and NEVER reach its UE round-trip."""
    with patch.object(bridge, "call_ue", return_value=_engine_version_resp(4, 27)) as m:
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 7, "method": "tools/call",
            "params": {"name": tool_name, "arguments": _VALID_ARGS[tool_name]},
        })

    # Only the get_engine_version preflight round-trip happened -- the
    # synthetic's UE round-trip was never reached.
    assert m.call_count == 1
    assert m.call_args[0][0] == "get_engine_version"

    assert "error" in resp, f"expected structured error envelope, got {resp}"
    err = resp["error"]
    assert err["code"] == "unsupported_on_engine_version"
    assert err["tool"] == tool_name
    assert err["min_engine_version"] == "5.0"
    assert err["engine_version"] == "4.27"
    assert "5.0" in err["message"]
    assert tool_name in err["message"]


def test_gate_passes_through_on_engine_5_7():
    """Connected editor is UE 5.7 -> gated tool runs normally. We assert the
    gate itself returns None (allow); the synthetic body's own behaviour is
    covered by test_bridge.py."""
    with patch.object(bridge, "call_ue", return_value=_engine_version_resp(5, 7)):
        gate = bridge.check_engine_gate(99, "get_camera_transform")
    assert gate is None, "engine 5.7 must pass the 5.0+ gate"


def test_gate_passes_through_on_exact_min_engine_5_0():
    """Boundary: connected editor exactly at min (5.0) is allowed."""
    with patch.object(bridge, "call_ue", return_value=_engine_version_resp(5, 0)):
        gate = bridge.check_engine_gate(99, "sequencer_add_transform_keyframe")
    assert gate is None, "engine exactly 5.0 must satisfy min_engine_version 5.0"


def test_gate_fails_open_when_engine_version_undeterminable():
    """If get_engine_version errors (UE down / handler missing), the gate
    must FAIL OPEN (return None / proceed) -- not hard-block. The underlying
    tool surfaces its own error if the API genuinely isn't present."""
    transport_err = {
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32099, "message": "UE server not reachable"},
    }
    with patch.object(bridge, "call_ue", return_value=transport_err):
        gate = bridge.check_engine_gate(99, "convert_hdri_to_cubemap")
    assert gate is None, "undeterminable engine version must fail OPEN"


def test_gate_ignores_non_gated_tool():
    """A tool without min_engine_version is never gated and never triggers
    an engine-version round-trip."""
    with patch.object(bridge, "call_ue") as m:
        gate = bridge.check_engine_gate(99, "find_actors_by_class")
    assert gate is None
    assert m.call_count == 0, "non-gated tool must not probe engine version"


def test_engine_version_lookup_is_memoised():
    """Repeated gated calls must cost ONE get_engine_version round-trip
    total -- the discovered (major, minor) is cached module-side."""
    with patch.object(bridge, "call_ue", return_value=_engine_version_resp(5, 7)) as m:
        bridge.check_engine_gate(1, "get_camera_transform")
        bridge.check_engine_gate(2, "set_camera_transform")
        bridge.check_engine_gate(3, "convert_hdri_to_cubemap")
    assert m.call_count == 1, (
        f"engine version should be discovered once and cached; "
        f"got {m.call_count} round-trips"
    )


def test_gate_parses_minor_dotted_fallback():
    """When the handler omits integer major/minor but supplies minor_dotted,
    the gate still resolves the version (defensive fallback path)."""
    resp = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"minor_dotted": "4.27", "full": "4.27.2-0"},
    }
    with patch.object(bridge, "call_ue", return_value=resp):
        gate = bridge.check_engine_gate(5, "get_camera_transform")
    assert gate is not None and gate["error"]["engine_version"] == "4.27"


def test_structured_error_shape_matches_spec():
    """Pin the exact structured-error envelope shape consumers will see."""
    with patch.object(bridge, "call_ue", return_value=_engine_version_resp(4, 27)):
        resp = bridge.handle({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {"name": "convert_hdri_to_cubemap", "arguments": {"hdri_path": "/Game/X"}},
        })
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 12
    err = resp["error"]
    assert set(err.keys()) == {
        "code", "message", "tool", "min_engine_version", "engine_version",
    }
    assert err["code"] == "unsupported_on_engine_version"
    assert err["tool"] == "convert_hdri_to_cubemap"
    assert err["min_engine_version"] == "5.0"
    assert err["engine_version"] == "4.27"
    # Serialisable -- it rides the existing JSON-RPC error path.
    json.dumps(resp)
