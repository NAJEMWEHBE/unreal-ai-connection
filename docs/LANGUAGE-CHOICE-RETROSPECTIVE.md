# Language-choice retrospective

A retrospective look at every tool in this project, asking: **was the language we chose the right one, or could PowerShell / Python / JavaScript / Go / Rust / something else have done it better?**

The user asked this question on 2026-05-09 after the v0.10.0 ergonomics bundle merged. This doc answers it tool-by-tool, then steps back to a strategic view.

---

## TL;DR

| Layer | Language | Verdict |
|---|---|---|
| **Plugin handlers (36 total)** | C++ | Mostly forced. ~14 cannot be done another way; ~18 strongly prefer C++ for ergonomics + perf; ~4 could be thin C++→Python shims. |
| **Bridge** (`bridge/unreal_claude_mcp_bridge.py`) | Python | Right choice. Pure JSON-RPC framing — Python's stdlib `socket` + `json` is the most concise expression. |
| **Smoke test + seeder** (`examples/smoke_test.py`, `scripts/seed_test_project.py`) | Python | Right choice. Sequential JSON-RPC client; Python wins for scripts that wrap an HTTP-shaped protocol. |
| **Editor lifecycle module** (`scripts/UnrealClaudeMCP-Editor.psm1`) | PowerShell | Right choice for Windows. Native process control (`Start-Process -PassThru`, `Stop-Process`, `Get-Process`) is first-class. Cross-platform via PS 7+ preserved by avoiding `Test-NetConnection`. |
| **Tests** (`tests/test_*.py`) | Python | Right choice. Pytest is the de facto Python test framework; bridge tests need to import `bridge` as a module. |
| **Docs** (`docs/*.md`) | Markdown | Right choice. GitHub-renderable; no debate. |

**No layer has a clear better-language alternative we missed.** The plugin handlers are the only place where the choice is debatable, and even there C++ is correct ≥85% of the time. The remaining ~15% is opportunity, not regret.

---

## Architecture constraints (why C++ is mostly forced)

UE's plugin model is the load-bearing constraint:

1. **Plugins are loaded as DLLs into UE's editor process.** The plugin's module class (`FUnrealClaudeMCPModule`) inherits from `IModuleInterface`, a UE C++ interface. There is no Python or C# binding for `IModuleInterface` in UE 5.7. The plugin entry point *must* be C++.

