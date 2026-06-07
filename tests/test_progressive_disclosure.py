"""
Progressive tool disclosure (opt-in) — bridge-side behaviour.

State-of-the-art MCP servers expose a small core + a discovery tool instead
of dumping every tool schema up front (Anthropic "Tool Search Tool"; arXiv
2603.20313; modelcontextprotocol discussion #532). This bridge gates that
behind UCMCP_TOOL_MODE=progressive and keeps the legacy "advertise all"
behaviour as the default.

These tests pin:
  1. Backward compat: default mode advertises the full catalog, unchanged.
  2. Progressive mode: tools/list returns CORE + search_tools only.
  3. search_tools is callable in BOTH modes and never crosses the wire.
  4. search_tools validates input and fails closed (no exceptions).
  5. Security: traversal / injection / oversized inputs are inert data,
     bounded work, and never leak a tool outside the public TOOLS catalog.
"""

import json
import os

import unreal_ai_connection_bridge as bridge


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _set_mode(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("UCMCP_TOOL_MODE", raising=False)
    else:
        monkeypatch.setenv("UCMCP_TOOL_MODE", value)


def _tools_list(req_id=1):
    return bridge.handle({"jsonrpc": "2.0", "id": req_id, "method": "tools/list"})


def _call_search(args, req_id=2):
    return bridge.handle({
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": "search_tools", "arguments": args},
    })


def _decode_search(resp):
    """Pull the JSON result payload out of a search_tools MCP envelope."""
    assert "result" in resp, resp
    text = resp["result"]["content"][0]["text"]
    return json.loads(text)


# --------------------------------------------------------------------------
# 1. Backward compatibility — default mode is unchanged
# --------------------------------------------------------------------------

def test_default_mode_advertises_full_catalog(monkeypatch):
    _set_mode(monkeypatch, None)
    resp = _tools_list()
    names = [t["name"] for t in resp["result"]["tools"]]
    assert len(names) == len(bridge.TOOLS)
    assert "search_tools" not in names  # not advertised in legacy mode


def test_unknown_mode_falls_back_to_full_catalog(monkeypatch):
    # A typo must never silently hide tools — fail safe to "all".
    _set_mode(monkeypatch, "porgressive")  # deliberately misspelled
    resp = _tools_list()
    assert len(resp["result"]["tools"]) == len(bridge.TOOLS)


def test_explicit_all_mode_advertises_full_catalog(monkeypatch):
    for val in ("all", "full", "ALL", "  Full  "):
        _set_mode(monkeypatch, val)
        resp = _tools_list()
        assert len(resp["result"]["tools"]) == len(bridge.TOOLS)


def test_tool_mode_reader(monkeypatch):
    _set_mode(monkeypatch, None)
    assert bridge.tool_mode() == "all"
    _set_mode(monkeypatch, "progressive")
    assert bridge.tool_mode() == "progressive"
    _set_mode(monkeypatch, "search")
    assert bridge.tool_mode() == "progressive"
    _set_mode(monkeypatch, "garbage")
    assert bridge.tool_mode() == "all"


# --------------------------------------------------------------------------
# 2. Progressive mode — small core + discovery tool
# --------------------------------------------------------------------------

def test_progressive_mode_advertises_core_plus_search(monkeypatch):
    _set_mode(monkeypatch, "progressive")
    resp = _tools_list()
    names = [t["name"] for t in resp["result"]["tools"]]
    # Far smaller than the full catalog.
    assert len(names) < len(bridge.TOOLS)
    # search_tools is present.
    assert "search_tools" in names
    # Every core tool that exists is advertised.
    for core in bridge.CORE_TOOL_NAMES:
        if core in {t["name"] for t in bridge.TOOLS}:
            assert core in names
    # Count = resolvable core tools + 1 (search_tools).
    resolvable_core = [n for n in bridge.CORE_TOOL_NAMES
                       if n in {t["name"] for t in bridge.TOOLS}]
    assert len(names) == len(resolvable_core) + 1


def test_advertised_search_descriptor_is_valid_mcp_tool(monkeypatch):
    _set_mode(monkeypatch, "progressive")
    resp = _tools_list()
    search = next(t for t in resp["result"]["tools"] if t["name"] == "search_tools")
    assert isinstance(search["description"], str) and search["description"]
    assert search["inputSchema"]["type"] == "object"
    assert "query" in search["inputSchema"]["properties"]
    assert "category" in search["inputSchema"]["properties"]


def test_core_tools_resolve_against_live_catalog():
    # core_tools() must only ever return real TOOLS entries (no phantoms).
    catalog = {t["name"] for t in bridge.TOOLS}
    for t in bridge.core_tools():
        assert t["name"] in catalog


# --------------------------------------------------------------------------
# 3. search_tools is callable in BOTH modes, bridge-side only
# --------------------------------------------------------------------------

def test_search_tools_callable_in_default_mode(monkeypatch, mock_socket_unused=None):
    _set_mode(monkeypatch, None)
    # Must NOT touch the UE socket — if it did, call_ue would blow up here.
    resp = _call_search({"query": "screenshot"})
    payload = _decode_search(resp)
    assert payload["ok"] is True


def test_search_tools_callable_in_progressive_mode(monkeypatch):
    _set_mode(monkeypatch, "progressive")
    resp = _call_search({"query": "screenshot"})
    payload = _decode_search(resp)
    assert payload["ok"] is True
    assert payload["returned"] >= 1


def test_search_tools_does_not_call_ue(monkeypatch):
    # Replace call_ue with a tripwire — search must never invoke it.
    def _tripwire(*a, **k):
        raise AssertionError("search_tools must not cross the wire to UE")
    monkeypatch.setattr(bridge, "call_ue", _tripwire)
    resp = _call_search({"query": "material"})
    assert _decode_search(resp)["ok"] is True


def test_search_returns_callable_real_tools(monkeypatch):
    resp = _call_search({"query": "keyframe"})
    payload = _decode_search(resp)
    catalog = {t["name"] for t in bridge.TOOLS}
    assert payload["tools"], "expected at least one keyframe-related tool"
    for entry in payload["tools"]:
        assert entry["name"] in catalog          # only real tools
        assert entry["inputSchema"]["type"] == "object"


def test_category_filter(monkeypatch):
    resp = _call_search({"category": "sequencer"})
    payload = _decode_search(resp)
    assert payload["ok"] is True
    assert payload["returned"] >= 1
    for entry in payload["tools"]:
        hay = (entry["name"] + " " + (entry["description"] or "")).lower()
        # Some sequencer keyword is present.
        assert any(k in hay for k in
                   ("sequence", "keyframe", "camera", "mrq", "playback", "cine"))


def test_query_and_category_combined(monkeypatch):
    resp = _call_search({"query": "texture", "category": "material"})
    payload = _decode_search(resp)
    assert payload["ok"] is True


# --------------------------------------------------------------------------
# 4. Validation / fail-closed — never raises, always structured
# --------------------------------------------------------------------------

def test_empty_search_is_rejected():
    payload = _decode_search(_call_search({}))
    assert payload["ok"] is False
    assert payload["error_code"] == "empty_search"


def test_non_string_query_rejected():
    payload = _decode_search(_call_search({"query": 123}))
    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_query"


def test_oversized_query_rejected():
    payload = _decode_search(_call_search({"query": "x" * 1000}))
    assert payload["ok"] is False
    assert payload["error_code"] == "query_too_long"


def test_unknown_category_rejected():
    payload = _decode_search(_call_search({"category": "does_not_exist"}))
    assert payload["ok"] is False
    assert payload["error_code"] == "unknown_category"


def test_non_string_category_rejected():
    payload = _decode_search(_call_search({"category": ["material"]}))
    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_category"


def test_bad_limit_rejected():
    for bad in (0, -5, "8", 3.5, True):
        payload = _decode_search(_call_search({"query": "actor", "limit": bad}))
        assert payload["ok"] is False
        assert payload["error_code"] == "invalid_limit"


def test_non_object_args_fail_closed():
    # tools/call sends a non-dict arguments -> handle() coerces, but call
    # search_tools_impl directly with a hostile type to prove it never raises.
    out = bridge.search_tools_impl(["not", "a", "dict"])
    assert out["ok"] is False
    assert out["error_code"] == "invalid_arguments"


# --------------------------------------------------------------------------
# 5. Security — inputs are inert, bounded, no leakage
# --------------------------------------------------------------------------

def test_path_traversal_query_is_inert():
    # A traversal-looking query is treated as plain text — it matches nothing
    # dangerous, never touches a filesystem, and returns a normal (likely
    # empty) result set instead of erroring or escaping.
    payload = _decode_search(_call_search({"query": "../../etc/passwd"}))
    assert payload["ok"] is True
    catalog = {t["name"] for t in bridge.TOOLS}
    for entry in payload["tools"]:
        assert entry["name"] in catalog


def test_injection_query_is_inert():
    for hostile in (
        "; rm -rf /",
        "$(whoami)",
        "__import__('os').system('echo pwned')",
        "'; DROP TABLE tools;--",
        "\x00../secret",
    ):
        payload = _decode_search(_call_search({"query": hostile}))
        # Either a clean miss (ok:True, possibly empty) or a structured
        # validation error — never an exception, never an escaped result.
        assert payload["ok"] in (True, False)
        if payload["ok"]:
            catalog = {t["name"] for t in bridge.TOOLS}
            for entry in payload["tools"]:
                assert entry["name"] in catalog


def test_result_count_hard_capped():
    # Even with a huge requested limit, never exceed the hard cap, and never
    # exceed the catalog size.
    payload = _decode_search(_call_search({"category": "inspect", "limit": 9999}))
    assert payload["ok"] is True
    assert payload["returned"] <= bridge._SEARCH_RESULT_HARD_CAP
    assert payload["returned"] <= len(bridge.TOOLS)


def test_search_never_advertises_itself():
    # search_tools is a bridge-only descriptor; it must not appear inside its
    # own results (it isn't in TOOLS, so this also proves no phantom leak).
    payload = _decode_search(_call_search({"query": "search"}))
    if payload["ok"]:
        assert all(e["name"] != "search_tools" for e in payload["tools"])


def test_engine_gating_metadata_carried_through():
    # A gated tool surfaced via search must still carry its min_engine_version
    # so the model doesn't pick something the connected editor can't run.
    payload = _decode_search(_call_search({"query": "cubemap"}))
    assert payload["ok"] is True
    gated = [e for e in payload["tools"] if e["name"] == "convert_hdri_to_cubemap"]
    if gated:
        assert gated[0].get("min_engine_version") == "5.0"


def test_results_are_deterministic():
    a = _decode_search(_call_search({"query": "material", "limit": 5}))
    b = _decode_search(_call_search({"query": "material", "limit": 5}))
    assert [t["name"] for t in a["tools"]] == [t["name"] for t in b["tools"]]
