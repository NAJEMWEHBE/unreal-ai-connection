<div align="center">

# Unreal AI Connection

**Drive Unreal Engine 5.7 from any MCP-compliant client over a local TCP socket.**

130 tools total. Zero pixel-clicking. ~50ms round-trip.

[![CI](https://github.com/NAJEMWEHBE/unreal-ai-connection/actions/workflows/tests.yml/badge.svg)](https://github.com/NAJEMWEHBE/unreal-ai-connection/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Unreal Engine](https://img.shields.io/badge/Unreal_Engine-5.7_official_(5.4--5.8_best--effort)-313131?logo=unrealengine)](docs/PHASE-H-COMPAT.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-7c3aed)](https://modelcontextprotocol.io/)
[![Tests](https://img.shields.io/badge/pytest-560_passing-success?logo=pytest&logoColor=white)](tests/)
[![Tools](https://img.shields.io/badge/tools-130-blue)](docs/TOOLS.md)
[![Changelog](https://img.shields.io/badge/changelog-keep_a_changelog-orange)](CHANGELOG.md)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

![Unreal AI Connection — abstract geometric AI core on the left, orange data streams flowing right into a wireframe low-poly 3D landscape with mountains and a glowing river](docs/images/hero-banner.png)

</div>

<div align="center">

![Live demo: an MCP client procedurally builds an elven city in Unreal Engine 5.7 and orbits a camera](docs/images/demo.gif)

*Live capture — an MCP client builds the scene and orbits the camera entirely over the local TCP socket. Reproduce with [`scripts/capture_demo_gif.py`](scripts/capture_demo_gif.py).*

</div>

<div align="center">

**Native C++ handlers — not Python Remote Execution.** ~50 ms round-trips across 129 tools · 559 tests · MIT · works with any MCP-compliant client (Claude Code, Cursor, Cline, Codex, Gemini, Continue, Windsurf, Zed, …).

The suite now spans **inspection and authoring** — read existing assets and *create* them: actors, levels, data tables/assets, Blueprints, and material graphs, all over the same socket.

⭐ **If this saves you time, a star helps other devs find it.**

</div>

Authoring high-quality assets: see [docs/ASSET-PIPELINE-BLENDER.md](docs/ASSET-PIPELINE-BLENDER.md).

---

## Install (one paste, any client)

> **Every route below only wires the stdio bridge.** You must separately install the UE 5.7 plugin into your project's `Plugins/` folder and launch the editor (it binds `127.0.0.1:18888`). See [`docs/setup/README.md`](docs/setup/README.md) for the prerequisite, and [`docs/DISTRIBUTION.md`](docs/DISTRIBUTION.md) for how this is published.

**Claude Code** — paste the owner/repo, no clone needed:

```text
/plugin marketplace add NAJEMWEHBE/unreal-ai-connection
/plugin install unreal-ai-connection@unreal-ai-connection
```

**Cursor** — one-click deeplink. Base64-encode your machine's bridge path into this template:

```text
cursor://anysphere.cursor-deeplink/mcp/install?name=unreal-ai-connection&config=<BASE64>
```

where `<BASE64>` is base64 of `{"command":"python3","args":["/ABSOLUTE/PATH/TO/bridge/unreal_ai_connection_bridge.py"]}` (on Windows use `"command":"py"` if `python3` is not on PATH). Manual fallback — `.cursor/mcp.json`:

```json
{ "mcpServers": { "unreal-ai-connection": { "command": "python3", "args": ["/ABSOLUTE/PATH/TO/bridge/unreal_ai_connection_bridge.py"] } } }
```

**VS Code** — install deeplink (URL-encode the JSON for your path):

```text
vscode:mcp/install?<URL-ENCODED {"name":"unreal-ai-connection","command":"python3","args":["/ABSOLUTE/PATH/.../bridge/unreal_ai_connection_bridge.py"]}>
```

> **Windows:** all snippets use `python3`; if that's not on PATH use the `py` launcher instead (`"command": "py"`). The `.claude-plugin/mcp-config.json` marketplace path uses `python3` for cross-platform consistency — Windows users without `python3` should use the manual per-client recipe in `docs/setup/` with `py`.

**Every other client** — copy-paste recipe per client:

| Client | Route | Recipe |
|---|---|---|
| Claude Code | `/plugin marketplace add NAJEMWEHBE/unreal-ai-connection` | [docs/setup/claude-code.md](docs/setup/claude-code.md) |
| Claude Desktop | edit `claude_desktop_config.json` | [docs/setup/claude-desktop.md](docs/setup/claude-desktop.md) |
| Cursor | deeplink / `.cursor/mcp.json` | [docs/setup/cursor.md](docs/setup/cursor.md) |
| Codex CLI | `codex mcp add unreal-ai-connection -- …` | [docs/setup/codex-cli.md](docs/setup/codex-cli.md) |
| Windsurf | `mcp_config.json` | [docs/setup/windsurf.md](docs/setup/windsurf.md) |
| Continue | `~/.continue/config.yaml` | [docs/setup/continue.md](docs/setup/continue.md) |
| Cline | MCP Marketplace tab / settings | [docs/setup/cline.md](docs/setup/cline.md) |
| Zed | `~/.config/zed/settings.json` | [docs/setup/zed.md](docs/setup/zed.md) |
| Gemini CLI | `~/.gemini/settings.json` | [docs/setup/gemini-cli.md](docs/setup/gemini-cli.md) |
| VS Code Copilot | `.vscode/mcp.json` | [docs/setup/vscode-copilot.md](docs/setup/vscode-copilot.md) |

Also discoverable in the [official MCP Registry](https://github.com/modelcontextprotocol/registry) as `io.github.najemwehbe/unreal-ai-connection` (feeds the VS Code MCP gallery, mcp.so, PulseMCP) and submittable to the Cline marketplace via [`llms-install.md`](llms-install.md).

---

## Jump to

- [How it fits together](#how-it-fits-together) — architecture diagram + per-call sequence
- [Why it exists](#why-it-exists) — the UE 5.7 Python dead-ends this plugin sidesteps
- [Why MCP specifically](#why-mcp-specifically) — one protocol, every conforming client
- [Tools](#tools) — 129 tools grouped into 15 expandable categories
- [Quick start](#quick-start) — copy-paste path to a running editor with the plugin live
- [What's in the box](#whats-in-the-box) — directory tree
- [Status](#status) — release / test / build state
- [Contributing](#contributing) — house rules + how to add a tool
- [License](#license)

---

## How it fits together

```mermaid
graph LR
    A[Any MCP client] -->|stdio MCP| B[Python Bridge]
    B -->|TCP 127.0.0.1:18888| C[UnrealAIConnection plugin<br/>UE editor module]
    C -->|native C++ API| D[Unreal Editor 5.7]
```

<details>
<summary><b>Per-call sequence</b> — click to see exactly what fires on a single tool call</summary>

```mermaid
sequenceDiagram
    participant User
    participant Client as MCP client<br/>(e.g. Claude Code)
    participant Bridge as Python bridge
    participant Plugin as UE plugin module
    participant Editor as Unreal Editor 5.7

    User->>Client: "Spawn a Cube at origin"
    Client->>Bridge: stdio MCP — tools/call spawn_actor
    Bridge->>Plugin: TCP 127.0.0.1:18888<br/>JSON-RPC framed
    Plugin->>Editor: GEditor->SpawnActor()
    Editor-->>Plugin: success + actor ref
    Plugin-->>Bridge: JSON-RPC result
    Bridge-->>Client: MCP envelope
    Client-->>User: rendered confirmation<br/>(~50ms total)
```

</details>

You ask Claude Code: *"Take a screenshot of my level and tell me what's there."* — Claude resolves the request to a tool call, the bridge forwards it as JSON-RPC to the running editor, the plugin captures the viewport, and Claude renders the image inline. Same flow works for spawning actors, inspecting Blueprints, mutating Widget Trees, executing arbitrary `unreal.*` Python, listing actors, focusing the viewport, loading levels, taking high-res screenshots.

The plugin binds to **`127.0.0.1` only** — your running editor is never reachable across the network.

---

## Why it exists

UE 5.7's Python reflection has known dead-ends. Most painfully: `EditorUtilityWidgetBlueprint.WidgetTree` is a `UPROPERTY()` without `EditAnywhere`, so neither `get_editor_property` nor direct attribute access can reach it. This blocks "let an LLM build me an editor utility panel" workflows entirely.

The plugin sidesteps these limits by calling UE's native C++ APIs directly inside the editor process. It's also dramatically faster than driving UE's GUI with screenshot pixel-clicks — **~50ms round-trip vs. minutes of GUI fiddling**.

---

## Why MCP specifically

MCP (Model Context Protocol) is a vendor-neutral I/O protocol designed for LLM tool-use. Because this plugin speaks MCP rather than baking in any one client, **every conforming client gets all 130 tools for free**: Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, Zed, Cline, and any future entrant. Switch clients without changing the plugin or the bridge.

The wire format is `stdio MCP` between client and bridge, then a tight `length-prefixed JSON-RPC over TCP 127.0.0.1:18888` between bridge and the running UE editor. Either side can be reimplemented in another language; the contract is the JSON.

---

## Tools

**130 tools total.** 95 are native C++ handlers registered by the plugin at editor startup; 35 are bridge-side synthetic tools (`wait_for_events`, `get_camera_transform`, `set_camera_transform`, `screenshot_actor`, `compile_mod_pak`, `compile_mod_pak_direct`, `bulk_delete_assets`, `bulk_move_assets`, `bulk_rename_assets`, `bulk_duplicate_assets`, `bulk_inspect_assets`, `inspect_data_asset`, `inspect_sound_class`, `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`, `inspect_metasound`, `find_unused_assets`, `get_reference_chain`, `bulk_compile_blueprints`, `audit_blueprint_compile_status`, `find_actors_by_class`, `bulk_focus_actors`, `bulk_screenshot_actors`, `bulk_set_actor_property`, `compare_assets`, `bulk_set_console_variables`, `inspect_dependency_graph`, `bulk_fix_redirectors`, `marketplace_search`, `marketplace_import`, `convert_hdri_to_cubemap`, `sequencer_add_transform_keyframe`, `import_mesh`, `material_auto_remap`) that compose existing handlers without a dedicated UE round-trip (or, for `compile_mod_pak` and `compile_mod_pak_direct`, shell out to RunUAT or UnrealPak entirely outside the UE process) — see `bridge/unreal_ai_connection_bridge.py`'s `SYNTHETIC_TOOLS`. Per-tool JSON schemas and examples live in [`docs/TOOLS.md`](docs/TOOLS.md). Grouped overview:

### Python execution (5 tools)

<details>
<summary><b>Python execution</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `execute_unreal_python` | Universal escape hatch — run arbitrary `unreal.*` Python in the editor's interpreter. Multi-line scripts work. |
| `run_python_file` | Execute a `.py` file from disk in the editor's Python interpreter. |
| `apply_python_to_selection` | Run a Python snippet with the editor's current selection bound as `actors` / `assets`. |
| `exec_python_persistent` | Persistent Python session — variables defined in one call survive into the next. |
| `reset_python_state` | Wipe the persistent session's globals. |

</details>

### Project / asset registry (10 tools)

<details>
<summary><b>Project / asset registry</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `get_project_summary` | Project name, engine version, enabled plugins, asset count. |
| `find_assets` | Query the asset registry by class + path + name. |
| `inspect_asset` | Class, tags, dependencies, referencers, on-disk size. |
| `move_asset` | Move an asset to a different folder; UE creates a redirector at the source path. |
| `rename_asset` | Change an asset's leaf name in place; UE creates a redirector at the old name. |
| `duplicate_asset` | Copy an asset to a new path. |
| `delete_asset` | Delete an asset; refuses if referenced by other packages unless `force=true`. |
| `fix_up_redirectors` | Resolve all object redirectors under a folder. |
| `create_data_table` | Create a new `UDataTable` asset whose rows conform to a given row `UScriptStruct`. |
| `create_data_asset` | Create a new `UDataAsset` (or subclass) asset from a `UDataAsset` subclass path. |

</details>

### Blueprint / widget / animation — introspection + authoring (17 tools)

<details>
<summary><b>Blueprint / widget / animation — introspection + authoring</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `inspect_blueprint` | Variables, function/event graphs, parent class of any Blueprint asset. |
| `compile_blueprint` | Recompile a Blueprint asset and report errors. |
| `inspect_widget_tree` | Read the widget hierarchy of a `UWidgetBlueprint` or EUW (the thing UE Python can't do). |
| `inspect_widget_blueprint` | Widget-BP-specific surface: animations, delegate bindings, palette category, inherited named slots, property-binding count, blueprint compile status. Pairs with `inspect_blueprint` + `inspect_widget_tree`. |
| `edit_widget_tree` | Mutate the tree: `set_root` / `add_child` / `set_property`. Solves the EUW WidgetTree blocker. |
| `inspect_anim_blueprint` | Read variables and state machines of an Animation Blueprint. |
| `inspect_anim_montage` | Read sections, slots, and notify tracks of an `UAnimMontage`. |
| `inspect_static_mesh` | LODs, materials, collision, bounds for a `UStaticMesh`. |
| `inspect_skeletal_mesh` | LODs, materials, sockets, skeleton info for a `USkeletalMesh`. |
| `inspect_physics_asset` | Body setups (one per simulated bone), constraint setups (joints between bodies), bounds-bodies subset, named physical-animation + constraint profiles. Cross-links to `inspect_skeletal_mesh` via `preview_skeletal_mesh`. |
| `inspect_niagara_system` | Emitters and exposed user parameters of a Niagara system. |
| `inspect_landscape` | Components, layers, and material info for a landscape actor. |
| `inspect_data_table` | RowStruct identity, sorted row names, per-property name+type for every `FProperty` on the row struct, plus client-strip / ignore-extra/missing-fields flags. |
| `inspect_curve` | UCurveBase channel layout (1ch UCurveFloat / 4ch UCurveLinearColor / 3ch UCurveVector), per-channel name + key count + per-channel + global time/value range. |
| `create_blueprint` | Create a new `UBlueprint` asset under `/Game/` from a parent class (default `/Script/Engine.Actor`). |
| `add_blueprint_variable` | Add a typed member variable (bool/int/float/string/name/vector/rotator/transform/object) to an existing `UBlueprint`. |
| `add_blueprint_function` | Add a new empty function graph to an existing `UBlueprint`. |

</details>

### Materials (6 tools)

<details>
<summary><b>Materials</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `create_material_instance` | Create a `UMaterialInstanceConstant` asset with a parent material set. |
| `set_mi_parameter` | Override a scalar/vector/texture parameter on a material instance. Type discriminator picks value shape. |
| `inspect_material` | List parameter names declared by a `UMaterial` or `UMaterialInstance` (scalar/vector/texture/static-switch). |
| `inspect_material_instance` | Read a material instance's parent + currently-overridden parameter values. |
| `add_material_expression` | Create a `UMaterialExpression` node inside an existing `UMaterial`'s graph, then recompile the material. |
| `connect_material_expression` | Wire an expression's output to a material property input (`property:BaseColor`) or another expression's input (`node:<ExprName>:<InputName>`), then recompile. |

</details>

### Textures (3 tools)

<details>
<summary><b>Textures</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `import_texture` | Bring an image file (PNG / JPG / EXR / TGA / BMP / HDR) from disk into the project as a `UTexture2D` asset via UE's canonical import path. |
| `configure_texture` | Adjust SRGB / compression / LOD group / filter on an existing texture asset. |
| `inspect_texture` | Texture class, surface dimensions, sRGB, compression, filter, LOD group, mip-gen, virtual-texture / never-stream flags, composite-texture cross-link. UTexture2D-specific size / mips / pixel format / imported source dimensions emitted conditionally. |

</details>

### Level Sequences (3 tools)

<details>
<summary><b>Level Sequences</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `inspect_sequence` | Read structure of a Level Sequence: tracks, sections, bindings, frame rate, playback range. |
| `create_sequence` | Create a new empty Level Sequence asset with a configured display rate and playback range. |
| `bind_actor_to_sequence` | Add a level actor as a possessable binding to a Level Sequence. |

</details>

### Level / actor authoring (21 tools)

<details>
<summary><b>Level / actor authoring</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `get_actors_in_level` | Name / class / transform of every actor; optional case-insensitive substring filter. |
| `spawn_actor` | Create an actor at a location with optional rotation, label, and initial properties. Class path supports built-ins and Blueprints. |
| `set_actor_transform` | Move / rotate / scale an existing actor by name. Absolute or relative mode. |
| `delete_actor` | Remove an actor by name. Force flag overrides children-attached safety check. |
| `set_actor_property` | Mutate any UPROPERTY on an actor. Supports primitives, FName/FText, vectors, rotators, colors, enums, and TSoftObjectPtr. |
| `add_component` | Attach a component (UActorComponent / USceneComponent subclass) to an existing actor at runtime, optionally socketed. |
| `duplicate_actor` | Clone an existing level actor (label or FName), optionally offset and relabel. Undoable (single Ctrl+Z). |
| `set_actor_folder` | Set an actor's World Outliner folder path (e.g. `Lighting/Key`); empty string moves it to the outliner root. Undoable. |
| `rename_actor` | Change an actor's World Outliner display label (`SetActorLabel`); the stable FName is unchanged. Undoable. |
| `focus_actor` | Select an actor by label and frame the viewport on it. |
| `load_level_by_path` | Open a level by package path. |
| `create_level` | Create a new empty level (`UWorld`) asset under `/Game/` and open it as the active level. |
| `build_lighting` | Invoke a static-lighting build on the active editor world. Non-interactive; may take time on large levels. |
| `find_actors_by_class` | Filter the active level's actors by class. Composes `get_actors_in_level` and matches against the short class name. Bridge-side synthetic. |
| `bulk_focus_actors` | Frame the viewport on each actor in a sequence, optionally screenshotting each one. Composes `focus_actor` (+ `get_viewport_screenshot`) per name. Bridge-side synthetic. |
| `bulk_screenshot_actors` | Focus + screenshot each actor in a sequence. Composes `screenshot_actor` per name. Bridge-side synthetic. |
| `bulk_set_actor_property` | Apply many `{actor, property, value}` mutations in one call. Composes `set_actor_property` per assignment. Bridge-side synthetic. |
| `compare_assets` | Symmetric diff between two assets' `inspect_asset` outputs. Bridge-side synthetic. |
| `bulk_set_console_variables` | Set many CVars in one call with optional atomic rollback. Composes `get_console_variable` + `set_console_variable`. Bridge-side synthetic. |
| `inspect_dependency_graph` | BFS the asset dependency graph (down by default, optional bidirectional sweep). Composes `inspect_asset` recursively. Bridge-side synthetic. |
| `bulk_fix_redirectors` | Resolve redirectors across many content folders in one call. Composes `fix_up_redirectors` per folder. Bridge-side synthetic. |

</details>

### Viewport / screenshots (3 tools)

<details>
<summary><b>Viewport / screenshots</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `get_viewport_screenshot` | Active viewport as a base64 PNG, returned inline. |
| `take_high_res_screenshot` | Trigger UE's `HighResShot` console command. |
| `render_camera_to_png` | Force a synchronous render of the level-editor viewport (or an off-screen SceneCapture2D at arbitrary resolution) and write a PNG — works headless where deferred screenshots fail. |

</details>

### Console / logs (5 tools)

<details>
<summary><b>Console / logs</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `get_log_lines` | Read recent UE Output Log entries from the in-process ring buffer. Filter by category and minimum verbosity. |
| `execute_console_command` | Run a UE console command (e.g. `stat fps`, `r.ScreenPercentage 50`) and capture its output. |
| `get_console_variable` | Read a single console variable's current value. |
| `set_console_variable` | Write a value to a console variable. |
| `find_console_variables` | Enumerate console variables matching a name pattern. |

</details>

### Long-running tasks (4 tools)

<details>
<summary><b>Long-running tasks</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `start_sleep_task` | Reference long-running task — sleeps for N seconds. Used to exercise the task pattern from clients. |
| `poll_task` | Read a task's current state / result. |
| `cancel_task` | Cancel an in-flight task by id. |
| `list_tasks` | Enumerate all tracked tasks and their states. |

</details>

### Event push / subscriptions (5 tools)

<details>
<summary><b>Event push / subscriptions</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `poll_events` | Drain queued editor events (actor spawn/delete, asset add/remove/rename/import, level save, map change) from the in-process EventBus. |
| `wait_for_events` | Bridge-side synthetic tool — block until matching events arrive or `timeout_ms` elapses, by polling `poll_events` at `poll_interval_ms` cadence. |
| `register_subscription` | Open a per-client subscription channel for a filtered event stream. |
| `poll_subscription` | Drain queued events from a specific subscription. |
| `unsubscribe` | Close a subscription. |

</details>

### Audio (3 tools — introspection trio)

<details>
<summary><b>Audio</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `inspect_sound_cue` | USoundCue duration, multipliers, attenuation cross-link, root sound-node class, full graph node list (sorted, with class taxonomy). |
| `inspect_sound_wave` | USoundWave sample rate, channels, frame count, duration, compression type + runtime format + compressed-data size, sound group, looping/streaming flags, loading behavior, subtitle + cue-point + loop-region counts. Editor-only LUFS / sample-peak / comment fields conditional. |
| `inspect_sound_attenuation` | USoundAttenuation 3D-playback rules: distance algorithm + shape, spatialization, air-absorption LPF/HPF, listener focus, occlusion tracing, reverb send, priority attenuation, plus assorted feature flags. Each major feature is gated by its master bitfield; sub-objects collapse to `{enabled: false}` when disabled. |

</details>

### Camera (3 tools — bridge-side synthetic)

<details>
<summary><b>Camera</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `get_camera_transform` | Read the level-editor viewport camera's location + rotation. Composes `execute_unreal_python` + `get_log_lines` via the marker pattern. |
| `set_camera_transform` | Set the level-editor viewport camera's location and/or rotation. Single `execute_unreal_python` round-trip. |
| `screenshot_actor` | Frame the viewport on a specific actor and capture a focused PNG. Composes `focus_actor` + `get_viewport_screenshot`. |

</details>

### Editor state / undo (2 tools)

<details>
<summary><b>Editor state / undo</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `undo_transaction` | Step the editor undo stack backward — the programmatic Ctrl+Z. Each mutating MCP edit (spawn/delete/transform/property/component) is one transaction, so this reverts the last such edit (or the last N via `count`). |
| `redo_transaction` | Step the editor undo stack forward — the programmatic Ctrl+Y. Re-applies transactions previously reverted by `undo_transaction` (or Ctrl+Z), up to `count` steps. |

</details>

### Self-introspection (1 tool)

<details>
<summary><b>Self-introspection</b> — click to expand the tool table</summary>

| Tool | Purpose |
|---|---|
| `list_tools` | Names of every registered method (for autodiscovery). |

</details>

Adding the next C++ handler is one `.cpp` file plus one line of registration — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). New synthetic tools are an entry in `SYNTHETIC_TOOLS` plus a function in [`bridge/unreal_ai_connection_bridge.py`](bridge/unreal_ai_connection_bridge.py).

---

## Quick start

### Engineers (you already build UE projects from source)

1. **Drop the plugin in.** Copy `UnrealAIConnection/` into `<YourProject>/Plugins/`.
2. **Regenerate project files.** Right-click `<YourProject>.uproject` → *Generate Visual Studio project files*.
3. **Build the editor.** Open the .sln, build *Development Editor | Win64*. First build takes ~5–15 min.
4. **Launch.** Open the .uproject. The MCP server auto-starts on `127.0.0.1:18888`. Look for these lines in the Output Log:
   ```
   [LogUnrealAIConnection] Module started
   LogUCMCPHandler: Registered handler 'execute_unreal_python'
     ... (85 more handler lines)
   [LogUCMCP] Listening on 127.0.0.1:18888
   ```
5. **Wire your MCP client.** Copy `examples/.mcp.json.example` to your project root as `.mcp.json`, edit the path to point at `bridge/unreal_ai_connection_bridge.py`, restart your client, and approve the new MCP server. Same bridge works with Claude Code, Claude Desktop, Cursor, Codex CLI, Windsurf, Continue, Cline, Zed, Gemini CLI, and VS Code Copilot — see [`docs/setup/`](docs/setup/) for per-client copy-paste recipes.

### Non-engineers / GUI-only users

See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — step-by-step, screenshot-first.

### Verify it works

The smoke test fires every default-on tool from a plain Python TCP client (not through Claude Code) — a fast way to confirm the plugin loaded and the server is alive:

```bash
python examples/smoke_test.py
```

You'll see structured JSON output for every default-on step (eleven banner-headed sections, plus a few unbannered checks for the asset registry, sequencer and materials handlers — the last two skip with a print if your project has no Level Sequences or Materials in `/Game/`). Last line: *"Smoke test complete."*

---

## What's in the box

```
UnrealAIConnection/                The Unreal Engine plugin (drop into <Project>/Plugins/)
  Source/UnrealAIConnection/         C++ editor module
  Resources/                      MCP manifest JSON
  UnrealAIConnection.uplugin         Plugin manifest

bridge/
  unreal_ai_connection_bridge.py     Python stdio ↔ TCP bridge for any MCP client

examples/
  smoke_test.py                   Connects to the live server, fires the safe tools
  .mcp.json.example               Template Claude Code MCP config

docs/
  INSTALLATION.md                 Step-by-step install for a UE 5.7 project
  TOOLS.md                        What each tool does + JSON examples
  ARCHITECTURE.md                 How the pieces fit + UE 5.7 API gotchas

skills/
  driving-unreal/                 Bundled know-how skill — which tools to chain for common UE workflows (auto-discovered by MCP clients)

tests/                            Pytest suite for the bridge (no UE required)
.github/workflows/                CI runs the bridge tests on every push & PR
```

---

## Status

| | |
|---|---|
| **Latest release** | v0.9.1 — 2026-05-23 (plus [`v0.9.1-ue5.6`](https://github.com/NAJEMWEHBE/unreal-ai-connection/releases/tag/v0.9.1-ue5.6) prebuilt 5.6 binaries — 2026-05-25) |
| **Tools** | **130 live** — 95 native C++ handlers (one MCP method per `Handler_*.cpp`) plus 35 bridge-side synthetic tools (Python-only composition over existing handlers; never crosses the TCP wire as a dedicated round-trip). See [`docs/TOOLS.md`](docs/TOOLS.md) for the per-tool reference. |
| **Tested on** | UE 5.7.4 / Windows 11 / Visual Studio Build Tools 2022 / MSVC 14.44 / NETFXSDK 4.8.1 |
| **Build status** | Plugin compiles + loads against UE 5.7.4 host on Windows 11; 95 handlers register, TCP server binds `127.0.0.1:18888`, bridge round-trip via `tools/call list_tools` returns full registry. |
| **Bridge tests** | 560 pytest cases, ~99% coverage |
| **CI** | GitHub Actions on every push and PR |
| **Development workflow** | Multi-agent ensemble — Opus orchestrates, Codex authors C++, Sonnet handles Python + recon, NVIDIA cloud + local OSS LLMs run pre-PR diff review, Copilot CLI gives a second opinion, Gemini auto-review fires on every PR open. No single model gates a merge. |

### Roadmap / status honesty

One in-flight item is stated plainly here so nothing is oversold:

- **Officially built & tested on UE 5.7.** Other UE versions are community / best-effort: the cross-engine compatibility scaffold lets you build from source for your engine version (uncertified, not actively maintained, contributions welcome). See [ADR-0001](docs/adr/ADR-0001-ue57-only-freeze-cross-engine-compat.md) / [docs/PHASE-H-COMPAT.md](docs/PHASE-H-COMPAT.md).
- **Prebuilt 5.6 binaries available (Win64).** Host-verified build (MSVC 14.38) + live smoke pass (all suites green) on a real 5.6 editor. Skip the source build: download the packaged plugin from the [`v0.9.1-ue5.6`](https://github.com/NAJEMWEHBE/unreal-ai-connection/releases/tag/v0.9.1-ue5.6) release and drop it into `YourProject/Plugins/`. **Load note:** enable the engine's `DMXEngine` + `DMXProtocol` plugins (the DMX handlers link against them) or the module fails to load.

---

## What this is NOT

- A general MCP server framework — this is bonded to UE's editor process.
- A live-broadcast tool — for that, look at vMix, OBS, NDI Studio Monitor.
- An Aximmetry / Pixotope / Disguise replacement — those have multi-engineer multi-year codebases.

---

## Contributing

Issues and PRs welcome. Two house rules:

1. **Verify UE API claims against UE 5.7 source.** Past reviewer subagents have made specific UE API claims that turned out wrong; ground-truth the engine source before committing.
2. **Each new MCP handler is one `Handler_*.cpp` file** in `Source/UnrealAIConnection/Private/MCP/Handlers/`, plus one `extern` declaration and one `Reg.Register(Make_Handler_*())` line in `UnrealAIConnectionModule.cpp`. Don't grow the foundation — add handlers.

### Running tests

Bridge unit tests run without UE in under a second:

```bash
pip install pytest pytest-cov
pytest tests/
```

CI runs the same suite on every push and PR (see `.github/workflows/tests.yml`). The live integration smoke test in `examples/smoke_test.py` requires a running UE editor — see [`tests/README.md`](tests/README.md).

---

## License

MIT — see [`LICENSE`](LICENSE). © 2026 HD Media (Kuwait).
