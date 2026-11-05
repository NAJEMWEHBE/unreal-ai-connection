"""The C++ sources, the bridge catalog, the manifest and docs/TOOLS.md agree.

test_manifest_sync.py checks the bridge TOOLS list against the manifest. This
file closes the other two sides: every method a C++ handler implements is
advertised (the four DMX tools once shipped unadvertised because nothing
checked this), and every advertised tool has its own TOOLS.md section.
"""

import pathlib
import re

from conftest import EXPECTED_CPP_HANDLER_COUNT, EXPECTED_SYNTHETIC_TOOL_COUNT

import unreal_ai_connection_bridge as bridge

ROOT = pathlib.Path(__file__).resolve().parent.parent
_METHOD = re.compile(r'GetMethodName\(\)[^{]*\{\s*return\s+TEXT\("(\w+)"\)')


def _cpp_methods() -> set[str]:
    names = set()
    for cpp in ROOT.glob("UnrealAIConnection*/Source/**/*.cpp"):
        names.update(_METHOD.findall(cpp.read_text(encoding="utf-8", errors="replace")))
    return names


def _native_catalog_names() -> set[str]:
    return {t["name"] for t in bridge.TOOLS} - set(bridge.SYNTHETIC_TOOLS)


def test_parser_sees_the_handlers():
    assert {"list_tools", "spawn_actor", "create_dmx_patch"} <= _cpp_methods()


def test_every_cpp_method_is_advertised():
    missing = _cpp_methods() - _native_catalog_names()
    assert not missing, f"C++ handlers not in the bridge TOOLS catalog: {sorted(missing)}"


def test_every_advertised_native_tool_has_a_cpp_handler():
    orphans = _native_catalog_names() - _cpp_methods()
    assert not orphans, f"catalog tools with no C++ GetMethodName(): {sorted(orphans)}"


def test_count_constants_match_the_code():
    assert len(_cpp_methods()) == EXPECTED_CPP_HANDLER_COUNT
    assert len(bridge.SYNTHETIC_TOOLS) == EXPECTED_SYNTHETIC_TOOL_COUNT


def test_every_tool_has_a_tools_md_section():
    text = (ROOT / "docs" / "TOOLS.md").read_text(encoding="utf-8")
    headings = set(re.findall(r"^##\s+`?(\w+)`?\s*$", text, flags=re.MULTILINE))
    missing = {t["name"] for t in bridge.TOOLS} - headings
    assert not missing, f"docs/TOOLS.md has no '## <name>' section for: {sorted(missing)}"
