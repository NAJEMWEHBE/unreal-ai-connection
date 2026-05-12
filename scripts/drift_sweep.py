#!/usr/bin/env python3
"""Mechanical doc-drift sweep.

Verifies that the high-traffic project docs mirror the canonical numbers
sourced from authoritative files (`tests/conftest.py` constants and a live
`pytest --collect-only` count). Prints `[file:line]` lines for every stale
number it finds and exits non-zero if any drift exists.

Safe to run in CI: no network, no LLM dep, no editor required.

Out of scope: this scanner intentionally skips `docs/HANDOFF.md` and
`docs/superpowers/plans/**` because both preserve sprint chronology --
historical tool/test counts in those files are correct as of THEIR
session and must not be rewritten.

Usage:

    python scripts/drift_sweep.py

Exit codes:
    0 -- no drift found
    1 -- drift found (each finding printed on its own line)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Union

REPO_ROOT = Path(__file__).resolve().parent.parent

# Canonical values can be int (counts) or str (version strings). The scan
# loop branches on isinstance(int) to know whether to coerce match.group(1)
# before comparing.
CanonicalValue = Union[int, str]

# Files that are expected to reflect the current canonical numbers.
# Anything not in this list (HANDOFF, superpowers plans, archived session
# memory) is intentionally NOT scanned -- those files freeze history.
SCAN_FILES = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "tests/README.md",
    "docs/INSTALLATION.md",
    "docs/RESTART-RECOVERY.md",
    "docs/TOOLS.md",
    "docs/ARCHITECTURE.md",
    ".github/copilot-instructions.md",
    "bridge/unreal_claude_mcp_bridge.py",
    "UnrealClaudeMCP/Resources/mcp_manifest.json",
]


# Each entry is (compiled_regex, canonical_key). The regex must have a
# single capturing group around the number to validate. Patterns are
# deliberately specific (must include a context word like "tools" or
# "pytest cases") to avoid matching unrelated digits.
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(\d+)\s+tools?\s+total\b", re.IGNORECASE), "tools"),
    (re.compile(r"\ball\s+(\d+)\s+tools?\b", re.IGNORECASE), "tools"),
    (re.compile(r"\b(\d+)\s+tools?\s+live\b", re.IGNORECASE), "tools"),
    (re.compile(r"\b(\d+)\s+tools?\s+shipped\b", re.IGNORECASE), "tools"),
    (re.compile(r"\b(\d+)\s+tools?\s+become\s+available\b", re.IGNORECASE), "tools"),
    (re.compile(r"across\s+all\s+(\d+)\s+tools?\b", re.IGNORECASE), "tools"),
    (re.compile(r"total\s+of\s+(\d+)\s+tools?\b", re.IGNORECASE), "tools"),
    (re.compile(r"\b(\d+)\s+native\s+C\+\+\s+handlers?\b", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\b(\d+)\s+UE\s+C\+\+\s+handlers?\b", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\((\d+)\s+native\s+C\+\+\s+handlers?", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\((\d+)\s+UE\s+C\+\+\s+handlers?", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\((\d+)\s+C\+\+\s+handlers?\s+\+", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\b(\d+)\s+are\s+(?:native\s+)?C\+\+\s+(?:handlers?|methods?)\b", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\b(\d+)\s+are\s+JSON-RPC\b", re.IGNORECASE), "cpp_handlers"),
    (re.compile(r"\b(\d+)\s+bridge[- ]side\s+synthetic", re.IGNORECASE), "synthetic_tools"),
    (re.compile(r"\b(\d+)\s+synthetic\s+tools?\b", re.IGNORECASE), "synthetic_tools"),
    (re.compile(r"\b(\d+)\s+bridge\s+synthetic\b", re.IGNORECASE), "synthetic_tools"),
    # "The remaining N --" pattern (TOOLS.md intro). Anchored to a dash
    # so it doesn't match generic "remaining N <word>" phrasings.
    (re.compile(r"remaining\s+(\d+)\s+[–—\-]+\s", re.IGNORECASE), "synthetic_tools"),
    (re.compile(r"\b(\d+)\s+pytest\s+cases?\b", re.IGNORECASE), "pytest_cases"),
    (re.compile(r"#\s*(\d+)\s+tests,", re.IGNORECASE), "pytest_cases"),
    # UE engine minor (e.g., "5.7"). The negative lookahead `(?!\.\d)`
    # prevents matching "5.7" inside "5.7.4" -- the patch-level "Tested on"
    # version in README is intentionally allowed to drift independently of
    # the EngineVersion constraint in the .uplugin manifest.
    (re.compile(r"\bUE\s+(\d+\.\d+)(?!\.\d)"), "ue_engine_minor"),
    (re.compile(r"\bUnreal\s+Engine\s+(\d+\.\d+)(?!\.\d)"), "ue_engine_minor"),
    # Plugin version. Anchored to specific phrasings ("Latest release",
    # "Current version", "plugin version") so generic historical version
    # mentions (e.g., "v0.3.0+ shared modules" in ARCHITECTURE.md) are not
    # caught and forced into drift.
    (re.compile(r"latest\s+release[^v\n]*v(\d+\.\d+\.\d+)", re.IGNORECASE), "plugin_version"),
    (re.compile(r"current\s+version[^v\n]*v(\d+\.\d+\.\d+)", re.IGNORECASE), "plugin_version"),
    (re.compile(r"plugin\s+version[^v\n]*v(\d+\.\d+\.\d+)", re.IGNORECASE), "plugin_version"),
]


def _read_conftest_constants() -> tuple[int, int]:
    """Return (cpp_handler_count, synthetic_tool_count) from conftest."""
    conftest_path = REPO_ROOT / "tests" / "conftest.py"
    text = _read_text_lenient(conftest_path)
    cpp_match = re.search(r"EXPECTED_CPP_HANDLER_COUNT\s*=\s*(\d+)", text)
    syn_match = re.search(r"EXPECTED_SYNTHETIC_TOOL_COUNT\s*=\s*(\d+)", text)
    if not cpp_match or not syn_match:
        raise RuntimeError(
            "tests/conftest.py is missing EXPECTED_CPP_HANDLER_COUNT or "
            "EXPECTED_SYNTHETIC_TOOL_COUNT -- canonical numbers cannot be "
            "determined. If the constants were renamed or moved, update "
            "_read_conftest_constants() to match."
        )
    return int(cpp_match.group(1)), int(syn_match.group(1))


def _read_live_pytest_count() -> int:
    """Run `pytest --collect-only -q` and parse the trailing count.

    Robust to several pytest output variants:
      - "N tests collected in ..."           (default -q output)
      - "N items collected"                  (older pytest / verbose mode)
      - "collected N items"                  (some plugin configurations)

    Returns exit-code information in the error message when pytest itself
    fails to collect (e.g. import error in a test file) so the failure is
    diagnosable from CI logs without re-running pytest by hand.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,  # we want to surface the parse / exit-code separately
    )
    combined = result.stdout + result.stderr
    patterns = (
        r"(\d+)\s+(?:tests?|items?)\s+collected",
        r"collected\s+(\d+)\s+(?:tests?|items?)",
    )
    for pattern in patterns:
        match = re.search(pattern, combined)
        if match:
            return int(match.group(1))
    raise RuntimeError(
        "Could not parse pytest --collect-only output "
        f"(pytest exit={result.returncode}). Last lines:\n"
        + "\n".join(combined.splitlines()[-10:])
    )


