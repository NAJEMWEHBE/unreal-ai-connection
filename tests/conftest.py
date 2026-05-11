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
# bridge/unreal_claude_mcp_bridge.py::SYNTHETIC_TOOLS. The split below is
# informational; tests assert against the total via EXPECTED_TOOL_COUNT.
EXPECTED_CPP_HANDLER_COUNT = 64
EXPECTED_SYNTHETIC_TOOL_COUNT = 10
EXPECTED_TOOL_COUNT = EXPECTED_CPP_HANDLER_COUNT + EXPECTED_SYNTHETIC_TOOL_COUNT
