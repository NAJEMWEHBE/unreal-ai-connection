# MCP Tools Reference

All tools are JSON-RPC 2.0 methods served on `127.0.0.1:18888` once the plugin is loaded. Each tool's params and result are documented with a working example.

To run any of these manually from the shell, use Python:

```python
import socket, json
s = socket.socket(); s.connect(('127.0.0.1', 18888))
s.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"<METHOD>","params":{...}}).encode())
print(s.recv(65536).decode())
```

---

## execute_unreal_python

Universal escape hatch. Runs an arbitrary block of Python in the editor's embedded interpreter via `IPythonScriptPlugin`. Multi-line scripts are supported — the handler writes the source to a temp `.py` file under `Intermediate/UnrealClaudeMCPPython/` and asks UE to execute it (this avoids `ExecPythonCommandEx`'s file-vs-source heuristic getting confused by multi-line input).

**Params**
- `code` (string, required) — Python source code

**Result**
- `ok` (bool) — true if the script ran without raising
- `output` (string) — what the interpreter printed
- `temp_script` (string) — path to the temp `.py` file (debugging aid)

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"execute_unreal_python","params":{
  "code":"import unreal\nunreal.log('hello from MCP')"
}}
```

---

## get_project_summary

Top-level snapshot of the open project.

**Params** — none

**Result**
- `project_name`, `project_id`, `project_version`, `company_name`, `engine_version` (strings)
- `plugins` (array) — `{name, version, category, enabled_by_default}`. `enabled_by_default` is a string: `"enabled"` / `"disabled"` / `"unspecified"`.
- `asset_count` (int) — assets under `/Game` and any plugin-content roots

---

## inspect_blueprint

Read parent class, declared variables, and function/event graph names of a Blueprint asset.

**Params**
- `path` (string, required) — e.g. `/Game/Blueprints/BP_MyActor.BP_MyActor`

**Result**
- `path`, `parent_class`, `blueprint_class` (strings)
- `variables` (array) — `{name, type_category, type_subcategory, default}`
- `function_graphs`, `event_graphs` (arrays of strings)

---

## inspect_widget_tree

Read the widget hierarchy of a `UWidgetBlueprint` or `UEditorUtilityWidgetBlueprint`. **This is the headline feature of the plugin** — UE Python can't reach `WidgetTree` because of `UPROPERTY()` reflection limits, but this handler accesses it via direct C++ and exposes it as JSON.

**Params**
- `path` (string, required)

**Result**
- `path`, `blueprint_class`, `root` (root widget name), `root_class` (strings)
- `widgets` (array) — `{name, class, parent}`
- `widget_count` (int)

---

## edit_widget_tree

Mutate the widget tree of a `UWidgetBlueprint` / EUW. Three ops.

**Params**
- `path` (string, required)
- `op` (string, required) — `"set_root"` | `"add_child"` | `"set_property"`
- `class` (string) — for set_root and add_child; one of `VerticalBox|HorizontalBox|CanvasPanel|TextBlock|Button|Border|Image|Spacer|EditableTextBox`, or a fully-qualified `UClass` path
- `name` (string) — name to assign to the new widget
- `parent` (string) — for add_child; name of the parent panel widget
- `widget` (string) — for set_property; target widget name
- `property` (string) — for set_property; UProperty name
- `value` (string) — for set_property; coerced to str/float/int/bool by property type
- `compile` (bool, optional, default false) — call `FKismetEditorUtilities::CompileBlueprint` after the edit. Skip for batch operations; compile once explicitly at the end (per-edit compile crashed UE during testing under high-frequency mutation).

**Result**
- `op` — the op that ran
- `created` (string) — for set_root and add_child, the widget name
- `set` (string) — for set_property, `"WidgetName.PropertyName"`

**Important persistence note**: every call marks the widget tree dirty + the BP structurally modified, then saves the asset to disk. The asset is recompiled automatically when next loaded by the editor, or you can explicitly compile via the `compile: true` parameter on the last edit in a batch.

---

## get_viewport_screenshot

Capture the active editor viewport as a PNG, return base64 inline.

**Params** — none

**Result**
- `width`, `height`, `png_bytes` (numbers)
- `png_base64` (string) — the PNG bytes encoded as base64

Beware: a 1920x1080 PNG can be 1-3 MB of base64. If you're proxying through Claude, the response may exceed Claude's context — for big captures use `take_high_res_screenshot` instead, which writes to disk.

---

## list_tools

Return the names of every registered MCP method.

**Params** — none

**Result**
- `tools` (array of strings, sorted)
- `count` (int)

---

## get_actors_in_level

Return name / class / transform of every actor in the active editor world.

**Params**
- `name_contains` (string, optional) — case-insensitive substring filter on actor label

**Result**
- `world` (string) — world name
- `total_actors` (int) — actors in the world before filtering
- `returned` (int) — actors after filtering
- `actors` (array) — `{name, label, class, loc_x, loc_y, loc_z, yaw, pitch, roll}`

---

## focus_actor

Select an actor by label or unique name and frame the editor viewport on it.

**Params**
- `name` (string, required) — actor label OR unique name

**Result**
- `focused` (string) — actor label
- `name` (string) — unique name
- `loc_x`, `loc_y`, `loc_z` (numbers)

---

## load_level_by_path

Load a UE level by package path.

**Params**
- `path` (string, required) — e.g. `/Game/Maps/MyLevel`

**Result**
- `path`, `loaded` (bool)

---

## take_high_res_screenshot

Trigger UE's `HighResShot` console command. Output goes to `<Project>/Saved/Screenshots/<Platform>Editor/` — `WindowsEditor` on Windows, `MacEditor` on Mac, `LinuxEditor` on Linux. The exact path is returned in `output_dir_hint` of the response.

**Params**
- `multiplier` (number, optional, default 1, capped at 8) — viewport-pixel scaling factor

**Result**
- `command` (string) — the command that was issued
- `multiplier` (number)
- `dispatched` (bool)
- `output_dir_hint` (string)

---

## import_texture

Import an image file (PNG/JPG/EXR/TGA/BMP/HDR) from disk into the project as a `UTexture2D` asset, using UE's canonical `UAssetImportTask` + `IAssetTools::ImportAssetTasks` path. The factory's settings inference, source-file metadata, and reimport hooks all behave exactly as if you had drag-dropped the file into the Content Browser manually.

**Params**
- `source_path` (string, required) — absolute filesystem path to the source image.
- `dest_path` (string, required) — UE package path; must start with `/Game/` (e.g. `/Game/Textures/Environment`).
- `dest_name` (string, optional) — asset-name override; defaults to the source filename stem.
- `replace_existing` (bool, optional, default `false`) — overwrite the asset if one already exists at `dest_path/dest_name`.
- `automated` (bool, optional, default `true`) — suppress modal dialogs; required for non-interactive use.
- `save` (bool, optional, default `true`) — persist the `.uasset` to disk after import.

**Result**
- `ok` (bool)
- `asset_path` (string) — UE package path of the new asset
- `asset_name` (string)
- `source_path` (string) — echo of the source argument
- `width`, `height` (int) — pixel dimensions
- `format` (string) — `EPixelFormat` name (e.g. `PF_B8G8R8A8`)
- `message` (string) — human-readable status

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"import_texture","params":{
  "source_path": "C:/Art/stone_diffuse.png",
  "dest_path": "/Game/Textures/Environment",
  "dest_name": "T_Stone_D",
  "replace_existing": false,
  "save": true
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "asset_path": "/Game/Textures/Environment/T_Stone_D",
  "asset_name": "T_Stone_D",
  "source_path": "C:/Art/stone_diffuse.png",
  "width": 2048,
  "height": 2048,
  "format": "PF_B8G8R8A8",
  "message": "Imported PNG (2048x2048) as UTexture2D."
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `import_texture: <error_code>: <human-readable detail>`. Clients can split on `: ` (twice) to extract the stable error code. Stable codes:

| Code | Trigger |
|---|---|
| `missing_params` | Request had no `params` object. |
| `missing_required_field` | `source_path` or `dest_path` was missing or empty. |
| `invalid_dest_path` | `dest_path` does not start with `/Game/`. |
| `source_not_found` | `source_path` does not exist on disk. |
| `unsupported_extension` | File extension not in `{png, jpg, jpeg, exr, tga, bmp, hdr}`. |
| `import_factory_failed` | UE's `IAssetTools::ImportAssetTasks` produced no imported object (factory rejected the input). |
| `imported_not_a_texture` | Defensive — the factory returned an object that is not a `UTexture2D`. Should never fire for the texture factory. |

---

## configure_texture

Adjust the four most common texture settings — `SRGB`, `CompressionSettings`, `LODGroup`, `Filter` — on an existing `UTexture` asset and persist the change. The handler wraps mutations in the required `PreEditChange` / `Modify` / set / `PostEditChangeProperty` / `UpdateResource` / `SaveLoadedAsset` sequence so that the in-editor preview and the on-disk `.uasset` stay consistent.

The `applied` object in the result contains *only* the fields the caller actually provided in the request. Fields that were not specified in the call do not appear in `applied`, which makes it safe to drive this tool in partial-update loops without accidentally inferring what was left unchanged.

**Params**
- `path` (string, required) — UE package path of an existing texture asset, e.g. `/Game/Textures/Environment/T_Stone_D`.
- `srgb` (bool, optional) — sets `UTexture::SRGB`.
- `compression` (string enum, optional) — maps to `TextureCompressionSettings`. Accepted values: `Default`, `Normalmap`, `Masks`, `Grayscale`, `Displacementmap`, `VectorDisplacementmap`, `HDR`, `UserInterface2D`, `BC7`, `HalfFloat`, `SingleFloat`, `Alpha`, `DistanceFieldFont`, `HDR_Compressed`, `HDR_F32`.
- `lod_group` (string enum, optional) — maps to `TextureGroup`. Common values: `World`, `WorldNormalMap`, `WorldSpecular`, `Character`, `CharacterNormalMap`, `CharacterSpecular`, `Weapon`, `WeaponNormalMap`, `WeaponSpecular`, `Vehicle`, `VehicleNormalMap`, `VehicleSpecular`, `Cinematic`, `Effects`, `EffectsNotFiltered`, `Skybox`, `UI`, `Lightmap`, `Shadowmap`, `RenderTarget`, `MobileFlattened`, `Pixels2D`, `HierarchicalLOD`. (Exhaustive list validated against `Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h` in UE 5.7.)
- `filter` (string enum, optional) — maps to `TextureFilter`. Accepted values: `Nearest`, `Bilinear`, `Trilinear`, `Default`.
- `compress` (bool, optional, default `true`) — whether to call `UpdateResource()` after mutation; set to `false` for batched edits and trigger a rebuild separately.

**Result**
- `ok` (bool)
- `path` (string) — UE package path of the modified asset
- `applied` (object) — only the fields that were present in the request, with their applied values
- `message` (string) — human-readable status, e.g. `"Applied 3 changes; resource rebuilt and saved."`

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"configure_texture","params":{
  "path": "/Game/Textures/Environment/T_Stone_D",
  "srgb": false,
  "compression": "Normalmap",
  "lod_group": "WorldNormalMap"
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "path": "/Game/Textures/Environment/T_Stone_D",
  "applied": {
    "srgb": false,
    "compression": "Normalmap",
    "lod_group": "WorldNormalMap"
  },
  "message": "Applied 3 changes; resource rebuilt and saved."
}}
```

**Note:** the canonical list of accepted enum values is the parser map in `Handler_ConfigureTexture.cpp`. This document mirrors it but the source is authoritative — UE versions can add or remove enum entries.

