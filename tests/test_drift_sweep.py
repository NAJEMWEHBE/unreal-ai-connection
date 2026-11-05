"""CI guard: run the doc-drift sweep and fail if any stale numbers remain.

The actual logic lives in `scripts/drift_sweep.py` so it can also be
invoked directly from a shell or a future pre-commit hook. The smoke
test just shells out and asserts a clean exit; the other tests exercise
specific helpers directly so the canonical-source contract has unit
coverage (not just whole-script integration).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "drift_sweep.py"


def _load_drift_sweep_module():
    """Import scripts/drift_sweep.py as a module for direct-call tests."""
    spec = importlib.util.spec_from_file_location("drift_sweep", SCRIPT)
    assert spec and spec.loader, "drift_sweep.py must be importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_doc_drift() -> None:
    """Every scanned doc mirrors the canonical tool/test counts and
    canonical version strings."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        "doc-drift sweep reported stale numbers:\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


def test_uplugin_versions_match_declared_constants() -> None:
    """_read_uplugin_versions() returns (plugin_version, ue_engine_minor)
    pulled live from the .uplugin manifest. The plugin_version must look
    like a semver triple (N.N.N); the engine minor must be a major.minor
    pair (N.N). Specific values are NOT asserted -- they bump every
    release -- but the SHAPE is part of the contract every consumer of
    canonical_numbers() relies on."""
    drift_sweep = _load_drift_sweep_module()
    plugin_version, ue_minor = drift_sweep._read_uplugin_versions()
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin_version), (
        f"plugin_version '{plugin_version}' is not a semver triple"
    )
    assert re.fullmatch(r"\d+\.\d+", ue_minor), (
        f"ue_engine_minor '{ue_minor}' is not a major.minor pair"
    )


def test_canonical_dict_contains_all_pattern_keys() -> None:
    """Every key referenced by PATTERNS must exist in canonical_numbers().
    Without this guard, a typo in a pattern's canonical-key string would
    surface only when that pattern fires on a real document and the
    scanner crashes with KeyError mid-scan -- a much worse signal than a
    test that catches the wiring gap at collection time."""
    drift_sweep = _load_drift_sweep_module()
    canonical = drift_sweep.canonical_numbers()
    pattern_keys = {key for _, key in drift_sweep.PATTERNS}
    missing = pattern_keys - canonical.keys()
    assert not missing, (
        f"PATTERNS reference canonical keys not provided by canonical_numbers(): {sorted(missing)}"
    )
