"""
Schema-discipline tests for the bridge tool catalog (`bridge.TOOLS`).

Two layers:

1. **Catalog-wide invariants** that every advertised inputSchema must hold
   (well-formed types/descriptions, internally-consistent numeric bounds,
   non-empty enums whose members match the declared type, and `default`
   values that satisfy their own declared `enum` / `minimum` / `maximum`).
   These guard the whole 147-tool surface against future drift, not just
   the params touched in the tool-schema-discipline pass.

2. **Locked constraints** -- the specific min/max/enum/default keywords
   added in the tool-schema-discipline change, asserted explicitly so a
   later edit can't silently loosen them. Each value mirrors the
   client-visible contract the live handler already enforces (REJECT
   semantics) or documents (safe defaults); see the PR body's
   verification table. CLAMP-only upper bounds are intentionally NOT
   asserted as `maximum` here -- adding one would turn a silent clamp
   into a client-visible rejection.

Runs without UE, like the rest of `tests/`.
"""

import numbers

import pytest

import unreal_ai_connection_bridge as bridge


def _iter_props():
    """Yield (tool_name, param_name, prop_schema) for every property."""
    for tool in bridge.TOOLS:
        schema = tool.get("inputSchema") or {}
        for pname, pschema in (schema.get("properties") or {}).items():
            yield tool["name"], pname, pschema


