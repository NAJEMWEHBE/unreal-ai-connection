"""Guard: every tool name a bundled skill presents as callable must exist in
the bridge ``TOOLS`` catalog.

The ``driving-unreal`` skill names ~50 tools in prose. PR #238 cross-checked
those by hand ("zero invented names", catching spec-era drift such as
``set_material_instance_parameter`` -> ``set_mi_parameter`` and
``get_sequence_info`` -> ``inspect_sequence``). This test automates that
check so future recipe edits cannot reintroduce a phantom tool name.

Extraction: backtick-quoted lowercase identifiers in ``skills/**/*.md``. A
candidate that is not a real tool is flagged when it is *tool-shaped* --
either multi-word snake_case (the shape of nearly every tool) or a close
miss of a real tool name (so a single-word tool like ``unsubscribe`` is
still covered even though it carries no underscore). Documented vocabulary
-- parameter names, error codes, anti-example tool names the docs say do NOT
exist -- is listed in ``NON_TOOL_TOKENS`` and excluded.
"""

import difflib
import glob
import os
import re

import unreal_ai_connection_bridge as bridge

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_GLOB = os.path.join(REPO_ROOT, "skills", "**", "*.md")

# Backtick-quoted lowercase identifiers (>=2 chars). Captures both multi-word
# (spawn_actor) and single-word (unsubscribe) names; CamelCase class names
# (VerticalBox) are not matched. Plain English single words that slip through
# are filtered below: they are only flagged if they near-miss a real tool.
_CANDIDATE_RE = re.compile(r"`([a-z][a-z0-9_]+)`")

# A non-catalog single word is only treated as a misspelled tool when its
# similarity to a real tool name clears this bar -- high enough that ordinary
# English words (text, force, compile) never trip it, low enough to catch a
# one-character typo of a real single-word tool.
_NEAR_MISS_CUTOFF = 0.85

# Tokens that LOOK like tool names but are not callable tools. Kept explicit
# (not a heuristic) so a genuinely misspelled tool name cannot hide here.
# Grouped by why each is not a tool. When a recipe legitimately introduces a
# new param/field token, add it here with its category.
NON_TOOL_TOKENS = {
    # JSON-Schema parameter names
    "continue_on_error",
    "time_seconds",
    "timeout_sec",
    "actor_name",
    "attach_to",
    "class_path",
    "component_name",
    "relative_transform",
    "name_contains",
    "path_under",
    # error codes returned by actor-targeting tools
    "ambiguous_actor",
    # error codes returned by pie_control action=start/stop
    "pie_already_active",
    "pie_not_active",
    # response / return fields
    "tick_resolution",
    "is_playing",
    "is_simulating",
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


def _flag_offending(candidate_refs, tool_names):
    """Return {name: (files, suggestion)} for tool-shaped names that are not
    real tools. ``suggestion`` is the closest real tool name, or None."""
    offending = {}
    for name, files in candidate_refs.items():
        if name in tool_names or name in NON_TOOL_TOKENS:
            continue
        near = difflib.get_close_matches(name, tool_names, n=1, cutoff=_NEAR_MISS_CUTOFF)
        # Multi-word snake_case is tool-shaped on its own; a single word is
        # only suspect when it near-misses a real tool name.
        if "_" in name or near:
            offending[name] = (sorted(files), near[0] if near else None)
    return offending


def test_skill_tool_references_exist_in_catalog():
    tool_names = {t["name"] for t in bridge.TOOLS}
    offending = _flag_offending(_candidate_refs(), tool_names)
    assert not offending, (
        "Skill docs reference tool-shaped names absent from the bridge TOOLS "
        "catalog. Either the name is misspelled (fix the skill) or it is "
        "documented vocabulary (add it to NON_TOOL_TOKENS with its category):\n"
        + "\n".join(
            f"  {name} -> {files}" + (f" (did you mean '{hint}'?)" if hint else "")
            for name, (files, hint) in sorted(offending.items())
        )
    )


def test_skill_references_at_least_one_real_tool():
    """Sanity check that extraction is actually finding tool references -- a
    regex regression that matched nothing would make the guard above pass
    vacuously."""
    tool_names = {t["name"] for t in bridge.TOOLS}
    referenced = set(_candidate_refs()) & tool_names
    assert referenced, "Extracted zero real tool references from skills/ -- regex likely broke"


def test_guard_catches_single_word_tool_typo():
    """Regression: a misspelled single-word tool (no underscore) must be
    flagged via near-miss matching, not silently skipped because it lacks an
    underscore."""
    tool_names = {t["name"] for t in bridge.TOOLS}
    assert "unsubscribe" in tool_names, "fixture assumes unsubscribe is a tool"
    flagged = _flag_offending({"unsubscribel": {"skills/fake.md"}}, tool_names)
    assert "unsubscribel" in flagged
    assert flagged["unsubscribel"][1] == "unsubscribe"


def test_guard_ignores_plain_english_single_words():
    """Ordinary single words in backticks (op, text, force, compile) must not
    be mistaken for tools."""
    tool_names = {t["name"] for t in bridge.TOOLS}
    refs = {word: {"skills/fake.md"} for word in ("op", "text", "force", "compile")}
    assert not _flag_offending(refs, tool_names)


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
