---
name: driving-unreal
description: >
  Use when driving an Unreal Engine 5.x editor through the unreal-ai-connection MCP tools — building
  or populating a level, spawning/transforming actors, authoring materials and material instances,
  setting up lighting and atmosphere, reconstructing a scene from a reference photo, setting up a
  sequencer/cinematic shot, taking viewport or off-screen renders, auditing or compiling blueprints,
  or doing asset hygiene (find/move/rename/delete/fix-up redirectors). Teaches which tools to chain,
  in what order, the verification checkpoints, and the UE 5.x traps that silently no-op or corrupt
  state. Routes through the MCP server named "unreal-ai-connection" (tools are exposed as
  mcp__unreal-ai-connection__<tool>; confirm with list_tools).
  Trigger phrases: "Unreal", "Unreal Engine", "UE5", "UE 5.7", "build a level", "populate the level",
  "spawn an actor", "place actors", "apply a material", "material instance", "set up a sequencer
  shot", "cinematic", "screenshot the viewport", "render the scene", "photo to Unreal", "compile the
  blueprints", "audit blueprint status", "clean up assets", "fix up redirectors", "in the editor".
---

# Driving Unreal Engine via the unreal-ai-connection MCP tools

This skill is the know-how layer for the `unreal-ai-connection` plugin. The plugin exposes ~105 UE
editor-automation tools; this file tells you **how to operate them** — the chaining order, the
verification checkpoints, and the failure modes. Deep step-by-step recipes and the full UE-quirk
table live in [`reference.md`](reference.md); read it when executing a specific workflow.

Tools are called as `mcp__unreal-ai-connection__<name>`. Below, names are written bare for brevity.

## Before you touch any tool

1. **Confirm the bridge is live.** The UE plugin binds `127.0.0.1:18888`. A cold editor launch takes
   ~2 minutes to bind; warm is seconds. Call `list_tools` first — if it returns the catalog, you are
   connected. Do not assume a tool exists; the live `list_tools` output is the source of truth.
2. **Establish a clean baseline.** For anything that places actors, plan an idempotent re-run: prefix
   your actor labels (e.g. `Test_*`) and start the workflow by deleting that prefix via
   `find_actors_by_class` + `delete_actor`, so repeated attempts don't pile up duplicates.

## Cross-cutting operating patterns (apply to every workflow)

These rules matter more than any single recipe. Internalize them.

- **Actor targeting is hybrid.** Actor-targeting tools accept either a display label or an FName.
  Label is tried first. If a label is ambiguous you get an `ambiguous_actor` error listing candidate
  FNames — retry with a specific FName. Don't guess; read the candidates back.

- **Verify visually, and pick the right capture tool.** Three ways to capture, with different
  trade-offs:
  - `render_camera_to_png` — off-screen render to a render target. **Deterministic; use this for
    headless/automated verification.** Provide an absolute output path whose parent exists.
  - `get_viewport_screenshot` / `take_high_res_screenshot` — capture the active viewport. Can return
    a **frozen frame if the editor window is backgrounded** (the viewport stops pumping render
    frames). Keep the editor foreground if you rely on these.
  - `screenshot_actor` — convenience wrapper that frames and renders a single actor.
  After any structural step (placement, material apply, lighting), capture and look before adding
  detail. Don't build blind.

- **Async events: never block the game thread.** Use bridge-side waits, not editor sleeps. Pattern:
  `register_subscription` → poll with `poll_events` / `poll_subscription`, or use `wait_for_events`
  to wrap the polling loop. The sequence cursor is **inclusive** (return events with `seq >= since`).
  If a response has `dropped: true`, the buffer overflowed — `unsubscribe` and re-`register`.
  (There is no `start_event_subscription` tool; the real entry point is `register_subscription`.)

- **Bulk ops share one envelope.** `bulk_*` tools take a `paths` list plus `continue_on_error`
  (default true) and return a per-path result `{path, ok, error?}`. Path validation rejects `..`
  segments and NUL bytes. Inspect the per-path `ok` flags — a top-level success does not mean every
  path succeeded.

- **Read back and verify after mutating assets.** After create/duplicate/move/rename, call
  `inspect_asset` / `inspect_material_instance`, and use `compare_assets` after batch renames, to
  catch silent reference loss before continuing.

- **Persist before any restart.** Spawned actors and edits are **unsaved** and revert on editor
  restart. Run `save_dirty_assets` before pausing, restarting, or when verification depends on state
  surviving.

- **`execute_unreal_python` is the escape hatch.** When a typed tool genuinely can't express
  something (e.g. non-transform sequencer channels), drop to `execute_unreal_python`. Note:
  `set_actor_property` already accepts JSON-**array** values for TArray/TSet props (e.g.
  `OverrideMaterials`), so those need no fallback. To get a result back, emit a UUID-tagged marker via
  `unreal.log("__UCMCP__<uuid>__<json>__END__")` and read it with `get_log_lines`. Two struct traps
  when building Python: `unreal.Rotator` positional args are **(roll, pitch, yaw)** and
  `unreal.Color` is **BGRA** — never construct positionally; build an empty struct and assign by
  property name (`r = unreal.Rotator(); r.pitch = ...; r.yaw = ...; r.roll = ...`).

## Workflow index (full recipes in reference.md)

| Workflow | Maturity | Use it to |
| --- | --- | --- |
| Build-a-level | PROVEN | Construct a scene from primitives + materials + lighting + camera |
| Material-instance customization | PROVEN | Create tinted/variant material instances and apply them |
| Asset hygiene | MATURE | Find/move/rename/delete assets safely; fix up redirectors |
| Round-trip authoring | PROVEN | Author → configure → verify → compile → save an asset safely |
| Photo → Unreal scene | FLAGSHIP | Reconstruct a faithful blockout + atmosphere from a reference image |
| Sequencer shot | EXPERIMENTAL | Scaffold a Level Sequence; keyframe via execute_unreal_python |
| Widget / UMG authoring | MATURE | Build/mutate a WidgetBlueprint or EUW tree; set properties; compile once |
| Console variable management | PROVEN | Discover, read, write, and restore CVars; run one-off console commands |

Open [`reference.md`](reference.md) for the ordered tool sequences, verification checkpoints, and the
UE 5.x gotcha table before running any of these.
