"""
Lean tool profile (UCMCP_TOOL_MODE=lean) — bridge-side behaviour.

The lean profile sits between progressive (7 core tools) and the full
catalog: a curated ~32-tool daily-driver set measured from real production
sessions (HDM rebuild-v2 benchmark, 2026-06). These tests pin:
  1. Lean mode advertises exactly the resolved LEAN names + search_tools.
  2. Resolution discipline: names missing from TOOLS drop out (no phantoms).
  3. Mode parsing is case/whitespace tolerant.
  4. Regressions: default mode still returns the full catalog; progressive
     mode is unchanged.
"""

import unreal_ai_connection_bridge as bridge


def _set_mode(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("UCMCP_TOOL_MODE", raising=False)
    else:
        monkeypatch.setenv("UCMCP_TOOL_MODE", value)


def test_lean_mode_advertises_lean_plus_search(monkeypatch):
    _set_mode(monkeypatch, "lean")
    assert bridge.tool_mode() == "lean"

    advertised = bridge.advertised_tools()
    names = [t["name"] for t in advertised]

    # search_tools is appended last, exactly once.
    assert names[-1] == "search_tools"
    assert names.count("search_tools") == 1

    # Exactly the resolved LEAN names, in LEAN_TOOL_NAMES order.
    catalog_names = {t["name"] for t in bridge.TOOLS}
    expected = [n for n in bridge.LEAN_TOOL_NAMES if n in catalog_names]
    assert names[:-1] == expected

    # No duplicates anywhere.
    assert len(set(names)) == len(names)


def test_lean_names_resolve_against_catalog():
    """Every LEAN name must exist in TOOLS. The two async-python tools land
    in the same PR; the membership check covers them like any other name."""
    catalog_names = {t["name"] for t in bridge.TOOLS}
    missing = [n for n in bridge.LEAN_TOOL_NAMES if n not in catalog_names]
    assert missing == [], f"LEAN_TOOL_NAMES not in TOOLS catalog: {missing}"


def test_lean_list_has_no_duplicate_names():
    assert len(set(bridge.LEAN_TOOL_NAMES)) == len(bridge.LEAN_TOOL_NAMES)


def test_lean_mode_case_and_whitespace_tolerant(monkeypatch):
    _set_mode(monkeypatch, "  LeAn  ")
    assert bridge.tool_mode() == "lean"


def test_default_mode_still_full_catalog(monkeypatch):
    _set_mode(monkeypatch, None)
    assert bridge.tool_mode() == "all"
    assert bridge.advertised_tools() == bridge.TOOLS


def test_progressive_mode_unchanged(monkeypatch):
    _set_mode(monkeypatch, "progressive")
    assert bridge.tool_mode() == "progressive"

    names = [t["name"] for t in bridge.advertised_tools()]
    catalog_names = {t["name"] for t in bridge.TOOLS}
    expected_core = [n for n in bridge.CORE_TOOL_NAMES if n in catalog_names]
    assert names == expected_core + ["search_tools"]
