# Unreal Claude MCP

**Drive Unreal Engine 5.7 from Claude (or any MCP client) over a local TCP socket.**

A self-contained Unreal Engine 5.7 plugin that runs an MCP (Model Context Protocol) server inside the editor's process. Eleven generic tools exposed today: arbitrary Python execution, project / actor / Blueprint / WidgetTree introspection, widget-tree mutation, viewport screenshots, level loading. A small Python bridge translates between Claude Code's stdio MCP transport and the plugin's raw JSON-RPC over TCP.

Released under MIT.

---

## Why this exists

UE 5.7's Python reflection has known dead-ends — most painfully, `EditorUtilityWidgetBlueprint.WidgetTree` is `UPROPERTY()` without `EditAnywhere`, so neither `get_editor_property` nor direct attribute access reach it. This blocks a lot of "let an LLM build me an editor utility panel" workflows. The plugin's MCP server bypasses these limits by calling UE's native C++ APIs directly inside the editor process.

It's also faster than driving the UE GUI with screenshot pixel-clicks: ~50ms round-trip from Claude's chat to a real UE state change, vs minutes for the GUI route.

## What's in the box

```
UnrealClaudeMCP/                The actual Unreal Engine plugin (drop into <Project>/Plugins/)
  Source/UnrealClaudeMCP/       C++ editor module
  Resources/                    MCP manifest JSON (tool surface description)
  UnrealClaudeMCP.uplugin       Plugin manifest

bridge/
  unreal_claude_mcp_bridge.py   Python stdio <-> TCP bridge for Claude Code MCP

examples/
  smoke_test.py                 Connects to the live server, fires the safe tools, prints results
  .mcp.json.example             Template Claude Code MCP config

docs/
  INSTALLATION.md               Step-by-step install for a UE 5.7 project
  TOOLS.md                      What each of the 11 tools does + JSON examples
  ARCHITECTURE.md               How the pieces fit together + UE 5.7 API gotchas
```

## Tools exposed (11)

| Tool | Purpose |
|---|---|
| `execute_unreal_python` | Run arbitrary `unreal.*` Python in the editor (universal escape hatch). Multi-line scripts work — bypasses the `ExecPythonCommandEx` file-vs-source ambiguity by writing to a temp `.py` file. |
| `get_project_summary` | Project name, engine version, enabled plugins, asset count. |
| `inspect_blueprint` | Variables, function/event graph names, parent class. |
| `inspect_widget_tree` | Read the widget hierarchy of a `UWidgetBlueprint` or EUW. The thing UE Python can't do. |
| `edit_widget_tree` | Mutate the tree: `set_root` / `add_child` / `set_property`. Solves the EUW WidgetTree blocker natively. |
| `get_viewport_screenshot` | Active viewport as base64 PNG. |
| `list_tools` | Names of every registered method (for autodiscovery). |
| `get_actors_in_level` | Name/class/transform of every actor; optional substring filter. |
| `focus_actor` | Select an actor by label and frame the viewport on it. |
| `load_level_by_path` | Open a level by package path. |
| `take_high_res_screenshot` | Trigger UE's `HighResShot` console command. |

Adding a 12th tool is a single `.cpp` file + one line of registration. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start (for engineers)

1. Drop `UnrealClaudeMCP/` into `<YourProject>/Plugins/`
2. Right-click `<YourProject>.uproject` → Generate Visual Studio project files
3. Build the Editor target (Development Editor | Win64) — first build takes ~5-15 min
4. Open the project. The MCP server auto-starts on `127.0.0.1:18888`. Output Log shows:
   ```
   [LogUnrealClaudeMCP] Editor module started
   [LogUnrealClaudeMCP] Registered handler 'execute_unreal_python'
   ... (11 lines)
   [LogUCMCP] Listening on 127.0.0.1:18888
   ```
5. From any TCP client send a JSON-RPC 2.0 request, e.g.:
   ```json
   {"jsonrpc":"2.0","id":1,"method":"list_tools"}
   ```
6. To wire Claude Code, copy `examples/.mcp.json.example` to your project root as `.mcp.json` (edit the path to point at `bridge/unreal_claude_mcp_bridge.py`), then restart Claude Code and approve the new MCP server.

## Quick start (for non-engineers / GUI-only)

See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) — step-by-step.

## Status

- v0.1.0 — first public release, 2026-05-07
- 11 tools live, smoke-tested end-to-end
- Tested on UE 5.7.4 / Windows 11 / Visual Studio 2026 Community / MSVC 14.50

## What this is NOT

- A general MCP server framework — this is bonded to UE's editor process
- A live-broadcast tool — for that, look at vMix, OBS, NDI Studio Monitor
- An Aximmetry / Pixotope / Disguise replacement — those have multi-engineer multi-year codebases

## Contributing

Issues / PRs welcome. Two rules:

1. Verify any UE API claim against UE 5.7 source. Reviewer subagents have made specific UE API claims that turned out wrong; ground-truth the source before committing.
2. Each new MCP handler is one `Handler_*.cpp` file in `Source/UnrealClaudeMCP/Private/MCP/Handlers/` plus one `extern` declaration + `Reg.Register(Make_Handler_*())` in `UnrealClaudeMCPModule.cpp`. Don't grow the foundation — add handlers.

### Tests

Bridge unit tests run without UE in under a second:

```bash
pip install pytest pytest-cov
pytest tests/
```

CI runs the same suite on every push and PR (see `.github/workflows/tests.yml`). The live integration smoke test in `examples/smoke_test.py` requires a running UE editor — see [`tests/README.md`](tests/README.md).

## License

MIT — see [`LICENSE`](LICENSE). © 2026 HD Media (Kuwait).