def _read_uplugin_versions() -> tuple[str, str]:
    """Return (plugin_version, ue_engine_minor) from the .uplugin manifest.

    The .uplugin file is JSON, so we parse it cleanly rather than regexing
    the source. ``VersionName`` is the plugin's user-facing version (e.g.
    "0.9.1"); ``EngineVersion`` is the engine compatibility constraint (e.g.
    "5.7.0") -- the scanner enforces the MINOR portion ("5.7") because
    individual docs use the minor form interchangeably with the patch form
    (e.g. "UE 5.7 plugin" in AGENTS.md, "UE 5.7.4 / Windows 11" in README's
    "Tested on" row). Forcing every "UE 5.7" mention to track patch-level
    bumps would create unwanted churn.

    Raises RuntimeError with a caller-actionable message if the .uplugin is
    missing or malformed -- callers should never silently fall back to
    stale defaults.
    """
    uplugin_path = REPO_ROOT / "UnrealClaudeMCP" / "UnrealClaudeMCP.uplugin"
    if not uplugin_path.exists():
        raise RuntimeError(
            f"Cannot find UnrealClaudeMCP.uplugin at {uplugin_path}. If the "
            "module was renamed or moved, update _read_uplugin_versions() "
            "to match the new path."
        )
    try:
        data = json.loads(_read_text_lenient(uplugin_path))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"UnrealClaudeMCP.uplugin is not valid JSON: {e}"
        ) from e
    version_name = data.get("VersionName")
    engine_version = data.get("EngineVersion")
    if not isinstance(version_name, str) or not isinstance(engine_version, str):
        raise RuntimeError(
            ".uplugin must declare both VersionName and EngineVersion as "
            "strings; got "
            f"VersionName={version_name!r}, EngineVersion={engine_version!r}."
        )
    # Strip the patch component from EngineVersion (5.7.0 -> 5.7). Docs use
    # the minor form; the patch is reserved for the README "Tested on" row.
    minor_match = re.match(r"(\d+\.\d+)", engine_version)
    if not minor_match:
        raise RuntimeError(
            f"EngineVersion '{engine_version}' does not start with a major.minor pair."
        )
    return version_name, minor_match.group(1)


