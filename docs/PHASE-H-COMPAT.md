# Phase H -- Cross-Engine Compatibility Strategy

> **STATUS: SCAFFOLDING ONLY -- NOT certified on any engine version other than UE 5.7.**
> Certification requires each target engine installed and a real build + smoke-test pass.

> **STATUS (2026-05-18, ADR-0001):** UE 5.7 is the officially supported & tested version. This cross-engine scaffold is **kept and available as a best-effort / community path** for other UE versions — build from source, uncertified, not actively maintained, contributions welcome. It is NOT removed or abandoned.

---

## 4.26 is OUT OF SCOPE

Unreal Engine 4.26 does not include the EditorSubsystem module.
UnrealClaudeMCP depends on subsystems introduced in 4.27 (UImportSubsystem, and the predecessor APIs to ULevelEditorSubsystem).
**4.26 support will never be pursued. The minimum supported version is 4.27.**

---

## Support Matrix

OK = no shim needed. SHIM = UCMCPCompat.h or handler guard required. N/A = feature unavailable.

> **Remaining-clusters status (2026-05-17): shim-wired (unverified-compile).**
> Ticker, save-delegate, ULevelEditorSubsystem, and FImageUtils-PNG seams are
> now wired through `UCMCPCompat.h` + their handlers (this branch). Import-subsystem,
> static/skeletal mesh getters, Niagara `GetFixedBounds`, and LWC narrowing were
> audited and found UNIFORM 4.27+ / already-correct -- NO shim, no edits (see the
> Phase H Cluster Progress table below for the per-cluster old->shim / uniform-no-shim
> rationale). The asset-registry cluster (#215) remains shim-wired. NONE of this has
> been verified by a host build against any engine other than UE 5.7. A build +
> smoke-test pass on each target engine (4.27, 5.0, 5.1+) is required to certify
> correctness; only then can the table's OK/SHIM cells be trusted.

| Engine | Load-gate | Asset-registry | FTSTicker | Save-delegate | UImportSubsystem | ULevelEditorSubsystem | FImageUtils | Mesh getters | LWC double |
|--------|-----------|----------------|-----------|---------------|------------------|-----------------------|-------------|--------------|------------|
| 4.27   | SHIM      | OK (ClassNames) | SHIM (FTicker) | SHIM (PostSaveWorld) | OK | N/A | OK (TArray) | OK | OK (float) |
| 5.0    | SHIM      | OK (ClassNames, last) | OK (FTSTicker) | OK (WithContext) | OK | OK | OK (TArray) | OK | SHIM (double) |
| 5.1    | SHIM      | SHIM (ClassPaths) | OK | OK | OK | OK | SHIM (TArrayView64) | OK | OK |
| 5.2    | SHIM      | OK | OK | OK | OK | OK | OK | OK | OK |
| 5.3    | SHIM      | OK | OK | OK | OK | OK | OK | OK | OK |
| 5.4    | SHIM      | OK | OK | OK | OK | OK | OK | OK | OK |
| 5.5    | SHIM      | OK | OK | OK | OK | OK | OK | OK | OK |
| 5.6    | SHIM      | OK | OK | OK | OK | OK | OK | OK | OK |
| 5.7    | OK (current) | OK | OK | OK | OK | OK | OK | OK | OK |
| 5.8    | SHIM (future) | OK | OK | OK | OK | OK | OK | OK | OK |

SHIM in Load-gate = the .uplugin EngineVersion field must be set per bucket.

---

## Release Strategy -- Three Version Buckets

A single compiled binary cannot span 4.27-5.8. Breaking changes:
- FARFilter API: ClassNames (<=5.0) vs ClassPaths (>=5.1)
- FTicker to FTSTicker rename at the 4.27/5.0 boundary
- LWC float to double at the 4.27/5.0 boundary
- ULevelEditorSubsystem absent before 5.0
- .uplugin EngineVersion acts as a hard load-gate

**Strategy: source releases in three buckets, each with its own .uplugin EngineVersion.**

### Bucket T1 -- UE 5.4 through 5.8 (primary)

- Uniform API surface; lowest migration cost.
- .uplugin EngineVersion: 5.4.0 (5.5-5.8 load fine with a lower EngineVersion).
- UCMCPCompat.h shims always take the >=5.1 branch.

> **UE 5.6 host-certified + binaries published (2026-05-25).** Built from the
> T1 variant against a real UE 5.6 editor with MSVC 14.38.33130, then
> smoke-tested live (`examples/smoke_test.py`) with the plugin loaded — all
> suites passed. Prebuilt Win64 binaries shipped as the
> [`v0.9.1-ue5.6`](https://github.com/NAJEMWEHBE/unreal-ai-connection/releases/tag/v0.9.1-ue5.6)
> release. **Load note:** the plugin's DMX handlers link against `DMXRuntime` /
> `DMXProtocol`, so the host project must enable the engine's `DMXEngine` +
> `DMXProtocol` plugins or `UnrealClaudeMCP` fails to load. **Toolchain note:**
> UE 5.6 pins MSVC `14.38.33130` (`Engine/Config/Windows/Windows_SDK.json`); a
> user-level `BuildConfiguration.xml` `<CompilerVersion>` pin to anything below
> 14.38 blocks the build. This is the first non-5.7 engine with a published
> binary; the SCAFFOLDING-ONLY caveats still apply to 4.27 / 5.0–5.5 / 5.8.

### Bucket T2 -- UE 5.0 through 5.3

- Requires FTSTicker, LWC double, PostSaveWorldWithContext, ULevelEditorSubsystem.
- .uplugin EngineVersion: 5.0.0.
- UCMCPCompat shims split at the 5.1 boundary within this bucket.
- Best-effort support; no CI runners currently provisioned.

### Bucket T3 -- UE 4.27

- Requires FTicker alias, PostSaveWorld (no context), float LWC, no ULevelEditorSubsystem.
- .uplugin EngineVersion: 4.27.0.
- Handler code calling ULevelEditorSubsystem must be stubbed for this bucket.
- Experimental / community-contributed.

---

## .uplugin EngineVersion Plan

Per-bucket variants are generated by `scripts/gen_uplugin_variants.py`
(pure stdlib, no UE, no network). It reads the **canonical**
`UnrealClaudeMCP/UnrealClaudeMCP.uplugin` and emits three copies into
`dist/uplugin-variants/`, each differing from the canonical file on
**exactly one line** (the `EngineVersion` load-gate):

    dist/uplugin-variants/UnrealClaudeMCP-T1.uplugin -> "EngineVersion": "5.4.0"   # UE 5.4 - 5.8
    dist/uplugin-variants/UnrealClaudeMCP-T2.uplugin -> "EngineVersion": "5.0.0"   # UE 5.0 - 5.3
    dist/uplugin-variants/UnrealClaudeMCP-T3.uplugin -> "EngineVersion": "4.27.0"  # UE 4.27

### Release-time use

    python scripts/gen_uplugin_variants.py            # writes the 3 variants
    python scripts/gen_uplugin_variants.py --check     # dry-run, prints the plan

Then, per bucket: copy the matching variant over `UnrealClaudeMCP.uplugin`
in the release tree (or stage it directly), zip the plugin, and ship.
`dist/` is git-ignored -- variants are build artifacts, never committed.
The canonical 5.7 `.uplugin` is treated as **read-only**: the generator
re-reads and asserts it is byte-unchanged after every run, and
`tests/test_uplugin_variants.py` enforces both that contract and the
per-bucket EngineVersion values.

> **The EngineVersion field is only a LOAD-GATE.** Emitting a T3 (4.27)
> variant does NOT mean the C++ compiles or runs on 4.27 -- the Phase H
> compat shims are unverified-compile. A per-engine host build + smoke
> pass is still required before a bucket can be called "supported".

Do NOT ship a single .uplugin without EngineVersion -- UE will load it on any version and crash at runtime when the API mismatches.

Current state: UnrealClaudeMCP/UnrealClaudeMCP.uplugin line 5 has "EngineVersion": "5.7.0".
This is correct for the active target and is deliberately left UNCHANGED
on this branch. Update to 5.4.0 only when T1 is formally widened, or use
the generator above to emit a widened variant without mutating the
shipped file.

---

---

## Phase H Cluster Progress

| Cluster | Status |
|---------|--------|
| Asset-registry (FilterAddClass, AssetClassName, AssetObjectPathString, GetAssetByObjectPath) | **shim-wired (unverified-compile)** -- #215 |
| FTSTicker / FTicker rename | **shim-wired (unverified-compile)** -- `FUCMCPTicker` swapped into `MCPServer.cpp` (AddTicker/RemoveTicker) + `MCPServer.h` (`FUCMCPTicker::FDelegateHandle`) |
| Save-delegate (PostSaveWorldWithContext / PostSaveWorld) | **shim-wired (unverified-compile)** -- `UCMCP_POST_SAVE_WORLD_DELEGATE`/`UCMCP_POST_SAVE_CONTEXT_TYPE` wired in `UnrealClaudeMCPModule.cpp` (register + unregister); `ObjectSaveContext.h` include gated `#if UCMCP_ENGINE_AT_LEAST(5,0)`. **UNVERIFIED**: 4.27 `PostSaveWorld(uint32,UWorld*,bool)` param list + `UCMCP_POST_SAVE_CONTEXT_TYPE==bool` placeholder |
| Import-subsystem (UImportSubsystem / OnAssetPostImport) | **uniform 4.27+ (no shim, verified by inspection)** -- subsystem + `(UFactory*,UObject*)` delegate uniform 4.27 -> 5.8; 4.26 OOS |
| ULevelEditorSubsystem | **shim-wired (unverified-compile)** -- `UCMCPCompat::LoadLevel` (+ `SaveCurrentLevel` for future use) added; `Handler_LoadLevel.cpp` rewired; `LevelEditorSubsystem.h` include gated `#if UCMCP_ENGINE_AT_LEAST(5,0)`. **UNVERIFIED**: 4.27 `FEditorFileUtils::LoadMap`/`SaveLevel` signatures |
| FImageUtils PNGCompressImageArray (TArray vs TArrayView64) | **shim-wired (unverified-compile)** -- `UCMCPCompat::EncodePngFColor` added; `Handler_GetViewportScreenshot.cpp` + `Handler_RenderCameraToPng.cpp` rewired. `Handler_TakeHighResScreenshot.cpp` uses the `HighResShot` console command (engine writes the PNG) -- NO FImageUtils site, no shim. **UNVERIFIED**: <=5.0 `CompressImageArray` overload |
| Mesh getters (GetBoundingBox / GetStaticMaterials / GetResourceForRendering / GetImportedBounds / GetMaterials) | **uniform 4.27+ (no shim, verified by inspection)** -- accessor form uniform 4.27 -> 5.8; FBox/FBoxSphereBounds LWC widens harmlessly into `SetNumberField(double)` |
| Niagara `GetFixedBounds` / `bFixedBounds` | **uniform 4.27+ (no shim, verified by inspection)** -- no API break across 4.27 -> 5.8 (Niagara public-accessor surface UNVERIFIED on a real 4.27 host) |
| LWC (float/double UCMCP_REAL) | **uniform / already-correct (no shim, verified by inspection)** -- audited `Handler_SetActorTransform/FocusActor/InspectStaticMesh/InspectLandscape/InspectSkeletalMesh`: zero fragile `(float)` casts on FVector/FRotator/FBox/FBoxSphereBounds. Code already declares intermediates `double` and feeds `SetNumberField(double)` (widening on 4.27, identity on 5.0+). No edits made |
| `.uplugin` per-bucket variants | **generator + test added** -- `scripts/gen_uplugin_variants.py` (pure stdlib) + `tests/test_uplugin_variants.py` (9 cases). Canonical 5.7 `.uplugin` left byte-unchanged |

> **UNVERIFIED-COMPILE CAVEAT:** Every cluster above marked "shim-wired"
> compiles only on UE 5.7 (the active dev engine) -- the >=5.x branch is the
> only path that has ever been built. The 4.27 / 5.0 / 5.1 branches and every
> "uniform 4.27+" inspection claim are SOURCE-AUTHORED ONLY. No host build or
> smoke pass has been run against UE 4.27, 5.0, 5.1, or any non-5.7 engine
> this session. Per-engine host builds (esp. UE 5.1 for the FImageUtils
> TArray64 boundary and UE 4.27 for the save-delegate / level / FEditorFileUtils
> branches) are MANDATORY before "5.1 supported" / "4.27 supported" can be
> claimed. The SCAFFOLDING-ONLY banner at the top of this document remains in
> effect.

---

## CI Matrix Plan

This section documents the INTENDED CI matrix. No workflow YAML is added here.

### Why GitHub-hosted runners cannot run this matrix

- Epic requires an EULA-accepted account; engine binaries are gated behind the Launcher.
- Engine source builds take 1-2 hours per version and need 200-300 GB disk.
- GitHub-hosted runners have ~14 GB free disk and no Epic credentials.

### Required infrastructure: self-hosted runners

- ue-5x-host: UE 5.4, 5.5, 5.6, 5.7, 5.8 installed (T1 bucket)
- ue-50-53-host: UE 5.0, 5.1, 5.2, 5.3 installed (T2 bucket)
- ue-427-host: UE 4.27 installed (T3 bucket)

### Intended matrix structure (documentation only -- not a real workflow)

    # NOT a real .github/workflows file
    strategy:
      matrix:
        include:
          - { engine: 5.7, bucket: T1, runner: ue-5x-host }
          - { engine: 5.6, bucket: T1, runner: ue-5x-host }
          - { engine: 5.5, bucket: T1, runner: ue-5x-host }
          - { engine: 5.4, bucket: T1, runner: ue-5x-host }
          - { engine: 5.3, bucket: T2, runner: ue-50-53-host }
          - { engine: 5.2, bucket: T2, runner: ue-50-53-host }
          - { engine: 5.1, bucket: T2, runner: ue-50-53-host }
          - { engine: 5.0, bucket: T2, runner: ue-50-53-host }
          - { engine: 4.27, bucket: T3, runner: ue-427-host }
    steps:
      - checkout source
      - apply bucket .uplugin EngineVersion
      - build: RunUAT BuildPlugin -Plugin=UnrealClaudeMCP.uplugin -TargetPlatforms=Win64
      - smoke-test: python examples/smoke_test.py

Certification = build + smoke test both pass on that specific engine version.

---

## Bridge / Manifest Metadata Plan

> **STATUS UPDATE (2026-05-16): this bridge slice is IMPLEMENTED and
> pytest-verified.** The `min_engine_version` / `max_engine_version` catalog
> fields, the bridge runtime gate, and the manifest-parity + gating
> behaviour tests are real and green (`tests/test_manifest_sync.py` +
> `tests/test_engine_gating.py`). This is the ONLY part of Phase H that is
> verified — the SCAFFOLDING-ONLY banner at the top of this document still
> holds for everything else (no engine other than UE 5.7 has been
> build- or smoke-certified; the .uplugin bucketing and CI matrix remain
> planning-only). Nothing here was tested against an actual UE 4.27 or
> 5.0–5.6 editor; the gate is exercised entirely via mocked
> `get_engine_version` round-trips.

### Schema (implemented)

Optional `min_engine_version` and `max_engine_version` are carried on each
tool entry in:

1. UnrealClaudeMCP/Resources/mcp_manifest.json (the tools array)
2. bridge/unreal_ai_connection_bridge.py TOOLS list (each tool dict)

Example:

    {
      "name": "sequencer_add_transform_keyframe",
      "description": "...",
      "min_engine_version": "5.0",
      "max_engine_version": null
    }

null max_engine_version = no upper bound known.
Absent min_engine_version = tool works on all supported engines (4.27+).

### Enforcement via tests (implemented)

tests/test_manifest_sync.py was extended (plus a focused
tests/test_engine_gating.py module) to:
- Verify the four gated tools carry min_engine_version "5.0" in BOTH the
  manifest and the bridge TOOLS list.
- Verify min_engine_version / max_engine_version values match across both
  locations for every tool (drift in either direction fails the suite).
- Assert no other tool accidentally carries the field.
- Unit-test the runtime gate: returns the structured error for engine 4.27
  and passes through for 5.7 / exactly-5.0; fails open when the engine
  version is undeterminable; memoises the discovered version.

### Runtime gate (implemented)

The bridge discovers the connected editor's `(major, minor)` once via the
native `get_engine_version` handler (reusing the existing `call_ue`
plumbing — no new round-trip type), memoises it, and on invocation of a
gated synthetic returns a structured error BEFORE the doomed UE call when
the engine is known and too old:

    {"error": {"code": "unsupported_on_engine_version",
               "message": "<tool>: ...",
               "tool": "<tool>",
               "min_engine_version": "5.0",
               "engine_version": "<actual, e.g. 4.27>"}}

Fail-open contract: if the engine version is genuinely undeterminable (UE
down / handler missing / unparseable) the gate proceeds rather than
hard-blocking. The gate runs AFTER each synthetic's own argument
validation, so genuinely-bad input still short-circuits with `-32602` and
zero round-trips.

### Synthetic tools requiring engine-version gating (implemented)

The following synthetic tools use Python unreal module APIs available only on UE 5.0+
(get_editor_subsystem, unreal.EditorActorSubsystem, MovieSceneTimeUnit).
They now carry min_engine_version: 5.0 in both the manifest and bridge.

| Tool | Reason for gating |
|------|------------------|
| get_camera_transform | Uses unreal.get_editor_subsystem(EditorActorSubsystem) -- 5.0+ |
| set_camera_transform | Same as above |
| sequencer_add_transform_keyframe | Uses unreal.MovieSceneTimeUnit -- introduced in 5.0+ |
| convert_hdri_to_cubemap | Uses unreal.EditorAssetLibrary subsystem APIs -- 5.0+ |
