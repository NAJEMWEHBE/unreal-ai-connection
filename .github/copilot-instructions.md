# Copilot review instructions — UnrealClaudeMCP

GitHub Copilot, when invoked as a PR reviewer on this repository, should
review under the constraints below. These mirror the conventions already
applied by the human + Codex + Gemini review loop; align with them rather
than re-litigating settled patterns.

## What this repo is

A UE 5.7 plugin + Python bridge exposing editor automation to any MCP-compliant
client (Claude Code, Codex CLI, Cursor, Gemini CLI, Continue, …) over a
localhost TCP socket. **103 tools total: 71 native C++ handlers + 32 bridge-side
synthetic tools.** Vendor-neutral by design — the wire protocol is open MCP.

Top-level read-first: [`docs/HANDOFF.md`](../docs/HANDOFF.md) (resumption context),
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) (UE 5.7 API gotchas),
[`docs/TOOLS.md`](../docs/TOOLS.md) (per-tool JSON examples).

## Review priorities (in order)

1. **Cross-handler consistency.** Every Inspect* handler shares a shape — error
   format, path normalization, asset-loading helper, field-name conventions.
   Catch deviations:
   - Error format: `"<tool>: <stable_code>: <human detail>"` with `'%s'`-quoted
     path values (single-quoted; **not** bare `%s`).
   - Path normalization: `UCMCPAssetPath::ToObjectPath(InputPath)` — never
     reimplement.
   - Asset load: `UEditorAssetLibrary::LoadAsset(ObjectPath)` — never
     `StaticLoadObject` or `LoadObject<T>` (HANDOFF trap-table line 131).
   - Asset references in result fields: `Asset->GetPathName()` — never
     `GetClass()->GetName()` (PR #51 lesson, returns class taxonomy not asset
     identity).
   - Bounds shape: `{min, max, size, center}` across all handlers
     (`inspect_static_mesh` sets the precedent).

