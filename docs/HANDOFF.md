# Handoff document

Single source of truth for resuming work on UnrealClaudeMCP in a fresh Claude Code session. Read this first; it captures everything carried in the prior session's working memory.

---

## Project at a glance

**What this is:** An Unreal Engine 5.7 plugin + Python bridge that exposes editor automation to MCP clients (Claude Code, etc.) over a localhost TCP socket. The plugin adds a JSON-RPC server inside the editor; each "handler" is one MCP tool (~150 LoC of C++ in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`). The bridge translates between Claude Code's stdio MCP protocol and the plugin's TCP wire format.

**Where it stands:** v0.10.0 ergonomics bundle complete. **36 handlers** live across 7 capability bundles (v0.4.0 advanced property types → v0.5.0 wire framing → v0.6.0 observability → v0.7.0 asset registry → v0.8.0 sequencer → v0.9.0 materials → v0.10.0 ergonomics). v0.10.0 added `run_python_file`, `fix_up_redirectors`, `apply_python_to_selection`, `compile_blueprint`. The autonomy-roadmap **Tier 1** (small, high-leverage) is now fully shipped; Tier 2 (event push, task tracking, persistent Python REPL) is the next strategic move.

---

## Open work + pending verification

**Open PRs:** none. As of end of 2026-05-09 session, every PR opened in the run-up to v0.10.0 has merged (PR #22 through PR #37).

**Recent merges (in v0.10.0 sprint, all 2026-05-09):**

Build-correctness + handler fixes (the verification-gap-closing wave):
- PR #22 — corrected six hallucinated `FMaterialParameterInfo::GetName()` call sites. v0.9.0 had been unbuildable against UE 5.7 since merge until #22 landed.
- PR #23 — `UMovieScene::GetBindings` deprecation fix + `LevelSequenceEditor` `.uplugin` dep.
- PR #27 — JSON keys for `inspect_material_instance` overrides disambiguate non-global parameters as `<Name>:Layer:<Index>` / `<Name>:Blend:<Index>`; global params keep bare `<Name>` (backward-compatible). Closed Gemini's deferred PR #22 finding.
- PR #29 — **recovery PR** for PR #26's content (PR #26 was reported MERGED but its merge commit was orphaned on a deleted base branch). Cherry-picked the original work + addressed Gemini's path-conditional smoke-test labels + transport-error handling in the seeder.
- PR #32 — `fix_up_redirectors` (Tier 1).
- PR #35 — **second recovery PR**: PR #32's merge commit took only its first commit, orphaning `apply_python_to_selection` (#33's content merged into #32's branch) + a Codex P1 fix (`ScanPathsSynchronous` before `GetAssets`). Cherry-picked both onto a fresh branch off main and self-merged.

Tooling + tests (the verification-infra wave):
- PR #25 — `scripts/UnrealClaudeMCP-Editor.psm1` PowerShell module: `Start-UCMCPEditor`, `Stop-UCMCPEditor`, `Wait-UCMCPReady`, `Test-UCMCPHandlers`. Used during every verification cycle since.
- PR #26 / #29 — `scripts/seed_test_project.py` + `examples/smoke_test.py` extended with `--material-instance` and `--sequence` opt-in args. Smoke now exercises 32→36 handlers end-to-end when seeded fixtures are present.
- PR #31 — `run_python_file` (Tier 1).
- PR #36 — `compile_blueprint` (Tier 1).

Docs:
- PR #24 — runbook target name + dev↔host sync step.
- PR #28 — first full HANDOFF revision after live-verification cycle. (Note: PR #24's path-quoting follow-up commit `be98d02` was committed after merge and landed on a closed branch — see closing-notes section. Re-applied in PR #30.)
- PR #30 — corrects PR #28's factual errors caught by Gemini + Codex (line number 647 for `UMaterialInstance::Parent`; squash-merge claim correction; placeholder consistency).
- PR #34 — **grants merge authority to the agent.** Directive #4 now ends with self-merge via `gh pr merge <N> --merge`. Author of this update self-merged 4 PRs (#34, #35, #36, #37) on the same day with zero orphaned-merge incidents.
- PR #37 — `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md`: every tool retrospectively analyzed. Categorizes 36 handlers into "forced C++" (×14), "preferred C++" (×18), "could-be-thin-shim" (×4); evaluates non-handler choices (Python bridge, PowerShell module, etc.); recommends a 5-step language-decision flow for new tools.

**Verification status:** runbook was executed multiple times on 2026-05-09 against UE 5.7.4. Caught PR #22 (real compile error — `error C2039: 'GetName': is not a member of 'FMaterialParameterInfo'`), the two warnings folded into PR #23, and Codex's P1 redirector-scan finding (PR #35). Final state: smoke_test.py passes all 13 sections with the bumped 36-tool assertion. Seeded fixtures (`/Game/SmokeTest_M` + `/Game/SmokeTest_MI` + `/Game/SmokeTest_LS`) exist in the host project. **All 36 handlers verified to register at editor startup.**

**Verification runbook** (6 steps, PowerShell, run on the user's host machine):

1. `cd C:\Users\NINOH\Desktop\UnrealClaudeMCP && git pull origin main`
2. `taskkill /IM UnrealEditor.exe /F` (Live Coding holds the DLL otherwise; safe if UE isn't running). Or, with the module: `Import-Module .\scripts\UnrealClaudeMCP-Editor.psm1; Stop-UCMCPEditor`.
3. **Sync dev plugin → host plugin.** The host project's `Plugins/UnrealClaudeMCP/` may be a plain copy on this machine, in which case it drifts from the dev tree silently. Verify with `Get-Item "<host-project>\Plugins\UnrealClaudeMCP" | Select-Object LinkType` — a `Junction` or `SymbolicLink` value means it auto-tracks; empty means it's a plain copy and you must sync. To sync (always quote both paths — Windows project locations like `C:\Users\<you>\Documents\Unreal Projects\…` contain spaces):
   ```
   robocopy "<repo>\UnrealClaudeMCP" "<host-project>\Plugins\UnrealClaudeMCP" /MIR /XD Binaries Intermediate .vs /NFL /NDL /NJH /NJS /NP
   ```
   Robocopy exit codes 0–7 mean success (1 = files copied, 2 = extras removed by `/MIR`). The `/XD Binaries Intermediate` exclusion preserves the host's UBT cache so step 4 stays incremental.
4. `& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" <HostProjectName>Editor Win64 Development -project="<full path to host .uproject>"` — must end with `Result: Succeeded`. **The target name is `<HostProjectName>Editor`, NOT `<PluginName>Editor`** — UE targets are project-level, not plugin-level. For the canonical `UnrealClaudeMCPTest` host project, that's `UnrealClaudeMCPTestEditor`.
5. Open the host `.uproject` in UE editor; confirm **36 handlers** register in the Output Log. Filter by category `LogUCMCPHandler` and you should see exactly 36 lines `Registered handler '<name>'`. The TCP server then binds `127.0.0.1:18888` (~10s on warm DDC, 1–5 min cold). With the module: `$proc = Start-UCMCPEditor -ProjectPath "<full path to host .uproject>"; $ready = Wait-UCMCPReady; $check = Test-UCMCPHandlers -LogPath "<host-project>\Saved\Logs\<HostProjectName>.log" -ExpectedCount 36` — exits with `$check.Pass` true on 36/36.
6. **Optional first-time seed** for runtime coverage of v0.8.0 + v0.9.0: `py -3 scripts\seed_test_project.py` (creates `/Game/SmokeTest_M`, `/Game/SmokeTest_MI`, `/Game/SmokeTest_LS`). Then run smoke with the opt-in args:
   ```
   py -3 examples\smoke_test.py --material-instance /Game/SmokeTest_MI --sequence /Game/SmokeTest_LS
   ```
   Without the opt-in args, smoke_test still runs but skips the material/sequencer runtime paths if `/Game/` has no matching assets. Either way, expect 13 sections + `Smoke test complete - all assertions passed.`

The host `.uproject` path varies per user; it lives outside this repo. If the build fails, work backward from the compile error to the most-recent merged spec — most prior defects were spec-level (wrong header path, wrong UE API signature) rather than implementation-level. PR #22's `FMaterialParameterInfo::GetName()` defect is a recent example: the spec referenced a non-existent accessor that compiled in Claude's *understanding* of UE 5.7 but not in any actual UE 5.7 source tree.

---

## Operating directives the user has granted

These are explicit user instructions that override default Claude behavior. They have stayed in force across the entire prior session.

1. **"Do everything"** — autonomous execution. Don't ask permission to proceed; pick a reasonable path and ship it. The user steps in only when they want to redirect.
2. **"Don't get hallucinated"** — every UE 5.7 API claim must be grounded in actual source (`F:/UE_5.7/Engine/Source/...` or `F:/UE_5.7/Engine/Plugins/...`). Cite line numbers in spec/commit messages. Past sessions caught real defects (`TC_BC4`, `TEXTUREGROUP_Bake`, `FStringOutputDevice`) by grounding before committing.
3. **"Use the right tool for the job"** — Python or C++ as fits. Don't dogmatically prefer one. The bridge is Python; the plugin is C++; bespoke per-asset operations route through `execute_unreal_python` rather than getting their own handler.
4. **"After every PR, check codex and gemini comments, then merge yourself"** — both bots review automatically. Standard workflow:
   - Open PR
   - Wait 1–3 minutes for codex (`chatgpt-codex-connector[bot]`) and Gemini (`gemini-code-assist[bot]`) to post
   - `gh api repos/NAJEMWEHBE/UnrealClaudeMCP/pulls/<N>/comments` to fetch inline findings
   - Categorize each finding: **already-fixed** / **valid-and-apply** / **valid-but-better-alternative** / **dismiss-with-rationale**
   - Apply valid findings (or your better alternative) as new commits on the same branch; push and re-verify. **Pushing the fix commit triggers a new review cycle** — wait for bot comments on the new commits (1–3 min) before merging, so the fixes themselves don't go unreviewed.
   - Document deferrals explicitly in the original PR's description
   - **Merge the PR yourself** once `gh pr view <N> --json mergeStateStatus,statusCheckRollup` shows clean state and CI is green. Use `gh pr merge <N> --merge` (real merge commit). The merge-strategy choice itself is stylistic; the load-bearing process rule from PR #24's incident is the *timing* one (verify the PR is still open before pushing follow-ups — never push to a closed branch). The orphaned-merge incidents from PR #26 and PR #32 add the corollary: prefer `gh pr merge` over the GitHub UI's merge button so the merge is authoritative against the *current* branch tip rather than a cached one that may miss late commits. When your judgment differs from a bot suggestion and you have source-grounded reasoning, your opinion wins (per the user's directive: *"if you find your opinion is better than them or suitable or more honest and efficient, go with your opinion"*).
5. **"Make them all"** (used when committing to a roadmap) — when the user authorizes a multi-bundle plan, push through all of them rather than splitting up the commitment.
6. **"Close UE editor after every test unit"** (added 2026-05-09) — never leave UE editor running across test cycles or builds. UE's Live Coding holds the plugin DLL lock and blocks UBT (`Unable to build while Live Coding is active`). One build failure on 2026-05-09 was directly caused by a UE editor left running between tests; the directive eliminates this failure mode. With the module: `Stop-UCMCPEditor` after every `Test-UCMCPHandlers` / smoke run / live test. The `Start → Wait → Test → Stop` pattern is the canonical test cycle.

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
| `UMaterialInstanceConstantFactoryNew::InitialParent` is declared as a bare `UPROPERTY()` without `EditAnywhere` or `BlueprintReadWrite` (`MaterialInstanceConstantFactoryNew.h:19`), so it is **not** reachable via Python's `set_editor_property` — `mi_factory.set_editor_property("initial_parent", parent)` fails with "Failed to find property 'initial_parent'". | Skip the factory's `InitialParent`; create the MI without a parent, then set the parent on `UMaterialInstance::Parent` (`MaterialInstance.h:647`, `EditAnywhere`/`BlueprintReadOnly` at line 646) post-creation. See `scripts/seed_test_project.py` for the working pattern. |
| `FPythonCommandEx::ExecuteFile` mode does not capture script stdout / eval-result back through `CommandResult` (which always shows `"None"` for file-mode runs); `EvaluateStatement` mode captures only the last expression's value. | For Python-script results that need to round-trip back to the bridge, emit a marker via `unreal.log("__MARKER__<json>__END__")` and retrieve through `get_log_lines` with `category_filter: "LogPython"`. Use a per-run UUID token in the marker to disambiguate from stale ring-buffer entries. |

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
      Handler_*.cpp                            36 handlers (v0.10.0 added run_python_file,
                                               fix_up_redirectors, apply_python_to_selection,
                                               compile_blueprint -- the Tier 1 ergonomics bundle)
    UnrealClaudeMCP.Build.cs                   Module deps (added MaterialEditor in v0.9.0; LevelSequence + MovieScene + MovieSceneTracks + LevelSequenceEditor in v0.8.0)
  Resources/mcp_manifest.json                  Tool catalog (mirrors bridge TOOLS)
  UnrealClaudeMCP.uplugin                      Plugin manifest

bridge/
  unreal_claude_mcp_bridge.py                  stdio↔TCP bridge for Claude Code MCP
                                               Wire format: 8-byte big-endian length prefix + UTF-8 body

examples/
  smoke_test.py                                Live integration smoke test against running editor
                                               (--material-instance / --sequence opt-in args added in PR #26;
                                               36-tool count assertion as of PR #36)
  .mcp.json.example                            Template Claude Code MCP config
  hello_run_python_file.py                     Test fixture for run_python_file (PR #31)

scripts/                                       Orchestration scripts (introduced 2026-05-09)
  UnrealClaudeMCP-Editor.psm1                  PowerShell module for editor lifecycle
                                               (Start/Stop/Wait/Test functions; PR #25)
  seed_test_project.py                         Idempotent seeder for /Game/SmokeTest_*
                                               throwaway assets (PR #26)

.mcp.json (gitignored)                         Local Claude Code MCP config; points at
                                               bridge/unreal_claude_mcp_bridge.py. Created
                                               2026-05-09 on this machine. When you launch
                                               Claude Code from the repo root and the editor is
                                               open, all 36 tools become first-class MCP tools.
                                               Template at examples/.mcp.json.example for new
                                               machines.

tests/
  test_bridge.py                               Bridge MCP protocol + schema tests
  test_bridge_edge_cases.py                    TCP error paths
  test_manifest_sync.py                        Drift detection between bridge TOOLS and manifest

docs/
  TOOLS.md                                     Per-tool params/results/examples (mirror of catalog;
                                               36 sections as of PR #36)
  ARCHITECTURE.md                              How pieces fit; UE 5.7 API gotchas
  INSTALLATION.md                              Step-by-step install
  HANDOFF.md                                   This file
  LANGUAGE-CHOICE-RETROSPECTIVE.md             Per-tool language verdict (C++ vs Python vs PowerShell
                                               vs alternatives); 5-step decision flow for new tools;
                                               PR #37
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
- *"Continue from where we left off — Tier 2 of the autonomy roadmap"* → editor event push (UE → Claude callbacks), long-running task tracking, persistent Python REPL. **This is the recommended next move.** Tier 1 complete.
- *"Finish Tier 1 — add `get_console_variable` / `set_console_variable`"* → only Tier 1 handler remaining. Small (~120 LoC paired). Closes the v0.10.0 ergonomics surface fully.
- *"Continue from option 3 (keyframe authoring) per `docs/HANDOFF.md`"* → start the deferred keyframe bundle (HANDOFF "Deferred work" section)
- *"Pick a net-new idea from `docs/HANDOFF.md` section 'Net-new ideas worth considering' that you think is highest leverage and implement it"* → freer hand
- *"Run the language-shim experiment from PR #37"* → write the next 4-6 ergonomic handlers as 2-3 C++ + 2-3 thin C++→Python shims. Compare maintenance cost over time. See `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md` for the rationale.

---

## Autonomy roadmap (2026-05-09 brainstorm)

Surfaces beyond the current 36 handlers that would meaningfully expand "Claude → Unreal" autonomy.

**Tier 1 status (ergonomic wins — 4 of 5 done):**
- ✅ `run_python_file(path)` — PR #31 merged
- ✅ `fix_up_redirectors(path)` — PR #32 merged (with PR #35 Codex P1 fix)
- ✅ `apply_python_to_selection(code)` — recovered via PR #35
- ✅ `compile_blueprint(path)` — PR #36 merged
- ⏳ `get_console_variable` / `set_console_variable` — last Tier 1 sibling, not yet started

**Tier 2 status (autonomy multipliers — none started):**
- ⏳ Editor event push (UE → Claude callbacks via WebSocket / bidirectional TCP)
- ⏳ Long-running task tracking (start/poll/cancel for cooks, packages, MRQ renders)
- ⏳ Persistent Python REPL (state across calls)

**Tier 3 status (coverage expansion — none started):**
- ⏳ Asset diff tool, multi-editor coordination, Niagara/Animation/Landscape openers, workspace state save/restore, watch_log, build farm integration, automation tests

The full roadmap entries (with what-and-why and implementation shape) are below. Tier 1 entries marked DONE here can be cross-referenced against the merged PRs.

### Editor event push (UE → Claude callbacks)

Today the bridge is request-response: Claude polls / asks. A complement: a notification channel that pushes editor events back to a registered listener. UE has `FEditorDelegates::OnAssetPostImport`, `FCoreDelegates::OnActorSpawned`, `FEditorDelegates::SaveWorld`, etc. — all natural pubsub points. Implementation shape: a new TCP endpoint (or upgrade to bidirectional WebSocket) that streams JSON events as they fire. Filter via subscribe params (`{"events": ["actor_spawned", "asset_imported"]}`). Lets Claude react ("user just dropped a chair into the level — let me reposition the camera") rather than only reacting to user prompts.

### Long-running task tracking

Cooks, packaging, MRQ renders, lightmap bakes — all minutes-to-hours, all currently invisible. A `start_task(...)` / `poll_task(task_id)` / `cancel_task(task_id)` triple plus an event-push integration would let Claude kick off work, get notified on completion, and surface progress. Backed by `FRunnableThread` or `FAsyncTaskNotificationFactory`.

### Workspace state save/restore

`save_workspace(name)` snapshots open editor tabs, viewport bookmarks, content browser path, and selection; `load_workspace(name)` restores. Lets Claude set up a "Material editor for SmokeTest_M with content browser at /Game/" context for a user with one call. UE 5 has `UEditorPerProjectUserSettings` and `FViewportLayout` — both reachable.

### `run_python_file(path)` + `apply_python_to_selection(code)`

From the existing deferred list. The first solves escaping pain (Python strings inside JSON inside Python is brittle); the second exposes selected actors as a `selection` variable so per-actor scripts are one-liners. Small, high-leverage.

### Multi-editor coordination

One Claude session driving multiple UE editors at once (`-port 18888`, `-port 18889`, …). Useful for cross-project asset migrations or A/B testing material changes. Today's bridge is hard-coded to one server; making the host/port configurable per request lets one Claude orchestrate N editors.

### Asset diff tool

`diff_asset(path_a, path_b)` returns a structural delta between two assets — Materials with which expressions added/removed, MIs with which override values changed, BPs with which nodes/connections changed. Backed by `FAssetData` + per-class differ. Critical for "what changed since last commit?" workflows.

### Project-wide refactoring

`rename_class_references(old, new)` — cascade a class rename across all BPs / Widget BPs / Materials that reference it. Backed by `FAssetRegistry` + `IAssetTools::FindReferencers`. Today you'd `execute_unreal_python` it manually, every time.

### Testing framework hooks

UE has a built-in automation framework (`FAutomationTestFramework`). `run_automation_tests(filter)` triggers a subset, returns pass/fail. Lets Claude run "all my tests" before claiming a feature done.

### Build farm integration

`cook_project(target)` / `package_project(platform, config)` — wraps `RunUAT.bat BuildCookRun`. Lets Claude validate "does this still cook for shipping config?" before merging. Long-running, so pairs with the task-tracking primitive above.

### Niagara / Animation / Landscape openers

These three subsystems are currently opaque. Even a basic `inspect_niagara_system(path)` / `inspect_anim_blueprint(path)` / `inspect_landscape(path)` would unblock a lot of "what's in this asset" questions. Each is its own bundle; pick by user demand.

### Persistent Python REPL

`exec_python_persistent(code)` keeps state across calls (vs. current `execute_unreal_python` which creates a fresh module each call). Lets Claude build up state — load an asset, manipulate it across several turns, save at the end — without re-loading every time. Backed by `IPythonScriptPlugin::ExecPythonCommandEx` with `EvaluateStatement` mode and a persistent globals dict.

### `watch_log(category, regex, timeout)` + `tail_log_lines`

Subscribe to log lines matching a pattern and block until match (with timeout). Useful for "I just kicked off a long thing, tell me when it logs success." `tail_log_lines` (vs. current `get_log_lines` which is bounded) is a streaming variant.

### `fix_up_redirectors(path)` + `compile_blueprint(path)`

From the existing deferred list — both small wraps of UE editor utilities (`IAssetTools::FixupReferencers`, `FKismetEditorUtilities::CompileBlueprint`).

---

## Closing notes from prior sessions

**Session 2026-05-09 (verification cycle + tooling expansion):** Executed the docs/HANDOFF.md runbook end-to-end against UE 5.7.4 for the first time since v0.5.0. Surfaced a real defect (PR #22's `FMaterialParameterInfo::GetName()` hallucination) plus two warnings (PR #23). After triaging Gemini's deferred finding and addressing it, opened three follow-on PRs for tooling: #25 (PowerShell editor lifecycle module), #26 (smoke-test seeder + opt-in args), #27 (layered/blended parameter key disambiguation). PR #26 was nominally merged but its merge commit was orphaned on a deleted base branch — recovered as PR #29. The session also revealed an environmental fact worth recording: **the host project's `Plugins/UnrealClaudeMCP/` was a plain copy, not a junction** — silently 5 bundles stale before the runbook's first execution. The runbook now mandates a sync step.

**Stacked-PR pitfall observed:** when stacking PR B on PR A's branch, retargeting B to `main` after A merges via `gh pr edit B --base main` does not always take effect before a subsequent `merge` action on B. If the user merges B before the retarget propagates, GitHub merges into the *original* base — and if that base branch has been deleted (typical post-merge cleanup), the merge commit becomes unreachable from any current branch, even though `gh pr view B` reports MERGED. **Mitigation:** verify the retarget with `gh pr view B --json baseRefName` immediately before any merge; if mid-flight, prefer rebasing B onto current `main` and pushing fresh rather than relying on auto-retarget. PR #26 → #29 was the worked example.

**What worked:** vertical-slice tasks per handler. Pre-verifying every UE API claim against source before writing. Codex + Gemini reviews catching real bugs that pytest couldn't (the post-verify lesson from PR #18→#19 is a particularly good one). Stable error-code prefixes that compose across bundles. Stacked PRs for prerequisite-dependent work (PR #26 stacked on #25 worked cleanly). Dogfooding the new module during the very PR that introduced it.

**What to watch:** my own confidence about UE 5.7 APIs is calibrated against headers, not against actual builds. The 2026-05-09 session shipped substantial code that's still pending live verification on the layered/blended path. If a build error surfaces, work backward from the compile message to the spec — most defects I caught were spec-level (wrong type, wrong header path) rather than implementation-level. **PR #24's path-quoting commit `be98d02` never reached main** — but not for the reason I originally wrote here. The merge was a real merge commit (not a squash), and `be98d02` was committed *after* the merge already happened, so it landed on a now-closed branch. **Real lesson:** before pushing a follow-up commit to address PR review feedback, verify the PR is still open (`gh pr view N --json state`) — if merged, open a new PR instead. Pushing to a merged-and-deleted branch silently loses work.

**The user's working style:** fast merge cycles (sometimes merging while I'm still composing the post-PR summary). Direct preferences. Doesn't fault deferrals if they're explicit. Values honesty about what's verified vs. what's just-shipped. Recently broadened the "right tool for the job" directive: when picking a language for new tooling, deliberate explicitly (Python vs. PowerShell vs. Go vs. Rust) and surface the reasoning rather than defaulting to what's adjacent. **As of 2026-05-09 the user has granted the agent merge authority** — open PR, address codex+gemini findings, then `gh pr merge <N> --merge` self-merge (see directive #4).

---

**Session 2026-05-09 (continued — Tier 1 ergonomics + autonomy directive):** Built and merged the v0.10.0 ergonomics bundle (4 of 5 Tier 1 handlers). Encoded the merge-authority directive in PR #34 and exercised it on PRs #35, #36, #37 with zero orphaned-merge incidents — proves `gh pr merge --merge` is the authoritative path that prevents the cached-branch-tip issue that bit PR #26 and PR #32. Recovered PR #32's lost commits (Codex P1 fix + apply_python_to_selection content) via PR #35. Shipped `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md` (PR #37) — every tool retrospectively analyzed for language fitness; 5-step decision flow for new tools.

**Lessons newly recorded:**
- **"Close UE editor after every test unit"** (directive #6 added). One build failure in this session was caused by Live Coding holding the DLL lock on a left-running editor. Eliminated by following the directive.
- **`.mcp.json` deployed locally** (gitignored at repo root, points at `bridge/unreal_claude_mcp_bridge.py`). Future sessions launching Claude Code from this directory get all 36 tools as first-class MCP tools, no raw TCP needed.
- **Self-merge authority works.** 4 PRs self-merged in a single session, all clean. The pattern: open → wait 1-3 min for codex+gemini → triage → push fix if needed → wait again → `gh pr merge <N> --merge`. Future sessions inherit this protocol from directive #4.

**What's queued for the next session (in priority-by-leverage order):**
1. **Last Tier 1 handler:** `get_console_variable` / `set_console_variable` (paired CVar handlers, ~120 LoC combined). Closes Tier 1 fully.
2. **Tier 2 entrypoint:** Editor event push (UE → Claude callbacks). Substantial design (WebSocket or bidirectional TCP, JSON event schema, subscription management) — this is the autonomy multiplier that turns request-response into pubsub.
3. **Language-shim experiment:** PR #37's recommendation. Write the next 4 ergonomic handlers as 2 C++ + 2 thin C++→Python shims. Compare maintenance cost.
4. **Bundle MIN tip:** The user's mental model is "small focused PRs that self-merge cleanly." Don't queue more than 2-3 in flight; each one open longer than ~10 min risks losing context. Tier 2's event-push will be a multi-PR sequence; plan it like the v0.7.0 sequencer bundle was planned (spec → design → implement vertically).