# JSON Schema type name -> python type(s) used for default/enum checks.
_TYPE_PY = {
    "integer": numbers.Integral,
    "number": numbers.Real,
    "string": str,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _declared_types(pschema):
    t = pschema.get("type")
    if isinstance(t, list):
        return t
    if isinstance(t, str):
        return [t]
    return []


# ---------------------------------------------------------------------------
# Layer 1: catalog-wide invariants
# ---------------------------------------------------------------------------

def test_every_property_declares_a_type():
    missing = [
        f"{tool}.{p}" for tool, p, s in _iter_props() if not s.get("type")
    ]
    assert not missing, f"properties missing a 'type': {missing}"


def test_every_property_has_a_description():
    missing = [
        f"{tool}.{p}" for tool, p, s in _iter_props()
        if not (s.get("description") or "").strip()
    ]
    assert not missing, f"properties missing a description: {missing}"


def test_numeric_bounds_are_internally_consistent():
    """minimum <= maximum and exclusiveMinimum < maximum where both present."""
    bad = []
    for tool, p, s in _iter_props():
        lo = s.get("minimum", s.get("exclusiveMinimum"))
        hi = s.get("maximum", s.get("exclusiveMaximum"))
        if lo is not None and hi is not None and lo > hi:
            bad.append(f"{tool}.{p}: lo={lo} > hi={hi}")
    assert not bad, f"inconsistent numeric bounds: {bad}"


def test_bounds_only_on_numeric_params():
    bad = []
    for tool, p, s in _iter_props():
        has_bound = any(
            k in s for k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")
        )
        if not has_bound:
            continue
        types = _declared_types(s)
        if not ({"integer", "number"} & set(types)):
            bad.append(f"{tool}.{p}: numeric bound on non-numeric type {types}")
    assert not bad, f"numeric bounds on non-numeric params: {bad}"


def test_enums_are_nonempty_and_match_declared_type():
    bad = []
    for tool, p, s in _iter_props():
        if "enum" not in s:
            continue
        members = s["enum"]
        if not isinstance(members, list) or not members:
            bad.append(f"{tool}.{p}: enum must be a non-empty list")
            continue
        types = _declared_types(s)
        py_types = tuple(_TYPE_PY[t] for t in types if t in _TYPE_PY)
        if py_types:
            for m in members:
                # bool is a subclass of int; exclude it from integer/number checks
                if isinstance(m, bool) and not (str in py_types):
                    bad.append(f"{tool}.{p}: enum member {m!r} is bool, type={types}")
                elif not isinstance(m, py_types):
                    bad.append(f"{tool}.{p}: enum member {m!r} not of type {types}")
    assert not bad, f"malformed enums: {bad}"


def test_defaults_satisfy_their_own_constraints():
    bad = []
    for tool, p, s in _iter_props():
        if "default" not in s:
            continue
        d = s["default"]
        if "enum" in s and d not in s["enum"]:
            bad.append(f"{tool}.{p}: default {d!r} not in enum {s['enum']}")
        if isinstance(d, numbers.Real) and not isinstance(d, bool):
            if "minimum" in s and d < s["minimum"]:
                bad.append(f"{tool}.{p}: default {d} < minimum {s['minimum']}")
            if "maximum" in s and d > s["maximum"]:
                bad.append(f"{tool}.{p}: default {d} > maximum {s['maximum']}")
            if "exclusiveMinimum" in s and d <= s["exclusiveMinimum"]:
                bad.append(f"{tool}.{p}: default {d} <= exclusiveMinimum {s['exclusiveMinimum']}")
    assert not bad, f"defaults violating own constraints: {bad}"


# ---------------------------------------------------------------------------
# Layer 2: locked constraints from the tool-schema-discipline pass
# ---------------------------------------------------------------------------

# (tool, param) -> exact keyword subset that MUST be present (and equal).
# Mirrors the verified REJECT contract / safe defaults. CLAMP upper bounds
# are deliberately absent (would be a behavior change to assert as maximum).
LOCKED = {
    ("find_unused_assets", "limit"): {"minimum": 1, "maximum": 10000, "default": 100},
    ("get_reference_chain", "depth"): {"minimum": 1, "maximum": 8, "default": 3},
    ("bulk_focus_actors", "delay_ms"): {"minimum": 0, "maximum": 10000, "default": 500},
    ("bulk_screenshot_actors", "delay_ms"): {"minimum": 0, "maximum": 10000, "default": 500},
    ("inspect_dependency_graph", "depth"): {"minimum": 1, "maximum": 8, "default": 2},
    ("inspect_dependency_graph", "max_nodes"): {"minimum": 1, "maximum": 100000, "default": 100},
    ("marketplace_search", "limit"): {"minimum": 1, "maximum": 50, "default": 10},
    ("convert_hdri_to_cubemap", "cube_size"): {"minimum": 16, "maximum": 8192, "default": 1024},
    ("convert_hdri_to_cubemap", "compression"): {
        "enum": ["TC_HDR", "TC_HDR_COMPRESSED", "TC_HDR_F32", "TC_DEFAULT"], "default": "TC_HDR"},
    ("sequencer_add_transform_keyframe", "time_seconds"): {"minimum": 0},
    ("sequencer_add_transform_keyframe", "interpolation"): {
        "enum": ["linear", "constant", "auto", "smart_auto", "cubic"], "default": "linear"},
    ("material_auto_remap", "tiling"): {"exclusiveMinimum": 0, "default": 1.0},
    ("pie_control", "action"): {"enum": ["start", "stop", "query"]},
    ("pie_control", "mode"): {"enum": ["play", "simulate"], "default": "play"},
    ("get_log_lines", "count"): {"default": 100},
    ("find_assets", "limit"): {"default": 100},
    ("list_tasks", "limit"): {"default": 100},
    ("start_sleep_task", "duration_ms"): {"minimum": 1},
}


_BY_NAME = {t["name"]: t for t in bridge.TOOLS}


@pytest.mark.parametrize("key", list(LOCKED), ids=lambda k: f"{k[0]}.{k[1]}")
def test_locked_constraints_present(key):
    tool, param = key
    expected = LOCKED[key]
    props = _BY_NAME[tool]["inputSchema"]["properties"]
    assert param in props, f"{tool}.{param} missing from schema"
    pschema = props[param]
    for kw, val in expected.items():
        assert pschema.get(kw) == val, (
            f"{tool}.{param}: expected {kw}={val!r}, got {pschema.get(kw)!r}"
        )


def test_clamped_upper_bounds_not_asserted_as_maximum():
    """Guardrail: params whose UE handler CLAMPS the upper bound must NOT
    advertise a `maximum` (that would convert a silent clamp into a
    client-visible rejection -- a behavior change). Locks the design
    decision from the tool-schema-discipline pass."""
    clamp_no_max = [
        ("get_log_lines", "count"),
        ("find_assets", "limit"),
        ("list_tasks", "limit"),
        ("start_sleep_task", "duration_ms"),
    ]
    offenders = []
    for tool, param in clamp_no_max:
        s = _BY_NAME[tool]["inputSchema"]["properties"][param]
        if "maximum" in s or "exclusiveMaximum" in s:
            offenders.append(f"{tool}.{param}")
    assert not offenders, (
        f"these clamp-upper-bound params must not declare a maximum: {offenders}"
    )
