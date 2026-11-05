"""Make the `bridge/` directory importable as a package-less module.

Also publishes the canonical tool-count constants used by drift tests. Bumping
the catalog in one place (here) avoids the recurring "two count assertions in
test_bridge.py + one in test_manifest_sync.py" miss documented in HANDOFF.md.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bridge"))


# Single source of truth for the total tool catalog size.
# Bumps as new C++ handlers register or new synthetic tools land in
# bridge/unreal_ai_connection_bridge.py::SYNTHETIC_TOOLS. The split below is
# informational; tests assert against the total via EXPECTED_TOOL_COUNT.
EXPECTED_CPP_HANDLER_COUNT = 118  # every GetMethodName() across the core + companion plugins (test_catalog_sync.py checks it against the C++ sources)
EXPECTED_SYNTHETIC_TOOL_COUNT = 37  # bumped from 35 — added Wave 1 studio-builder synthetics: batch_capture_cameras, batch_spawn_from_csv
EXPECTED_TOOL_COUNT = EXPECTED_CPP_HANDLER_COUNT + EXPECTED_SYNTHETIC_TOOL_COUNT


def is_tool_error(resp: dict) -> bool:
    """True when a tools/call response is a failed tool result (`isError`)."""
    result = resp.get("result") if isinstance(resp, dict) else None
    return isinstance(result, dict) and result.get("isError") is True


def tool_error(resp: dict) -> dict:
    """The error object a failed tools/call carries.

    Tool failures are MCP results with `isError: true` whose first text block
    is the JSON error object (`code`, `message`, optional `error_code`...).
    Fails loudly when `resp` is a success or a JSON-RPC protocol error.
    """
    import json

    assert is_tool_error(resp), f"expected an isError tool result, got: {resp}"
    return json.loads(resp["result"]["content"][0]["text"])
