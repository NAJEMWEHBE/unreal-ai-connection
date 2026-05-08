# Handoff document

Single source of truth for resuming work on UnrealClaudeMCP in a fresh Claude Code session. Read this first; it captures everything carried in the prior session's working memory.

---

## Project at a glance

**What this is:** An Unreal Engine 5.7 plugin + Python bridge that exposes editor automation to MCP clients (Claude Code, etc.) over a localhost TCP socket. The plugin adds a JSON-RPC server inside the editor; each "handler" is one MCP tool (~150 LoC of C++ in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`). The bridge translates between Claude Code's stdio MCP protocol and the plugin's TCP wire format.

**Where it stands:** v0.9.0 shipped. **32 handlers** live across 6 capability bundles (v0.4.0 advanced property types → v0.5.0 wire framing → v0.6.0 observability → v0.7.0 asset registry → v0.8.0 sequencer → v0.9.0 materials). Plus v0.9.1 — a wire-framing partial-message state-machine fix — is in flight.

---

## Open work + pending verification

**No open PRs at this time** — the user has merged everything from v0.4.0 through the v0.9.1 wire-framing fix (PRs #11–#20 all merged).

**Critical verification gap:** all merged work since v0.5.0 has been validated via 98/98 pytest (the bridge unit tests) only. The C++ side has not been built and smoke-tested in the user's actual UE editor for the current tip of main. Any "shipped" claim in this doc means exactly that — the code compiles in Claude's understanding of UE 5.7 source headers, but no live binary verification yet on the user's machine.

**Verification runbook** (5 steps, PowerShell, run on the user's host machine):
1. `cd C:\Users\NINOH\Desktop\UnrealClaudeMCP && git pull origin main`
2. `taskkill /IM UnrealEditor.exe /F` (Live Coding holds the DLL otherwise; safe if UE isn't running)
3. `& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" UnrealClaudeMCPEditor Win64 Development -project="<full path to host .uproject>"` — must end with `BUILD SUCCESSFUL`
4. Open the host `.uproject` in UE editor; confirm 32 handlers register in the Output Log
5. `py -3 examples\smoke_test.py` from the repo root; expect 13 sections + `Smoke test complete - all assertions passed.`

The host `.uproject` path varies per user; it lives outside this repo. If the build fails, work backward from the compile error to the most-recent merged spec — most prior defects were spec-level (wrong header path, wrong UE API signature) rather than implementation-level.

---

## Operating directives the user has granted

These are explicit user instructions that override default Claude behavior. They have stayed in force across the entire prior session.

1. **"Do everything"** — autonomous execution. Don't ask permission to proceed; pick a reasonable path and ship it. The user steps in only when they want to redirect.
2. **"Don't get hallucinated"** — every UE 5.7 API claim must be grounded in actual source (`F:/UE_5.7/Engine/Source/...` or `F:/UE_5.7/Engine/Plugins/...`). Cite line numbers in spec/commit messages. Past sessions caught real defects (`TC_BC4`, `TEXTUREGROUP_Bake`, `FStringOutputDevice`) by grounding before committing.
3. **"Use the right tool for the job"** — Python or C++ as fits. Don't dogmatically prefer one. The bridge is Python; the plugin is C++; bespoke per-asset operations route through `execute_unreal_python` rather than getting their own handler.
4. **"After every PR, check codex and gemini comments"** — both bots review automatically. Standard workflow:
   - Open PR
   - Wait 1–3 minutes for codex (`chatgpt-codex-connector[bot]`) and Gemini (`gemini-code-assist[bot]`) to post
   - `gh api repos/NAJEMWEHBE/UnrealClaudeMCP/pulls/<N>/comments` to fetch inline findings
   - Categorize each finding: **already-fixed** / **valid-and-apply** / **valid-but-better-alternative** / **dismiss-with-rationale**
   - Apply valid findings (or your better alternative) on a follow-up branch + PR
   - Document deferrals explicitly in the original PR's description
5. **"Make them all"** (used when committing to a roadmap) — when the user authorizes a multi-bundle plan, push through all of them rather than splitting up the commitment.

---

## Established conventions (hard-won, do not relitigate)

### Error format

Every handler's `OutError` follows: `<tool>: <error_code>: <human-readable detail>`.

The `<error_code>` portion is a **stable parseable token** clients can branch on. Established codes (reusable across handlers): `missing_required_field`, `missing_params`, `asset_not_found`, `invalid_path`, `invalid_asset_name`, `dest_exists`, `create_failed`, `save_failed`, `actor_not_found`, `ambiguous_actor`, `not_a_sequence`, `not_a_material`, `not_a_material_instance`, `parameter_not_applied`, `has_referencers`, `delete_failed`, `rename_failed`, `unknown_enum_value`, `invalid_value_shape`, `invalid_value_type`, `invalid_tag_value`.

### UE 5.7 traps already mapped

These are the bugs that bit prior sessions. Don't re-discover them.

| Trap | What to do |
|---|---|
| `FOutputDevice` subclasses default to `CanBeUsedOnAnyThread() = false`, which routes log dispatch through GLog's serializing queue and stalls the game thread under load | Always override to `return true`. See `LogCapture.h`. |
| `FOutputDevice::Serialize` has both 3-arg and 4-arg variants; UE 5.7's pure virtual is 3-arg | Implement the 3-arg signature. |
| `ELogVerbosity::Type` packs flag bits (`SetColor`, `BreakOnLog`) in the upper byte | Mask with `ELogVerbosity::VerbosityMask` (= `0xf`) before switching. |
| `FPackageName::GetAssetPackageExtension()` returns `.uasset` only — wrong for `UWorld` levels (`.umap`) | Use `FPackageName::DoesPackageExist(PackagePath, &OutFilename)` which auto-resolves. |
| `UEditorAssetLibrary::DeleteAsset` is documented as a force-delete; no built-in referencer check | Run `IAssetRegistry::GetReferencers` first. |
| `UMaterialEditingLibrary::SetMaterialInstance*ParameterValue`'s bool return is unreliable across UE versions (false on success in some, false on legitimate failure in others) | Combine **pre-verify** (`Get<Type>ParameterNames` to catch typos) **+ post-verify** (scan `MIC->{Scalar,Vector,Texture}ParameterValues` array to confirm the override landed). Ignore the bool. |
| `GEngine->Exec` returns false on unrecognized commands | Capture and propagate as `command_execution_failed`. |
| `UEditorAssetLibrary::SaveAsset` returns false on SCC checkout failure or read-only file | Capture and propagate as `save_failed` with explicit "created in memory but not persisted to disk" wording. |
| Non-blocking sockets return `BytesRead == 0` for "no data right now," NOT for "disconnect" | Disambiguate via `ISocketSubsystem::Get()->GetLastErrorCode() == SE_EWOULDBLOCK`. See v0.9.1's `MCPServer.cpp`. |
| `Helper.AddDefaultValue_Invalid_NeedsRehash` for TSet/TMap leaves the container in invalid state on early return | Always `EmptyElements()` + `Rehash()` on error paths. |
| `EmptyAndAddUninitializedValues` for TArray leaves slots uninitialized on mid-loop early return → UB | Pre-initialize every slot via `Inner->InitializeValue` before the coercion loop. |

### Vertical-slice task decomposition

When implementing a bundle, each task is one self-contained vertical slice that ends with a green commit:
1. Create `Handler_<Name>.cpp` + register in `UnrealClaudeMCPModule.cpp`
2. Add bridge `TOOLS` entry in `bridge/unreal_claude_mcp_bridge.py`
3. Add manifest entry in `UnrealClaudeMCP/Resources/mcp_manifest.json`
4. Add bridge schema test in `tests/test_bridge.py`
5. Bump count assertion in `test_bridge.py` (twice — `test_tools_list_has_*` and `test_handle_tools_list_returns_all_tools`) and `test_manifest_sync.py`
6. Add `## <name>` section in `docs/TOOLS.md`
7. Run `py -3 -m pytest tests/ -q` — must be green
8. Commit

### Manifest sync trap

`test_manifest_sync.py` substring-searches for the word "required" in manifest param descriptions to validate against the bridge's `required[]` list. Any phrase like `"required-value"` or `"...required for X..."` in a param description trips a false positive. Phrase optional fields without the word "required" appearing.

### Spec → plan → implementation flow

Every bundle follows this sequence (driven by the `superpowers:brainstorming` and `superpowers:writing-plans` skills):
1. Verify UE 5.7 APIs against source headers
2. Consider 2-3 approaches; pick one with rationale
3. Write spec to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
4. Spec self-review (placeholders / consistency / scope / ambiguity)
5. (For larger bundles) Write plan to `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`
6. Implement task by task, green pytest after each
7. Push, open PR, address codex + Gemini findings

In autonomy mode, the brainstorming skill's "user approves design" gate is skipped — the spec stands as the design contract.

---

## Repository file map

```
UnrealClaudeMCP/                               UE plugin (drops into <Project>/Plugins/)
  Source/UnrealClaudeMCP/
    Public/MCP/MCPServer.h                     TCP server header (per-client state structs as of v0.9.1)
    Private/MCP/MCPServer.cpp                  TCP server impl (state-machine framing as of v0.9.1)
    Private/MCP/MCPDispatcher.cpp              Method dispatcher
    Private/MCP/MCPHandler.h                   IUCMCPHandler interface + registry
    Private/MCP/LogCapture.{h,cpp}             FOutputDevice ring buffer for get_log_lines
    Private/MCP/PropertyCoercion.{h,cpp}       JSON ↔ FProperty coercion (v0.4.0 advanced types)
    Private/MCP/ActorIdentity.{h,cpp}          Hybrid label-or-FName actor lookup
    Private/MCP/Handlers/                      One file per handler, ~150 LoC each
      AssetPathUtil.h                          Shared path normalization helpers (v0.7.0)
      Handler_*.cpp                            32 handlers
    UnrealClaudeMCP.Build.cs                   Module deps (added MaterialEditor in v0.9.0; LevelSequence + MovieScene + MovieSceneTracks + LevelSequenceEditor in v0.8.0)
  Resources/mcp_manifest.json                  Tool catalog (mirrors bridge TOOLS)
  UnrealClaudeMCP.uplugin                      Plugin manifest

bridge/
  unreal_claude_mcp_bridge.py                  stdio↔TCP bridge for Claude Code MCP
                                               Wire format: 8-byte big-endian length prefix + UTF-8 body

examples/
  smoke_test.py                                Live integration smoke test against running editor
  .mcp.json.example                            Template Claude Code MCP config

tests/
  test_bridge.py                               Bridge MCP protocol + schema tests
  test_bridge_edge_cases.py                    TCP error paths
  test_manifest_sync.py                        Drift detection between bridge TOOLS and manifest

docs/
  TOOLS.md                                     Per-tool params/results/examples (mirror of catalog)
  ARCHITECTURE.md                              How pieces fit; UE 5.7 API gotchas
  INSTALLATION.md                              Step-by-step install
  HANDOFF.md                                   This file
  superpowers/specs/                           Design specs per bundle, dated
  superpowers/plans/                           Implementation plans per bundle, dated
```

---

## Deferred work (queued for future bundles)

These are real items the user has either explicitly deferred or are obvious follow-ups. **None are committed to** — the user picks the priority.

### Keyframe authoring (sequencer follow-up — was "Option 3" in the prior session)

Goal: let the LLM animate things in a `ULevelSequence`, not just create empty cinematics.

Minimum useful surface: 1 handler `set_transform_keyframe(sequence_path, binding_guid, frame, location, rotation)` that adds a keyframe to a `UMovieScene3DTransformTrack`'s position / rotation channels.

Scope estimate: ~600–800 LoC. Comparable to v0.7.0 (5 handlers / ~860 LoC).

Risk: HIGH. UE's channel APIs are heavily templated:
- `UMovieScene3DTransformTrack::CreateNewSection` returns `UMovieSceneSection*`
- `UMovieScene3DTransformSection::GetChannelProxy()` returns a `FMovieSceneChannelProxy&`
- Per-axis channels: `Proxy.GetChannel<FMovieSceneFloatChannel>("Location.X")` etc., or `GetChannelByName` for stable lookup
- `FMovieSceneFloatChannel::AddCubicKey(FrameNumber, Value, ERichCurveTangentMode, FMovieSceneTangentData)` — interpolation mode handling is its own complexity axis
- Section bounds: must call `Section->SetRange(TRange<FFrameNumber>::Inclusive(0, N))` to be playable
- `FKeyHandle` returned by AddKey is opaque; for retrieval, iterate `Channel.GetData().GetTimes()`

Pre-implementation steps for the next session:
1. Grep `F:/UE_5.7/Engine/Source/Runtime/MovieSceneTracks/Public/Sections/MovieScene3DTransformSection.h` for the actual channel layout
2. Check `MovieSceneTracks/Public/Channels/MovieSceneFloatChannel.h` for `AddCubicKey` / `AddLinearKey` signatures
3. Verify per-axis channel name strings (`"Location.X"` may have changed in 5.7)

Apply the same pre-verify + post-verify pattern: confirm the keyframe is in the channel's time/value arrays after the call, don't trust whatever bool the AddKey API returns.

### Material-graph editing (materials follow-up)

Goal: let the LLM author new materials from `UMaterialExpression` nodes (color → multiply → output), not just override instance parameters.

Scope: huge. UE has 200+ `UMaterialExpression` subclasses. Plausible decomposition into 3-4 sub-bundles (constant nodes / scalar ops / texture sampling / output binding). Each sub-bundle ~600 LoC.

This is the deferred item with the most uncertainty — defer until concrete user demand.

### Movie Render Queue

Goal: render a `ULevelSequence` to a video file.

Scope: distinct subsystem (`MovieRenderPipelineCore`, `MovieRenderPipelineSettings`). Single handler `render_sequence_to_disk(sequence_path, output_path, preset_name)` is the minimum useful surface. ~300 LoC plus a config preset.

UE 5.7 entry point: `UMoviePipelineQueueEngineSubsystem::RenderQueueWithExecutor`.

### Smaller items

- **Spawnable bindings** (sequencer): `bind_spawnable_to_sequence` — needs a template object passed in. v0.8.x.
- **Bulk asset operations**: `bulk_delete_assets`, `bulk_move_assets` — partial-success error handling. v0.7.x.
- **Static-switch parameter mutation**: `SetMaterialInstanceStaticSwitchParameterValue` triggers shader recompiles; out of v0.9.0 scope. v0.9.x.
- **Material function instances + layer/blend parameters**: `EMaterialParameterAssociation::LayerParameter` / `BlendParameter`. v0.9.x.

---

## Net-new ideas worth considering (not deferred — proposed)

These are workflows the current 32 tools don't cover. Pick whichever looks most useful before committing.

1. **`run_python_file(path)`** — like `execute_unreal_python` but reads from a file instead of taking the source as a string parameter. Avoids escaping pain for any non-trivial script.
2. **`apply_python_to_selection(code)`** — convenience handler that exposes the currently-selected actors to the script as a `selection` variable. Cuts a lot of `unreal.EditorLevelLibrary.get_selected_level_actors()` boilerplate.
3. **`watch_log(category, regex, timeout)`** — wait synchronously for a specific log line. Useful for "I just kicked off a long-running thing, tell me when it's done."
4. **`compile_blueprint(path)`** — explicit Blueprint recompile + validation. Currently `edit_widget_tree` does this implicitly with `compile=true` but there's no general handler.
5. **`fix_up_redirectors(path)`** — wrap `IAssetTools::FixupReferencers` to clean up the redirectors that `move_asset` and `rename_asset` leave behind.
6. **`get_console_variable(name)` / `set_console_variable(name, value)`** — first-class CVar handling, cleaner than `execute_console_command`.
7. **`screenshot_actor(actor_name)`** — frame the viewport on an actor and capture a focused thumbnail. Useful for asset-pipeline doc generation.

---

## How to resume in a fresh session

1. Open a new Claude Code session in the same repo.
2. Send: *"Read `docs/HANDOFF.md` and continue from there. The user is in autonomy mode — pick the next reasonable thing to do."*
3. The fresh session reads this doc (~6 KB), absorbs the operating directives, sees what's open, and proceeds.

For specific resumption:
- *"Continue from option 3 (keyframe authoring) per `docs/HANDOFF.md`"* → start the keyframe bundle
- *"Verify open PRs first (#19, #20) before any new work"* → defer new bundles until smoke runs
- *"Pick a net-new idea from `docs/HANDOFF.md` section 'Net-new ideas worth considering' that you think is highest leverage and implement it"* → freer hand

---

## Closing notes from the prior session

What worked: vertical-slice tasks per handler. Pre-verifying every UE API claim against source before writing. Codex + Gemini reviews catching real bugs that pytest couldn't (the post-verify lesson from PR #18→#19 is a particularly good one). Stable error-code prefixes that compose across bundles.

What to watch: my own confidence about UE 5.7 APIs is calibrated against headers, not against actual builds. The prior session shipped substantial code that's still pending live verification. If a build error surfaces, work backward from the compile message to the spec — most defects I caught were spec-level (wrong type, wrong header path) rather than implementation-level.

The user's working style: fast merge cycles (sometimes merging while I'm still composing the post-PR summary). Direct preferences. Doesn't fault deferrals if they're explicit. Values honesty about what's verified vs. what's just-shipped.
