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

**Errors:** `source_not_found`, `unsupported_extension`, `invalid_dest_path`, `dest_collision_no_replace`, `import_factory_failed`, `imported_not_a_texture`.

---

## configure_texture

Adjust the four most common texture settings — `SRGB`, `CompressionSettings`, `LODGroup`, `Filter` — on an existing `UTexture` asset and persist the change. The handler wraps mutations in the required `PreEditChange` / `Modify` / set / `PostEditChangeProperty` / `UpdateResource` / `SaveLoadedAsset` sequence so that the in-editor preview and the on-disk `.uasset` stay consistent.

The `applied` object in the result contains *only* the fields the caller actually provided in the request. Fields that were not specified in the call do not appear in `applied`, which makes it safe to drive this tool in partial-update loops without accidentally inferring what was left unchanged.

**Params**
- `path` (string, required) — UE package path of an existing texture asset, e.g. `/Game/Textures/Environment/T_Stone_D`.
- `srgb` (bool, optional) — sets `UTexture::SRGB`.
- `compression` (string enum, optional) — maps to `TextureCompressionSettings`. Accepted values: `Default`, `Normalmap`, `Masks`, `Grayscale`, `Displacementmap`, `VectorDisplacementmap`, `HDR`, `UserInterface2D`, `BC7`, `HalfFloat`, `SingleFloat`, `EncodedReflectionCapture`, `Alpha`, `DistanceFieldFont`, `HDR_Compressed`, `BC4`, `BC5`.
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

**Errors:** `asset_not_found`, `asset_not_a_texture`, `no_changes_specified`, `unknown_enum_value`, `save_failed`.

---

## Adding more tools

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the recipe. Short version: one `.cpp` file in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, two registration lines in `UnrealClaudeMCPModule.cpp`, one entry in `Resources/mcp_manifest.json`, one entry in `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, rebuild, restart.