**Errors:** Returned as JSON-RPC `error.message` strings in the format `configure_texture: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_params` | Request had no `params` object. |
| `missing_required_field` | `path` was missing or empty. |
| `no_changes_specified` | None of `srgb` / `compression` / `lod_group` / `filter` were provided. The handler is a mutation tool — calling it with zero changes is treated as caller error, not a successful no-op. |
| `asset_not_found` | `LoadObject<UTexture>(path)` returned null (asset doesn't exist or isn't a `UTexture` subclass). |
| `unknown_enum_value` | A string value for `compression` / `lod_group` / `filter` did not match any valid enum. The error message lists the offending field and value. |
| `save_failed` | `UEditorAssetLibrary::SaveLoadedAsset` returned `false` after mutations were applied in-memory. |

---

## find_assets

Query the UE asset registry by class, path, and optional name substring. This is the discovery tool for level-building workflows: call it first to learn which Static Meshes, Blueprints, or other assets are available before spawning them into the level.

**Params**
- `class_path` (string, required) — UE class path, e.g. `/Script/Engine.StaticMesh`, `/Script/Engine.Blueprint`, `/Script/Engine.Texture2D`.
- `path_under` (string, optional, default `/Game/`) — recursive path filter; must start with `/Game/` or `/Engine/`.
- `name_contains` (string, optional) — case-insensitive substring filter on asset name.
- `limit` (int, optional, default `100`) — cap result count; max `500` (silently capped).

