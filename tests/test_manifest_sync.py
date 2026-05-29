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

import unreal_ai_connection_bridge as bridge
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


# --- Phase H: engine-version gating metadata parity ----------------------
# docs/PHASE-H-COMPAT.md "Bridge / Manifest Metadata Plan": the catalog now
# carries optional min_engine_version / max_engine_version. The four
# synthetic tools that call UE-5.0+ unreal.* Python APIs must declare
# min_engine_version "5.0", and the bridge TOOLS list + mcp_manifest.json
# must agree field-for-field. No other tool may carry the field.

ENGINE_GATED_TOOLS = {
    "convert_hdri_to_cubemap",
    "sequencer_add_transform_keyframe",
    "get_camera_transform",
    "set_camera_transform",
    "import_mesh",
    "material_auto_remap",
}


def test_engine_gated_tools_declare_min_engine_version_in_bridge():
    bridge_by_name = {t["name"]: t for t in bridge.TOOLS}
    for name in ENGINE_GATED_TOOLS:
        entry = bridge_by_name[name]
        assert entry.get("min_engine_version") == "5.0", (
            f"Bridge tool '{name}' must carry min_engine_version '5.0' "
            f"(Phase H gating); got {entry.get('min_engine_version')!r}"
        )
        # max_engine_version is supported but unused -> must be present-as-None.
        assert entry.get("max_engine_version", "MISSING") is None, (
            f"Bridge tool '{name}' must carry max_engine_version null."
        )


def test_engine_gated_tools_declare_min_engine_version_in_manifest():
    manifest = _load_manifest()
    by_name = {t["name"]: t for t in manifest["tools"]}
    for name in ENGINE_GATED_TOOLS:
        entry = by_name[name]
        assert entry.get("min_engine_version") == "5.0", (
            f"Manifest tool '{name}' must carry min_engine_version '5.0'; "
            f"got {entry.get('min_engine_version')!r}"
        )
        assert entry.get("max_engine_version", "MISSING") is None, (
            f"Manifest tool '{name}' must carry max_engine_version null."
        )


def test_engine_version_metadata_parity_bridge_vs_manifest():
    """min/max_engine_version must agree across both artefacts for every
    tool -- catches drift in either direction (companion to the existing
    name/required parity checks)."""
    manifest = _load_manifest()
    manifest_by_name = {t["name"]: t for t in manifest["tools"]}
    bridge_by_name = {t["name"]: t for t in bridge.TOOLS}

    for name in manifest_by_name:
        m = manifest_by_name[name]
        b = bridge_by_name[name]
        assert m.get("min_engine_version") == b.get("min_engine_version"), (
            f"Tool '{name}': min_engine_version drift -- "
            f"manifest={m.get('min_engine_version')!r} "
            f"bridge={b.get('min_engine_version')!r}"
        )
        assert m.get("max_engine_version") == b.get("max_engine_version"), (
            f"Tool '{name}': max_engine_version drift -- "
            f"manifest={m.get('max_engine_version')!r} "
            f"bridge={b.get('max_engine_version')!r}"
        )


def test_no_other_tool_carries_min_engine_version():
    """Exactly the four gated tools may declare min_engine_version. A stray
    field elsewhere would silently block a tool that works on 4.27+."""
    for t in bridge.TOOLS:
        if t["name"] in ENGINE_GATED_TOOLS:
            continue
        assert "min_engine_version" not in t or t.get("min_engine_version") is None, (
            f"Bridge tool '{t['name']}' unexpectedly carries "
            f"min_engine_version={t.get('min_engine_version')!r}"
        )
    manifest = _load_manifest()
    for t in manifest["tools"]:
        if t["name"] in ENGINE_GATED_TOOLS:
            continue
        assert "min_engine_version" not in t or t.get("min_engine_version") is None, (
            f"Manifest tool '{t['name']}' unexpectedly carries "
            f"min_engine_version={t.get('min_engine_version')!r}"
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
