# Design — DMX companion plugin + folder-typo docs fix

**Date:** 2026-05-30
**Status:** approved (brainstorming → implementation)

## Problem

The core `UnrealAIConnection` plugin hard-links `DMXRuntime` + `DMXProtocol` (Build.cs)
for 4 MCP tools (`create_dmx_patch`, `dmx_stream_set/stop/status`, in 2 handler files).
Because those modules belong to the `DMXEngine` / `DMXProtocol` plugins, every host
project must enable those plugins or `UnrealEditor-UnrealAIConnection.dll` fails to load
(GetLastError=126). PR #271 fixed the load failure by declaring the DMX plugins as
required deps — but that **forces DMX on every user** for 4 niche tools, which conflicts
with the maintainer de-emphasising DMX.

## Decision

Keep all 4 DMX tools, but move them out of the core plugin into a **separate, optional
companion plugin** so the core has **zero** DMX dependency and loads everywhere.

## Why this is clean (verified against the code)

- `FUCMCPHandlerRegistry` is an **exported singleton** (`UNREALAICONNECTION_API`,
  `::Get()`), and `Register()` is public — another module can add handlers to it.
- The dispatcher resolves handlers **per request**: `FUCMCPHandlerRegistry::Get().Find(Method)`
  (MCPDispatcher.cpp:71). So a companion module that registers its handlers at load
  time (after the core) is fully visible to the running TCP server — no snapshot.
- `MCP/MCPHandler.h` is **Public**, and the 2 DMX handlers include **only** that public
  header (plus DMX module headers). So they compile unchanged inside a companion module
  that depends on the core module + the DMX modules.

## Changes

### Core plugin (`UnrealAIConnection`) — remove DMX
- `UnrealAIConnection.Build.cs`: drop `"DMXRuntime"`, `"DMXProtocol"`.
- `UnrealAIConnection.uplugin`: drop the `DMXEngine` + `DMXProtocol` plugin entries
  (reverts PR #271; DMX moves to the companion).
- `UnrealAIConnectionModule.cpp`: remove the 4 DMX `extern` decls + 4 `Reg.Register(...)`
  lines.
- `MCPHandler.h` / `MCPHandler.cpp`: add `void Unregister(const FString& Method)` to
  `FUCMCPHandlerRegistry` (so the companion can cleanly remove its handlers on
  ShutdownModule / Live-Coding reload — avoids a dangling `TSharedRef` to a handler
  whose owning module unloaded).

### New companion plugin (`UnrealAIConnectionDMX/`)
- `UnrealAIConnectionDMX.uplugin`: `"EnabledByDefault": false`, `"Installed": false`;
  `Plugins` deps = `UnrealAIConnection` + `DMXEngine` + `DMXProtocol`.
- `Source/UnrealAIConnectionDMX/UnrealAIConnectionDMX.Build.cs`: deps = `Core`,
  `CoreUObject`, `Engine`, `Json`, `UnrealAIConnection`, `DMXRuntime`, `DMXProtocol`.
- `Source/UnrealAIConnectionDMX/Public/UnrealAIConnectionDMXModule.h` +
  `Private/UnrealAIConnectionDMXModule.cpp`: `StartupModule()` registers the 4 handlers
  via `FUCMCPHandlerRegistry::Get().Register(...)`; `ShutdownModule()` calls
  `Unregister(...)` for each.
- Move `Handler_CreateDmxPatch.cpp` + `Handler_DmxStream.cpp` →
  `Source/UnrealAIConnectionDMX/Private/Handlers/` (unchanged content).

### Catalog — unchanged (143 / 106 native / 37 synthetic)
The bridge `TOOLS`, `mcp_manifest.json`, `docs/TOOLS.md`, and conftest counts continue to
advertise all 4 DMX tools. When the companion plugin is enabled they register + work;
when it is not, those 4 calls return `-32601 method not found` (graceful). No drift/test
count change — the tools still exist, just provided by the companion.

### Task 2 — folder typo (docs only)
Leave the local folder `F:\ai\Unreal ai conncetion` (works; the GitHub repo + plugin +
module names are all spelled correctly). Replace the one hardcoded misspelled path in
`docs/HANDOFF.md` (the `cd` runbook step) with a `<repo root>` placeholder so no doc
enshrines the typo and the review bots stop flagging it. Zero disruption.

## Verification

1. **Static (proves DMX un-forced):** core `Build.cs` + `.uplugin` contain no `DMX*`.
2. **Build core** (existing `build57_uac.bat`) → `Result: Succeeded` with no DMX modules
   linked.
3. **Build companion** against the core (RunUAT BuildPlugin or a project build with the
   core present) → `Result: Succeeded`.
4. **Live:** deploy both to the host (which has DMX enabled), launch UE → confirm the 4
   DMX tools register (companion loaded) and `create_dmx_patch` still works; core binds
   `127.0.0.1:18888`. (Core-loads-without-DMX is proven by #1 — the link that caused the
   load failure is gone.)
5. `drift_sweep` clean (143/106/37/577), `pytest` green.
6. Bot-gate, then merge.