**Result**
- `ok` (bool)
- `matched` (int) — total assets matching the filter before `limit` is applied
- `returned` (int) — actual count in the response
- `assets` (array) — `{name, package_path, class}` per asset, sorted by name

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"find_assets","params":{
  "class_path": "/Script/Engine.StaticMesh",
  "path_under": "/Engine/BasicShapes/",
  "limit": 10
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "matched": 5,
  "returned": 5,
  "assets": [
    {"name": "Cone",   "package_path": "/Engine/BasicShapes/Cone",   "class": "StaticMesh"},
    {"name": "Cube",   "package_path": "/Engine/BasicShapes/Cube",   "class": "StaticMesh"},
    {"name": "Cylinder","package_path": "/Engine/BasicShapes/Cylinder","class": "StaticMesh"},
    {"name": "Plane",  "package_path": "/Engine/BasicShapes/Plane",  "class": "StaticMesh"},
    {"name": "Sphere", "package_path": "/Engine/BasicShapes/Sphere", "class": "StaticMesh"}
  ]
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `find_assets: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `class_path` was missing or empty. |
| `invalid_class_path` | `class_path` does not resolve to a known UClass. |
| `invalid_path_filter` | `path_under` does not start with `/Game/` or `/Engine/`. |
| `invalid_tag_value` | A `tags` map entry's value is neither a string nor null (v0.7.0). |

### v0.7.0 additions

Two optional parameters extend the v0.4.0 contract without breaking existing callers:

- `tags` (object) — map of tag-name → required-value (string) or `null` (any value). Multiple entries are AND-combined by the asset registry.
- `include_tags` (bool, default `false`) — when `true`, each returned asset includes a `tags` map of all its registry tags.

Tag values are stringified via UE's `FAssetTagValueRef::AsString()`, so numeric or object-path tags appear as their string representation. Tag names are FName-compared (case-insensitive); tag values are FString-compared (case-sensitive).

**Example — Texture2D assets in `/Game/Textures/` with `LODGroup` tag set to `TEXTUREGROUP_World`, returning the full tag map for each:**

```json
{"jsonrpc":"2.0","id":1,"method":"find_assets","params":{
  "class_path": "/Script/Engine.Texture2D",
  "path_under": "/Game/Textures/",
  "tags": {"LODGroup": "TEXTUREGROUP_World"},
  "include_tags": true
}}
```

---

## spawn_actor

Create an actor in the current editor world at a given location, with optional rotation, label override, and initial properties. The `class_path` accepts both built-in classes (e.g. `/Script/Engine.StaticMeshActor`) and Blueprint-generated classes (note the `_C` suffix for Blueprint classes: `/Game/Blueprints/BP_MyActor.BP_MyActor_C`). Initial properties are applied via `PropertyCoercion` immediately after spawn; if any property fails, the actor is still placed and the error message identifies which property caused the failure.

**Params**
- `class_path` (string, required) — actor class path.
- `location` (object, optional, default `{x:0, y:0, z:0}`) — world-space `{x, y, z}` floats.
- `rotation` (object, optional, default `{pitch:0, yaw:0, roll:0}`) — Euler angles in degrees.
- `label` (string, optional) — visible name in World Outliner; defaults to UE's auto-name.
- `properties` (object, optional) — map of `{"PropertyName": value}` applied after spawn.

**Result**
- `ok` (bool)
- `name` (string) — unique FName assigned by UE (e.g. `BP_MyActor_C_4`)
- `label` (string) — World Outliner label
- `class` (string) — class name
- `location` (object) — `{x, y, z}`
- `rotation` (object) — `{pitch, yaw, roll}`

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"spawn_actor","params":{
  "class_path": "/Script/Engine.StaticMeshActor",
  "location": {"x": 0, "y": 0, "z": 0},
  "label": "SmokeCube1"
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "name": "StaticMeshActor_3",
  "label": "SmokeCube1",
  "class": "StaticMeshActor",
  "location": {"x": 0.0, "y": 0.0, "z": 0.0},
  "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `spawn_actor: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `class_path` was missing or empty. |
| `invalid_class_path` | Class not found in the asset registry. |
| `class_not_spawnable` | Class is abstract, does not derive from `AActor`, or has `bEditorOnly = true`. |
| `spawn_failed` | `UWorld::SpawnActor` returned null (UE refused for an internal reason). |
| `property_application_failed` | One of the `properties` entries failed to apply. The actor is still spawned; the message identifies which property failed. Caller can use `delete_actor` for atomic-like cleanup. |

---

## set_actor_transform

Move, rotate, or scale an existing actor by label or FName. Uses the hybrid label-or-FName identification scheme: label match is tried first; if multiple actors share the label, an `ambiguous_actor` error is returned with all matching FNames listed so the caller can retry with the specific FName. Only the fields provided are changed; omitted fields retain their current values.

**Params**
- `name` (string, required) — actor label or FName.
- `location` (object, optional) — `{x, y, z}` world-space position; unchanged if omitted.
- `rotation` (object, optional) — `{pitch, yaw, roll}` in degrees; unchanged if omitted.
- `scale` (object, optional) — `{x, y, z}` scale multiplier; unchanged if omitted.
- `relative` (bool, optional, default `false`) — when `true`, deltas are added to the current values instead of replacing them.

**Result**
- `ok` (bool)
- `name` (string) — actor FName
- `applied` (object) — only the fields that were actually changed, with their new values

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"set_actor_transform","params":{
  "name": "SmokeCube2",
  "location": {"x": 200, "y": 200, "z": 50},
  "rotation": {"pitch": 0, "yaw": 45, "roll": 0}
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "name": "StaticMeshActor_4",
  "applied": {
    "location": {"x": 200.0, "y": 200.0, "z": 50.0},
    "rotation": {"pitch": 0.0, "yaw": 45.0, "roll": 0.0}
  }
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `set_actor_transform: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `name` was missing or empty. |
| `actor_not_found` | No actor matches the given name. |
| `ambiguous_actor` | Label matches multiple actors. Error message lists candidate FNames. |
| `no_changes_specified` | None of `location` / `rotation` / `scale` were provided. |

---

## delete_actor

Remove an actor from the current editor world by label or FName. Uses the same hybrid label-or-FName identification scheme as the other write handlers. The optional `force` flag controls whether to proceed when the actor has attached children: without it the call is refused with a `has_children` error, giving the caller a chance to decide what to do with dependents first.

**Params**
- `name` (string, required) — actor label or FName.
- `force` (bool, optional, default `false`) — when `false`, refuses deletion if the actor has attached children (`has_children` error). When `true`, deletes anyway; children become detached, mirroring UE's native delete-with-children behavior.

**Result**
- `ok` (bool)
- `name` (string) — actor FName
- `deleted` (bool)

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"delete_actor","params":{
  "name": "SmokeCube1",
  "force": true
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "name": "StaticMeshActor_3",
  "deleted": true
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `delete_actor: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `name` was missing or empty. |
| `actor_not_found` | No actor matches the given name. |
| `ambiguous_actor` | Label matches multiple actors. Error message lists candidate FNames. |
| `has_children` | `force=false` and the actor has attached children. |

---

## set_actor_property

Mutate any `UPROPERTY` on an actor by label or FName. Uses the hybrid label-or-FName identification scheme. The property name must match the C++ field name exactly (case-sensitive). The result includes both the `old_value` and `new_value` encoded in the same JSON shape as the input, so changes can be verified or undone.

**Params**
- `name` (string, required) — actor label or FName.
- `property` (string, required) — `UPROPERTY` field name (case-sensitive, matches C++ exactly).
- `value` (any, required) — JSON value coerced to the property's native type.

**Supported types in v0.4.0**

| FProperty C++ type | JSON value shape |
|---|---|
| `bool` | JSON bool |
| `int8`, `int16`, `int32`, `int64` | JSON number (range-checked) |
| `uint8`, `uint16`, `uint32`, `uint64` | JSON number (range-checked) |
| `float`, `double` | JSON number |
| `FString` | JSON string |
| `FText` | JSON string |
| `FName` | JSON string |
| `FVector` | `{x, y, z}` |
| `FVector2D` | `{x, y}` |
| `FRotator` | `{pitch, yaw, roll}` |
| `FLinearColor` | `{r, g, b, a}` (0–1 floats) |
| `FColor` | `{r, g, b, a}` (0–255 ints) |
| Enum (`UEnum`-decorated) | JSON string (enum value name) |
| `TSoftObjectPtr<T>` | JSON string (asset path) |
| **USTRUCT** (any reflected struct, recursive) | JSON object — fields coerced individually |
| **TArray<T>** | JSON array (element-wise) |
| **TMap<FString \| FName, V>** | JSON object (string keys only in v0.4.0) |
| **TSet<T>** | JSON array (deduplicated server-side) |
| **FObjectProperty** (hard UObject*) | JSON string (asset path) — `LoadObject`'d + class-checked |

FInstancedStruct is deferred to v0.4.x. The error message for unsupported types includes the FProperty class name (e.g. `"StructProperty(MyCustomStruct)"`) so the caller can fall back to `execute_unreal_python`.

**Property-name path traversal**

The `property` param accepts dotted paths like `"RootComponent.RelativeLocation"` to access nested properties via recursive traversal. Each segment either traverses an `FStructProperty` (accessing the struct's memory directly) or dereferences an `FObjectProperty` pointer. Errors include the path: `path_traversal_null at .RootComponent: cannot continue through null UObject`. Recursion depth is capped at 8 levels.

**Result**
- `ok` (bool)
- `name` (string) — actor FName
- `property` (string) — property name as given
- `old_value` (any) — previous value in JSON form
- `new_value` (any) — applied value in JSON form

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"set_actor_property","params":{
  "name": "PointLight_2",
  "property": "Intensity",
  "value": 12000.0
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "name": "PointLight_2",
  "property": "Intensity",
  "old_value": 5000.0,
  "new_value": 12000.0
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `set_actor_property: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `name`, `property`, or `value` was missing. |
| `actor_not_found` | No actor matches the given name. |
| `ambiguous_actor` | Label matches multiple actors. Error message lists candidate FNames. |
| `field_not_found` | During path traversal, a segment does not exist on the current struct. |
| `wrong_object_class` | During path traversal, an `FObjectProperty` points to an object of an incompatible class. |
| `recursion_depth_exceeded` | Path traversal exceeded the 8-level depth limit. |
| `path_traversal_null` | During path traversal, encountered a null `FObjectProperty` pointer. Error message includes the path segment. |
| `path_traversal_invalid_type` | Path segment refers to a property that is neither `FStructProperty` nor `FObjectProperty`, blocking further traversal. |
| `property_not_found` | No `UPROPERTY` with that name exists on the actor's class (when property is not a dotted path). |
| `unsupported_property_type` | Property exists but its FProperty class is not supported (e.g., FInstancedStruct). The error message includes the FProperty class name. |
| `value_coercion_failed` | Value could not be coerced to the property type (e.g. string given for an int, range overflow). |

---

## add_component

Attach a new component to an existing actor at runtime by label or FName. Uses the hybrid label-or-FName identification scheme. The `relative_transform` parameter uses the same `{location, rotation, scale}` shape as `set_actor_transform`, expressed relative to the parent component. For `USceneComponent` subclasses the component is attached to the root component by default, or to a named component (and optional socket) via `attach_to` and `socket`.

**Params**
- `actor_name` (string, required) — label or FName of the host actor.
- `class_path` (string, required) — component class path, e.g. `/Script/Engine.StaticMeshComponent`, `/Script/Engine.PointLightComponent`.
- `component_name` (string, optional) — FName for the new component; defaults to UE's auto-name.
- `attach_to` (string, optional) — name of an existing component on the actor to attach to; defaults to the root component.
- `socket` (string, optional) — socket name on the parent component.
- `relative_transform` (object, optional, default identity) — `{location: {x,y,z}, rotation: {pitch,yaw,roll}, scale: {x,y,z}}` relative to the parent.

**Result**
- `ok` (bool)
- `actor` (string) — actor FName
- `component` (string) — new component FName
- `class` (string) — component class name
- `attached_to` (string) — parent component name

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"add_component","params":{
  "actor_name": "SmokeCube1",
  "class_path": "/Script/Engine.PointLightComponent",
  "relative_transform": {"location": {"x": 0, "y": 0, "z": 100}}
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "actor": "StaticMeshActor_3",
  "component": "PointLightComponent_1",
  "class": "PointLightComponent",
  "attached_to": "DefaultSceneRoot"
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `add_component: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `actor_name` or `class_path` was missing or empty. |
| `actor_not_found` | No actor matches the given name. |
| `ambiguous_actor` | Label matches multiple actors. Error message lists candidate FNames. |
| `invalid_component_class` | Class not found, abstract, or not a `USceneComponent` / `UActorComponent` subclass. |
| `attach_target_not_found` | `attach_to` was specified but no component with that name exists on the actor. |
| `socket_not_found` | `socket` was specified but the parent component does not have that socket. |

---

## get_log_lines

Read recent UE Output Log entries from the in-process ring buffer maintained by `FUCMCPLogCapture`. The buffer holds the last 1000 log lines across all categories. Useful for inspecting errors, warnings, and debug output without opening the UE Output Log panel.

**Params**
- `count` (int, optional, default `100`) — maximum number of lines to return. Capped at 1000.
- `category_filter` (string, optional) — case-insensitive substring filter on the log category (e.g. `"LogTemp"` returns all categories containing "LogTemp").
- `min_verbosity` (string, optional, default `"Log"`) — minimum severity to include. One of `Fatal`, `Error`, `Warning`, `Display`, `Log`, `Verbose`, `VeryVerbose`. Lines at or above this level are included (Fatal is highest severity; VeryVerbose is lowest).

**Result**
- `ok` (bool)
- `returned` (int) — number of lines in the response array
- `lines` (array) — `{timestamp, category, verbosity, message}` per line, in chronological order (oldest first within the filtered set)

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"get_log_lines","params":{
  "count": 10,
  "min_verbosity": "Warning"
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "returned": 2,
  "lines": [
    {"timestamp": "2026.05.08-13.30.45", "category": "LogTemp", "verbosity": "Warning", "message": "something unexpected"},
    {"timestamp": "2026.05.08-13.31.02", "category": "LogUCMCP", "verbosity": "Error", "message": "handler error"}
  ]
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `get_log_lines: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `invalid_verbosity` | `min_verbosity` is not one of the seven accepted values. |

---

## execute_console_command

Execute a UE console command in the editor world context and optionally capture its output. Suitable for stat commands (`stat fps`, `stat unit`), CVar mutations (`r.ScreenPercentage 50`), and any other console command the running editor supports. Output is captured via a `FStringOutputDevice` for the duration of the call; it does not permanently redirect the console.

**Params**
- `command` (string, required) — the console command to execute (e.g. `"stat fps"`, `"r.ScreenPercentage 50"`).
- `capture_output` (bool, optional, default `true`) — when `true`, captures and returns the command output string. When `false`, output flows to the normal Output Log and the result's `output` field is empty.

**Result**
- `ok` (bool)
- `command` (string) — echo of the command that was executed
- `output` (string) — captured console output, or `""` if `capture_output=false`

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"execute_console_command","params":{
  "command": "stat fps"
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "command": "stat fps",
  "output": "Frame: 16.6ms (60 FPS)\n..."
}}
```

**Errors:** Returned as JSON-RPC `error.message` strings in the format `execute_console_command: <error_code>: <human-readable detail>`. Stable codes:

| Code | Trigger |
|---|---|
| `missing_required_field` | `command` was missing or empty. |
| `command_execution_failed` | `GEngine` or `GEditor` is null (not running in an editor context). |

---

## inspect_asset

Read every fact the asset registry knows about a single asset.

**Params**
- `path` (string, required) — asset path or package path. Both forms accepted: `/Game/Textures/T_Stone` and `/Game/Textures/T_Stone.T_Stone`.

**Result**
- `name` — leaf name (no folder, no `.Name` suffix)
- `package_path` — `/Game/...` form without `.Name`
- `asset_path` — same path with `.Name` suffix
- `class` — leaf class name (e.g. `Texture2D`)
- `class_path` — full class path (e.g. `/Script/Engine.Texture2D`)
- `tags` (object) — all registry tags coerced to strings via `FAssetTagValueRef::AsString()`
- `dependencies` (array of string) — package paths this asset hard-references
- `referencers` (array of string) — package paths that hard-reference this asset
- `package_size_bytes` — integer (on-disk size in bytes) or `null` (transient/in-memory asset)

**Errors:** `missing_required_field`, `asset_not_found`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_asset","params":{
  "path": "/Engine/BasicShapes/Cube"
}}
```

---

## move_asset

Move an asset to a different folder. The leaf name does not change.

**Params**
- `path` (string, required) — source asset path. Both forms accepted (with or without `.Name` suffix).
- `dest_folder` (string, required) — destination folder under `/Game/` or `/Engine/`.

**Result**
- `ok` (bool)
- `old_path` (string) — the source asset path before the move
- `new_path` (string) — the destination asset path after the move

**Errors:** `missing_required_field`, `asset_not_found`, `invalid_dest_folder`, `dest_exists`, `rename_failed`.

**Behavior note:** UE creates a redirector at the source path so existing references in other assets continue to resolve. Redirectors persist until you run *Fix Up Redirectors* in the Content Browser.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"move_asset","params":{
  "path": "/Game/Textures/T_Stone",
  "dest_folder": "/Game/Textures/Environment"
}}
```

---

## rename_asset

Rename an asset's leaf name. The containing folder does not change.

**Params**
- `path` (string, required) — source asset path. Both forms accepted (with or without `.Name` suffix).
- `new_name` (string, required) — new leaf name. No `/` or `.` characters allowed.

**Result**
- `ok` (bool)
- `old_path` (string) — the source asset path before the rename
- `new_path` (string) — the destination asset path after the rename

**Errors:** `missing_required_field`, `asset_not_found`, `invalid_asset_name`, `dest_exists`, `rename_failed`.

**Behavior note:** Same redirector behavior as `move_asset`. The old name continues to resolve via the redirector until *Fix Up Redirectors* is run.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"rename_asset","params":{
  "path": "/Game/Textures/T_Stone",
  "new_name": "T_StoneAlbedo"
}}
```

---

## duplicate_asset

Copy an asset to a new path. The source asset is preserved at its original location; the duplicate is created at `dest_path`.

**Params**
- `path` (string, required) — source asset path. Both forms accepted (with or without `.Name` suffix).
- `dest_path` (string, required) — destination asset path. Must not already exist.

**Result**
- `ok` (bool)
- `src_path` (string) — the source asset path (normalized)
- `dest_path` (string) — the new asset path (normalized)

**Errors:** `missing_required_field`, `asset_not_found`, `dest_exists`, `duplicate_failed`.

**Behavior note:** Unlike `move_asset` and `rename_asset`, no redirector is created — duplication is a copy, not a relocation, so existing references continue to point at the source. Callers that want to switch references to the duplicate must update them explicitly. Like `save_asset`, a `duplicate_failed` error is most often a Source Control checkout failure or a destination folder that's read-only.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"duplicate_asset","params":{
  "path": "/Game/Textures/T_Stone",
  "dest_path": "/Game/Textures/Variants/T_Stone_Mossy"
}}
```

---

## delete_asset

Delete an asset from the project. By default, refuses if any other package references the asset.

**Params**
- `path` (string, required) — asset path to delete. Both forms accepted.
- `force` (bool, optional, default `false`) — when `true`, delete even if referenced.

**Result**
- `ok` (bool)
- `deleted_path` (string) — the asset path that was deleted

**Errors:** `missing_required_field`, `asset_not_found`, `has_referencers`, `delete_failed`.

**Safety**

UE's `EditorAssetLibrary::DeleteAsset` is documented as a force-delete that does **not** check referencers — it will happily delete a texture being used by 50 actors and cascade into broken references. The handler runs `IAssetRegistry::GetReferencers` first and refuses unless `force=true`. The `has_referencers` error message lists up to 5 referencer package names so the caller can see what would break.

`force=true` cannot be recovered via Undo. Make sure your source-control checkout has the asset before deleting.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"delete_asset","params":{
  "path": "/Game/Textures/T_Stone_OldVariant",
  "force": false
}}
```

If `T_Stone_OldVariant` is referenced by `M_Stone` and `M_StoneWet`, the response is:
```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"delete_asset: has_referencers: '/Game/Textures/T_Stone_OldVariant' is referenced by 2 package(s): /Game/Materials/M_Stone, /Game/Materials/M_StoneWet. Set force=true to delete anyway."}}
```

---

## inspect_sequence

Read the structure of a Level Sequence asset.

**Params**
- `path` (string, required) — Level Sequence asset path. Both forms accepted (with or without `.Name` suffix).

**Result**
- `name`, `package_path` — asset identity
- `tick_resolution` (int) — internal tick rate (typically 24000 for 24fps display rate × 1000 sub-frame divisor)
- `display_rate_fps` (number) — sequence's display frame rate (e.g. 24.0, 30.0)
- `playback_range` — `{start_frames, end_frames}` in **tick units** (divide by `tick_resolution / display_rate_fps` for display frames)
- `bindings` — array of `{guid, name, type, bound_actor_label?}` entries; `type` is `"possessable"` or `"spawnable"`. `bound_actor_label` is only present for possessables and equals the binding name (which itself was set to the actor's label by `bind_actor_to_sequence`).
- `tracks` — array of `{name, class, section_count, binding_guid}` entries. Master tracks have `binding_guid: ""`; binding-attached tracks carry their owning binding's GUID.

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_sequence`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_sequence","params":{
  "path": "/Game/Cinematics/MainCinematic"
}}
```

---

## create_sequence

Create a new empty Level Sequence asset.

**Params**
- `path` (string, required) — destination folder under `/Game/`.
- `name` (string, required) — leaf asset name. No `/` or `.` characters allowed.
- `display_rate_fps` (number, optional, default `30.0`) — sequence display frame rate.
- `playback_end_frames` (int, optional, default `240`) — end of playback range in **display** frames (not ticks).

**Result**
- `ok`, `asset_path`, `package_path`
- `display_rate_fps` (number) — final value applied to the MovieScene (round-tripped through `FFrameRate(N, 1000)`)
- `playback_range` — `{start_frames, end_frames}` in **tick units** (the conversion uses UE's `FFrameRate::TransformTime`)

**Errors:** `missing_required_field`, `invalid_path`, `invalid_asset_name`, `dest_exists`, `create_failed`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"create_sequence","params":{
  "path": "/Game/Cinematics",
  "name": "MyNewSequence",
  "display_rate_fps": 24.0,
  "playback_end_frames": 144
}}
```

