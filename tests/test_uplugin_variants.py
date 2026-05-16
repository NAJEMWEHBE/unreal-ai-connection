"""Contract tests for scripts/gen_uplugin_variants.py (Phase H).

The generator emits per-engine-bucket .uplugin variants whose ONLY
difference from the shipped canonical .uplugin is the ``EngineVersion``
load-gate. These tests assert:

  - the generator script exists and imports as a module
  - it produces exactly the three bucket variants
  - each variant carries the correct EngineVersion for its bucket
  - each variant differs from the canonical .uplugin on EXACTLY the
    EngineVersion field (every other field preserved verbatim)
  - the canonical shipped .uplugin is byte-identical before and after a
    generation run (the generator must never mutate the shipped file)
  - --check dry-run writes nothing

The generator is pure stdlib, so these run without UE and in < 1s. They
do NOT certify cross-engine C++ correctness -- that needs a host build.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GEN_SCRIPT = REPO_ROOT / "scripts" / "gen_uplugin_variants.py"
CANONICAL_UPLUGIN = REPO_ROOT / "UnrealClaudeMCP" / "UnrealClaudeMCP.uplugin"

# Mirrors scripts/gen_uplugin_variants.py BUCKETS. Duplicated on purpose:
# the test is the independent spec; if the generator's bucket table drifts
# from this, that is exactly the regression we want to catch.
EXPECTED_BUCKETS = {
    "UnrealClaudeMCP-T1.uplugin": "5.4.0",
    "UnrealClaudeMCP-T2.uplugin": "5.0.0",
    "UnrealClaudeMCP-T3.uplugin": "4.27.0",
}


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_uplugin_variants", GEN_SCRIPT
    )
    assert spec and spec.loader, "gen_uplugin_variants.py must be importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_script_exists_and_nonempty() -> None:
    assert GEN_SCRIPT.is_file(), "scripts/gen_uplugin_variants.py must exist"
    assert GEN_SCRIPT.stat().st_size > 0, "generator must be non-empty"


def test_generator_imports_as_module() -> None:
    gen = _load_generator()
    assert hasattr(gen, "generate"), "generator must expose generate()"
    assert hasattr(gen, "build_variant_text"), "must expose build_variant_text()"
    assert hasattr(gen, "BUCKETS"), "must expose the BUCKETS table"


def test_bucket_table_matches_expected() -> None:
    gen = _load_generator()
    table = {fname: ev for fname, ev, _label in gen.BUCKETS}
    assert table == EXPECTED_BUCKETS, (
        "generator BUCKETS drifted from the test spec; if this is an "
        "intentional release-bucket change, update EXPECTED_BUCKETS too"
    )


def test_generate_writes_three_variants(tmp_path, monkeypatch) -> None:
    gen = _load_generator()
    monkeypatch.setattr(gen, "OUTPUT_DIR", tmp_path / "uplugin-variants")
    written = gen.generate()
    assert len(written) == 3, "exactly three bucket variants expected"
    names = sorted(p.name for p in written)
    assert names == sorted(EXPECTED_BUCKETS), (
        f"variant filenames {names} != {sorted(EXPECTED_BUCKETS)}"
    )
    for path in written:
        assert path.is_file(), f"{path} should have been written"


def test_each_variant_has_correct_engine_version(tmp_path, monkeypatch) -> None:
    gen = _load_generator()
    monkeypatch.setattr(gen, "OUTPUT_DIR", tmp_path / "uplugin-variants")
    written = gen.generate()
    for path in written:
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_ev = EXPECTED_BUCKETS[path.name]
        assert data["EngineVersion"] == expected_ev, (
            f"{path.name} EngineVersion {data['EngineVersion']!r} != "
            f"expected {expected_ev!r}"
        )


def test_variant_differs_only_in_engine_version(tmp_path, monkeypatch) -> None:
    gen = _load_generator()
    monkeypatch.setattr(gen, "OUTPUT_DIR", tmp_path / "uplugin-variants")
    canonical = json.loads(
        CANONICAL_UPLUGIN.read_text(encoding="utf-8")
    )
    written = gen.generate()
    for path in written:
        variant = json.loads(path.read_text(encoding="utf-8"))
        # Same set of keys, in any representation.
        assert variant.keys() == canonical.keys(), (
            f"{path.name} changed the .uplugin key set"
        )
        for key in canonical:
            if key == "EngineVersion":
                continue
            assert variant[key] == canonical[key], (
                f"{path.name} changed field '{key}' -- only EngineVersion "
                f"may differ from the canonical .uplugin"
            )


def test_canonical_uplugin_byte_unchanged(tmp_path, monkeypatch) -> None:
    gen = _load_generator()
    monkeypatch.setattr(gen, "OUTPUT_DIR", tmp_path / "uplugin-variants")
    before = CANONICAL_UPLUGIN.read_bytes()
    gen.generate()
    after = CANONICAL_UPLUGIN.read_bytes()
    assert before == after, (
        "the shipped UnrealClaudeMCP.uplugin must be byte-identical after "
        "a generation run -- the generator must treat it as read-only"
    )


def test_check_mode_writes_nothing(tmp_path, monkeypatch) -> None:
    gen = _load_generator()
    out_dir = tmp_path / "uplugin-variants"
    monkeypatch.setattr(gen, "OUTPUT_DIR", out_dir)
    gen.generate(check_only=True)
    assert not out_dir.exists(), (
        "--check / check_only must not create the output dir or any file"
    )


def test_canonical_uplugin_is_valid_json_with_engine_version() -> None:
    data = json.loads(CANONICAL_UPLUGIN.read_text(encoding="utf-8"))
    assert "EngineVersion" in data, (
        "canonical .uplugin MUST declare EngineVersion -- it is the "
        "release load-gate every bucket variant rewrites"
    )
