# PORT-SPEC 00 — Overview

Spec for the successor effort: porting this repo's crown-jewel tool families onto **Epic's UE 5.8 MCP Toolset Registry** as custom Toolsets. Planning map and decision detail: [wayfinder map #290](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/290) — this file restates only what a cold build session needs.

**How to use this spec:** read this file, then exactly one `docs/PORT-SPEC/<family>.md` file. Each family file contains full `UFUNCTION(meta=(AICallable))` stubs (signature = schema), donor-handler pointers into this repo's `UnrealAIConnection*/` sources, and per-stub `Smoke:` acceptance blocks. Build the bodies; nothing else is left to decide.

## Destination & locked decisions

| Decision | Value |
|---|---|
| Deliverable | New UE plugin exposing our tool families as Toolset Registry toolsets |
| Engine | **UE 5.8 exact pin.** No compat scaffold, no multi-version support |
| Transport | Clients connect **directly** to Epic's embedded server (`127.0.0.1:8000/mcp`, Streamable HTTP). No stdio bridge, no client-side codebase |
| Tool surface | **Redesigned per family from workflows** — fewer, fatter, workflow-shaped tools. The old 151-tool surface is donor C++ only, not a template |
| Scope | 4 families: MRQ, Blueprint K2 graph, material graphs (gap-fill — resolved at [#302](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/302)), DMX/nDisplay/OCIO. All port-vs-extend re-grills resolved |
| Out of scope | Building = separate effort. stdio bridge. Multi-version compat. Re-porting families Epic covers (actors/scene/material instances/objects; Level Sequence — ruled out at [#300](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/300): Epic's 8 sequencer toolsets ≈230 tools cover it, donor's MRQ render + sequence_snapshot migrated to the MRQ family; data tables/assets — ruled out at [#303](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/303): Epic's DataTableTools/CurveTableTools/DataAssetTools/AssetTools/ObjectTools strictly superset the donor family, donor has no unique capability to port; Niagara — ruled out at [#304](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/304): Epic's NiagaraToolsets (5 C++ toolset classes, 34 tools — creation, stack/module authoring, deep inspection, diagnostics) plus SceneTools/ActorTools/Component tools cover the donor's 4-tool surface; sole residue (socket-targeted component attach) is a generic-component gap outside this spec) |

## Toolset authoring primer (UE 5.8, distilled)

Verified against Epic's 5.8 docs + Epic's own [`create-toolset` skill](https://github.com/EpicGames/unreal-engine-skills-for-claude-code-plugin/blob/main/skills/create-toolset/SKILL.md); full citations in [#291](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/291). Items marked (skill-only) come from the Epic GitHub skill, not the doc site.

**Shape.** A toolset = `UCLASS(BlueprintType, Hidden)` deriving `UToolsetDefinition` (`ToolsetRegistry/ToolsetDefinition.h`). A tool = a `static` method on it tagged `UFUNCTION(meta = (AICallable))`. One toolset class per `.h/.cpp` file. Class doc comment becomes the toolset description; method doc comments (`/** @param @return */`) become tool descriptions — **keep them AI-facing, no test steps or internal notes**.

**Schemas.** Reflected from real C++ types — no manifest, no DSL. `UPROPERTY` metadata (`ClampMin`/`ClampMax`) flows into the JSON Schema automatically. Return real structured types; never JSON-in-a-string ("code smell" — Epic's words). Custom mappings via `FToolsetJsonConverter` subclasses; shipped: `FToolsetColorConverter`, `FToolsetReferenceConverter` (`UObject*`/`UClass*` become `{"refPath": "/Path/To.Object"}` — confirmed live), `FToolsetTransformConverter` (optional location/rotation/scale). Reactive tool, not a default.

**Registration.** Editor auto-discovers `UToolsetDefinition` subclasses via reflection at startup; `ModelContextProtocol.RefreshTools` re-polls. Explicit dynamic path (skill-only): `UToolsetRegistry::RegisterToolsetClass()` / `UnregisterToolsetClass()` from module startup/shutdown. Live Coding handles body edits; **new `UFUNCTION`s need an editor restart**. Cooked builds: no auto-discovery, editor-only adapter — this plugin is editor-only by design.

**Errors.** Raise, never return: `UKismetSystemLibrary::RaiseScriptError(TEXT("..."))` then return default — signature takes a single `FString` only (verified against 5.8 source, `KismetSystemLibrary.h:124`; the `EScriptExceptionType` variant floating in secondary sources does not exist). No bool/error-string/result-wrapper returns.

**Async / long-running** (skill-only, high confidence): return a `UToolCallAsyncResult` subclass; call `SetValue()` / `SetError()` on completion. Progress notification interval: `ModelContextProtocol.ProgressIntervalSeconds` (default 1.0). MRQ family leans on this hardest.

**Limits.** All tool calls run serially on the **game thread**; no overlapping calls. Loopback-only, no auth, non-loopback `Origin` rejected. `ModelContextProtocol.PaginationPageSize` (0 = off) caps paginated items; `WrapPODToolResultsInObject` (default true) wraps primitives in `{"result": ...}`. No documented hard byte cap. Whole feature is **Experimental** in 5.8.

**References to imitate.** C++: `UAttributeSetToolset` (`Engine/Plugins/Experimental/Toolsets/GASToolsets/`) — simplest shipped C++ shape. Python: `ActorTools` (`Engine/Plugins/Experimental/ToolsetRegistry/Content/Python/toolset_registry/toolsets/core/actor.py`). Plugin layout convention: `Engine/Plugins/Experimental/Toolsets/<Name>/`.

## Plugin conventions for this port

- One plugin, working name `CrownJewelToolsets`; one module; one toolset class per family file, named `U<Family>Toolset` (e.g. `UK2GraphToolset`).
- Tool naming: `snake_case` verbs, workflow-shaped (`build_material_graph`, not `create_node` × 40).
- Doc comments AI-facing only. Acceptance criteria live in the family file's `Smoke:` blocks, never in code comments.
- Object parameters use typed references (accept the `{"refPath": ...}` converter form); transforms via the transform converter.
- Sync by default; `UToolCallAsyncResult` only where the operation genuinely outlives a frame budget (renders, long imports).

## Acceptance strategy

Decided in [#295](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/295): **proof = live MCP smoke, end-to-end.** Each stub in a family file carries an adjacent `Smoke:` block — setup, call args, observable editor assertion, teardown. A family counts as built when every Smoke block passes in one sitting against a live 5.8 editor via a real MCP client; failures get recorded on the family's ticket. No bridge-schema tier (nothing can drift), no automation-test tier in the bar.

## Connecting a client (verified live, [#296](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/296))

1. Enable plugins `ModelContextProtocol`, `ToolsetRegistry`, `AllToolsets` (+ ours). Reusable smoke project: `F:\ai\claude-work\McpSmoke\`.
2. Auto-start lives in **EditorPerProjectUserSettings**, not DefaultEngine.ini: `Saved/Config/WindowsEditor/EditorPerProjectUserSettings.ini` → `[/Script/ModelContextProtocolEngine.ModelContextProtocolSettings]` `bAutoStartServer=True`. Or run `ModelContextProtocol.StartServer` in the console.
3. Server speaks Streamable HTTP at `http://127.0.0.1:8000/mcp`, protocol `2025-06-18`, `Mcp-Session-Id` header. Quirk: `initialize`/`tools/list` answer plain JSON; `tools/call` answers SSE frames on the same POST — clients must send `Accept: application/json, text/event-stream`.
4. With tool search on (default), only 3 meta-tools are advertised: `list_toolsets` / `describe_toolset` / `call_tool`. `call_tool` shape: `{toolset_name, tool_name (unprefixed), arguments}`. Wrong/missing args return the full schema in the error text.
5. `ModelContextProtocol.GenerateClientConfig` writes per-client config (Claude Code, Cursor, VS Code, Gemini, Codex). Codex TOML generation is write-once.

## Family files

Build order (moat order, decided on the map): `blueprint-k2.md` → `pro-viz.md` (DMX/nDisplay/OCIO) → `mrq.md` (includes `sequence_snapshot`, inherited from the descoped Level Sequence family) → `material-graphs.md`. **All four family files are present** — the spec's family set is complete (map tickets #298–#304; #300 Level Sequence, #303 data tables/assets, and #304 Niagara closed out-of-scope).
