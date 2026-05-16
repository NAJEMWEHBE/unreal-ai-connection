#!/usr/bin/env python3
"""Generate per-engine-bucket .uplugin variants for Phase H release.

The shipped UnrealClaudeMCP/UnrealClaudeMCP.uplugin pins
``"EngineVersion": "5.7.0"``. That field is a HARD load-gate: UE refuses
to load a plugin whose EngineVersion does not satisfy the running engine,
*regardless of the C++ compat shims*. A single binary also cannot span
4.27 -> 5.8 (FARFilter / FTSTicker / LWC / ULevelEditorSubsystem breaks),
so Phase H ships SOURCE in three engine buckets, each with its own
EngineVersion in the .uplugin:

    T1  UE 5.4 - 5.8   -> "EngineVersion": "5.4.0"
    T2  UE 5.0 - 5.3   -> "EngineVersion": "5.0.0"
    T3  UE 4.27        -> "EngineVersion": "4.27.0"

This script reads the CANONICAL .uplugin and emits the three variants
into ``dist/uplugin-variants/`` WITHOUT mutating the shipped file. Only
the ``EngineVersion`` value differs between the canonical file and a
variant; every other field (Modules, Plugins, Description, Version, ...)
is preserved verbatim, including key order and the 4-space indentation
the repo uses, so a reviewer diffing a variant against the canonical sees
exactly one changed line.

Pure standard library. No network, no third-party deps, no UE required.
Safe to run in CI and at release time.

Usage:

    python scripts/gen_uplugin_variants.py            # writes the 3 variants
    python scripts/gen_uplugin_variants.py --check     # dry-run; prints plan

Release-time use is documented in docs/PHASE-H-COMPAT.md.

UNVERIFIED-COMPILE NOTE: emitting a variant .uplugin only changes the
load-gate. It does NOT certify that the C++ compiles or runs on that
engine bucket -- the Phase H compat shims are source-authored and
unverified-compile. A per-engine host build + smoke pass is still
required before any bucket can be called "supported".
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_UPLUGIN = REPO_ROOT / "UnrealClaudeMCP" / "UnrealClaudeMCP.uplugin"
OUTPUT_DIR = REPO_ROOT / "dist" / "uplugin-variants"

# (variant filename, EngineVersion value, human label). The EngineVersion
# is the LOWEST engine in the bucket -- UE loads a plugin whose declared
# EngineVersion is <= the running engine, so "5.4.0" covers 5.4 - 5.8.
BUCKETS: list[tuple[str, str, str]] = [
    ("UnrealClaudeMCP-T1.uplugin", "5.4.0", "T1 (UE 5.4 - 5.8)"),
    ("UnrealClaudeMCP-T2.uplugin", "5.0.0", "T2 (UE 5.0 - 5.3)"),
    ("UnrealClaudeMCP-T3.uplugin", "4.27.0", "T3 (UE 4.27)"),
]


def read_canonical_text() -> str:
    """Return the raw canonical .uplugin text (UTF-8).

    Raises a caller-actionable error if the file is missing or invalid
    JSON -- the generator must never silently emit a malformed variant.
    """
    if not CANONICAL_UPLUGIN.exists():
        raise FileNotFoundError(
            f"Canonical .uplugin not found at {CANONICAL_UPLUGIN}. If the "
            "module was renamed or moved, update CANONICAL_UPLUGIN."
        )
    text = CANONICAL_UPLUGIN.read_text(encoding="utf-8")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Canonical .uplugin is not valid JSON: {exc}"
        ) from exc
    return text


def build_variant_text(canonical_text: str, engine_version: str) -> str:
    """Return variant .uplugin text with only EngineVersion swapped.

    The canonical file is parsed with object_pairs_hook=OrderedDict so key
    order is preserved, EngineVersion is replaced, and the result is
    re-serialized with the repo's 4-space indent + trailing newline. This
    keeps a variant-vs-canonical diff to exactly one line.
    """
    data = json.loads(canonical_text, object_pairs_hook=OrderedDict)
    if "EngineVersion" not in data:
        raise KeyError(
            "Canonical .uplugin has no EngineVersion field; cannot emit a "
            "load-gated variant. A bucketed release REQUIRES EngineVersion."
        )
    data["EngineVersion"] = engine_version
    return json.dumps(data, indent=4) + "\n"


def _display_path(path: Path) -> str:
    """Render a path relative to the repo root when it lives under it,
    else as-is. Keeps console output tidy for the normal release run
    without exploding when callers (e.g. tests) point OUTPUT_DIR at a
    tmp dir outside the repo tree."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def generate(check_only: bool = False) -> list[Path]:
    """Emit (or, with check_only, just plan) the three bucket variants.

    Returns the list of variant paths that would be / were written.
    OUTPUT_DIR is read from the module namespace at call time so tests
    can redirect it without touching the shipped repo tree.
    """
    canonical_text = read_canonical_text()
    written: list[Path] = []
    if not check_only:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, engine_version, label in BUCKETS:
        out_path = OUTPUT_DIR / filename
        variant_text = build_variant_text(canonical_text, engine_version)
        if check_only:
            print(
                f"[plan] {label}: {_display_path(out_path)} "
                f"-> EngineVersion {engine_version}"
            )
        else:
            out_path.write_text(variant_text, encoding="utf-8")
            print(
                f"[write] {label}: {_display_path(out_path)} "
                f"-> EngineVersion {engine_version}"
            )
        written.append(out_path)

    # Hard invariant: the generator must NEVER mutate the shipped file.
    after = CANONICAL_UPLUGIN.read_text(encoding="utf-8")
    if after != canonical_text:
        raise RuntimeError(
            "Canonical .uplugin changed during generation -- this is a bug; "
            "the generator must treat the shipped file as read-only."
        )
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="dry-run: print the plan, write nothing",
    )
    args = parser.parse_args(argv)
    generate(check_only=args.check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