---

## bind_actor_to_sequence

Add a level actor as a possessable binding to a Level Sequence.

**Params**
- `sequence_path` (string, required) — Level Sequence asset path. Both forms accepted.
- `actor_name` (string, required) — actor label or FName in the current editor world. Hybrid identification: ambiguous labels return `ambiguous_actor` listing all candidates' FNames.

**Result**
- `ok` (bool)
- `sequence_path` (string) — normalized asset path
- `binding_guid` (string) — GUID of the created possessable binding (canonical hyphenated form)
- `actor_label` (string) — the actor's `GetActorLabel()` value, also stored as the binding's name
- `binding_type` (string) — always `"possessable"` in v0.8.0

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_sequence`, `actor_not_found`, `ambiguous_actor`, `bind_failed`.

**Behavior note:** v0.8.0 only supports possessables (existing actors). Spawnables — per-sequence-instance actors instantiated from a template — are deferred to v0.8.x. Use `execute_unreal_python` for spawnable workflows in the meantime.

The binding name is set to the actor's `GetActorLabel()`, which means `inspect_sequence` reports it as `bound_actor_label` for round-trip consistency.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"bind_actor_to_sequence","params":{
  "sequence_path": "/Game/Cinematics/MainCinematic",
  "actor_name": "MyHero_Actor"
}}
```

---

## create_material_instance

Create a `UMaterialInstanceConstant` asset and set its parent to an existing `UMaterial` or `UMaterialInstance`.

**Params**
- `parent_path` (string, required) — path of the parent material or material instance.
- `path` (string, required) — destination folder under `/Game/`.
- `name` (string, required) — leaf asset name. No `/` or `.` allowed.

**Result**
- `ok`, `asset_path`, `package_path`
- `parent_path` (string) — full path of the parent (echoes back what was set)

**Errors:** `missing_required_field`, `invalid_path`, `invalid_asset_name`, `parent_not_found`, `parent_not_a_material`, `dest_exists`, `create_failed`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"create_material_instance","params":{
  "parent_path": "/Game/Materials/M_Stone",
  "path": "/Game/Materials/Instances",
  "name": "MI_Stone_Wet"
}}
```

The new instance starts with no parameter overrides; use `set_mi_parameter` to customize it.

---

## set_mi_parameter

Override a scalar/vector/texture parameter on a `UMaterialInstanceConstant`. Single handler with a `type` discriminator — the JSON `value` shape varies by type.

**Params**
- `path` (string, required) — material instance asset path.
- `parameter` (string, required) — parameter name as declared on the parent material.
- `type` (string, required) — one of `"scalar"`, `"vector"`, `"texture"`.
- `value` (varies by type, required):
  - `"scalar"` → number, e.g. `0.75`
  - `"vector"` → object `{r, g, b, a}` (each in `[0, 1]`; `a` defaults to `1.0` if omitted)
  - `"texture"` → string asset path of a `UTexture`

**Result**
- `ok`, `path`, `parameter`, `type` — echo back what was set
- `applied_value` — same shape as input `value` (textures normalized to canonical asset path)

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_material_instance`, `invalid_parameter_type`, `invalid_value_shape`, `texture_not_found`, `parameter_not_applied`.

`parameter_not_applied` fires when UE's setter returns false — typically because the parameter name isn't declared on the parent material. Use `inspect_material` on the parent first to learn what parameters are available.

**Examples**

Scalar:
```json
{"jsonrpc":"2.0","id":1,"method":"set_mi_parameter","params":{
  "path": "/Game/Materials/MI_Stone_Wet",
  "parameter": "Roughness",
  "type": "scalar",
  "value": 0.85
}}
```

Vector:
```json
{"jsonrpc":"2.0","id":1,"method":"set_mi_parameter","params":{
  "path": "/Game/Materials/MI_Stone_Wet",
  "parameter": "BaseColorTint",
  "type": "vector",
  "value": {"r": 0.6, "g": 0.6, "b": 0.7}
}}
```

Texture:
```json
{"jsonrpc":"2.0","id":1,"method":"set_mi_parameter","params":{
  "path": "/Game/Materials/MI_Stone_Wet",
  "parameter": "BaseColorMap",
  "type": "texture",
  "value": "/Game/Textures/T_Stone_Wet_D"
}}
```

---

## inspect_material

List parameter names declared by a `UMaterial` or `UMaterialInstance`. Discovery tool: pair with `find_assets` to find materials, then `inspect_material` to learn what parameters are available, then `set_mi_parameter` to override.

**Params**
- `path` (string, required) — material asset path. Both forms accepted.

**Result**
- `name`, `package_path`, `class`
- `scalar_parameters` (array of string) — sorted alphabetically
- `vector_parameters` (array of string) — sorted alphabetically
- `texture_parameters` (array of string) — sorted alphabetically
- `static_switch_parameters` (array of string) — sorted alphabetically

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_material`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_material","params":{
  "path": "/Game/Materials/M_Stone"
}}
```

Static-switch parameters are listed for visibility but cannot be set via `set_mi_parameter` in v0.9.0 — that mutator is deferred to v0.9.x because it triggers shader recompiles.

---

## inspect_material_instance

Read a `UMaterialInstanceConstant`'s parent and currently-overridden parameter values. Only **overridden** parameters appear in the output — parameters inherited unchanged from the parent are not listed. Pair with `inspect_material` (on the parent path) to see the full set of available parameters.

**Params**
- `path` (string, required) — material instance asset path.

**Result**
- `name`, `package_path`
- `parent_path` (string) — full path of the parent material; empty string if no parent (rare; usually means a partially-initialized asset)
- `scalar_overrides` (object) — `{parameter_name: number}` map of overridden scalar parameters
- `vector_overrides` (object) — `{parameter_name: {r, g, b, a}}` map of overridden vector parameters
- `texture_overrides` (object) — `{parameter_name: asset_path_string}` map of overridden texture parameters; empty string if a texture override exists but the texture pointer is null

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_material_instance`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_material_instance","params":{
  "path": "/Game/Materials/MI_Stone_Wet"
}}
```

---

## run_python_file

Execute a `.py` file from disk via the editor's embedded Python interpreter. Complement to `execute_unreal_python` — for non-trivial scripts, embedding the source as a JSON-RPC string requires double-escaping every quote and backslash. Pointing at a file on disk eliminates that pain entirely.

**Params**
- `path` (string, required) — filesystem path to a `.py` file. Absolute or relative; relative paths resolve via `FPaths::ConvertRelativePathToFull` against the editor's CWD (typically the project root for editor sessions).

**Result**
- `ok` (bool) — `true` if the script ran without raising
- `output` (string) — `FPythonCommandEx::CommandResult`. **Caveat:** `ExecuteFile` mode does NOT return script stdout / eval-result through this field; it's `"None"` for file-mode runs. To round-trip a result back, the script should emit `unreal.log("__MARKER__<json>__END__")` and the caller retrieves it via `get_log_lines` with `category_filter: "LogPython"`. See `scripts/seed_test_project.py` for the canonical pattern.
- `path` (string) — the resolved absolute path that was executed. Useful for confirming the path resolution.

**Errors:** `missing_required_field`, `file_not_found`, `python_unavailable`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"run_python_file","params":{
  "path": "C:/Users/me/Desktop/scripts/setup_lighting.py"
}}
```

Or relative to the editor's CWD (typically the project root):
```json
{"jsonrpc":"2.0","id":1,"method":"run_python_file","params":{
  "path": "Saved/MyScripts/quick_fixup.py"
}}
```

---

## fix_up_redirectors

Cascade-update consumers of `UObjectRedirector` assets under a folder, then delete the redirector `.uasset` stubs. This is the programmatic equivalent of right-clicking a folder in the Content Browser and selecting "Fix Up Redirectors in Folder" — the standard cleanup after `move_asset` / `rename_asset` operations or any project-wide reorganization that leaves stale path stubs behind.

**Params**
- `path` (string, required) — package path under which to recursively scan, e.g. `/Game/` or `/Game/Materials`. Required (no default) so a typo or missing param can't accidentally rewrite the entire project.

**Result**
- `ok` (bool) — `true` on dispatch success
- `path` (string) — the path that was scanned (with trailing slash normalized off)
- `redirectors_found` (int) — number of `UObjectRedirector` assets enumerated and queued for fixup
- `note` (string) — reminder that fixup may complete asynchronously when source-control is active. Use `IAssetTools::IsFixupReferencersInProgress()` from a follow-up `execute_unreal_python` call (or wait briefly) before assuming all redirectors are removed.

**Errors:** `missing_required_field`, `invalid_path`.

**Behavior notes**
- Uses `ERedirectFixupMode::DeleteFixedUpRedirectors` (the editor's default) — successfully-redirected stubs are deleted; only stubs whose consumers couldn't be updated remain.
- `bCheckoutDialogPrompt = false` — no interactive UI; runs silently against any non-read-only files.
- Returns `redirectors_found = 0` if the path exists but contains none — that's a success state, not an error.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"fix_up_redirectors","params":{
  "path": "/Game/Materials"
}}
```

Or for the whole `/Game/` tree (typical post-bulk-rename cleanup):
```json
{"jsonrpc":"2.0","id":1,"method":"fix_up_redirectors","params":{
  "path": "/Game/"
}}
```

---

## apply_python_to_selection

Run user Python with the editor's current selection pre-bound as Python locals. Convenience wrapper around `execute_unreal_python` that injects boilerplate to fetch:

| Local | Type | Source |
|---|---|---|
| `selection` | `list[unreal.Actor]` | `unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()` (UE 5.x), with fallback to `unreal.EditorLevelLibrary.get_selected_level_actors()` |
| `selected_assets` | `list[unreal.Object]` | `unreal.EditorUtilityLibrary.get_selected_assets()` |

Both bindings use try/except so a missing API in older Python plugin builds defaults to an empty list rather than killing the script. The user's code runs *after* the boilerplate, so it can reference either name directly without re-implementing the lookup.

**Params**
- `code` (string, required) — Python source. The boilerplate above is prepended.

**Result**
- `ok` (bool) — `true` if the script ran without raising
- `output` (string) — `FPythonCommandEx::CommandResult`. Same caveat as `run_python_file` and `execute_unreal_python`: `ExecuteFile` mode returns `"None"` here. Round-trip results via `unreal.log("__MARKER__<json>__END__")` + `get_log_lines{category_filter:"LogPython"}`.
- `temp_script` (string) — path to the wrapper script that was written and executed (cleaned up after run via `ON_SCOPE_EXIT`)

**Errors:** `missing_required_field`, `python_unavailable`, `write_failed`.

**Example — translate every selected actor up by 100 units**
```json
{"jsonrpc":"2.0","id":1,"method":"apply_python_to_selection","params":{
  "code": "for a in selection:\n    loc = a.get_actor_location()\n    a.set_actor_location(unreal.Vector(loc.x, loc.y, loc.z + 100), False, False)\nunreal.log(f'__MOVED__{len(selection)}__END__')"
}}
```

**Example — print parent class of every selected asset**
```json
{"jsonrpc":"2.0","id":1,"method":"apply_python_to_selection","params":{
  "code": "import json\nresult = [{'name': a.get_name(), 'class': a.get_class().get_name()} for a in selected_assets]\nunreal.log('__SEL__' + json.dumps(result) + '__END__')"
}}
```

---

