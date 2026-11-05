"""One asset-path rule, one error code, for every path-taking synthetic.

Embedded NUL bytes and `..` segments are rejected before any editor
round-trip, and every tool reports the same mistake with the same
`<tool>: invalid_path: ...` error code (they used to differ by tool:
`invalid_path` in the older bulk_* tools, `path_invalid` in the newer ones).
"""

from unittest.mock import patch

import pytest
from conftest import tool_error

import unreal_ai_connection_bridge as bridge

# tool -> builds arguments with the bad path in the asset-path field
_CASES = {
    "bulk_delete_assets": lambda p: {"paths": [p]},
    "bulk_move_assets": lambda p: {"paths": [p], "dest_folder": "/Game/Dest"},
    "bulk_rename_assets": lambda p: {"renames": [{"path": p, "new_name": "NewName"}]},
    "bulk_duplicate_assets": lambda p: {"duplicates": [{"path": p, "dest_path": "/Game/Copy"}]},
    "bulk_inspect_assets": lambda p: {"paths": [p]},
    "get_reference_chain": lambda p: {"path": p},
    "bulk_compile_blueprints": lambda p: {"paths": [p]},
    "compare_assets": lambda p: {"path_a": p, "path_b": "/Game/B"},
    "inspect_dependency_graph": lambda p: {"path": p},
}

_BAD_PATHS = {"nul": "/Game/A\x00B", "dotdot": "/Game/../Secret"}


def _call(tool, args):
    with patch.object(bridge, "call_ue") as m:
        resp = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }
        )
    m.assert_not_called()
    return tool_error(resp)


@pytest.mark.parametrize("bad", _BAD_PATHS.values(), ids=_BAD_PATHS.keys())
@pytest.mark.parametrize("tool", _CASES)
def test_bad_asset_path_gets_the_same_error_code_everywhere(tool, bad):
    err = _call(tool, _CASES[tool](bad))
    assert err["code"] == -32602
    assert err["message"].startswith(f"{tool}: invalid_path: "), err["message"]


@pytest.mark.parametrize("bad", _BAD_PATHS.values(), ids=_BAD_PATHS.keys())
def test_destination_and_folder_fields_keep_their_field_specific_codes(bad):
    move = _call("bulk_move_assets", {"paths": ["/Game/A"], "dest_folder": bad})
    assert move["message"].startswith("bulk_move_assets: invalid_dest_folder: ")
    dup = _call("bulk_duplicate_assets", {"duplicates": [{"path": "/Game/A", "dest_path": bad}]})
    assert dup["message"].startswith("bulk_duplicate_assets: invalid_dest_path: ")
    fix = _call("bulk_fix_redirectors", {"folders": [bad]})
    assert fix["message"].startswith("bulk_fix_redirectors: folder_invalid: ")


def test_asset_names_with_double_dots_inside_a_segment_still_pass():
    assert bridge._validate_asset_path("t", "/Game/My..Asset", "path") is None
