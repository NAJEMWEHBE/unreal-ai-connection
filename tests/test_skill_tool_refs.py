"""Guard: every tool name a bundled skill presents as callable must exist in
the bridge ``TOOLS`` catalog.

The ``driving-unreal`` skill names ~50 tools in prose. PR #238 cross-checked
those by hand ("zero invented names", catching spec-era drift such as
``set_material_instance_parameter`` -> ``set_mi_parameter`` and
``get_sequence_info`` -> ``inspect_sequence``). This test automates that
check so future recipe edits cannot reintroduce a phantom tool name.

Extraction: backtick-quoted snake_case tokens in ``skills/**/*.md``. Tokens
that are documented vocabulary -- parameter names, error codes, response
fields, or anti-example tool names the docs explicitly say do NOT exist --
are listed in ``NON_TOOL_TOKENS`` and excluded. Everything else must resolve
to a real tool.
"""

import glob
import os
import re

import unreal_ai_connection_bridge as bridge

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_GLOB = os.path.join(REPO_ROOT, "skills", "**", "*.md")

# Backtick-quoted snake_case identifiers with at least one underscore -- the
# shape of a tool name. Single-word lowercase tokens (text, force, compile,
# path) and CamelCase class names (VerticalBox) are not tool-shaped and are
# intentionally ignored.
_CANDIDATE_RE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")

# Tokens that LOOK like tool names but are not callable tools. Kept explicit
# (not a heuristic) so a genuinely misspelled tool name cannot hide here.
# Grouped by why each is not a tool. When a recipe legitimately introduces a
# new param/field token, add it here with its category.
NON_TOOL_TOKENS = {
    # JSON-Schema parameter names
    "continue_on_error",
    "time_seconds",
    "timeout_sec",
    # error codes / response fields
    "ambiguous_actor",
    "tick_resolution",
    # anti-example: the docs explicitly state this tool does NOT exist
    # (the real entry point is register_subscription)
    "start_event_subscription",
}


def _skill_files():
    files = glob.glob(SKILL_GLOB, recursive=True)
    assert files, f"No skill markdown found under {SKILL_GLOB}"
    return files


def _candidate_refs():
    """Map each backtick snake_case token -> sorted list of skill files it
    appears in (relative to the repo root, for readable failure output)."""
    found: dict[str, set[str]] = {}
    for path in _skill_files():
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        rel = os.path.relpath(path, REPO_ROOT)
        for name in _CANDIDATE_RE.findall(text):
            found.setdefault(name, set()).add(rel)
    return found


def test_skill_tool_references_exist_in_catalog():
    tool_names = {t["name"] for t in bridge.TOOLS}
    offending = {
        name: sorted(files)
        for name, files in _candidate_refs().items()
        if name not in tool_names and name not in NON_TOOL_TOKENS
    }
    assert not offending, (
        "Skill docs reference tool-shaped names absent from the bridge TOOLS "
        "catalog. Either the name is misspelled (fix the skill) or it is "
        "documented vocabulary (add it to NON_TOOL_TOKENS with its category):\n"
        + "\n".join(f"  {name} -> {files}" for name, files in sorted(offending.items()))
    )


def test_skill_references_at_least_one_real_tool():
    """Sanity check that extraction is actually finding tool references -- a
    regex regression that matched nothing would make the guard above pass
    vacuously."""
    tool_names = {t["name"] for t in bridge.TOOLS}
    referenced = set(_candidate_refs()) & tool_names
    assert referenced, "Extracted zero real tool references from skills/ -- regex likely broke"


def test_non_tool_denylist_has_no_real_tools():
    """Guard the denylist itself: if a name listed here becomes a real tool,
    it must be removed from NON_TOOL_TOKENS, otherwise the catalog check would
    silently stop validating skill references to it."""
    tool_names = {t["name"] for t in bridge.TOOLS}
    leaked = NON_TOOL_TOKENS & tool_names
    assert not leaked, (
        f"NON_TOOL_TOKENS lists names that are now real tools: {sorted(leaked)}. "
        "Remove them so the catalog check covers their skill references."
    )
