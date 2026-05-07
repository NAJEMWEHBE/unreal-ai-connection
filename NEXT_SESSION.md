# Next session — pickup notes for UnrealClaudeMCP

> **This file is a handoff for the next Claude Code session that will work on this repo.** It is safe to delete once the project is "shipped" — the user (Najem) said: *"keep the file there. No problem. We will remove it to finish the product."*

---

## What this repo is

`UnrealClaudeMCP` — a UE 5.7 plugin that runs an MCP (Model Context Protocol) server inside the editor's process so any MCP client (Claude Code, Claude Desktop, your own tooling) can drive Unreal Engine over a local TCP socket. v0.1.0 ships with **11 generic editor-automation tools** + a Python stdio↔TCP bridge.

Released MIT, copyright HD Media (Kuwait).

## Current state (as of 2026-05-07)

- **Code**: complete. 11 handlers, single Editor module, manifest, bridge, smoke test, `.mcp.json.example`, LICENSE, .gitignore, README, three docs (INSTALLATION / ARCHITECTURE / TOOLS), this file, and `PUSH_TO_GITHUB.md`.
- **Local git**: initialized on `main`, single commit `09764df` (well, two commits if this file lands in its own follow-up commit), working tree clean.
- **Remote**: pushed to GitHub as `UnrealClaudeMCP` under user `najmwahba.98@gmail.com` — see this repo's URL in your browser.
- **Build status**: NOT yet compiled in a fresh project. The code was lifted from a working private fork (the HD Media plugin where every handler was smoke-tested end-to-end), then renamed to use generic `FUCMCP*` / `IUCMCP*` prefixes and `UnrealClaudeMCP` module name. **First-build verification is the #1 task for the next session.**

## What was done (so the next session doesn't repeat the work)

This repo was extracted from a private HD Media plugin with the following changes:

1. **Stripped HD Media domain code**:
   - `HDMediaAxStudioOperator` actor + 5 spawn UFUNCTIONs
   - `HDMediaStudioPanelWidget` UEditorUtilityWidget subclass
   - `Handler_SetChromaKey` (writes to MPC_HDMediaAx)
   - `setup_plugin.py` and other content-creation scripts
   - The Runtime module (kept only the Editor module since all 11 tools are editor-side)

2. **Renamed everything**:
   - Plugin / module: `HDMediaCamPlugin` → `UnrealClaudeMCP`
   - Class prefix: `FHDMediaMCP*` → `FUCMCP*`
   - Interface prefix: `IHDMediaMCPHandler` → `IUCMCPHandler`
   - API macro: `HDMEDIACAMPLUGINEDITOR_API` → `UNREALCLAUDEMCP_API`
   - Log categories: `LogHDMediaMCP` → `LogUCMCP`, etc.
   - Bridge: `hdmedia_mcp_bridge.py` → `unreal_claude_mcp_bridge.py`
   - Server name in MCP envelope: `hdmedia-unreal` → `unreal-claude-mcp`
   - Temp Python dir: `Intermediate/HDMediaMCPPython/` → `Intermediate/UnrealClaudeMCPPython/`

3. **Doc fixes** caught while writing the clean copies:
   - `TOOLS.md` had `Saved/Screenshots/Windows/`; UE 5.7 actually writes to `WindowsEditor/`. Fixed.
   - `TOOLS.md` was missing the `compile` flag for `edit_widget_tree` (only README had it). Now in TOOLS too.
   - `ARCHITECTURE.md` Build.cs example listed `Composure` and `MovieRenderPipelineCore` — those are HD Media-only. Replaced with the actual current dep list.
   - Bridge schema for `edit_widget_tree` added the `compile` flag (was stale on the HD Media bridge).

## Top open work for the next session

In rough priority:

### 1. Verify the first build in a fresh project
Drop `UnrealClaudeMCP/` into a clean UE 5.7 project's `Plugins/` folder, regen VS files, build Development Editor | Win64. **Watch for**:
- Any `LNK2019` (missing module dep — fix in `UnrealClaudeMCP.Build.cs`)
- Any reference to a `HDMedia*` symbol I missed in renaming
- `[LogUnrealClaudeMCP] Editor module started` in the Output Log + the 11 handler-registration lines + `[LogUCMCP] Listening on 127.0.0.1:18888`

