# Plan — On-pause Blueprint suggestions (Cursor-style)

**Status:** Future idea, parked. Not currently scheduled. Captured here so the next agent (and human) can pick it up if/when the priorities call for it.

**Date filed:** 2026-05-10
**Origin:** User asked whether UnrealClaudeMCP could do Copilot-style Blueprint autocomplete. Real-time keystroke-fast inline suggestions don't fit the project's architecture (request/response MCP, Claude-class inference latency, no Slate Blueprint-editor hooks). A *Cursor-style* "on-pause" variant — inference triggered when the user stops moving for ~600ms, suggestion presented in a floating panel — sits in a much more realistic latency budget. User chose to keep this parked rather than build now.

## What it is

When the user is editing a Blueprint graph and stops touching the mouse / keyboard for ~600ms (the "pause"), the editor:

1. Snapshots the current graph context (topology, focused node, recent edits).
2. Sends it to Claude in the background via a new MCP path.
3. Renders Claude's "next node + connections" suggestion as a floating panel anchored near the cursor.
4. Hotkey to accept; any input (mouse-move, keystroke) dismisses.

Distinct from **chat-driven Blueprint generation** (works today, no code changes — *"make me a dash ability with X behavior"* → Claude calls `execute_unreal_python` to construct the whole graph).

Distinct from **full real-time ghost nodes** (Copilot-style, sub-200ms, would need a small Blueprint-tuned model and deep Slate work — not this plan).

## Why parked, not built now

1. **Chat-driven flow is already strong.** ~60 tools today handle the *whole-graph* AI use case well. On-pause inference is an ergonomic refinement, not a capability gap.
2. **Significant scope.** Roughly one month of focused work (vs. ~1 day for a typical new handler). New surface areas the project has never touched: Slate UI, `FBlueprintEditor` hooks, async cancellation logic.
3. **No concrete user demand yet.** The primary use case (rapid prototyping) is served. On-pause helps when *polishing* an existing graph, which is a different workflow phase that hasn't been the bottleneck.

## Rough scope (when revisited)

### New code surfaces

| Component | Where | Roughly |
|---|---|---|
| Slate UI for the floating suggestion panel | New `Source/UnrealClaudeMCP/Private/UI/` directory; `SCompoundWidget` subclass anchored to graph viewport | ~300 LoC |
| `FBlueprintEditor` event observation | New module-init code hooking into `IBlueprintEditor` graph-change events | ~150 LoC |
| Debounce + idle-detection logic | C++ timer firing 600ms after the last graph mutation; cancels on any new input | ~50 LoC |
| New MCP tool `add_blueprint_node` | Mirror of existing `Handler_*.cpp` pattern; takes `{path, graph_name, class_path, location, connections[]}` | ~200 LoC handler + manifest entry + bridge `TOOLS` entry + `TOOLS.md` section |
| Prompt template for "next-node suggestion" | New file in a `bridge/prompts/` directory (doesn't exist yet) | Iterative |
| Bridge-side helper (optional) | If we want suggest-and-apply to be one MCP call rather than two | ~100 LoC in `bridge.py` |

### Architectural notes

- **Direction-of-control problem persists.** Even on-pause, the editor has to push the trigger to Claude. The current MCP architecture is request/response with the *client* initiating. Two paths to resolve this:
  - **Sidecar process**: a new lightweight process holds the Claude API connection and listens for events from the plugin. The existing bridge stays a passive translator. Cleanest separation; most new code.
  - **Reuse `EventBus` + chat-driven flow**: the plugin emits a `blueprint_pause` event via the existing Tier 2 `EventBus` (see `Source/UnrealClaudeMCP/Private/MCP/EventBus.h`). The user has Claude Code open in chat; Claude polls events and reacts. Reuses everything we already have, but only works if the chat session is alive. Probably the right starting point.
- **Latency assumption**: 1–3s round-trip for Claude. UI must show "thinking…" during inference and not block any user input.
- **Cancellation must be aggressive**: if the user starts moving the mouse while Claude is thinking, abort the in-flight call (or at minimum suppress the result); otherwise the suggestion pops up after the user has already moved on, which is worse than no suggestion.

### Prompt design (the actually-hard part)

This is harder than the C++. Inputs to the prompt:

- Current graph topology — nodes + connections, serialized via `inspect_blueprint` shape
- Cursor position relative to graph (for placement hints)
- Focused/selected node, if any
- Recent edits (last N graph mutations as a small change-log)
- Asset context — parent class, related blueprints in same folder
- Project context — possibly a one-paragraph summary the user maintains in `docs/`

Output the model should produce:

- Node class to place (e.g. `K2Node_CallFunction` with target `MyClass::MyFunction`, or a comment node, or a getter)
- Suggested location (relative offsets from cursor)
- Pin connections to existing nodes (source/target node IDs + pin names)
- One-sentence rationale shown in the suggestion panel ("you just spawned an actor; this gets a reference to it")

## Open questions to resolve before building

1. **Does Claude's current Blueprint reasoning quality justify this?** Chat-driven generation works because the user reads the result and confirms. On-pause inserts (or proposes inserting) directly into the graph — the quality bar is higher. May need a Blueprint-aware grounding step or a small specialized model.
2. **Single node, or multi-node clusters?** Copilot suggests up to a few lines. The Blueprint analogue ("here's a 3-node cluster that does X") is harder to evaluate but probably more useful.
3. **What happens with rejection?** Hotkey-to-dismiss is obvious, but does the model see "the user rejected this shape"? Local feedback only, or stateless one-shots? Stateless is much easier to ship.
4. **Sharing context with chat-driven?** If the user is also asking Claude things in chat, does on-pause share the same conversation, or is it a separate stateless thread? Almost certainly the latter — but worth deciding explicitly.
5. **Model choice.** Claude Sonnet 4.6 / 4.7 will be slow but high-quality. A small open-weights Blueprint-tuned model would be faster but needs training data we don't have. Realistic: ship with Claude, accept 1–3s latency, revisit if needed.

## Path to revisiting

Pull this off the shelf if any of these become true:

- Multiple users explicitly ask for in-graph suggestions (vs. chat-driven whole-graph generation)
- A small Blueprint-tuned model becomes available that can run sub-500ms locally
- The chat-driven flow turns out to have a specific failure mode that on-pause would address better (e.g. the user wants help in the *middle* of a complex graph rather than starting fresh)

Otherwise, the chat-driven path is good enough.

## Related

- `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md` — framework for deciding between C++ and Python implementations of new functionality. Relevant to the "where does the orchestration live — sidecar, bridge, or plugin?" question.
- `Source/UnrealClaudeMCP/Private/MCP/EventBus.h` (Tier 2) — the natural place to surface `blueprint_pause` events if we go the EventBus-based path instead of a sidecar.
- `Handler_AddComponent.cpp` — closest existing template for what `Handler_AddBlueprintNode.cpp` would look like (mutation handler taking a class path + structural args).
- `Handler_EditWidgetTree.cpp` — the precedent for *graph-shaped* mutations from the MCP layer (different graph type, but the schema-shape concerns translate).