## compile_blueprint

Explicit Blueprint recompile via `FKismetEditorUtilities::CompileBlueprint`. Pairs with `edit_widget_tree`'s `compile=true` flag for users who want to compile a Blueprint **without** mutating it first — e.g. when a BP was modified externally via `execute_unreal_python` and now needs a recompile, or to recover from a `BS_Dirty` state.

**Params**
- `path` (string, required) — Blueprint asset path, e.g. `/Game/Blueprints/BP_MyActor`.
- `skip_save` (bool, optional, default `false`) — suppress the project's "Save On Compile" auto-save. Passes `EBlueprintCompileOptions::SkipSave` to UE.

**Result**
- `ok` (bool) — `true` unless the BP's status is `BS_Error` after compile
- `path` (string) — package path of the BP that was compiled
- `status` (string) — one of `up_to_date`, `up_to_date_with_warnings`, `error`, `dirty`, `unknown`, `being_created` (see UE 5.7's `EBlueprintStatus` at `Blueprint.h:41`)
- `saved` (bool) — whether auto-save was allowed (`!skip_save`)
- `note` (string, only when `status == "error"`) — pointer to `get_log_lines{category_filter:"LogBlueprint"}` for compile-error detail

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_blueprint`.

**Behavior notes**
- Default flags = `EBlueprintCompileOptions::None`. The full pipeline runs: skeleton class regen, node expansion, validation, code gen, reinstancing.
- Compile errors aren't reported in the `result` directly — they're emitted by `FKismetCompilerContext` to `LogBlueprint`. Use `get_log_lines{category_filter:"LogBlueprint", count:50}` afterward to inspect them.
- Save-On-Compile is a project setting (`Project Settings → Editor → Blueprint`), not a hardcoded behavior. `skip_save:true` gives you the bypass when you want to compile transiently without touching disk.

**Example — compile a BP and check status**
```json
{"jsonrpc":"2.0","id":1,"method":"compile_blueprint","params":{
  "path": "/Game/Blueprints/BP_MyActor"
}}
```

**Example — recompile without auto-save (for transient work)**
```json
{"jsonrpc":"2.0","id":1,"method":"compile_blueprint","params":{
  "path": "/Game/Blueprints/BP_MyActor",
  "skip_save": true
}}
```

---

## get_console_variable

Read a single UE Console Variable by name. Returns the current value in all four representations (string / int / float / bool), the detected type, the read-only flag, the human-readable last-setter (e.g. `"Console"`, `"DeviceProfile"`, `"ProjectSetting"`), and the help text from the CVar's registration.

Distinct from `execute_console_command`: this reads CVar state directly via `IConsoleManager::FindConsoleVariable`, never invokes the console exec engine. Use this when you want the CVar's value without side effects, or when you want type metadata that an Exec call can't surface.

**Params**
- `name` (string, required) — exact CVar name, case-sensitive (e.g. `"r.ScreenPercentage"`, `"Slate.bAllowToolTips"`).

**Result**
- `ok` (bool)
- `name` (string) — echo of the requested CVar name
- `type` (string) — one of `int`, `float`, `bool`, `string`, `unknown`. Derived from `IConsoleVariable::IsVariable*()`.
- `read_only` (bool) — `(GetFlags() & ECVF_ReadOnly) != 0`. Read-only CVars only accept `set_console_variable` during very early init.
- `set_by` (string) — humanized last-setter, derived from `GetConsoleVariableSetByName(GetFlags())`. One of `Constructor`, `Scalability`, `GameSetting`, `ProjectSetting`, `SystemSettingsIni`, `PluginLowPriority`, `DeviceProfile`, `PluginHighPriority`, `GameOverride`, `ConsoleVariablesIni`, `Hotfix`, `Preview`, `Commandline`, `Code`, `Console`.
- `value_string` (string) — `GetString()`
- `value_int` (number) — `GetInt()`, coerced from underlying type
- `value_float` (number) — `GetFloat()`, coerced from underlying type
- `value_bool` (bool) — `GetBool()`, coerced from underlying type
- `help` (string) — `GetHelp()` text, or `""` if none

**Errors:** `missing_required_field`, `cvar_not_found`.

| Code | Trigger |
|---|---|
| `missing_required_field` | `name` was missing or empty. |
| `cvar_not_found` | `IConsoleManager::FindConsoleVariable` returned null. The error message points to `execute_console_command` for cases where the name is actually a console *command* rather than a CVar. |

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"get_console_variable","params":{
  "name": "r.ScreenPercentage"
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "name": "r.ScreenPercentage",
  "type": "float",
  "read_only": false,
  "set_by": "Console",
  "value_string": "100",
  "value_int": 100,
  "value_float": 100.0,
  "value_bool": true,
  "help": "To render in lower resolution and upscale for better performance..."
}}
```

---

## set_console_variable

Mutate a UE Console Variable by name. The `value` param is polymorphic — accepts JSON `string`, `number`, or `boolean` — and is coerced to the canonical string form (`"42"`, `"1.5"`, `"1"`/`"0"` for bool) before being passed to `IConsoleVariable::Set`. UE's underlying parser handles type coercion against the CVar's declared type on the receiving side.

**SetBy priority:** all `Set` calls are issued at `ECVF_SetByConsole` priority — the highest tier (matches "user typed it in the editor console" semantics). This guarantees the call overrides values set by ini files, scalability profiles, code defaults, etc., rather than being silently dropped by UE's priority arbitration.

**Pre-rejection:** CVars with the `ECVF_ReadOnly` flag (`r.RHIThreadEnable`, `r.SkinCache.CompileShaders`, etc.) only accept `Set` during very early initialization. After editor startup, they silently no-op. Rather than letting that disappear, the handler returns a `read_only` error early.

**Post-verify:** after the `Set`, the handler reads the value back via `GetString()` and includes both the requested and the actual landed value. A mismatch (rare with `ECVF_SetByConsole` but possible — e.g. a CVar with a custom on-set callback that rejects certain values) is surfaced as a `note` field, not an error, since UE accepted the request.

**Params**
- `name` (string, required) — exact CVar name, case-sensitive.
- `value` (string | number | bool, required) — new value. JSON numbers and bools are formatted to canonical strings before being passed to UE. To set a string CVar, send a JSON string; to set a numeric CVar, send a JSON number; to set a bool CVar, send a JSON bool (or `0`/`1` as a number).

**Result**
- `ok` (bool)
- `name` (string)
- `type` (string) — one of `int`, `float`, `bool`, `string`, `unknown`
- `requested_value` (string) — the canonical string form that was passed to `IConsoleVariable::Set`
- `value_string` / `value_int` / `value_float` / `value_bool` — the post-set values in all four representations
- `set_by` (string) — humanized post-set last-setter (typically `"Console"` since that's what we set)
- `note` (string, only when post-set value differs from requested) — diagnostic explaining the mismatch

**Errors:** `missing_required_field`, `cvar_not_found`, `read_only`, `invalid_value_type`.

| Code | Trigger |
|---|---|
| `missing_required_field` | `name` or `value` missing/empty. |
| `cvar_not_found` | `IConsoleManager::FindConsoleVariable` returned null. |
| `read_only` | The CVar has `ECVF_ReadOnly`. The error message points to `DefaultEngine.ini` `[ConsoleVariables]` and Scalability/DeviceProfile inis as the legitimate setting sites. |
| `invalid_value_type` | `value` is a JSON object, array, or null. Only string / number / bool are accepted. |

**Example — set a numeric CVar**
```json
{"jsonrpc":"2.0","id":1,"method":"set_console_variable","params":{
  "name": "r.ScreenPercentage",
  "value": 75
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "name": "r.ScreenPercentage",
  "type": "float",
  "requested_value": "75",
  "value_string": "75",
  "value_int": 75,
  "value_float": 75.0,
  "value_bool": true,
  "set_by": "Console"
}}
```

**Example — set a bool CVar**
```json
{"jsonrpc":"2.0","id":1,"method":"set_console_variable","params":{
  "name": "Slate.bAllowToolTips",
  "value": false
}}
```

**Example — read-only rejection**
```json
{"jsonrpc":"2.0","id":1,"method":"set_console_variable","params":{
  "name": "r.SkinCache.CompileShaders",
  "value": 1
}}
```
```json
{"jsonrpc":"2.0","id":1,"error":{
  "code": -32603,
  "message": "set_console_variable: read_only: 'r.SkinCache.CompileShaders' has the ECVF_ReadOnly flag and only accepts changes during early initialization. Set it via DefaultEngine.ini ([ConsoleVariables] section) or a Scalability/DeviceProfile ini for persistent config."
}}
```

---

## poll_events

**Tier 2 entrypoint (PR #40 / v0.11.0).** Drain editor events fired since the caller's last poll. UE delegates push structured events into a 1000-entry ring buffer (`FUCMCPEventBus`); this handler returns the slice with `seq > since_seq`, capped at `max_count`.

This is the inversion of the rest of the catalog: instead of Claude querying UE state on demand, UE notifies Claude when state changes. Combined with regular polling, it enables reactive flows like "user dropped a chair into the level → reposition camera" or "asset import finished → trigger texture-config pipeline". See [`docs/superpowers/specs/2026-05-09-tier2-event-push-design.md`](superpowers/specs/2026-05-09-tier2-event-push-design.md) for the full Tier 2 multi-PR roadmap.

**Wired event types (8 total — PR #40 starter set + PR #41 expansion):**

| Event | Source | Payload fields |
|---|---|---|
| `actor_spawned` | `UEngine::OnLevelActorAdded(AActor*)` | `actor_label`, `actor_name`, `class`, `level` |
| `actor_deleted` | `UEngine::OnLevelActorDeleted(AActor*)` | `actor_label`, `actor_name`, `class`, `level` |
| `asset_added` | `IAssetRegistry::OnAssetAdded(const FAssetData&)` (TS_ delegate, fires from background scan threads) | `package_path`, `asset_path`, `name`, `class`, `class_path` |
| `asset_removed` | `IAssetRegistry::OnAssetRemoved(const FAssetData&)` (TS_) | `package_path`, `asset_path`, `name`, `class`, `class_path` |
| `asset_renamed` | `IAssetRegistry::OnAssetRenamed(const FAssetData&, const FString&)` (TS_) | `new_asset_path`, `old_asset_path`, `new_package_path`, `name`, `class`, `class_path` |
| `asset_post_import` | `FEditorDelegates::OnAssetPostImport(UFactory*, UObject*)` | `asset_path`, `name`, `class`, `factory` |
| `level_post_save` | `FEditorDelegates::PostSaveWorldWithContext(UWorld*, FObjectPostSaveContext)` | `level` |
| `map_changed` | `FEditorDelegates::MapChange(uint32 MapChangeEventFlags)` | `flags` (raw bitmap), `flag_names` (decoded array — `"new_map"` / `"map_rebuild"` / `"world_torn_down"`) |

Note on `asset_added` vs `asset_post_import`: `asset_added` fires for **any** new registry entry — including the project's startup scan (high volume) and in-memory creations. `asset_post_import` fires only when a `UFactory` actually imports an asset (single `import_texture`, batch reimport, drag-and-drop). If you want to react specifically to user-driven imports, filter on `asset_post_import`; if you want every new asset regardless of provenance, use `asset_added`.

Future PRs will add `blueprint_compiled` (no global delegate today; needs per-BP subscription), `mi_parameter_changed`, plus a `wait_for_events` long-poll variant for sub-second latency.

**Params**
- `since_seq` (int, optional, default `-1`) — return events with `seq >= since_seq` (**inclusive cursor**). `-1` = "from oldest buffered". On the first poll, leave at default to discover the current `next_seq`; on subsequent polls, pass the previous response's `next_seq` to consume only newly-fired events. Inclusive semantics matter: `next_seq` is the id about to be assigned (not yet pushed), so the next event to fire will land at exactly that seq, and an exclusive filter would silently drop it.
- `max_count` (int, optional, default `100`) — cap returned events. Hard max `1000` (= ring buffer size). Must be a finite integer; fractional values (e.g. `0.5`) are rejected with `invalid_value_shape` to prevent silent truncation to 0 from corrupting the caller's cursor state.
- `event_filter` (array of string, optional) — substring filters on event type names (e.g. `["actor_spawned", "asset_"]`). Multiple entries are OR-combined. Empty / omitted = no filter.

**Result**
- `ok` (bool)
- `next_seq` (int) — the seq the next-fired event would receive. Pass back as `since_seq` on the next poll for steady-state delta consumption.
- `first_seq_in_buffer` (int) — smallest seq currently in the ring (or `-1` if buffer is empty).
- `returned` (int) — count of events in `events` (≤ `max_count`).
- `dropped` (bool) — `true` iff `since_seq` was below `first_seq_in_buffer` (some events the caller asked for have been evicted from the ring). Recover by re-syncing whatever editor state matters via the explicit query handlers (`get_actors_in_level`, `find_assets`, etc.) and resume polling with `next_seq` from the response.
- `events` (array) — each entry has `seq` (int), `event` (string event type), `ts` (string `YYYY.MM.DD-HH.MM.SS`), `data` (object with event-specific payload).
- `note` (string, only when `dropped=true`) — diagnostic explaining the recovery action.

**Errors:** `invalid_value_shape`.

| Code | Trigger |
|---|---|
| `invalid_value_shape` | `since_seq` / `max_count` not numeric, `max_count` ≤ 0, `event_filter` not an array, or `event_filter` element not a string. |

**Behavior notes**
- The asset-registry initial scan at editor startup floods `asset_added` for every asset in the project. The 1000-entry ring will overflow; subsequent polls with a small `since_seq` will see `dropped=true` until the caller catches up. Workflows that don't care about startup-scan events should poll once after startup with the latest `next_seq` and discard the snapshot, then begin consuming deltas.
- The bus is type-agnostic: adding new event sources in future PRs means adding lambda subscriptions in `UnrealClaudeMCPModule::StartupModule`, not changing this handler or the bus itself.
- `IAssetRegistry::OnAssetAdded` is a `TS_` (thread-safe) delegate — it can fire from background threads. The bus's `FCriticalSection` discipline handles this safely.

**Example — first poll (discovery)**
```json
{"jsonrpc":"2.0","id":1,"method":"poll_events","params":{}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "next_seq": 4523,
  "first_seq_in_buffer": 3523,
  "returned": 100,
  "dropped": false,
  "events": [
    {"seq": 3523, "event": "asset_added", "ts": "2026.05.09-16.42.01",
     "data": {"package_path":"/Game/Textures/T_Stone","asset_path":"/Game/Textures/T_Stone.T_Stone",
              "name":"T_Stone","class":"Texture2D","class_path":"/Script/Engine.Texture2D"}},
    "..."
  ]
}}
```

**Example — steady-state delta consumption**
```json
{"jsonrpc":"2.0","id":2,"method":"poll_events","params":{
  "since_seq": 4523,
  "event_filter": ["actor_spawned", "actor_deleted"]
}}
```

---

## wait_for_events

**Tier 2 PR #42 / v0.11.x — bridge-side composition tool.** Repeatedly calls `poll_events` at `poll_interval_ms` cadence until matching events arrive or `timeout_ms` expires. Same buffer, cursor semantics, and event payloads as `poll_events`; adds bounded waiting and a `timed_out` field.

**Why this is implemented in the bridge (Python), not UE (C++):**

The MCP dispatcher runs synchronously inside `FUCMCPServer::TickClients`, which is an `FTSTicker` callback on UE's **game thread**. A C++ wait handler would freeze the same thread that *fires most editor delegates* (`actor_spawned`, `actor_deleted`, `map_changed`, `level_post_save`, etc., all game-thread events). Result: the wait would deterministically time out for game-thread events because the game thread is asleep — the events that should fire during the wait literally cannot fire.

The bridge runs in a separate Python process, so its `time.sleep` doesn't block UE at all. UE's game thread keeps running between polls (each poll is ~1ms under the bus's lock), events fire normally, and the wait actually waits for things that can happen.

This is the first **synthetic tool** — a bridge-side implementation that composes UE handlers (`poll_events` here) into a higher-level operation. Future tools that compose multiple UE handlers without needing new C++ can follow this same pattern.

**When to use `wait_for_events` vs `poll_events`:**

- **`poll_events`** for steady-state polling at human-meaningful intervals (1-2 s). Single round-trip per call.
- **`wait_for_events`** when you want sub-second latency for a specific reactive workflow ("user dropped a chair → reposition camera within 200 ms"). One MCP call → multiple cheap UE round-trips behind the scenes.

**Params**
- `timeout_ms` (int, optional, default `500`) — maximum wait in milliseconds. Hard cap `30000` (30 s); over-cap values are silently clamped.
- `poll_interval_ms` (int, optional, default `100`, range `25-1000`) — bridge-side polling cadence. Lower = faster reaction but more UE round-trips. Below 25 ms the round-trip overhead dominates; above 1 s defeats the long-poll purpose. Out-of-range values are silently clamped to the bracket.
- `since_seq` (int, optional, default `-1`) — same as `poll_events`: events with `seq >= since_seq` are returned (inclusive cursor).
- `max_count` (int, optional, default `100`) — cap returned events. Hard max `1000`.
- `event_filter` (array of string, optional) — substring filters on event type names; OR-combined.

**Result** (same shape as `poll_events`, plus `timed_out`)
- `ok` (bool)
- `next_seq` (int) — pass back as `since_seq` on the next call
- `first_seq_in_buffer` (int) — smallest seq currently buffered (or `-1` if empty)
- `returned` (int) — count of events in `events`
- `dropped` (bool) — caller's `since_seq` fell below `first_seq_in_buffer` at some point during the wait
- `timed_out` (bool) — `true` iff the wait elapsed without any matching events arriving (and `dropped` is also false). Distinguishes "nothing happened" from "I missed events".
- `events` (array) — same shape as `poll_events`
- `note` (string, only when `dropped=true`) — diagnostic

**Errors:** `invalid_value_shape` (any of the optional numeric params with non-numeric or non-integer JSON type).

**Example — wait up to 1 second for the next actor_spawned**
```json
{"jsonrpc":"2.0","id":1,"method":"wait_for_events","params":{
  "timeout_ms": 1000,
  "since_seq": 4523,
  "event_filter": ["actor_spawned"]
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "next_seq": 4524,
  "first_seq_in_buffer": 3523,
  "returned": 1,
  "dropped": false,
  "timed_out": false,
  "events": [
    {"seq": 4523, "event": "actor_spawned", "ts": "2026.05.09-17.05.42",
     "data": {"actor_label":"StaticMeshActor_2", "actor_name":"StaticMeshActor_2",
              "class":"StaticMeshActor", "level":"/Game/Maps/MyMap"}}
  ]
}}
```

**Example — wait that times out (no events fired in the window)**
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "next_seq": 4524,
  "first_seq_in_buffer": 3524,
  "returned": 0,
  "dropped": false,
  "timed_out": true,
  "events": []
}}
```

**Example — fast reaction (low poll interval)**
```json
{"jsonrpc":"2.0","id":1,"method":"wait_for_events","params":{
  "timeout_ms": 5000,
  "poll_interval_ms": 50,
  "event_filter": ["asset_post_import"]
}}
```
At 50 ms polling, latency is bounded by ~50 ms + UE round-trip. UE round-trip is typically <5 ms on localhost, so total reaction time is ~55 ms.

---

## register_subscription

**Tier 2 PR #43.** Create a server-side cursor + filter on the `FUCMCPEventBus`. Returns a `subscription_id` (FGuid string) that pairs with `poll_subscription` (drain matched events) and `unsubscribe` (release). The cursor starts at the bus's current `next_seq` — subscribers see events fired **after** subscription, not historical ones (avoids the asset-registry initial-scan flood being delivered to every newly-created subscription).

**Why use subscriptions vs `poll_events`:**
- `poll_events` makes the client manage `since_seq` across calls; if the client loses cursor state (restart, crash), it has to re-sync.
- `register_subscription` + `poll_subscription` puts the cursor on the server. The client just calls `poll_subscription` with the id and gets only events it hasn't seen.
- The filter is also server-side — no need to re-send it on every poll. Modest wire-savings for filter-heavy workflows.

**Lifecycle (PR #43):** subscriptions live until explicit `unsubscribe`. **No TTL** in PR #43 — orphan subscriptions accumulate if clients don't clean up. If observable in real workflows, a follow-up PR will add inactivity-based cleanup.

**Params**
- `event_filter` (array of string, optional) — substring filters on event type names; OR-combined. Empty / omitted = no filter.

**Result**
- `ok` (bool)
- `subscription_id` (string) — FGuid in canonical hyphenated form (e.g. `"5C2D8F1A-..."`); pass to `poll_subscription` and `unsubscribe`
- `initial_next_seq` (int) — the seq the next-fired event would receive, captured at subscription time
- `event_filter` (array of string) — echo of the supplied filter

**Errors:** `invalid_value_shape`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"register_subscription","params":{
  "event_filter": ["actor_spawned", "asset_added"]
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "subscription_id": "5C2D8F1A-1234-5678-9ABC-DEF012345678",
  "initial_next_seq": 4523,
  "event_filter": ["actor_spawned", "asset_added"]
}}
```

---

## unsubscribe

Remove a subscription created via `register_subscription`. **Idempotent**: calling on an unknown id returns `ok=true` with `was_present=false` rather than an error, so callers can blanket-unsubscribe on shutdown without worrying about partial state.

**Params**
- `subscription_id` (string, required) — id returned by `register_subscription`.

**Result**
- `ok` (bool)
- `subscription_id` (string) — echo
- `was_present` (bool) — `true` if the subscription existed (and was removed); `false` if the id was unknown

**Errors:** `missing_required_field`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"unsubscribe","params":{
  "subscription_id": "5C2D8F1A-..."
}}
```

---

## poll_subscription

Drain events for a server-side subscription. The per-sub cursor advances atomically with the read — a successful poll never returns the same events twice. No `since_seq` param (cursor is server-side); no `event_filter` param (filter was set at `register_subscription` time and is immutable for that sub — re-register if you need a different filter).

**Params**
- `subscription_id` (string, required) — id returned by `register_subscription`.
- `max_count` (int, optional, default `100`) — cap returned events. Hard max `1000`.

**Result** (same per-event shape as `poll_events`)
- `ok` (bool)
- `subscription_id` (string) — echo
- `next_seq` (int) — bus's current next seq (informational; subscription cursor is server-managed)
- `first_seq_in_buffer` (int) — smallest seq in the ring (or `-1` if empty)
- `returned` (int) — count of events in `events`
- `dropped` (bool) — subscription cursor fell below `first_seq_in_buffer` between polls (events the sub asked for were evicted)
- `events` (array)
- `note` (string, only when `dropped=true`) — recovery hint

**Errors:** `missing_required_field`, `invalid_value_shape`, `subscription_not_found`.

| Code | Trigger |
|---|---|
| `missing_required_field` | `subscription_id` missing or empty. |
| `invalid_value_shape` | `max_count` non-numeric, fractional, non-finite, or ≤ 0. |
| `subscription_not_found` | The id is not in the registry (never created, already unsubscribed, or editor restarted between subscription and poll). |

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"poll_subscription","params":{
  "subscription_id": "5C2D8F1A-..."
}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "subscription_id": "5C2D8F1A-...",
  "next_seq": 4530,
  "first_seq_in_buffer": 3530,
  "returned": 3,
  "dropped": false,
  "events": [
    {"seq": 4527, "event": "actor_spawned", "ts": "...", "data": {"actor_label":"...", ...}},
    {"seq": 4528, "event": "asset_added",   "ts": "...", "data": {"package_path":"...", ...}},
    {"seq": 4529, "event": "actor_spawned", "ts": "...", "data": {"actor_label":"...", ...}}
  ]
}}
```

A subsequent `poll_subscription` call with the same id (and no new events fired in between) will return `returned: 0`, `events: []` — the cursor advanced past seq 4529 on the previous call, so there's nothing new to deliver.

---

## start_sleep_task

**Tier 2 PR #44 — task framework tracer.** Spawns a background worker on `EAsyncExecution::ThreadPool` that sleeps for `duration_ms` then completes the task with `result: { slept_ms }`. Returns immediately with the `task_id`; poll via `poll_task` and cancel via `cancel_task`.

The framework around this handler (`FUCMCPTaskRegistry`) is the durable bit; future task types (cooks, MRQ renders, lightmap bakes) will reuse the same registry and the same `poll_task` / `cancel_task` handlers, just with different `start_*_task` entry points.

**Why this exists:** validates the registry's threading and cancellation paths end-to-end without UE-specific complications. Also genuinely useful for "wait N ms then do something" workflows, though `wait_for_events` covers that case better when you're waiting on editor state.

**Params**
- `duration_ms` (int, required) — how long the worker should sleep. Hard cap **1 hour** (3,600,000 ms); over-cap requests are silently clamped.

**Result** (returned immediately by `start_sleep_task`, not after the sleep)
- `ok` (bool)
- `task_id` (string) — pass to `poll_task` / `cancel_task`
- `type` (string) — `"sleep"`
- `status` (string) — `"pending"` (the worker may already be running by the time you read this; poll for the latest)
- `duration_ms` (int) — echo of the (possibly-clamped) duration
- `note` (string) — operational hint

**Errors:** `missing_required_field`, `invalid_value_shape`.

**Lifecycle:** task lives in the registry until editor restart. PR #44 has **no TTL** — completed/cancelled/failed tasks accumulate. If observable, a follow-up PR will add cleanup.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"start_sleep_task","params":{"duration_ms": 5000}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "task_id": "5C2D8F1A-...",
  "type": "sleep",
  "status": "pending",
  "duration_ms": 5000,
  "note": "Worker spawned on EAsyncExecution::ThreadPool. Poll via poll_task..."
}}
```

---

## poll_task

Read the current state of a task started via any `start_*_task` handler. **Non-blocking** — returns the registry snapshot and never waits for the task to advance.

**Status values**

| Status | Meaning |
|---|---|
| `pending` | Registered but worker hasn't started yet (briefly, between `start_*_task` returning and the worker's first `MarkRunning` call). |
| `running` | Worker is actively executing. |
| `completed` | Finished successfully. `result` populated; `end_time` populated. |
| `cancelled` | Cancellation was requested AND the worker observed it. `end_time` populated. |
| `failed` | Worker hit an error. `error` populated; `end_time` populated. |

**Params**
- `task_id` (string, required) — id from the `start_*_task` call.

**Result**
- `ok` (bool)
- `task_id`, `type` (string) — echoes
- `status` (string) — see table above
- `start_time` (string) — `YYYY.MM.DD-HH.MM.SS`
- `end_time` (string) — populated for terminal states; empty otherwise
- `cancel_requested` (bool) — has cancellation been requested? (independent of whether the worker has observed it yet)
- `result` (object) — only when `status == "completed"`; shape depends on the task type
- `error` (string) — only when `status == "failed"`

**Errors:** `missing_required_field`, `task_not_found`.

**Example — task in progress**
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "task_id": "5C2D8F1A-...",
  "type": "sleep",
  "status": "running",
  "start_time": "2026.05.09-17.40.00",
  "end_time": "",
  "cancel_requested": false
}}
```