def _read_text_lenient(path: Path) -> str:
    """Read a tracked doc with encoding-tolerance.

    Defaults to UTF-8 but falls back to ``errors='replace'`` on a decode
    failure so a single mis-encoded byte in a doc cannot crash the whole
    sweep. UTF-8 with replacement is enough for regex matching; the bad
    bytes never appear in canonical-number context anyway.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def canonical_numbers() -> dict[str, CanonicalValue]:
    """Build the canonical dict that PATTERNS validate against.

    Values are a mix of int (counts) and str (version strings); the scan
    loop branches on isinstance(int) to decide whether to int-coerce the
    matched group before comparison.
    """
    cpp, syn = _read_conftest_constants()
    plugin_version, ue_engine_minor = _read_uplugin_versions()
    return {
        "tools": cpp + syn,
        "cpp_handlers": cpp,
        "synthetic_tools": syn,
        "pytest_cases": _read_live_pytest_count(),
        "plugin_version": plugin_version,
        "ue_engine_minor": ue_engine_minor,
    }


def scan(canonical: dict[str, CanonicalValue]) -> list[str]:
    """Return one finding string per stale value across the scan list."""
    findings: list[str] = []
    for relpath in SCAN_FILES:
        path = REPO_ROOT / relpath
        if not path.exists():
            continue
        lines = _read_text_lenient(path).splitlines()
        for lineno, line in enumerate(lines, start=1):
            for pattern, key in PATTERNS:
                for match in pattern.finditer(line):
                    expected = canonical[key]
                    raw = match.group(1)
                    found: CanonicalValue = (
                        int(raw) if isinstance(expected, int) else raw
                    )
                    if found != expected:
                        findings.append(
                            f"{relpath}:{lineno}: '{match.group(0).strip()}' "
                            f"({key}={found!r}, expected {expected!r})"
                        )
    return findings


def main() -> int:
    canonical = canonical_numbers()
    findings = scan(canonical)
    if not findings:
        print(
            "doc-drift sweep: clean ("
            f"tools={canonical['tools']}, "
            f"cpp_handlers={canonical['cpp_handlers']}, "
            f"synthetic_tools={canonical['synthetic_tools']}, "
            f"pytest_cases={canonical['pytest_cases']}, "
            f"plugin_version={canonical['plugin_version']}, "
            f"ue_engine_minor={canonical['ue_engine_minor']})."
        )
        return 0
    print("doc-drift sweep: drift detected.")
    for finding in findings:
        print(f"  {finding}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
