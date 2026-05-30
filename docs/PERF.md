# Performance & token economy

The plugin is built to be **fast per call** and **light on tokens by default**. This
doc records the two design choices that deliver that and the measured numbers behind
them.

All numbers below were captured against a **live UE 5.7 editor** on the host machine
(`HDMediaVirtualStudio`), driving the plugin directly over `127.0.0.1:18888` (the same
wire the MCP bridge uses), best-of-3 per call. Measured 2026-05-30 on a 143-tool build.

> Re-measure anytime: launch the host project, then run the latency/size probes in
> `examples/` against `127.0.0.1:18888`. Per-call latency is dispatch overhead (one TCP
> round-trip + one editor-thread hop), so it does **not** grow with the tool count.

---

## Faster — per-call latency (~49 ms)

After [#252](https://github.com/NAJEMWEHBE/unreal-ai-connection/pull/252) disabled the
editor's background-CPU throttle (`bThrottleCPUWhenNotForeground`), commands no longer
wait on the throttled game thread when the editor window is unfocused — the usual state
while an MCP client drives it.

| Tool                     | Latency (best of 3) |
|--------------------------|---------------------|
| `get_engine_version`     | 49.1 ms             |
| `get_actors_in_level`    | 49.7 ms             |
| `list_levels`            | 48.5 ms             |
| `get_project_summary`    | 49.8 ms             |

**≈49 ms/call.** This is per-call dispatch overhead (length-framed TCP request →
game-thread execution → framed response); it is independent of how many tools the
catalog exposes, since each call is a single round-trip.

---

## Lighter — opt-in fat results (`verbose`)

[#264](https://github.com/NAJEMWEHBE/unreal-ai-connection/pull/264) made the heavy
responses **summary-by-default**: the common path returns a compact summary, and the
full blob is available on request via `verbose=true`. This keeps an LLM client's context
small unless it explicitly asks for detail.

| Tool                         | Default        | `verbose=true` | Cut  |
|------------------------------|----------------|----------------|------|
| `get_project_summary`        | **273 B**      | 25,248 B       | ~99% |
| `inspect_dependency_graph`   | 73 B (`max_nodes=100`, bounded) | — (capped) | — |

Also trimmed under the same opt-in pattern:
- **`bulk_inspect_assets`** — each result is a summary (`path`, `class`,
  `dependency_count`, `referencer_count`); `verbose=true` restores the full per-asset
  `inspect_asset` blob.
- **`inspect_data_table`** — the `rows[]` name array (tables hold thousands of rows) is
  omitted; `row_count` is always present; `verbose=true` materializes `rows[]`.

A full editor exposes 200+ plugins and assets number in the thousands, so the default
summaries are the difference between a ~273-byte reply and a ~25 KB one for a single
`get_project_summary` call — a meaningful saving across an agent session.

---

## Why it stays fast as the catalog grows

The catalog is now 143 tools (106 native C++ handlers + 37 bridge-side synthetic
tools). Adding tools does **not** add per-call latency: dispatch is an O(1) hash lookup
to the registered handler, and each MCP `tools/call` is one independent round-trip.
Token weight is governed per-tool by the `verbose` default above, not by catalog size.
