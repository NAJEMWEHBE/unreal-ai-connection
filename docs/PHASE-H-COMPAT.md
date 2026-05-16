# Phase H -- Cross-Engine Compatibility Strategy

> **STATUS: SCAFFOLDING ONLY -- NOT certified on any engine version other than UE 5.7.**
> Certification requires each target engine installed and a real build + smoke-test pass.

---

## 4.26 is OUT OF SCOPE

Unreal Engine 4.26 does not include the EditorSubsystem module.
UnrealClaudeMCP depends on subsystems introduced in 4.27 (UImportSubsystem, and the predecessor APIs to ULevelEditorSubsystem).
**4.26 support will never be pursued. The minimum supported version is 4.27.**

---

## Support Matrix

OK = no shim needed. SHIM = UCMCPCompat.h or handler guard required. N/A = feature unavailable.

> **Asset-registry cluster status (2026-05-16): shim-wired (unverified-compile).**
> The 5 call-sites in Handler_FindAssets.cpp, Handler_FixUpRedirectors.cpp, Handler_ListLevels.cpp,
> Handler_InspectAsset.cpp, and UnrealClaudeMCPModule.cpp have been rewired to UCMCPCompat shims
> on branch feat/phase-h-asset-registry-shims. This has NOT been verified by a host build against
> any engine version other than UE 5.7. A build + smoke-test pass on each target engine (4.27, 5.0,
> 5.1+) is required to certify correctness.

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

Recommendation: per-bucket variant zips.

    UnrealClaudeMCP-T1.zip -> "EngineVersion": "5.4.0"
    UnrealClaudeMCP-T2.zip -> "EngineVersion": "5.0.0"
    UnrealClaudeMCP-T3.zip -> "EngineVersion": "4.27.0"

Do NOT ship a single .uplugin without EngineVersion -- UE will load it on any version and crash at runtime when the API mismatches.

Current state: UnrealClaudeMCP/UnrealClaudeMCP.uplugin line 5 has "EngineVersion": "5.7.0".
This is correct for the active target. Update to 5.4.0 when T1 is formally widened.

---

---

## Phase H Cluster Progress

| Cluster | Status |
|---------|--------|
| Asset-registry (FilterAddClass, AssetClassName, AssetObjectPathString, GetAssetByObjectPath) | **shim-wired (unverified-compile)** -- branch feat/phase-h-asset-registry-shims |
| FTSTicker / FTicker rename | PENDING -- not yet shimmed in handler call-sites |
| Save-delegate (PostSaveWorldWithContext / PostSaveWorld) | PENDING -- UCMCP_POST_SAVE_WORLD_DELEGATE macro defined but 4.27 param list UNVERIFIED |
| LWC (float/double UCMCP_REAL) | PENDING -- alias defined but mesh/transform handler call-sites not shimmed |
| Mesh getters (GetStaticMaterials / GetMaterials) | PENDING |
| FImageUtils PNGCompressImageArray (TArray vs TArrayView64) | PENDING |

> **UNVERIFIED-COMPILE CAVEAT:** The asset-registry cluster edits compile on UE 5.7
> (the active dev engine) but have not been tested against UE 4.27, 5.0, or 5.1.
> Host builds against each bucket are required before certifying cross-engine correctness.
> The SCAFFOLDING-ONLY banner at the top of this document remains in effect.

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
