"""Smoke tests for scripts/install.ps1 and scripts/install.sh.

These tests verify static properties of the installer scripts — they don't
actually exec them against a real UE project, since that requires a real .uproject
plus a real UE engine on the host. The actionable property checks:

  - both scripts exist and are non-empty
  - both scripts mention every supported client (so per-client setup recipes
    and the installer agree on the client list)
  - both scripts reference the bridge path under bridge/
  - the PowerShell script declares the right parameter set
  - the bash script's --client switch has the right ValidateSet
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PS_SCRIPT = ROOT / "scripts" / "install.ps1"
SH_SCRIPT = ROOT / "scripts" / "install.sh"

SUPPORTED_CLIENTS = {
    "claude-code", "claude-desktop", "cursor", "codex-cli", "windsurf",
    "continue", "cline", "zed", "gemini-cli", "vscode-copilot",
}


def test_powershell_script_exists():
    assert PS_SCRIPT.is_file()
    assert PS_SCRIPT.stat().st_size > 500


def test_bash_script_exists():
    assert SH_SCRIPT.is_file()
    assert SH_SCRIPT.stat().st_size > 500


def test_powershell_script_lists_every_client():
    text = PS_SCRIPT.read_text(encoding="utf-8")
    # ValidateSet block enumerates the supported clients
    validate_match = re.search(r"\[ValidateSet\(\s*([^)]+)\)\]", text, re.S)
    assert validate_match is not None, "ValidateSet block not found"
    block = validate_match.group(1)
    declared = {tok.strip().strip("'").strip('"') for tok in block.split(",") if tok.strip()}
    assert SUPPORTED_CLIENTS <= declared, f"missing clients: {SUPPORTED_CLIENTS - declared}"


def test_bash_script_lists_every_client():
    text = SH_SCRIPT.read_text(encoding="utf-8")
    # Case-statement validation enumerates every client
    for client in SUPPORTED_CLIENTS:
        assert client in text, f"install.sh missing client: {client}"


def test_powershell_references_bridge():
    text = PS_SCRIPT.read_text(encoding="utf-8")
    assert "unreal_claude_mcp_bridge.py" in text
    assert "bridge\\unreal_claude_mcp_bridge.py" in text or "bridge/unreal_claude_mcp_bridge.py" in text


def test_bash_references_bridge():
    text = SH_SCRIPT.read_text(encoding="utf-8")
    assert "bridge/unreal_claude_mcp_bridge.py" in text


def test_powershell_has_dry_run():
    text = PS_SCRIPT.read_text(encoding="utf-8")
    assert "[switch]$DryRun" in text
    assert "DryRun" in text


def test_bash_has_dry_run():
    text = SH_SCRIPT.read_text(encoding="utf-8")
    assert "--dry-run" in text
    assert "DRY_RUN" in text


def test_powershell_python_version_gate():
    text = PS_SCRIPT.read_text(encoding="utf-8")
    # Must require 3.11+
    assert "Python 3.11+" in text or "3.11+" in text
    assert "py" in text  # py launcher


def test_bash_python_version_gate():
    text = SH_SCRIPT.read_text(encoding="utf-8")
    assert "Python 3.11+" in text or "3.11+" in text
    assert "python3" in text


def test_bash_shebang():
    text = SH_SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash"), "shebang missing"


def test_powershell_strict_mode():
    """ErrorActionPreference Stop = fail-fast on errors."""
    text = PS_SCRIPT.read_text(encoding="utf-8")
    assert "$ErrorActionPreference = 'Stop'" in text


def test_bash_strict_mode():
    """set -euo pipefail is the bash equivalent."""
    text = SH_SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text


def test_no_personal_paths():
    """No NINOH or other personal paths leaked into the scripts."""
    for script in (PS_SCRIPT, SH_SCRIPT):
        text = script.read_text(encoding="utf-8")
        assert "NINOH" not in text
        assert "F:\\ax plug in" not in text
        assert "HDMediaVirtualStudio" not in text