**Example — task completed**
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "task_id": "5C2D8F1A-...",
  "type": "sleep",
  "status": "completed",
  "start_time": "2026.05.09-17.40.00",
  "end_time": "2026.05.09-17.40.05",
  "cancel_requested": false,
  "result": {"slept_ms": 5000}
}}
```

---

## cancel_task

Request **cooperative** cancellation of a running task. Sets the task's atomic cancellation flag; the worker observes the flag on its next polling iteration (typical cadence ~50 ms) and exits cleanly to `status="cancelled"`.

**Cancellation discipline:** UE 5.7 has no safe forced-thread-termination — `FRunnableThread::Kill(true)` risks corrupting game state. So workers that don't poll the cancellation flag will **run to completion regardless**. The framework's job is to provide the signal; the worker's job is to honor it. All PR #44+ task types are required to poll the flag at sub-second cadence.

**Idempotent.** Calling on an unknown id or an already-terminal task returns `ok=true` with `accepted=false` (and a `note` explaining why) rather than an error — safe to blanket-cancel on shutdown.

**Params**
- `task_id` (string, required) — id from the `start_*_task` call.

**Result**
- `ok` (bool)
- `task_id` (string) — echo
- `accepted` (bool) — `true` if the cancellation flag was set; `false` if the id was unknown OR the task was already in a terminal state
- `note` (string) — explanation

**Errors:** `missing_required_field`.

**Example — successful cancel**
```json
{"jsonrpc":"2.0","id":1,"method":"cancel_task","params":{"task_id":"5C2D8F1A-..."}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "task_id": "5C2D8F1A-...",
  "accepted": true,
  "note": "Cancellation requested. Worker will observe within ~50ms..."
}}
```

A subsequent `poll_task` will show `cancel_requested: true` immediately, then transition to `status: "cancelled"` once the worker's next polling slice fires.

---

## list_tasks

Enumerate all tasks in the `FUCMCPTaskRegistry` with optional filters and a limit. The natural complement to `poll_task` — answers "what's running right now?" without requiring callers to remember every `task_id`.

**Atomic snapshot.** The full task set is captured under the registry's lock so the result is internally consistent (no half-mutated entries from a worker transition mid-call). Filtering and limiting happen post-snapshot, so they don't extend the lock-hold time.

**Params** (all optional)
- `status_filter` (string, enum) — one of `"pending"` / `"running"` / `"completed"` / `"cancelled"` / `"failed"`. If absent, all statuses are returned.
- `type_filter` (string) — exact-match filter on task type (e.g. `"sleep"`). If absent, all types are returned.
- `limit` (integer) — max items to return. Default `100`. Clamped to `[1, 500]`.

**Result**
- `ok` (bool)
- `total` (int) — total tasks in the registry **before** any filter
- `matched` (int) — count after `status_filter` + `type_filter` (AND-combined)
- `returned` (int) — count after `limit` (`returned ≤ matched ≤ total`)
- `tasks` (array) — each entry mirrors `poll_task`'s shape: `{ task_id, type, status, start_time, end_time, cancel_requested, result?, error? }`. `result` is omitted when null; `error` is omitted when empty.

**Errors:** `unknown_status_value` (status_filter not in the enum), `invalid_value_shape` (limit not a finite integer).

**Behavior notes**
- The invariant is `returned ≤ matched ≤ total`. Filters reduce `total` to `matched`; the limit reduces `matched` to `returned`. With no filters and no truncation, all three are equal — to detect truncation specifically, compare `returned == matched` rather than relying on a strict inequality.
- Tasks accumulate indefinitely (no TTL in PR #44 framework). On long-lived editor sessions, expect `total` to grow until the editor restarts.
- Mirrors `find_assets`'s `total/matched/returned/assets` convention.

**Example — running tasks only**
```json
{"jsonrpc":"2.0","id":1,"method":"list_tasks","params":{
  "status_filter": "running"
}}
```

**Example — at most 10 sleep tasks of any status**
```json
{"jsonrpc":"2.0","id":1,"method":"list_tasks","params":{
  "type_filter": "sleep",
  "limit": 10
}}
```

---

## exec_python_persistent

**Tier 2 PR #45.** Like `execute_unreal_python` but state **persists across calls**. Variables, imports, and function/class definitions defined in one call are visible in the next — letting Claude build up state across turns without re-loading every time.

**Implementation:** UE's `FPythonCommandEx` has a `FileExecutionScope` field; the default `Private` (used by `execute_unreal_python`) creates a fresh globals dict per call, while `Public` (used here) shares the dict with the editor's Python console. **The persistent state is just UE's standard console namespace** — same one you'd get if you typed at the editor's `>>>` prompt.

**Why a separate handler instead of an opt-in flag on `execute_unreal_python`:** persistent state is a sticky semantic surprise. A handler that might-or-might-not share globals based on a flag is harder to reason about than two clearly-named variants. The handler name signals the contract.

**Output-capture caveat (same as `execute_unreal_python`, `run_python_file`, `apply_python_to_selection`):** `ExecuteFile` mode does not return stdout / eval-result via `CommandResult`. To round-trip results, emit a marker via `unreal.log("__MARKER__<json>__END__")` and retrieve through `get_log_lines{category_filter:"LogPython"}`.

**Params**
- `code` (string, required) — Python source to execute against the persistent globals dict.

**Result**
- `ok` (bool)
- `output` (string) — `FPythonCommandEx::CommandResult` (typically `"None"` for ExecuteFile; on failure, the Python exception trace)
- `temp_script` (string) — path to the wrapper script that was written and executed (cleaned up via `ON_SCOPE_EXIT`)
- `scope` (string) — always `"public"` (echo of the FileExecutionScope used)

**Errors:** `missing_required_field`, `python_unavailable`, `write_failed`.

**Example — build up state across calls**

Call 1:
```json
{"jsonrpc":"2.0","id":1,"method":"exec_python_persistent","params":{
  "code": "import unreal\nactors = unreal.EditorActorSubsystem.get_selected_level_actors()\nprint(f'captured {len(actors)} actors')"
}}
```

Call 2 (sees `unreal` import + `actors` variable from call 1):
```json
{"jsonrpc":"2.0","id":2,"method":"exec_python_persistent","params":{
  "code": "for a in actors:\n    a.set_actor_location(unreal.Vector(0, 0, 100), False, False)\nunreal.log(f'__MOVED__{len(actors)}__END__')"
}}
```

Then `get_log_lines{category_filter:"LogPython"}` to read the marker.

---

## reset_python_state

Clear all user-defined names from UE Python's public globals dict. Pairs with `exec_python_persistent`: lets Claude wipe accumulated state and start fresh without restarting the editor.

**Preservation rule:** names starting with `_` (Python dunders like `__name__` / `__builtins__`, plus conventional private names) are preserved. Imports the user explicitly added (e.g. `import unreal`) ARE cleared — re-import in the next `exec_python_persistent` call.

**Params:** none.

**Result**
- `ok` (bool)
- `scope` (string) — always `"public"`
- `note` (string) — explanatory text

**Errors:** `python_unavailable`, `reset_failed`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"reset_python_state","params":{}}
```
```json
{"jsonrpc":"2.0","id":1,"result":{
  "ok": true,
  "scope": "public",
  "note": "All user-defined names cleared from UE Python's public globals dict..."
}}
```

