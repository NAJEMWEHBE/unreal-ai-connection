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
EXPECTED_CPP_HANDLER_COUNT = 110  # Wave 1 native lane (place_actors_raycast, batch_material_assign, light_raycast_placement, nanite_collision_toggle) + Wave 2 (post_process_grade_preset, sequence_snapshot, material_blend_override, export_actor_as_gltf) + Wave 3/4 first-cut (decal_scatter in core; inspect_ocio_config + inspect_ndisplay_config in optional companions) + mesh_bake_ao_to_vertex_color (optional UnrealAIConnectionGeometry companion)
EXPECTED_SYNTHETIC_TOOL_COUNT = 37  # bumped from 35 — added Wave 1 studio-builder synthetics: batch_capture_cameras, batch_spawn_from_csv
EXPECTED_TOOL_COUNT = EXPECTED_CPP_HANDLER_COUNT + EXPECTED_SYNTHETIC_TOOL_COUNT
