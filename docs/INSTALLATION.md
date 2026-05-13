# Installation

This guide assumes Unreal Engine 5.7 is installed (any patch version) and that you have a UE 5.7 project open or ready to receive the plugin. Tested on UE 5.7.4 / Windows 11 / Visual Studio 2026 Community / MSVC 14.50.

## 1. Drop the plugin into your project

1. Locate `UnrealClaudeMCP/` from this repo (the inner folder, not the repo root)
2. Copy the entire folder to `<YourUE5Project>/Plugins/UnrealClaudeMCP/`. Create the `Plugins/` folder if it doesn't exist.

Your project layout should now look like:
```
<YourUE5Project>/
  <YourUE5Project>.uproject
  Plugins/
    UnrealClaudeMCP/
      UnrealClaudeMCP.uplugin
      Source/
      Resources/
```

## 2. Regenerate Visual Studio project files

Right-click your project's `.uproject` file in File Explorer → **Generate Visual Studio project files**. This step is mandatory because we just added new source files.

(If you don't see this option, install Visual Studio 2022+ with the "Game development with C++" workload.)

## 3. Build the Editor target

You have two options. Pick whichever you prefer.

### Option A — From Visual Studio
1. Open the `.sln` file that was just generated
2. Set Solution Configuration to **Development Editor**
3. Set Solution Platform to **Win64**
4. Build → Build Solution. First build takes 5-15 minutes. Subsequent builds are seconds.

### Option B — From the command line (no IDE needed)
Run this from a Developer Command Prompt or any shell:

```
"<UE_INSTALL>/Engine/Build/BatchFiles/Build.bat" UnrealEditor Win64 Development -Project="<full path to your .uproject>" -WaitMutex
```

Replace `<UE_INSTALL>` with your UE 5.7 install root (e.g. `F:/UE_5.7/`).

## 4. Open your project in UE 5.7

The MCP server auto-starts when the editor module loads. Open Window → Output Log. You should see something like:

```
[LogUnrealClaudeMCP] Editor module started
[LogUnrealClaudeMCP] Registered handler 'edit_widget_tree'
[LogUnrealClaudeMCP] Registered handler 'execute_unreal_python'
... (71 lines of handler registrations) ...
[LogUCMCP] Listening on 127.0.0.1:18888
```

If you see all 71 handler registrations and the "Listening" line, you're done.

## 5. Smoke test from any Python

`examples/smoke_test.py` runs the safe (non-destructive) tools sequentially. From a regular shell with Python on PATH:

```
py examples\smoke_test.py
```

You should see structured JSON responses for each tool, including a base64-encoded PNG for `get_viewport_screenshot`.

To exercise the destructive tools too (widget-tree mutation, level load), pass the asset paths explicitly:

```
py examples\smoke_test.py --widget /Game/UI/WBP_SmokeTest.WBP_SmokeTest --level /Game/Maps/MyMap
```

> **Use a throwaway Widget BP for `--widget`**: the smoke test will set its root and add a child. Don't point it at production UI.

## 6. Wire to Claude Code (optional)

If you use Claude Code:

1. Copy `examples/.mcp.json.example` to your UE project's root as `.mcp.json`
2. Edit the path in `args` to point at the actual location of `bridge/unreal_claude_mcp_bridge.py`
3. Restart Claude Code
4. Claude Code will detect the new MCP server and prompt you to **Approve** it (security gate)
5. After approval, all 100 tools are available to your MCP client in chat as `mcp__unreal-claude-mcp__*` (71 dispatched directly to UE, plus 29 bridge-side synthetic tools — camera read/write, focused-actor screenshot, `wait_for_events`, `compile_mod_pak` headless `.pak` build, `compile_mod_pak_direct` headless `.pak` build via UnrealPak (RunUAT bypass), `bulk_delete_assets`, `bulk_move_assets`, `bulk_rename_assets`, `bulk_duplicate_assets`, `bulk_inspect_assets`, `inspect_data_asset`, `inspect_sound_class`, `inspect_sound_submix`, `inspect_audio_bus`, `inspect_material_function`, `inspect_metasound`, `find_unused_assets`, `get_reference_chain`, `bulk_compile_blueprints`, `audit_blueprint_compile_status`, `find_actors_by_class`, `bulk_focus_actors`, `bulk_screenshot_actors`, `bulk_set_actor_property`, `compare_assets`, `bulk_set_console_variables`, `inspect_dependency_graph`, and `bulk_fix_redirectors`)

You can now ask Claude things like:
- "List all actors in the level"
- "Inspect the BP_MyActor blueprint and tell me its parent class"
- "Take a viewport screenshot"
- "Add a TextBlock named 'Title' to the root of WBP_MyPanel"

Claude calls the tool natively — no GUI driving, no screenshot-based clicking.

## Troubleshooting

**The MCP server didn't start (no "Listening on 127.0.0.1:18888" line)**
- Check Output Log for `LogPluginManager: Mounting Project plugin UnrealClaudeMCP` — if missing, the plugin isn't being loaded. Verify it's enabled in Edit → Plugins.
- Check if another process is using port 18888 (`netstat -an | findstr 18888`). To change the port, edit `kMCPDefaultPort` in `UnrealClaudeMCPModule.cpp` and rebuild.

**TCP client times out**
- The dispatcher runs on the game thread (FTSTicker callback). If UE is showing a modal dialog (e.g. "Project file out of date") or in the middle of a long compile, the ticker is blocked.
- Dismiss modals; try again.

**Claude Code says "MCP server not running"**
- The bridge tries to connect to `127.0.0.1:18888`. If UE editor isn't running with the plugin loaded, the bridge returns an error to Claude. Open the project and try again.

**Visual Studio 2026 compiler warning**
- UE 5.7 marks VS 2026 as "not a preferred version" — the build still succeeds; the warning is cosmetic.

## What if I want to add another tool?

See [`ARCHITECTURE.md`](ARCHITECTURE.md). Short version: one `.cpp` file in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, one `extern` declaration + one `Reg.Register(...)` line in `UnrealClaudeMCPModule.cpp`. Rebuild. Restart UE. New tool available.
