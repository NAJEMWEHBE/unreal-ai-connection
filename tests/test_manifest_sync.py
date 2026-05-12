"""
Drift detection: the bridge's static `TOOLS` list, the C++ plugin's
`Resources/mcp_manifest.json`, and the live UE handler set are kept in
sync by hand. These tests catch drift between the two artefacts that
ship together (bridge + manifest) before users see it.

The third artefact - the live handler set - can only be checked against
a running editor (`examples/smoke_test.py` covers that).
"""

import json
import os

import unreal_claude_mcp_bridge as bridge
from conftest import EXPECTED_TOOL_COUNT


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(
    REPO_ROOT, "UnrealClaudeMCP", "Resources", "mcp_manifest.json"
)


def _load_manifest():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_manifest_file_exists():
    assert os.path.isfile(MANIFEST_PATH), f"Missing manifest: {MANIFEST_PATH}"


def test_manifest_tool_names_match_bridge():
    manifest = _load_manifest()
    manifest_names = {t["name"] for t in manifest["tools"]}
    bridge_names = {t["name"] for t in bridge.TOOLS}
    assert manifest_names == bridge_names, (
        f"Drift between bridge TOOLS and mcp_manifest.json:\n"
        f"  Only in bridge:   {bridge_names - manifest_names}\n"
        f"  Only in manifest: {manifest_names - bridge_names}"
    )


def test_manifest_tool_count_matches_bridge():
    manifest = _load_manifest()
    assert len(manifest["tools"]) == len(bridge.TOOLS) == EXPECTED_TOOL_COUNT


def test_manifest_transport_block():
    """Sanity-check the manifest transport metadata stays consistent with
    the bridge defaults. Catches accidental port/host changes."""
    manifest = _load_manifest()
    t = manifest["transport"]
    assert t["type"] == "tcp"
    assert t["host"] == "127.0.0.1"
    assert t["port"] == 18888
    # Bridge reads UCMCP_PORT from env, but the *default* must match.
    assert int(os.environ.get("UCMCP_PORT", "18888")) == t["port"]


def test_manifest_version_matches_bridge_server_version():
    manifest = _load_manifest()
    assert manifest["version"] == bridge.SERVER_VERSION


def test_manifest_required_params_match_bridge_required():
    """Where the manifest documents a param as 'required' in its description
    string, the bridge's JSON Schema MUST list it in `required[]`. Otherwise
    Claude will silently send a `tools/call` missing a param the UE handler
    needs."""
    manifest = _load_manifest()
    bridge_by_name = {t["name"]: t for t in bridge.TOOLS}

    for tool in manifest["tools"]:
        name = tool["name"]
        params = tool.get("params") or {}
        if not isinstance(params, dict):
            continue  # tools that document free-form `returns: "see ..."`

        manifest_required = {
            k for k, v in params.items()
            if isinstance(v, str) and "required" in v.lower()
        }

        bridge_tool = bridge_by_name[name]
        bridge_required = set(bridge_tool["inputSchema"].get("required", []))

        missing_in_bridge = manifest_required - bridge_required
        assert not missing_in_bridge, (
            f"Tool '{name}': manifest marks {missing_in_bridge} as required "
            f"but bridge schema does not. Drift will cause MCP clients to "
            f"send malformed tools/call payloads."
        )


def test_bridge_required_params_documented_in_manifest():
    """Reverse-direction drift check. If the bridge's JSON Schema lists a
    param in `required[]`, the manifest must document it (either as a key
    in the params dict, or via free-form 'see docs/TOOLS.md' pointer). An
    orphan-required in the bridge means doc-reading clients won't know to
    send the field even though the bridge will reject without it.

    Companion to test_manifest_required_params_match_bridge_required, which
    only catches drift in the manifest -> bridge direction."""
    manifest = _load_manifest()
    manifest_by_name = {t["name"]: t for t in manifest["tools"]}

    for bridge_tool in bridge.TOOLS:
        name = bridge_tool["name"]
        bridge_required = set(bridge_tool["inputSchema"].get("required", []))
        if not bridge_required:
            continue

        manifest_tool = manifest_by_name[name]
        params = manifest_tool.get("params") or {}

        # Free-form pointer (e.g. "see docs/TOOLS.md"); we accept this as
        # documentation-deferred and don't try to cross-check field-by-field.
        if isinstance(params, str):
            continue

        documented_keys = set(params.keys()) if isinstance(params, dict) else set()
        orphans = bridge_required - documented_keys
        assert not orphans, (
            f"Tool '{name}': bridge schema requires {orphans} but manifest "
            f"params dict does not document them. Add an entry to "
            f"manifest.tools[].params describing the required field."
        )
