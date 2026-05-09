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

## Adding more tools

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the recipe. Short version: one `.cpp` file in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, two registration lines in `UnrealClaudeMCPModule.cpp`, one entry in `Resources/mcp_manifest.json`, one entry in `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, rebuild, restart.