2. **UE 5.7 access modifiers.** Direct field access on UE classes is a common
   defect class:
   - Bitfield flags (`uint8 : 1`) use explicit `!= 0` for unambiguous bool
     conversion (PR #70 lesson).
   - **Always check access modifier before direct field read.** Recent
     failures: `USoundCue::SubtitlePriority` is protected, `MaxAudibleDistance`
     is private (PR #76 → cleanup #79). `USoundWave::SampleRate` /
     `ImportedSampleRate` are protected (PR #77 → cleanup #78). Always
     prefer the public `GetX()` accessor method.
   - `WITH_EDITORONLY_DATA` fields must be guarded with `#if WITH_EDITORONLY_DATA`.
   - Forward-declared types tripping `->` access need full include (e.g.
     `Animation/AnimNotifies/AnimNotifyState.h`, not `Animation/AnimNotifyState.h`
     — different subdir; cleanup PR #80 lesson).

3. **Enum-to-string discipline.**
   - When mapping a UE enum to strings, **enumerate the complete value set**
     declared in the enum, not just prevalent ones. The `default` case is for
     forward compat with future-version additions, not a substitute for
     handling current values. PR #52→#53 missed `BS_Error`; PR #67→#68 missed
     `BS_BeingCreated`. Both shipped to main before the gap was found.
   - For verbose UE enums, use `EnumToCleanString<T>` helper (strips
     `Enum::` prefix). See `Handler_InspectSoundWave.cpp:42-53` for the
     canonical implementation.

4. **`TArray<TObjectPtr<>>` null-skip.** Entries can be null after
   deletes/reimports/partial-load states. Iterate with explicit null-check
   `continue`; emit count of valid entries only (PR #55→#57 lesson). Examples:
   `Bindings` / `Animations` / `AllNodes` / `SkeletalBodySetups` /
   `ConstraintSetup` arrays.

5. **Sort container outputs for stable ordering.** TMap and TSet iteration
   order is unspecified. `inspect_widget_blueprint::inherited_slots_with_content`,
   `inspect_data_table::rows`, `inspect_sound_cue::nodes` all sort before
   emission. Diff-friendly JSON for callers.

6. **Bridge-side synthetic tools** (`SYNTHETIC_TOOLS` dict in
   `bridge/unreal_claude_mcp_bridge.py`) are pure Python. They:
   - Compose existing UE handlers via `call_ue` OR shell out to host commands
     (e.g. `compile_mod_pak` shells `RunUAT.bat`).
   - **MUST be added** to: bridge `TOOLS` list, `SYNTHETIC_TOOLS` dict,
     `Resources/mcp_manifest.json` tools array, `tests/test_bridge.py`
     expected-set + count assertion, `tests/test_manifest_sync.py` count
     assertion, `docs/TOOLS.md` section. Missing any of the six → bridge
     test failure or manifest-sync drift.
   - Synthetic tools must **preserve upstream RPC error codes** when wrapping
     `call_ue` failures (don't hardcode `-32603`; propagate
     `upstream_err.get("code", -32603)`). Trap-table line 136.

7. **Cold-compile before merge** (discipline introduced post-recovery
   2026-05-10). Pre-merge pytest validates bridge schema + manifest drift
   only — never compiles C++. PRs that touch handler `.cpp` MUST be
   cold-compiled on a host machine before merge to surface C2027 / C2248 /
   C4996 / C1083 errors that pytest misses. Cleanup PRs #78/#79/#80 caught
   5 latent defects across 4 handlers that had shipped without compile.

8. **Vendor-neutral framing.** Tool descriptions, manifest entries, and
   docs use vendor-neutral language ("the LLM client", "the AI agent").
   Don't bake "Claude Code" specifically into anything that ships. Repo /
   plugin / bridge filenames retain "Claude" for legacy reasons — those
   are decorative.

## Handler shape (one file = one tool)

A new C++ handler is exactly:
- One `Handler_<Name>.cpp` in
  `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/`
- One `class FHandler_<Name> : public IUCMCPHandler` with **EXACTLY ONE**
  `virtual FString GetMethodName() const override` and **EXACTLY ONE**
  `virtual TSharedPtr<FJsonObject> Handle(const TSharedPtr<FJsonObject>& Params, FString& OutError) override`. No method-name variants. No additional Handle overloads.
- One `TSharedRef<IUCMCPHandler> Make_Handler_<Name>()` factory at the bottom.
- One `extern` forward decl + one `Reg.Register(Make_Handler_<Name>());`
  line in `UnrealClaudeMCPModule.cpp` (preserving 4-space indent — never
  tabs).
- Direct `#include "MCP/MCPHandler.h"` — never an `__has_include` ladder
  (PR #69 retrospective).

Cite UE 5.7 API claims with `header.h:line` in the file's top comment
block. Reviewer agents (Claude, Gemini, Codex) catch unverified API
claims; pre-empt by citing source.

## What to skip / not flag

- Bridge tests are pytest, not C++. Don't suggest mocking the UE TCP layer
  beyond what `test_bridge.py` already does.
- Don't suggest moving handlers into other modules — the one-file-per-handler
  shape is load-bearing.
- Don't suggest renaming the repo / plugin / bridge filename to drop "Claude"
  — those are decorative legacy. The vendor-neutral framing lives in
  description copy.

## Severity tagging

When emitting findings, prefer:
- **P0** = build-breaking or wire-protocol-breaking (e.g. compile error,
  schema drift, manifest+bridge name mismatch).
- **P1** = real semantic bug or established-pattern violation (e.g.
  protected-field direct access, missing null-skip on `TArray<TObjectPtr>`,
  enum case missing).
- **P2** = nit / style (skip these unless the change affects meaning).

Surfacing P0/P1 is the value; cosmetic nits typically aren't actioned
between PR and merge per directive #7 (mechanical PRs ship optimistically
on CI green).