---

## find_console_variables

**Language-shim experiment, PR #46 (C++ canonical handler).** Prefix-search the `IConsoleManager` registry; returns matching CVar names with their types and read-only flags. Pairs with `get_console_variable` / `set_console_variable` for discovery workflows.

**Why C++:** iterating UE's internal console registry via `ForEachConsoleObjectThatStartsWith` is dramatically cleaner than the Python equivalent (which would multi-call into `unreal.SystemLibrary` per CVar). See `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md` for the full comparison.

**Params**
- `prefix` (string, optional) — case-sensitive prefix to filter on (e.g. `"r.Screen"`, `"Slate."`, `"a."`). Empty / omitted = match all.
- `limit` (int, optional, default `100`) — cap returned variables. Hard max `1000`.

**Result**
- `ok`, `prefix`, `limit`, `returned` (counts)
- `variables` (array) — each entry: `{ name, type ("int"/"float"/"bool"/"string"/"unknown"), read_only (bool) }`
- `note` (only when result count hit the cap) — diagnostic

**Errors:** `invalid_value_shape`.

**Example — find all `r.Lumen.*` CVars**
```json
{"jsonrpc":"2.0","id":1,"method":"find_console_variables","params":{"prefix":"r.Lumen.","limit":50}}
```

---

## inspect_static_mesh

