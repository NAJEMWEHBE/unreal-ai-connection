# UnrealClaudeMCP v0.2.0 — Texture pipeline design

**Date:** 2026-05-08
**Status:** Proposed (awaiting final review)
**Author:** Claude (with Najem)
**Target release:** v0.2.0

---

## Goal

Add two new MCP tools to UnrealClaudeMCP that let an MCP client (Claude Code, etc.) bring image files into a UE 5.7 project as `UTexture2D` assets and adjust the standard import-time settings on existing texture assets. After this PR ships, the project's tool count grows from 11 → 13.

## Scope

**In scope (this PR):**
- `import_texture` — copy a PNG / JPG / EXR / TGA / BMP / HDR file from disk into a `/Game/...` package as a `UTexture2D` asset, using UE's canonical asset-import pipeline.
- `configure_texture` — adjust the four most common settings (`SRGB`, `CompressionSettings`, `LODGroup`, `Filter`) on an existing `UTexture` asset and persist the change.

**Out of scope (deferred):**
- `create_texture_from_bytes` (raw RGBA buffer over the wire) — requires upgrading the TCP framing from "one recv = one message" to length-prefixed framing first. Will be its own PR (see [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md#json-rpc-framing)).
- Material authoring, material instances, material assignment to mesh components — separate brainstorm cycles, separate PRs.
- Async / non-blocking import for large files — current architecture runs handlers synchronously on the game thread. Documented as a known limitation; large EXR import will briefly stall the editor's tick. Acceptable for v0.2.0.

## Design decision: two thin handlers, not one combined handler

We considered three shapes:

1. **Two thin handlers** — `import_texture` and `configure_texture` as separate MCP methods.
2. **One combined handler** — `import_texture` accepts optional configure params and does both atomically.
3. **Three handlers** — both of the above, plus a third compound handler.

**We chose Option 1.** Reasons:

- Single responsibility per handler matches the existing one-handler-per-leaf pattern in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`.
- Configuring an *already-imported* texture doesn't require running an import — Option 1 keeps that path clean.
- Forward-compatible: a future PR can add a compound handler (Option 3's third tool) without breaking either of the two we ship in v0.2.0. Option 2 would have foreclosed that flexibility.
- Testability: each handler is independently testable. Option 2 would couple test failures.

This mirrors the decomposition already used in [`Handler_EditWidgetTree.cpp`](../../../Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_EditWidgetTree.cpp), which split a graph-mutation operation into `set_root` / `add_child` / `set_property` ops rather than one mega-edit handler.

---

## Tool 1: `import_texture`

Brings an image file from disk into a UE project as a `UTexture2D` asset, using the canonical `UAssetImportTask` + `IAssetTools::ImportAssetTasks` path.

### Params

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `source_path` | string | ✅ | — | Absolute filesystem path. Validated for existence and supported extension before constructing the import task. |
| `dest_path` | string | ✅ | — | UE package path, must start with `/Game/`. e.g. `/Game/Textures/Environment`. |
| `dest_name` | string | ❌ | filename stem | Asset name override. If omitted, derived from `source_path` filename without extension, sanitized for UE naming rules. |
| `replace_existing` | bool | ❌ | `false` | Maps to [`UAssetImportTask::bReplaceExisting`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Editor/UnrealEd/Public/AssetImportTask.h). |
| `automated` | bool | ❌ | `true` | Maps to `bAutomated` — silences modal dialogs that would otherwise block the editor thread. |
| `save` | bool | ❌ | `true` | Maps to `bSave` — saves the resulting `.uasset` to disk after import. |

### Result

```json
{
  "ok": true,
  "asset_path": "/Game/Textures/Environment/T_Stone_D",
  "asset_name": "T_Stone_D",
  "source_path": "C:/Art/stone_diffuse.png",
  "width": 2048,
  "height": 2048,
  "format": "PF_B8G8R8A8",
  "message": "Imported PNG (2048x2048) as UTexture2D."
}
```

### Errors (returned in JSON-RPC `error` field)

| Code | Trigger |
|---|---|
| `source_not_found` | `source_path` does not exist on disk or is not readable. |
| `unsupported_extension` | Extension not in `{png, jpg, jpeg, exr, tga, bmp, hdr}`. |
| `invalid_dest_path` | `dest_path` does not start with `/Game/` or contains illegal characters. |
| `dest_collision_no_replace` | Target asset exists and `replace_existing=false`. |
| `import_factory_failed` | `IAssetTools::ImportAssetTasks` populated `ImportedObjectPaths` as empty (factory rejected the input or returned no result). |
| `imported_not_a_texture` | Import succeeded but the resulting object is not a `UTexture2D` (defensive — should never happen with this factory). |

### C++ approach (high level)

1. Validate `source_path` (exists, readable extension).
2. Validate `dest_path` (starts with `/Game/`, no illegal characters).
3. Construct a `UAssetImportTask*`. Set `Filename`, `DestinationPath`, `DestinationName`, `bAutomated`, `bSave`, `bReplaceExisting`.
4. Acquire `IAssetTools` via `FAssetToolsModule::Get().Get()`.
5. Call `ImportAssetTasks({Task})`. (Returns void per [`IAssetTools.h:539`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Developer/AssetTools/Public/IAssetTools.h).)
6. Read `Task->ImportedObjectPaths` — must be non-empty.
7. Cast `Task->GetObjects()[0]` to `UTexture2D*` to read `GetSizeX()`, `GetSizeY()`, `GetPixelFormat()`.
8. Return JSON success payload.

Reference: [`UAssetImportTask` source](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Editor/UnrealEd/Public/AssetImportTask.h).

---

## Tool 2: `configure_texture`

Adjusts settings on an already-imported `UTexture` asset and persists the change.

### Params

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `path` | string | ✅ | — | UE package path of an existing texture asset, e.g. `/Game/Textures/Environment/T_Stone_D`. |
| `srgb` | bool | ❌ | unchanged | Sets `UTexture::SRGB` (defined at [`Texture.h:1531`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Runtime/Engine/Classes/Engine/Texture.h)). |
| `compression` | string enum | ❌ | unchanged | Maps to `TextureCompressionSettings`. Accepted values: `Default`, `Normalmap`, `Masks`, `Grayscale`, `Displacementmap`, `VectorDisplacementmap`, `HDR`, `UserInterface2D`, `BC7`, `HalfFloat`, `SingleFloat`, `EncodedReflectionCapture`, `Alpha`, `DistanceFieldFont`, `HDR_Compressed`, `BC4`, `BC5`. |
| `lod_group` | string enum | ❌ | unchanged | Maps to `TextureGroup`. Common values: `World`, `WorldNormalMap`, `WorldSpecular`, `Character`, `CharacterNormalMap`, `CharacterSpecular`, `Weapon`, `WeaponNormalMap`, `WeaponSpecular`, `Vehicle`, `VehicleNormalMap`, `VehicleSpecular`, `Cinematic`, `Effects`, `EffectsNotFiltered`, `Skybox`, `UI`, `Lightmap`, `Shadowmap`, `RenderTarget`, `MobileFlattened`, `IESLightProfile`, `Bake`, `Pixels2D`, `HierarchicalLOD`. **Implementation note:** the exhaustive list must be verified against `Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h` in UE 5.7 before shipping — projects sometimes add custom groups via DefaultEngine.ini, but those are out of scope for v0.2.0. |
| `filter` | string enum | ❌ | unchanged | Maps to `TextureFilter`. Accepted values: `Nearest`, `Bilinear`, `Trilinear`, `Default`. |
| `compress` | bool | ❌ | `true` | Whether to call `UpdateResource()` after mutating. Set `false` for batch operations and trigger compile separately. |

### Result

```json
{
  "ok": true,
  "path": "/Game/Textures/Environment/T_Stone_D",
  "applied": {
    "srgb": false,
    "compression": "Normalmap",
    "lod_group": "WorldNormalMap",
    "filter": "Default"
  },
  "message": "Applied 4 changes; resource rebuilt and saved."
}
```

`applied` only contains the fields that were actually present in the request — fields the caller didn't specify don't appear.

### Errors

| Code | Trigger |
|---|---|
| `asset_not_found` | `LoadObject<UTexture>(nullptr, *Path)` returned null. |
| `asset_not_a_texture` | The asset exists but is not a `UTexture` subclass. |
| `no_changes_specified` | None of `srgb`, `compression`, `lod_group`, `filter` were provided in the params. The handler is a mutation tool — calling it with zero changes is treated as caller error, not as a successful no-op. |
| `unknown_enum_value` | A string passed for `compression` / `lod_group` / `filter` does not match any enum value. Error message lists the offending field and value. |
| `save_failed` | `UEditorAssetLibrary::SaveLoadedAsset` returned `false`. |

### C++ approach (high level)

1. `UTexture* Tex = LoadObject<UTexture>(nullptr, *Path)` — fail with `asset_not_found` if null.
2. Cast guard — fail with `asset_not_a_texture` if the load returns a non-`UTexture` object.
3. `Tex->PreEditChange(nullptr)` — required per the doc comment at [`Texture.h:1883`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Runtime/Engine/Classes/Engine/Texture.h) ("If you need the texture resource after you've made modifications, you should wrap your changes in PreEditChange/PostEditChange").
4. `Tex->Modify()` — marks the package dirty and registers the change for undo.
5. For each provided field, set the corresponding UPROPERTY directly:
   - `Tex->SRGB = ...;`
   - `Tex->CompressionSettings = ...;`
   - `Tex->LODGroup = ...;`
   - `Tex->Filter = ...;`
6. `FPropertyChangedEvent EmptyEvent(nullptr); Tex->PostEditChangeProperty(EmptyEvent);` — triggers UE's `ValidateSettingsAfterImportOrEdit` and resource-rebuild cascade.
7. If `compress=true`, call `Tex->UpdateResource()` to force the GPU side to rebuild.
8. `UEditorAssetLibrary::SaveLoadedAsset(Tex)` to persist to disk.
9. Return JSON success payload.

---

## Build dependencies

`Source/UnrealClaudeMCP/UnrealClaudeMCP.Build.cs` needs two additions:

```csharp
// New in v0.2.0
"UnrealEd",      // UAssetImportTask, UTextureFactory, UFactory
"AssetTools",    // IAssetTools interface (loaded via FAssetToolsModule::Get())
```

`UnrealEd` is currently transitively linked via `Kismet`, but we want to declare it explicitly now that we're directly referencing `UAssetImportTask` and `UTextureFactory`. Implicit transitive linkage is fragile across UE versions — Epic has occasionally restructured which module owns which symbol.

## File layout

```
Source/UnrealClaudeMCP/Private/MCP/Handlers/
  Handler_ImportTexture.cpp        (NEW, ~250 LoC)
  Handler_ConfigureTexture.cpp     (NEW, ~150 LoC)

Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp
  + 2 extern declarations
  + 2 Reg.Register calls

Source/UnrealClaudeMCP/UnrealClaudeMCP.Build.cs
  + "UnrealEd", "AssetTools"

Resources/mcp_manifest.json
  + 2 tool entries with JSON Schema params

bridge/unreal_claude_mcp_bridge.py
  + 2 entries in TOOLS list (mirrors manifest)

tests/test_bridge_edge_cases.py
  + tests for the 2 new tools' parameter validation

tests/fixtures/
  test_texture.png                 (NEW, small fixture for live smoke test)

docs/TOOLS.md
  + 2 new sections, one per tool

README.md
  + 2 new rows in the tools table
  + bump tool count from 11 → 13 in 3 places
```

## Testing strategy

### Bridge unit tests (no UE editor required, runs in CI)
- Both new entries in the `TOOLS` list are well-formed JSON-RPC method declarations.
- The bridge correctly translates `tools/call` envelopes for both methods to raw JSON-RPC.
- Manifest sync test (existing pattern in [`tests/test_manifest_sync.py`](../../../tests/test_manifest_sync.py)) covers both new tools.

### Negative-path bridge tests
- Empty `source_path` → `invalid_argument`.
- `dest_path` not starting with `/Game/` → `invalid_dest_path`.
- Unknown enum value in `configure_texture.compression` → `unknown_enum_value`, error message includes both the field name and the offending value.

### Live integration smoke test (requires running UE editor)
Add to [`examples/smoke_test.py`](../../../examples/smoke_test.py):
1. Import `tests/fixtures/test_texture.png` into `/Game/_UnrealClaudeMCPSmoke/T_Test_D`.
2. Verify response: `ok=true`, dimensions match, format is `PF_B8G8R8A8`.
3. Configure: set `srgb=false`, `compression=Normalmap`, `lod_group=WorldNormalMap`.
4. Verify response: `applied` map contains all three fields.
5. Re-inspect via existing `inspect_blueprint`-style introspection (or a one-shot `execute_unreal_python` reading the asset back) to confirm the settings stuck.
6. Clean up: delete `/Game/_UnrealClaudeMCPSmoke/`.

The smoke test stays optional in CI (existing convention — it requires a running UE).

## Documentation updates

1. **`README.md`** — add 2 rows to the tools table. Bump "11 tools" to "13 tools" in the heading paragraph and the status block.
2. **`docs/TOOLS.md`** — add full sections for both tools, following the existing format (params, result, example, important notes).
3. **`docs/ARCHITECTURE.md`** — append one new entry to the "UE 5.7 API gotchas" scar collection: the `PreEditChange` / `Modify` / `set property` / `PostEditChangeProperty` / `UpdateResource` / `SaveLoadedAsset` dance for texture mutation, with a one-line note on what breaks if you skip `UpdateResource()` (in-editor preview keeps showing the old texture even after save).

## Forward compatibility note

Both new tools are designed to be additive. Future PRs that build on this design (e.g., a compound `import_texture_with_settings` handler that bundles import + configure into one call) can be added without changing the v0.2.0 surface. Anyone who builds on top of `import_texture` + `configure_texture` will continue to work unchanged.

This matches semantic versioning: v0.2.0 is the minor-version bump that introduces new capabilities. A future v0.3.0 may add bundled or async variants. We will not remove or rename either of the two tools added here without a major-version bump.

---

## References (UE 5.7 source, all citations)

- [`Engine/Source/Editor/UnrealEd/Public/AssetImportTask.h`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Editor/UnrealEd/Public/AssetImportTask.h) — `UAssetImportTask` UCLASS with `Filename`, `DestinationPath`, `DestinationName`, `bAutomated`, `bSave`, `bReplaceExisting`, `Factory`, `Options`, `ImportedObjectPaths`, `GetObjects()`.
- [`Engine/Source/Developer/AssetTools/Public/IAssetTools.h:539`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Developer/AssetTools/Public/IAssetTools.h) — `virtual void ImportAssetTasks(const TArray<UAssetImportTask*>& ImportTasks) = 0;`.
- [`Engine/Source/Editor/UnrealEd/Classes/Factories/TextureFactory.h`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Editor/UnrealEd/Classes/Factories/TextureFactory.h) — `UTextureFactory` properties: `CompressionSettings`, `MipGenSettings`, `LODGroup`, `NoAlpha`, `bFlipNormalMapGreenChannel`, `ColorSpaceMode`. Static `SuppressImportOverwriteDialog(bool)`.
- [`Engine/Source/Runtime/Engine/Classes/Engine/Texture.h`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Runtime/Engine/Classes/Engine/Texture.h) — `UTexture` UPROPERTYs: `SRGB:1` (line 1531), `CompressionSettings` (line 1470), `Filter` (line 1474), `LODGroup` (line 1502). Mutation pattern doc at line 1883. `UpdateResource()` at line 1694.

## Prior-art survey notes

Surveyed the most prominent existing Unreal MCP projects. **None of the substantive ones handle texture import.** Catalog:

- [chongdashu/unreal-mcp](https://github.com/chongdashu/unreal-mcp) (MIT, UE 5.5+) — actor / blueprint / blueprint-graph / editor control. No texture pipeline.
- [GenOrca/unreal-mcp](https://github.com/GenOrca/unreal-mcp) — Python+C++. No texture pipeline.
- [tumourlove/monolith](https://github.com/tumourlove/monolith) — claims 1,226 actions across 16 modules including Materials. Did not deeply audit; likely the closest prior art if texture-related ideas are needed.
- [flopperam/unreal-engine-mcp](https://github.com/flopperam/unreal-engine-mcp) — world-building focused.
- [ChiR24/Unreal_mcp](https://github.com/ChiR24/Unreal_mcp) — TypeScript+C++ scope.

This means UnrealClaudeMCP v0.2.0 will be a meaningful capability addition to the public Unreal MCP ecosystem, not a duplicate of an existing tool.
