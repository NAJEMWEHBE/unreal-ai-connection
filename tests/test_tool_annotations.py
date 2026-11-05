"""MCP tool annotations and protocol-version negotiation.

`readOnlyHint: true` lets a client auto-approve a tool, so a wrong `true` on
a tool that changes the project is a safety bug. The read-only set is an
explicit list in the bridge; these tests check it against the C++ sources
(no handler that opens an undo transaction may be read-only) and force a
decision for every new inspect_/get_/list_/find_ tool.
"""

import pathlib
import re

import pytest

import unreal_ai_connection_bridge as bridge

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Tools whose names look like reads but that change something, reviewed by
# hand. A new tool with a read-style name must land here or in
# bridge.READ_ONLY_TOOLS.
_READ_STYLE_BUT_NOT_READ_ONLY = {
    "find_actors_by_class",  # its optional `level` loads that map (switches the editor level)
    "get_viewport_screenshot",  # writes a PNG under the project
    "poll_events",  # consumes the caller's event cursor
    "poll_subscription",  # consumes the subscription's events
    "wait_for_events",  # polls poll_events
}
_READ_STYLE = re.compile(
    r"^(inspect_|get_|list_|find_|audit_|compare_|bulk_inspect_|poll_|wait_for_|search_)"
    r"|^marketplace_search$"
)


def _mutating_native_tools() -> set[str]:
    """Native methods whose handler declares IsMutating() true (undo transaction)."""
    names = set()
    for cpp in ROOT.glob("UnrealAIConnection*/Source/**/Handler_*.cpp"):
        src = cpp.read_text(encoding="utf-8", errors="replace")
        if re.search(r"IsMutating\(\)\s*const\s*override\s*\{\s*return\s+true", src):
            names.update(re.findall(r'GetMethodName\(\)[^{]*\{\s*return\s+TEXT\("(\w+)"\)', src))
    return names


def _advertised():
    return {t["name"]: t for t in [*bridge.TOOLS, bridge.SEARCH_TOOLS_DESCRIPTOR]}


def test_every_advertised_tool_carries_annotations_and_a_title():
    for name, tool in _advertised().items():
        ann = tool.get("annotations")
        assert isinstance(ann, dict), name
        assert isinstance(ann.get("readOnlyHint"), bool), name
        assert isinstance(ann.get("openWorldHint"), bool), name
        assert isinstance(tool.get("title"), str) and tool["title"], name


def test_read_only_set_only_names_real_tools():
    assert bridge.READ_ONLY_TOOLS <= set(_advertised())


def test_no_undo_transaction_handler_is_marked_read_only():
    mutating = _mutating_native_tools()
    assert mutating, "parser found no IsMutating() handlers -- did the C++ move?"
    assert not (bridge.READ_ONLY_TOOLS & mutating)


def test_read_only_tools_all_have_read_style_names():
    for name in bridge.READ_ONLY_TOOLS:
        assert _READ_STYLE.search(name), name


def test_every_read_style_tool_has_a_reviewed_decision():
    undecided = {
        n
        for n in _advertised()
        if _READ_STYLE.search(n)
        and n not in bridge.READ_ONLY_TOOLS
        and n not in _READ_STYLE_BUT_NOT_READ_ONLY
    }
    assert not undecided, f"decide read-only or not for: {sorted(undecided)}"


@pytest.mark.parametrize(
    ("name", "read_only"),
    [
        ("inspect_asset", True),
        ("get_actors_in_level", True),
        ("search_tools", True),
        ("delete_asset", False),
        ("execute_unreal_python", False),
        ("spawn_actor", False),
        ("get_viewport_screenshot", False),
    ],
)
def test_known_tools_get_the_right_read_only_hint(name, read_only):
    assert _advertised()[name]["annotations"]["readOnlyHint"] is read_only


def test_only_network_tools_are_open_world():
    open_world = {n for n, t in _advertised().items() if t["annotations"]["openWorldHint"]}
    assert open_world == {"marketplace_search", "marketplace_import"}


def test_titles_are_readable():
    tools = _advertised()
    assert tools["get_engine_version"]["title"] == "Get Engine Version"
    assert tools["inspect_ocio_config"]["title"] == "Inspect OCIO Config"
    assert tools["set_mi_parameter"]["title"] == "Set MI Parameter"


# -------- protocol version negotiation ----------------------------------------


def _initialize(version):
    params = {"capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}
    if version is not None:
        params["protocolVersion"] = version
    resp = bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})
    return resp["result"]["protocolVersion"]


@pytest.mark.parametrize("version", ["2025-06-18", "2025-03-26", "2024-11-05"])
def test_initialize_echoes_a_supported_client_version(version):
    assert _initialize(version) == version


@pytest.mark.parametrize("version", ["1999-01-01", "garbage", None])
def test_initialize_offers_the_latest_version_otherwise(version):
    assert _initialize(version) == bridge.PROTOCOL_VERSION == "2025-06-18"


def test_level_switching_actor_search_is_not_read_only():
    # find_actors_by_class with `level` calls load_level_by_path first, which
    # switches the open map: a client must not auto-approve it.
    assert "find_actors_by_class" not in bridge.READ_ONLY_TOOLS
    tool = _advertised()["find_actors_by_class"]
    assert tool["annotations"]["readOnlyHint"] is False