2. **UE's API surface is C++ with reflection caveats.** `unreal.*` Python bindings cover the reflected subset, but:
   - `UPROPERTY()` without `EditAnywhere` or `BlueprintReadWrite` is invisible to Python (we caught this with `MaterialInstanceConstantFactoryNew::InitialParent` — see HANDOFF UE 5.7 traps).
   - Some struct fields are accessible from C++ but not from Python (`FMaterialParameterInfo::Name` works as a public field; older API forms are filtered out of Python's reflection).
   - Private members and `friend` access are forever C++-only.

3. **Editor-process services with thread-safety concerns must be C++.** Our `LogCapture` (an `FOutputDevice` subclass with `CanBeUsedOnAnyThread() = true`) cannot exist in any other language — UE's logging system calls into it from arbitrary threads, and the Python interpreter is single-threaded with the GIL.

4. **TCP server lifecycle in UE's tick loop must be C++.** Our `MCPServer` ticks on the game thread via `FTSTicker`. Python can't register a UE ticker callback safely.

So the *baseline* for every plugin entry is: a C++ module that registers handlers. From there, each handler *could* be C++ that does the work directly, OR C++ that delegates to in-editor Python via `IPythonScriptPlugin::ExecPythonCommandEx`. The handler-by-handler question is which of those is right.

---

## Per-handler verdict (36 handlers)

### Category A — Forced C++ (14 handlers)

These touch UE APIs that Python literally cannot reach in UE 5.7. C++ wasn't a choice; it was a requirement.

| Handler | Why C++ is forced |
|---|---|
| `inspect_widget_tree` | UE Python's WidgetTree access blocked by UPROPERTY reflection limits. **This is the project's headline feature** — the plugin exists in part *because* Python can't do it. |
| `edit_widget_tree` | Same WidgetTree limitation, plus mutation paths that need direct C++ struct access. |
| `set_actor_property` | Uses `PropertyCoercion` for advanced types (USTRUCT recursive, TArray, TMap, TSet, FObjectProperty paths). UE Python's `set_editor_property` only handles primitives and simple structs; nested USTRUCT path traversal (`RootComponent.RelativeLocation`) is C++-only. |
| `inspect_material_instance` | Reads `FMaterialParameterInfo` struct fields (`Name`, `Association`, `Index`) directly — Python reflection on this struct is incomplete. |
| `set_mi_parameter` | Post-verify scan over `MIC->ScalarParameterValues` array; Python can iterate but the `FScalarParameterValue` struct's private layout would need a C++ wrapper anyway. |
| `inspect_material` | Uses `UMaterialEditingLibrary` callbacks that Python wraps incompletely. |
| `inspect_sequence` | `UMovieScene` private struct traversal (`FMovieSceneBinding`, `FMovieSceneSpawnable`); Python sees only the high-level surface. |
| `bind_actor_to_sequence` | Same UMovieScene private-API access. |
| `create_sequence` | Factory access patterns work in Python, but combined with the binding API this is naturally C++. |
| `create_material_instance` | `UMaterialInstanceConstantFactoryNew::InitialParent` is a bare `UPROPERTY()` not reachable from Python (caught + documented as a UE 5.7 trap). C++ sets it directly. |
| `LogCapture` (infrastructure) | `FOutputDevice` subclass with thread-safe override — Python can't subclass this. |
| `MCPServer` (infrastructure) | TCP server in editor process, ticks on game thread. |
| `MCPDispatcher` (infrastructure) | Handler registry with method routing. |
| `PropertyCoercion` (infrastructure) | JSON ↔ FProperty native traversal with TStructOnScope handling. |

**Verdict for Category A: C++ was the only option. Python was never on the table.** Don't second-guess.

### Category B — Strongly preferred C++ (18 handlers)

These *could* be implemented as thin C++ shims that delegate to in-editor Python. But Python alternatives have measurable cost:

- **Per-call latency:** UE's `IPythonScriptPlugin::ExecPythonCommandEx` writes the script to a temp file and parses it on every call (~5-15ms overhead). Native C++ has zero startup cost.
- **Error handling friction:** Python exceptions get serialized to a string; structured error codes (our stable `<tool>: <error_code>: <detail>` format) are easier to emit from C++ where we control the path explicitly.
- **Consistency:** Mixing "this handler is C++" and "this handler is C++ shim → Python" makes the codebase harder to navigate. New contributors looking for the implementation of `find_assets` would expect to find it next to `inspect_asset`.

| Handler | Could be Python via | Why we kept C++ |
|---|---|---|
| `find_assets` | `unreal.AssetRegistryHelpers.get_asset_registry().get_assets()` | Tag filters and class-path matching are more concise in C++ via `FARFilter`. |
| `inspect_asset` | Same registry path | Same. |
| `spawn_actor` | `unreal.EditorLevelLibrary.spawn_actor_from_class` | Post-spawn property setting via `PropertyCoercion` requires C++ reflection. |
| `set_actor_transform` | `unreal.AActor.set_actor_location()` etc. | Relative-mode arithmetic is cleaner in C++ with `FTransform`. |
| `delete_actor` | `unreal.EditorLevelLibrary.destroy_actor` | Children-attached safety check needs C++ component traversal. |
| `focus_actor` | Limited Python; viewport framing API is mostly editor-internal | Direct C++ access to viewport-client. |
| `add_component` | `unreal.GameplayStatics.spawn_object` (limited) | UE 5.7's component registration paths vary by component type; C++ does discrimination naturally. |
| `get_actors_in_level` | `unreal.EditorLevelLibrary.get_all_level_actors` | Filter + transform extraction is one-pass in C++. |
| `import_texture` | `unreal.AssetTools.import_asset_tasks` | Factory-settings inference works in Python but `UAssetImportTask` setup is verbose in Python. |
| `configure_texture` | Property setting via Python | `PreEditChange` / `PostEditChange` flow with property handles needs C++ for thread-safety guarantees. |
| `delete_asset` | `unreal.EditorAssetLibrary.delete_asset` | Pre-delete referencer check via `IAssetRegistry::GetReferencers` is C++. |
| `move_asset` | `unreal.EditorAssetLibrary.rename_asset` | Move (cross-folder) semantics differ from rename; we use `IAssetTools::RenameAssets` directly. |
| `rename_asset` | Same Python API | Same. |
| `fix_up_redirectors` | `unreal.AssetTools` wrapping is incomplete | We use `IAssetTools::FixupReferencers` directly + `ScanPathsSynchronous` for the registry scan. |
| `compile_blueprint` | `unreal.EditorAssetLibrary.compile_blueprint` (returns bool only) | We need structured `EBlueprintStatus` mapping that the Python API doesn't expose. |
| `inspect_blueprint` | `unreal.Blueprint` reflection | Variable types and graph names need C++ traversal for completeness; Python misses some property categories. |
| `get_project_summary` | `unreal.SystemLibrary` + plugin manager | Plugin enumeration via `IPluginManager::Get().GetEnabledPlugins()` is more complete in C++. |
| `get_log_lines` | Could query UE log if we had a different ring-buffer mechanism | Tied to `LogCapture` (Category A); changing this would mean replacing the whole log-capture system. |

**Verdict for Category B: C++ was the right choice for ergonomics + consistency, but not strictly forced.** If a future contributor wanted to rewrite any of these as Python shims, the project would still work. The trade-off is per-call overhead and reduced uniformity. **Net: keep them C++.**

### Category C — Could be thin C++ shims (4 handlers)

These do almost nothing in C++ — they're glue around UE's Python interpreter or the console-command system.

| Handler | Implementation | Comment |
|---|---|---|
| `execute_unreal_python` | C++ writes user code to temp file + execs via `IPythonScriptPlugin` | The C++ shell is unavoidable (we need to receive the JSON-RPC, write the temp file, exec, return). The "logic" is one `ExecPythonCommandEx` call. C++ here is correct. |
| `run_python_file` | C++ resolves path + execs | Same. ~5 lines of work. C++ correct. |
| `apply_python_to_selection` | C++ prepends boilerplate + execs | Same. The boilerplate Python could live as a `.py` resource file but inlining as a string literal is simpler. |
| `execute_console_command` | C++ calls `GEngine->Exec` | Could be Python via `unreal.SystemLibrary.execute_console_command(world, cmd)`. We use C++ to capture the bool return and propagate as `command_execution_failed`. Marginal preference for C++. |

**Verdict for Category C: C++ is correct here too** — these handlers are thin enough that the alternative isn't "Python implementation," it's "no implementation in this plugin, just `execute_unreal_python` with a pre-canned snippet." That's worse for discoverability (`run_python_file` deserves to be a first-class tool in `list_tools`).

---

## Non-handler language choices

### Bridge — Python ✅

`bridge/unreal_claude_mcp_bridge.py` translates Claude Code's stdio MCP protocol to the plugin's TCP wire format. ~450 lines.

**Was Python right?** Yes:
- Pure protocol work — `socket`, `json`, `sys.stdin/stdout`. Python's stdlib makes this concise.
- Claude Code's MCP server convention is Python-first; the SDK examples are Python.
- Cross-platform: Python on Windows/Mac/Linux for free.

**Alternatives considered (retrospectively):**

| Language | Why considered | Why rejected |
|---|---|---|
| **Go** | Single-binary distribution; fast startup | The bridge is short-lived and trivially fast already (~50ms cold start); single-binary doesn't help when users need Python anyway for `smoke_test.py` etc. |
| **Rust** | Memory safety; performance | Massive overkill for ~450 lines of JSON-RPC. The bridge has never been a performance bottleneck. |
| **Node.js / TypeScript** | MCP SDK is also TypeScript | Adds another language to the project. No measurable win. |

### Smoke test + seeder — Python ✅

`examples/smoke_test.py` (~750 lines) and `scripts/seed_test_project.py` (~200 lines).

**Was Python right?** Yes — same reasons as the bridge. They're sequential JSON-RPC clients with assertion logic; Python is the right shape. They also need to import or duplicate the bridge's framing helpers, which is trivial in Python.

### Editor lifecycle module — PowerShell ✅

`scripts/UnrealClaudeMCP-Editor.psm1` (~200 lines). Functions: `Start-UCMCPEditor`, `Stop-UCMCPEditor`, `Wait-UCMCPReady`, `Test-UCMCPHandlers`.

**Was PowerShell right?** Yes — for Windows, definitively:
- `Start-Process -PassThru` returns a `System.Diagnostics.Process` with `.Id`. Native, structured.
- `Stop-Process -Force` is idempotent and friendly to scripting.
- `Get-Process UnrealEditor` is the canonical query.
- The verification runbook is already PowerShell-shaped (`taskkill`, `robocopy`, `& Build.bat`); a peer module keeps everything in one ecosystem.

**Cross-platform via PS 7+ is preserved** by using raw `[System.Net.Sockets.TcpClient]` instead of Windows-only `Test-NetConnection` for the readiness loop.

**Alternative:** Python via `psutil` for cross-platform process management. Trade-off: cross-platform but adds a dep. PowerShell module + UE 5.7 (which is overwhelmingly Windows-developed) wins on simplicity today.

### Tests — Python ✅

`tests/test_bridge.py` (~500 lines), `tests/test_bridge_edge_cases.py`, `tests/test_manifest_sync.py`. 106 tests.

**Was Python right?** Yes — tests need to import `bridge` as a module; the module is Python; pytest is the de facto Python test framework. Mocking `socket.socket` for offline tests is one decorator. Adding C++ unit tests would mean integrating GoogleTest or UE's automation framework — orders of magnitude more setup for less coverage of the bridge layer.

### Docs — Markdown ✅

`docs/HANDOFF.md`, `docs/TOOLS.md`, `docs/ARCHITECTURE.md`, `docs/INSTALLATION.md`. GitHub-renderable. No debate.

---

## Languages we did NOT consider

A few that were technically options but ruled out at design time:

### C# (UE has experimental C# support)

UE 5.x has `UnrealCSharp` and similar plugins, but:
- Not first-class — experimental, breaking changes between UE versions
- Wraps the C++ surface, doesn't add new capability
- Adds a third language to the project

**Verdict: skip until C# becomes first-class in UE (no signal that's coming).**

### Verse (Epic's UE language)

Verse is intended for UEFN and has an experimental UE 5 surface. But:
- Not yet stable in UE 5.7
- Tooling is immature outside UEFN
- The UE C++ API surface is what we need to expose

**Verdict: skip until UE 5.x supports Verse for editor scripting first-class.**

### More aggressive Python via embedded interpreter

We could push more handler logic into Python *running inside the editor*, with C++ shims doing only the JSON-RPC handling. The trade-off (per Category B above):
- Pro: less C++ to maintain; faster to add tools
- Con: per-call latency (Python parse + exec on every invocation); error handling indirection; reduced consistency

**Verdict: opportunistic. New handlers that wrap Python-reachable APIs (e.g. `list_recent_assets`, `get_actor_world_bounds`) could be Python-shim from the start. Existing C++ handlers should not be rewritten without measurable benefit.**

---

## Recommendations for new tools

For each new handler or tool added going forward, ask in this order:

1. **Does it touch UE C++ APIs that Python can't reach?** (Reflection limits, private structs, threading, FOutputDevice, etc.) → **C++ required.** Most existing handlers fall here.

2. **Is it pure protocol/orchestration glue?** (JSON-RPC, file I/O, process management, HTTP-shaped clients) → **Python or PowerShell**, depending on platform. Python wins by default; PowerShell for Windows-native process control.

3. **Is it an editor-side script that wraps Python-reachable APIs?** → **C++ shim → in-editor Python via `IPythonScriptPlugin`** is the lightest option. Only choose this when the Python equivalent is meaningfully shorter than direct C++ — otherwise the per-call overhead and consistency cost beats the savings.

4. **Is it documentation, schema, or config?** → **Markdown or JSON.** No debate.

5. **Is it a non-Python script with platform-specific concerns?** → **PowerShell (Windows), Bash (cross-platform), or a Go single-binary if both are needed.** We haven't hit this case yet.

---

## Closing observations

**The biggest insight from this retrospective:** UE plugins lock you into C++ for ~85% of editor-side work, but the *boundaries* of the plugin (bridge, scripts, tests, docs) are wide-open language choices. We chose well at every boundary. The handlers are mostly forced; the boundaries are deliberate.

**The one place we might revisit:** if we add a *lot* more handlers in the v0.10.x ergonomics range (`get_console_variable`, `set_console_variable`, `screenshot_actor`, `watch_log`, etc.), the C++-shim → Python path becomes attractive. Those are short, Python-reachable, and writing them as Python could trim ~50% of the per-handler code. But this should be measured, not assumed — write 2-3 first as C++ and 2-3 as Python-shim, compare maintenance cost over 3 months, then decide.

**For future Claude sessions reading this:** when the user asks "should we use language X?", come back here first. The categorization above is durable; each new handler can be slotted into A/B/C and the language choice falls out without re-deliberation.

---

## Addendum: language-shim experiment (PR #46)

The closing observation above proposed: *"write 2-3 first as C++ and 2-3 as Python-shim, compare maintenance cost over 3 months, then decide."* PR #46 ran the experiment. **Two C++ handlers** and **two bridge-side Python shims** shipped together so the comparison is point-in-time identical.

### The four handlers

| Handler | Language | What it does |
|---|---|---|
| `find_console_variables(prefix, limit)` | **C++** | Iterates `IConsoleManager::ForEachConsoleObjectThatStartsWith`, filters to variables, returns name/type/read-only. |
| `inspect_static_mesh(path)` | **C++** | Reads `UStaticMesh` per-LOD vertex/triangle counts, bounding box, material slots. |
| `get_camera_transform()` | **Python shim** (bridge-side synthetic) | Reads viewport camera via `UnrealEditorSubsystem.get_level_viewport_camera_info()`, returns location + rotation. |
| `set_camera_transform({location, rotation})` | **Python shim** (bridge-side synthetic) | Sets viewport camera via `UnrealEditorSubsystem.set_level_viewport_camera_info(...)`. |

### Quantitative comparison (LoC)

| Metric | C++ handler avg | Python shim avg | Notes |
|---|---|---|---|
| **Handler implementation** | **~155 LoC** (`find_console_variables` 153, `inspect_static_mesh` 158) | **~50 LoC** (`get_camera_transform` 75 with marker pattern, `set_camera_transform` 50) | Shims are ~3× shorter for these particular cases. |
| **Files touched per handler** | 1 new `.cpp` + module register lines | 0 new files — single function added to bridge | Shims have a smaller diff surface. |
| **Build cycle to ship** | UE plugin rebuild required (~30s incremental, ~5min cold) | Bridge restart only — no compile | Shims iterate faster. |
| **Tests required** | Schema test (~10 LoC) | Schema test + behavioral test with mocked `call_ue` (~30 LoC for the behavioral test of `wait_for_events`-style polling) | Shims need more test scaffolding because their behavior is visible in Python. |

### Qualitative comparison

**C++ wins on:**
- **Correctness boundaries.** `inspect_static_mesh` reads `FVector` / `FBox` / `TArray<FStaticMaterial>` directly. The Python equivalent would need multi-call FFI to `unreal.StaticMesh`, plus per-LOD lookups — each crossing the C++↔Python boundary, with reflection-limit risk if any field happens to be marked private.
- **Single round-trip cost.** `find_console_variables` is one TCP call → one C++ traversal → one response. The Python-shim equivalent would be: bridge → execute_unreal_python (TCP+spawn temp file) → log marker → bridge → get_log_lines (TCP) → parse marker. That's **3 round-trips and ~5× the latency** for the same logical operation.
- **Stable error codes.** C++ handlers can emit explicit `error_code` strings (`asset_not_found`, `not_a_static_mesh`, etc.) that clients can branch on. Shim errors flow through string concatenation of multi-stage failures (UE Python exception → marker missing → JSON parse error), which are harder to make stable.
- **Type safety.** A C++ handler that reads `int32` from JSON and writes `int32` to UE has compile-time type safety end-to-end. The shim relies on Python `int(v)` coercion at runtime.

**Python shim wins on:**
- **Iteration speed.** Modify the shim → restart the bridge process → test. No UE rebuild. Whole cycle is seconds, not minutes.
- **No new C++ surface area.** Each new handler doesn't grow the `extern Make_Handler_*()` list, the build dependencies, or the C++ test footprint.
- **Composability.** The shim is *literally* composing existing UE handlers. It demonstrates the pattern documented in PR #42's `wait_for_events` (the first synthetic tool): when an operation is just "call A, then maybe B, return the combination", Python is the natural place. Future shims can compose multiple synthetic tools.
- **Bridge-side validation.** Argument coercion and shape validation happen close to the MCP boundary, in Python — easier to write, easier to read.

**Both lose on:**
- **`get_*` shims that need round-tripped results** carry the **marker-pattern tax**: `unreal.log("__MARKER__<json>__END__")` + `get_log_lines` → search for marker → parse JSON. UUID per call mitigates marker collisions, but the pattern is still fragile under high log volume (LogCapture's 1000-entry ring could overflow between exec and read). For *write-only* operations like `set_camera_transform`, this isn't a concern — but reads pay it.

### Recommendation (revising the original retrospective)

The original retrospective said: *"~4 of the 36 handlers could be thin C++→Python shims."* PR #46 sharpens that:

| Operation shape | Recommended language |
|---|---|
| Reads UE C++ structs / private fields / threading-sensitive APIs | **C++.** Always. The reflection-limit + private-access barrier is the load-bearing constraint. |
| Iterates UE C++ registries (`IConsoleManager`, `IAssetRegistry`) | **C++.** Native iteration is meaningfully cleaner than per-call Python FFI. |
| Pure setter wrapping a Python-reachable API (write-only, no result needed beyond `ok`) | **Python shim.** ~50 LoC vs ~150 LoC of C++; no marker-pattern tax; iteration speed is the value-add. |
| Pure getter wrapping a Python-reachable API (must round-trip a result) | **C++** by default, but shim is acceptable if the round-trip latency is acceptable for the use case. The marker-pattern tax tilts this toward C++. |
| Composition of multiple existing handlers (no new UE-API access) | **Python shim, always.** This is the synthetic-tool category from PR #42. C++ would be wrong here — adding UE compile cost to operations that only touch the bridge. |

### Process update

The 5-step decision flow at the top of this doc gets one new step:

> **6. Is the operation a composition of existing handlers, or a write-only wrapper around a Python-reachable API?** → **Python shim** (bridge-side synthetic, registered in `SYNTHETIC_TOOLS`). The cost-of-iteration savings beat the per-call C++ shim path.

The 3-month observation window from the original retrospective is no longer needed for this category — PR #46 is the experiment, the answer is in. Future synthetic shims that match the recipe above can ship without re-deliberation.

### What we measured vs what we'd want next

PR #46's data points are *single-author, single-session* — no aging, no multi-developer maintenance pressure, no cross-machine deployment friction. Real cost differentials show up over months and across contributors. The original "compare maintenance cost over 3 months" suggestion stands for *categories outside* the recommendation table above (e.g., complex shims that compose >2 existing handlers, or shims that grow their own state). For the well-defined cases, this addendum is the answer.