**Language-shim experiment, PR #46 (C++ canonical handler).** Read structural properties of a `UStaticMesh`: LOD count, per-LOD vertex/triangle counts, bounding box, material slots. Pairs with `inspect_asset` (registry-level metadata) and `inspect_material` (parameters).

**Why C++:** direct field access on `UStaticMesh` (`GetNumLODs`, `GetNumVertices(i)`, `GetBoundingBox`, `GetStaticMaterials`) — the Python equivalent would be multi-call FFI with reflection-limit risk on private struct fields.

**Params**
- `path` (string, required) — UE asset path of a `UStaticMesh`, e.g. `/Engine/BasicShapes/Cube`.

**Result**
- `ok`, `name`, `package_path`
- `num_lods`, `total_vertices`, `total_triangles` (across all LODs)
- `lods` (array) — `{ index, vertices, triangles }` per LOD
- `bounds` — `{ min: {x,y,z}, max: {x,y,z}, size: {x,y,z}, center: {x,y,z} }`
- `material_slots` (array) — `{ index, slot_name, material_path }`

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_static_mesh`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_static_mesh","params":{"path":"/Engine/BasicShapes/Cube"}}
```

---

## inspect_niagara_system

**Tier 3 (PR #51 — first multi-agent PR with explorer-fed Codex prompt).** Read structural properties of a `UNiagaraSystem`: emitters, user-exposed parameters, and system-level settings. Pairs with `inspect_asset` (registry-level metadata) for VFX-pipeline introspection.

**Why C++:** direct access to `UNiagaraSystem`'s emitter handle list, exposed parameter store, and warmup/bounds settings. The Python equivalent would need multi-call FFI through `unreal.NiagaraSystem` and per-emitter handle traversal.

**Why a separate handler from `inspect_blueprint`:** Niagara systems aren't `UBlueprint`s — they're a distinct asset class with their own emitter/parameter model that has no equivalent in regular blueprints.

**Required UE 5.7 discipline:** `UNiagaraSystem` uses `LoadBehavior = LazyOnDemand` (`NiagaraSystem.h:233`). The handler calls `EnsureFullyLoaded()` (`NiagaraSystem.h:526`) immediately after `Cast<UNiagaraSystem>` and before reading any emitters or parameters — otherwise lazy fields return uninitialized data. This is unique to Niagara among the inspect handlers.

**Required Build.cs deps:** `Niagara` and `NiagaraCore` (runtime modules — NOT `NiagaraEditor`, which is a heavy editor-only dep).

**Params**
- `path` (string, required) — UE asset path of a `UNiagaraSystem`, e.g. `/Game/FX/NS_Fire`.

**Result**
- `ok`, `name`, `path`
- `is_looping` (bool) — whether the system loops forever
- `has_gpu_emitters` (bool) — at least one emitter uses GPU simulation
- `needs_warmup` (bool); when true, also: `warmup_tick_count`, `warmup_time`, `warmup_tick_delta`
- `effect_type` (string) — asset path of the `UNiagaraEffectType` instance (e.g. `/Game/FX/EffectTypes/EFT_Hero.EFT_Hero`), omitted when none set
- `fixed_bounds` (`{ min: {x,y,z}, max: {x,y,z} }`) — only emitted when `bFixedBounds == true`
- `emitter_count`, `emitters` (array of `{ name, enabled, mode }`) — `mode` is `"Standard"` or `"Stateless"`
- `user_parameter_count`, `user_parameters` (array of `{ name, type }`) — `type` is the runtime-safe class/struct name (e.g. `"float"`, `"NiagaraBool"`, `"LinearColor"`)

**Errors:** `missing_required_field`, `asset_not_found`, `not_a_niagara_system`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_niagara_system","params":{"path":"/Game/FX/NS_Fire"}}
```

---

## inspect_anim_blueprint

**Tier 3 (PR #52 — first PR with full multi-agent collaboration).** Read structural properties of a `UAnimBlueprint`: parent class, target skeleton, template flag, compile status, baked state machines, anim functions (with `implemented` flag), sync groups, and parent anim blueprint chain. Pairs with the existing `Inspect*` family; complements `inspect_blueprint` by exposing the anim-specific surface that regular `UBlueprint` introspection can't see.

**Why C++:** direct access to `UAnimBlueprintGeneratedClass`'s baked state-machine list, anim-function metadata (with `bImplemented` flags), and sync-group names. The Python equivalent would require multi-call FFI through `unreal.AnimBlueprint` → generated class indirection.

**Why a separate handler from `inspect_blueprint`:** anim BPs *are* `UBlueprint`s, so the existing handler exposes their parent class / variables / function names — but the anim-specific surface (skeleton, state machines, sync groups, anim functions, parent anim BP chain) lives on `UAnimBlueprint` and `UAnimBlueprintGeneratedClass` and is invisible to a generic `UBlueprint` reader.

**Required UE 5.7 discipline:** `GetAnimBlueprintGeneratedClass()` returns null when the blueprint has never been compiled. The handler guards every compiled-data access (state machines, anim functions, sync groups) behind this null-check and emits `is_compiled: false` with empty arrays in that case, rather than crashing.

**Required Build.cs deps:** **none** — the runtime `Engine` module (already a dependency) provides `UAnimBlueprint`, `UAnimBlueprintGeneratedClass`, and the supporting types. **Do NOT add `AnimGraph`** (editor-only — would break server cooks).

**Params**
- `path` (string, required) — UE asset path of a `UAnimBlueprint`, e.g. `/Game/Animation/ABP_Hero`.

**Result**
- `ok`, `name`, `path`
- `parent_class` (string) — the immediate parent class name (often `AnimInstance` for natively-parented anim BPs)
- `is_template` (bool) — anim BP templates have no skeleton; affects whether `target_skeleton` is emitted
- `target_skeleton` (string) — asset path of the `USkeleton`. **Omitted** when `is_template == true` or the skeleton is null.
- `blueprint_status` (string) — one of `UpToDate` / `UpToDateWithWarnings` / `Dirty` / `Unknown`. Note that `Status` is transient; treat `is_compiled` (below) as the authoritative "compiled data is available" signal.
- `is_compiled` (bool) — true when `GetAnimBlueprintGeneratedClass()` is non-null. When false, all compiled-data arrays below are empty and the corresponding `*_count` fields are 0.
- `parent_anim_blueprint` (string) — asset path of the parent anim BP, when this blueprint subclasses another anim BP (rather than a native `UAnimInstance`). **Omitted** when null.
- `state_machine_count`, `state_machines` (array of `{ name }`)
- `anim_function_count`, `anim_functions` (array of `{ name, implemented }`)
- `sync_group_count`, `sync_groups` (array of strings — group names)

**Errors:** `missing_required_field`, `asset_not_found`, `not_an_anim_blueprint`.

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"inspect_anim_blueprint","params":{"path":"/Game/Animation/ABP_Hero"}}
```

---

## get_camera_transform

**Language-shim experiment, PR #46 (Python shim — bridge-side synthetic tool).** Read the level-editor viewport camera's location and rotation.

**Implementation:** the bridge composes `execute_unreal_python` + `get_log_lines` via the **marker pattern** — runs Python that calls `UnrealEditorSubsystem.get_level_viewport_camera_info()` and emits `unreal.log("__CAM_<uuid>__" + json + "__END__")`, then drains LogPython lines and parses the marker. Two UE round-trips per call.

**Why a Python shim:** the underlying API is fully Python-reachable; the equivalent C++ handler would be ~150 LoC vs ~75 LoC for the shim. Trade-off: ~5× round-trip latency and marker-pattern fragility under high log volume. See `docs/LANGUAGE-CHOICE-RETROSPECTIVE.md`.

**Params:** none.

**Result**
- `ok` (bool)
- `location` — `{ x, y, z }` world-space
- `rotation` — `{ pitch, yaw, roll }` in degrees

**Errors** (Python-flow style, not stable codes):
- `python_failed` — UE Python raised; the trace is in the message
- `marker_not_found` — log buffer overflowed between exec and read; retry typically resolves
- `marker_parse_failed` — JSON corruption between log and bridge

---

## set_camera_transform

**Language-shim experiment, PR #46 (Python shim — bridge-side synthetic tool).** Set the level-editor viewport camera's location and/or rotation. Single UE round-trip (write-only — no result to round-trip).

**Implementation:** validates location/rotation shape locally in the bridge (so a bad input fails before crossing the wire), then runs `unreal.UnrealEditorSubsystem.get_editor_subsystem(...).set_level_viewport_camera_info(unreal.Vector(...), unreal.Rotator(...))` via `execute_unreal_python`.

**Why a Python shim:** write-only setters that wrap Python-reachable APIs are the strongest case for shims (no marker-pattern tax, ~50 LoC).

**Params**
- `location` (object, optional) — `{ x, y, z }`. Missing fields default to `0`.
- `rotation` (object, optional) — `{ pitch, yaw, roll }` in degrees. Missing fields default to `0`.

At least one of `location` / `rotation` should be provided in practice; both omitted is a no-op.

**Result**
- `ok` (bool)
- `location`, `rotation` — the values that were applied (with defaults filled in)

**Errors:** `invalid_value_shape` (any field non-numeric), `python_failed` (UE Python raised).

**Example — frame top-down on origin**
```json
{"jsonrpc":"2.0","id":1,"method":"set_camera_transform","params":{
  "location": {"x": 0, "y": 0, "z": 1000},
  "rotation": {"pitch": -90, "yaw": 0, "roll": 0}
}}
```

---

## screenshot_actor

Frame the level-editor viewport on a specific actor and capture a focused PNG screenshot. Useful for asset-pipeline thumbnail generation and for giving the LLM a visual of one specific thing in the scene.

**Bridge-side synthetic tool.** Composes two existing handlers — no new C++ handler. The two-round-trip composition is structurally correct because UE's game thread runs at least one tick between the bridge's separate JSON-RPC requests, so the screenshot captures the post-move frame, not a pre-move frame mid-camera-animation. A single C++ handler doing both ops in one game-thread call would race the camera move against the readback.

**Composition**
1. `focus_actor { name }` — selects the actor and frames the viewport on it
2. `get_viewport_screenshot {}` — captures the (now-framed) viewport as base64 PNG

**Params**
- `name` (string, required) — actor label or unique name to focus on. Same matching rules as `focus_actor`.

**Result**
- `ok` (bool)
- `focused` (string) — the actor label that was focused
- `name` (string) — the actor's unique name
- `loc` (`{ x, y, z }`) — the focused actor's world location
- `width`, `height` (int) — viewport dimensions in pixels
- `png_bytes` (int) — size of the encoded PNG
- `png_base64` (string) — the PNG, base64-encoded inline

**Errors:** `missing_required_field`, `focus_failed` (actor not found, no GEditor, no editor world), `screenshot_failed` (no active viewport, ReadPixels failed, viewport size zero).

**Example**
```json
{"jsonrpc":"2.0","id":1,"method":"screenshot_actor","params":{
  "name": "MyHeroBP"
}}
```

---

## Adding more tools

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the recipe. Short version: one `.cpp` file in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, two registration lines in `UnrealClaudeMCPModule.cpp`, one entry in `Resources/mcp_manifest.json`, one entry in `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, rebuild, restart.
