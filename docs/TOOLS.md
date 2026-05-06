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
- `name_contains` (string, optional) — substring filter on actor label

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

Trigger UE's `HighResShot` console command. Output goes to `<Project>/Saved/Screenshots/WindowsEditor/`.

**Params**
- `multiplier` (number, optional, default 1, capped at 8) — viewport-pixel scaling factor

**Result**
- `command` (string) — the command that was issued
- `multiplier` (number)
- `dispatched` (bool)
- `output_dir_hint` (string)

---

## Adding more tools

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the recipe. Short version: one `.cpp` file in `Source/UnrealClaudeMCP/Private/MCP/Handlers/`, two registration lines in `UnrealClaudeMCPModule.cpp`, one entry in `Resources/mcp_manifest.json`, one entry in `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list, rebuild, restart.