If the build is clean, run `py examples\smoke_test.py` from a separate shell. The 7 default-ON tests should all return JSON without errors.

### 2. Smoke test against UE 5.6 (the original target)
Najem's project tracker says HDMVS targets UE 5.6.1, but the home PC currently has UE 5.7.4. The plugin code targets `EngineVersion: 5.7.0` in `.uplugin`. If you want UE 5.6 compatibility:
- Change `EngineVersion` in `UnrealClaudeMCP.uplugin`
- Verify `FTSTicker`, `FTcpListener`, `IPythonScriptPlugin::ExecPythonCommandEx`, `UWidgetBlueprint::WidgetTree` API surface in 5.6 (most should be identical)
- Smoke test

### 3. Cross-platform — Mac / Linux
Today the code is Windows-tested only. Likely-broken assumptions:
- The temp .py file path uses `FPaths::ProjectIntermediateDir()` — should work cross-platform
- The console command in `Handler_TakeHighResScreenshot` writes to `Saved/Screenshots/WindowsEditor/`; on Mac it's `MacEditor/`. Doc + code should use `output_dir_hint` derived from `UGameUserSettings` or just remove the platform-specific path hint.

### 4. Add 1-3 high-value handlers
Candidates the user specifically values, ordered by leverage:
- `spawn_actor` — generic version of HD Media's spawn buttons. Params: `class_path`, `loc`, `rot`. One handler ~50 LOC.
- `set_actor_transform` — paired with focus_actor, gives full place-and-frame.
- `compile_blueprint` — separate from `edit_widget_tree` so the AI can batch-edit then compile once explicitly.
- `save_all` — `UEditorAssetSubsystem::SaveAllAssets` wrapper.

Recipe is in `docs/ARCHITECTURE.md` "Adding a new handler — full recipe" section.

### 5. CI
Right now there's no CI. Easiest path:
- GitHub Actions runner with windows-2022, install UE 5.7 binary release (or use a community `setup-unreal-engine` action), build the plugin against a stub project. ~30 min to set up.
- Smoke test in CI is harder because it needs an editor instance with the plugin loaded; defer until handlers stabilize.

## Where the originals live (for context retrieval)

- **Private HD Media plugin** (full version with Studio Operator + chroma key + setup scripts): `C:\Users\NINOH\Desktop\HDMediaUnrealMCP\`. Three local commits, no remote, NOT pushed.
- **HD Media VP project** that uses the plugin: `C:\Users\NINOH\Desktop\HDMediaVirtualStudio\`.
- **User memory** for this user: `C:\Users\NINOH\.claude\projects\C--Users-NINOH-Desktop-ax-plug-in\memory\MEMORY.md` — read this for Najem's preferences (GUI-only, no coding, blunt comms, evidence-based, no terminal instructions).

## The constraints Najem set, that you should honor in the next session

1. **No CLI instructions in chat.** If a disk op needs running, run it via tools yourself. Don't paste commands and tell him to run them.
2. **GUI-only mindset.** He's a creative director, not a developer. Show results, not config. Screenshots > prose.
3. **Don't lecture on version churn.** UE version targeting (5.6 vs 5.7) has flipped before. If he says "use 5.6 now", do it without commentary about migration cost.
4. **Don't push HD Media's plugin.** That code stays in `HDMediaUnrealMCP/` (private, local-only). This repo is the generic public release only.

## Last thing

If the next session is in **Claude Code in the cloud** (web-based, no local file access), you won't be able to read `C:\Users\NINOH\...`. In that case clone this repo (`git clone <url>`), work inside the clone, and Najem can `git pull` to bring changes back to his local box. The MCP bridge is local-only by design, so any handler work in the cloud is by-source-only — the live smoke test still has to happen on Najem's machine.
