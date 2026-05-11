# Texture Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two MCP handlers — `import_texture` and `configure_texture` — to UnrealClaudeMCP v0.2.0, bringing the tool count from 11 to 13 and giving MCP clients editor-time control over UE 5.7 texture assets.

**Architecture:** Each handler is a single C++ leaf in `Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_*.cpp`, mirroring the existing 11-handler pattern. `import_texture` uses the canonical `UAssetImportTask` + `IAssetTools::ImportAssetTasks` path. `configure_texture` mutates `UTexture` properties via the documented `PreEditChange`/`PostEditChange` dance. The Python bridge gets two new entries in its static `TOOLS` list and the JSON manifest grows to match.

**Tech Stack:** UE 5.7 C++ (UnrealEd, AssetTools modules), Python 3.x (pytest for the bridge tests), JSON-RPC 2.0 over local TCP.

**Spec:** [`docs/superpowers/specs/2026-05-08-texture-pipeline-design.md`](../specs/2026-05-08-texture-pipeline-design.md). Read this first if you are picking up this plan cold — the spec is the contract; this plan is the recipe.

**Branch:** `feat/v0.2.0-texture-pipeline` (already created; spec is committed at `40d266f`).

---

## Phase 1 — Bridge & manifest (Python only, runs in CI)

This phase is pure Python. No UE editor required. It updates the two static catalogs (`Resources/mcp_manifest.json` and `bridge/unreal_claude_mcp_bridge.py`) in lockstep, plus the existing pytest tests that hard-code the count "11".

### Task 1: Update existing test counts and add new manifest + bridge entries

**Files:**
- Modify: `tests/test_bridge.py` (lines 22-23, 39-46, 121-124)
- Modify: `UnrealClaudeMCP/Resources/mcp_manifest.json`
- Modify: `bridge/unreal_claude_mcp_bridge.py` (the `TOOLS` list)

**Why bundled:** these three files are tied — the manifest and the bridge are kept in sync (per `tests/test_manifest_sync.py`), and the bridge tests assert the count matches. Updating them as one commit keeps the repo compileable + tests passing at every step.

- [ ] **Step 1: Write/update the failing tests first**

Open `tests/test_bridge.py`. Replace the three count-related tests:

```python
# Replace test_tools_list_has_eleven_entries (around line 22):
def test_tools_list_has_thirteen_entries():
    assert len(bridge.TOOLS) == 13


# Replace test_tool_names_are_unique_and_match_handlers (around line 36):
def test_tool_names_are_unique_and_match_handlers():
    names = [t["name"] for t in bridge.TOOLS]
    assert len(names) == len(set(names)), "duplicate tool names"
    expected = {
        "execute_unreal_python", "get_project_summary", "inspect_blueprint",
        "inspect_widget_tree", "edit_widget_tree", "get_viewport_screenshot",
        "list_tools", "get_actors_in_level", "focus_actor",
        "load_level_by_path", "take_high_res_screenshot",
        "import_texture", "configure_texture",
    }
    assert set(names) == expected


# Replace test_handle_tools_list_returns_all_tools (around line 120):
def test_handle_tools_list_returns_all_tools():
    resp = bridge.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert resp["id"] == 2
    assert "tools" in resp["result"]
    assert len(resp["result"]["tools"]) == 13
```

Also append two new schema tests at the bottom of the `# -------- TOOLS schema --------` section (around line 62):

```python
def test_import_texture_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "import_texture")
    schema = tool["inputSchema"]
    assert schema["required"] == ["source_path", "dest_path"]
    props = schema["properties"]
    assert props["source_path"]["type"] == "string"
    assert props["dest_path"]["type"] == "string"
    assert props["dest_name"]["type"] == "string"
    assert props["replace_existing"]["type"] == "boolean"
    assert props["automated"]["type"] == "boolean"
    assert props["save"]["type"] == "boolean"


def test_configure_texture_schema():
    tool = next(t for t in bridge.TOOLS if t["name"] == "configure_texture")
    schema = tool["inputSchema"]
    assert schema["required"] == ["path"]
    props = schema["properties"]
    assert props["path"]["type"] == "string"
    assert props["srgb"]["type"] == "boolean"
    assert props["compression"]["type"] == "string"
    assert props["lod_group"]["type"] == "string"
    assert props["filter"]["type"] == "string"
    assert props["compress"]["type"] == "boolean"
```

- [ ] **Step 2: Run tests to confirm they fail (TDD red)**

```bash
cd C:/Users/<USERNAME>/Desktop/UnrealClaudeMCP
pytest tests/ -v
```

Expected: 5 failures — `test_tools_list_has_thirteen_entries` (got 11), `test_tool_names_are_unique_and_match_handlers` (set mismatch), `test_handle_tools_list_returns_all_tools` (got 11), `test_import_texture_schema` (KeyError), `test_configure_texture_schema` (KeyError). All other tests should still pass.

- [ ] **Step 3: Add the two new entries to `bridge/unreal_claude_mcp_bridge.py`**

In the `TOOLS` list (lines 41-137), append these two dictionaries before the closing `]`:

```python
    {
        "name": "import_texture",
        "description": "Import an image file (PNG/JPG/EXR/TGA/BMP/HDR) from disk into the project as a UTexture2D asset, using the canonical UE asset import pipeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Absolute filesystem path to the source image file."},
                "dest_path": {"type": "string", "description": "UE package path; must start with /Game/ (e.g. /Game/Textures/Environment)."},
                "dest_name": {"type": "string", "description": "Optional asset-name override; defaults to filename stem."},
                "replace_existing": {"type": "boolean", "description": "Overwrite existing asset at dest_path/dest_name (default false)."},
                "automated": {"type": "boolean", "description": "Suppress modal dialogs (default true)."},
                "save": {"type": "boolean", "description": "Save the .uasset to disk after import (default true)."},
            },
            "required": ["source_path", "dest_path"],
        },
    },
    {
        "name": "configure_texture",
        "description": "Adjust SRGB/CompressionSettings/LODGroup/Filter on an existing UTexture asset and persist the change. Triggers UE's standard PreEditChange/PostEditChange flow and rebuilds the GPU resource.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "UE package path of the existing texture asset, e.g. /Game/Textures/Environment/T_Stone_D."},
                "srgb": {"type": "boolean", "description": "Set UTexture::SRGB."},
                "compression": {"type": "string", "description": "TextureCompressionSettings enum name (e.g. Default, Normalmap, Masks, BC7, HDR)."},
                "lod_group": {"type": "string", "description": "TextureGroup enum name (e.g. World, WorldNormalMap, UI, Lightmap)."},
                "filter": {"type": "string", "description": "TextureFilter enum name: Nearest | Bilinear | Trilinear | Default."},
                "compress": {"type": "boolean", "description": "Call UpdateResource() after mutation (default true). Set false for batches."},
            },
            "required": ["path"],
        },
    },
```

- [ ] **Step 4: Add the two new entries to `UnrealClaudeMCP/Resources/mcp_manifest.json`**

Inside the `"tools"` array, before the closing `]`, append:

```json
    {
      "name": "import_texture",
      "description": "Import an image file (PNG/JPG/EXR/TGA/BMP/HDR) from disk into the project as a UTexture2D asset, using the canonical UE asset import pipeline.",
      "params": {
        "source_path": "string (required) - absolute filesystem path",
        "dest_path": "string (required) - UE package path starting with /Game/",
        "dest_name": "string (optional) - asset-name override",
        "replace_existing": "bool (optional, default false)",
        "automated": "bool (optional, default true)",
        "save": "bool (optional, default true)"
      },
      "returns": { "ok": "bool", "asset_path": "string", "asset_name": "string", "source_path": "string", "width": "int", "height": "int", "format": "string", "message": "string" }
    },
    {
      "name": "configure_texture",
      "description": "Adjust SRGB/Compression/LODGroup/Filter on an existing UTexture asset and persist the change.",
      "params": {
        "path": "string (required) - UE package path of an existing texture",
        "srgb": "bool (optional)",
        "compression": "string (optional) - TextureCompressionSettings enum name",
        "lod_group": "string (optional) - TextureGroup enum name",
        "filter": "string (optional) - Nearest | Bilinear | Trilinear | Default",
        "compress": "bool (optional, default true)"
      },
      "returns": { "ok": "bool", "path": "string", "applied": "object", "message": "string" }
    }
```

Also bump the top-level `"description"` field on line 4 from `"Eleven generic editor-automation tools"` to `"Thirteen generic editor-automation tools"`.

- [ ] **Step 5: Run tests to confirm they pass (TDD green)**

```bash
pytest tests/ -v
```

Expected: all tests pass, including the 5 that just changed. Total test count grows by 2 (the two new schema tests).

- [ ] **Step 6: Commit**

```bash
git add bridge/unreal_claude_mcp_bridge.py UnrealClaudeMCP/Resources/mcp_manifest.json tests/test_bridge.py
git commit -m "$(cat <<'EOF'
feat(bridge): add import_texture and configure_texture catalog entries

Adds the two new tools to the static MCP catalog (manifest + bridge
TOOLS list), updates existing pytest assertions that hard-coded "11
tools" to expect 13, and adds schema tests for both new entries.

Manifest sync test (tests/test_manifest_sync.py) automatically exercises
the new pair. C++ handlers are NOT yet implemented — the bridge will
forward calls to UE which will return MethodNotFound until Phase 3.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Phase 2 — Build dependencies

### Task 2: Add UnrealEd and AssetTools to Build.cs

**Files:**
- Modify: `UnrealClaudeMCP/Source/UnrealClaudeMCP/UnrealClaudeMCP.Build.cs`

**Why a separate task:** changing module deps can introduce LNK errors that are unrelated to handler logic. Isolating this commit means a future bisect can identify "deps broke" vs "handler logic broke" cleanly.

- [ ] **Step 1: Open the Build.cs and locate the `PrivateDependencyModuleNames` (or `PublicDependencyModuleNames`) list**

The exact form depends on the file — read it first to see whether deps are listed in one block or split. Add the two new modules to the same list that already contains `"UnrealEd"`-adjacent modules like `"EditorScriptingUtilities"`, `"UMGEditor"`, `"Kismet"`. If neither `"UnrealEd"` nor `"AssetTools"` is present, add both:

```csharp
"UnrealEd",
"AssetTools",
```

- [ ] **Step 2: Generate Visual Studio project files**

Right-click your `.uproject` → "Generate Visual Studio project files", or from a Developer Command Prompt:

```bash
"C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe" -projectfiles -project="<absolute path to your .uproject>" -game -engine
```

(Path will vary based on where UE is installed. The plugin will be inside whichever host project you've added it to.)

- [ ] **Step 3: Build the editor target**

In Visual Studio: select **Development Editor | Win64**, then Build → Build Solution. Or from CLI:

```bash
"<UE root>\Engine\Build\BatchFiles\Build.bat" <ProjectName>Editor Win64 Development -project="<absolute path>" -waitmutex
```

Expected: clean build, no LNK errors, no warnings about unused dependencies.

If you see `LNK2019: unresolved external symbol` referencing `UAssetImportTask` or `IAssetTools`, the dep names are typo'd or in the wrong list. Fix and rebuild.

- [ ] **Step 4: Smoke-check the editor still launches**

Open the host project. The MCP server should start as before. Output Log shows:

```
[LogUnrealClaudeMCP] Editor module started
[LogUnrealClaudeMCP] Registered handler 'execute_unreal_python'
... (11 lines, same as before)
[LogUCMCP] Listening on 127.0.0.1:18888
```

If the editor crashes on startup or any of the 11 existing handlers fails to register, revert and investigate.

- [ ] **Step 5: Run bridge tests to confirm they still pass**

```bash
pytest tests/ -v
```

Expected: all tests pass. (Build.cs changes don't touch the Python side, so this is a sanity check.)

- [ ] **Step 6: Commit**

```bash
git add UnrealClaudeMCP/Source/UnrealClaudeMCP/UnrealClaudeMCP.Build.cs
git commit -m "$(cat <<'EOF'
build: add UnrealEd and AssetTools module deps for v0.2.0 textures

Currently UnrealEd is transitively linked via Kismet but we'll be
directly referencing UAssetImportTask, UTextureFactory, and the
IAssetTools interface in the Phase 3 / Phase 4 handlers. Declaring
these deps explicitly avoids fragile transitive linkage and gives
LNK errors meaningful names if a future UE version restructures
which module owns which symbol.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3 — `import_texture` handler (C++)

### Task 3: Create handler skeleton (registers + returns NotImplemented)

**Files:**
- Create: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ImportTexture.cpp`
- Modify: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp` (add extern + Reg.Register)

**Why skeleton-first:** lets us prove registration / module wiring works before any UE-API code lands. The smoke check is simple — `list_tools` should now return 12 entries on the UE side (still 13 in the bridge catalog; mismatch is fine until Task 7).

- [ ] **Step 1: Write the handler skeleton**

Create `Handler_ImportTexture.cpp` with this exact content:

```cpp
// Copyright (c) 2026 HD Media (Kuwait). MIT License.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"

class FHandler_ImportTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("import_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(
        const TSharedPtr<FJsonObject>& Params,
        FString& OutError) override
    {
        OutError = TEXT("import_texture not yet implemented");
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ImportTexture()
{
    return MakeShared<FHandler_ImportTexture>();
}
```

- [ ] **Step 2: Register the handler in `UnrealClaudeMCPModule.cpp`**

Find the existing `extern` declarations near the other `Make_Handler_*` lines. Add:

```cpp
extern TSharedRef<IUCMCPHandler> Make_Handler_ImportTexture();
```

In `StartupModule()`, near the other `Reg.Register(Make_Handler_*())` calls:

```cpp
Reg.Register(Make_Handler_ImportTexture());
```

- [ ] **Step 3: Build the editor**

Same build step as Task 2 Step 3.

Expected: clean build.

- [ ] **Step 4: Verify registration via live smoke**

Start the UE editor. Output Log should now show 12 lines of `Registered handler '...'` instead of 11, with `import_texture` among them. Also send a `list_tools` request via the existing smoke test:

```bash
python examples/smoke_test.py
```

Expected: `import_texture` appears in the returned list.

Calling the handler:

```python
import socket, json
s = socket.socket(); s.connect(('127.0.0.1', 18888))
s.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"import_texture","params":{}}).encode())
print(s.recv(65536).decode())
```

Expected: `{"jsonrpc":"2.0","id":1,"error":{"code":-32603,"message":"import_texture not yet implemented"}}`.

- [ ] **Step 5: Commit**

```bash
git add UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ImportTexture.cpp UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp
git commit -m "$(cat <<'EOF'
feat(unreal): scaffold import_texture handler skeleton

Registers the handler with the MCP dispatcher and returns a
"not yet implemented" error on call. This commit proves the
registration wiring works in isolation; actual import logic
follows in the next commit.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 4: Implement `import_texture` param validation

**Files:**
- Modify: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ImportTexture.cpp`

- [ ] **Step 1: Replace the `Handle()` body with parameter validation**

```cpp
virtual TSharedPtr<FJsonObject> Handle(
    const TSharedPtr<FJsonObject>& Params,
    FString& OutError) override
{
    if (!Params.IsValid())
    {
        OutError = TEXT("import_texture: missing params");
        return nullptr;
    }

    FString SourcePath, DestPath, DestName;
    if (!Params->TryGetStringField(TEXT("source_path"), SourcePath) || SourcePath.IsEmpty())
    {
        OutError = TEXT("import_texture: 'source_path' is required and must be non-empty");
        return nullptr;
    }
    if (!Params->TryGetStringField(TEXT("dest_path"), DestPath) || DestPath.IsEmpty())
    {
        OutError = TEXT("import_texture: 'dest_path' is required and must be non-empty");
        return nullptr;
    }
    if (!DestPath.StartsWith(TEXT("/Game/")))
    {
        OutError = TEXT("import_texture: 'dest_path' must start with /Game/");
        return nullptr;
    }
    Params->TryGetStringField(TEXT("dest_name"), DestName);

    bool bReplaceExisting = false, bAutomated = true, bSave = true;
    Params->TryGetBoolField(TEXT("replace_existing"), bReplaceExisting);
    Params->TryGetBoolField(TEXT("automated"), bAutomated);
    Params->TryGetBoolField(TEXT("save"), bSave);

    // File existence + extension check
    if (!FPaths::FileExists(SourcePath))
    {
        OutError = FString::Printf(TEXT("import_texture: source_path not found: %s"), *SourcePath);
        return nullptr;
    }
    const FString Ext = FPaths::GetExtension(SourcePath, /*bIncludeDot*/ false).ToLower();
    static const TSet<FString> Allowed = { TEXT("png"), TEXT("jpg"), TEXT("jpeg"),
                                            TEXT("exr"), TEXT("tga"), TEXT("bmp"),
                                            TEXT("hdr") };
    if (!Allowed.Contains(Ext))
    {
        OutError = FString::Printf(TEXT("import_texture: unsupported extension '%s'"), *Ext);
        return nullptr;
    }

    OutError = TEXT("import_texture: validation passed; import not yet wired");
    return nullptr;
}
```

Add the necessary includes at the top of the file:

```cpp
#include "Misc/Paths.h"
#include "Containers/Set.h"
```

- [ ] **Step 2: Build the editor**

Same as before.

Expected: clean build.

- [ ] **Step 3: Live-test all four error paths**

With UE running, execute these via Python:

```python
import socket, json
def call(params):
    s = socket.socket(); s.connect(('127.0.0.1', 18888))
    s.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"import_texture","params":params}).encode())
    print(s.recv(65536).decode()); s.close()

call({})  # expect "missing params" or "source_path required"
call({"source_path": "C:/none.png"})  # expect "dest_path required"
call({"source_path": "C:/none.png", "dest_path": "Foo"})  # expect "must start with /Game/"
call({"source_path": "C:/none.png", "dest_path": "/Game/X"})  # expect "source_path not found"
call({"source_path": "C:/Windows/notepad.exe", "dest_path": "/Game/X"})  # expect "unsupported extension 'exe'"
```

All five should return JSON-RPC error responses with the relevant message.

- [ ] **Step 4: Commit**

```bash
git add UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ImportTexture.cpp
git commit -m "$(cat <<'EOF'
feat(unreal): import_texture param validation

Validates source_path / dest_path / dest_name / replace_existing /
automated / save params before any UE API call. Rejects:
- missing required fields
- dest_path not starting with /Game/
- source_path that does not exist on disk
- unsupported file extensions

Returns "validation passed; import not yet wired" on the success
path so the next commit can swap in the real ImportAssetTasks call
without re-arranging error handling.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 5: Wire `UAssetImportTask` + `IAssetTools::ImportAssetTasks`

**Files:**
- Modify: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ImportTexture.cpp`

- [ ] **Step 1: Replace the success-path stub with the actual import call**

Replace the final two lines (`OutError = ...; return nullptr;`) with:

```cpp
    // Build the import task
    UAssetImportTask* Task = NewObject<UAssetImportTask>();
    Task->Filename = SourcePath;
    Task->DestinationPath = DestPath;
    if (!DestName.IsEmpty())
    {
        Task->DestinationName = DestName;
    }
    Task->bReplaceExisting = bReplaceExisting;
    Task->bAutomated = bAutomated;
    Task->bSave = bSave;
    Task->bAsync = false;

    // Keep alive for the duration of the call
    Task->AddToRoot();
    ON_SCOPE_EXIT { Task->RemoveFromRoot(); };

    // Acquire IAssetTools and run the import
    FAssetToolsModule& AssetToolsModule =
        FModuleManager::LoadModuleChecked<FAssetToolsModule>("AssetTools");
    AssetToolsModule.Get().ImportAssetTasks({ Task });

    if (Task->ImportedObjectPaths.Num() == 0)
    {
        OutError = FString::Printf(
            TEXT("import_texture: factory rejected the input (source=%s, dest=%s)"),
            *SourcePath, *DestPath);
        return nullptr;
    }

    const FString AssetPath = Task->ImportedObjectPaths[0];
    UTexture2D* Imported = nullptr;
    for (UObject* Obj : Task->GetObjects())
    {
        Imported = Cast<UTexture2D>(Obj);
        if (Imported) break;
    }
    if (!Imported)
    {
        OutError = TEXT("import_texture: imported object is not a UTexture2D");
        return nullptr;
    }

    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("ok"), true);
    Result->SetStringField(TEXT("asset_path"), AssetPath);
    Result->SetStringField(TEXT("asset_name"), Imported->GetName());
    Result->SetStringField(TEXT("source_path"), SourcePath);
    Result->SetNumberField(TEXT("width"), Imported->GetSizeX());
    Result->SetNumberField(TEXT("height"), Imported->GetSizeY());
    Result->SetStringField(TEXT("format"),
        StaticEnum<EPixelFormat>()->GetNameStringByValue((int64)Imported->GetPixelFormat()));
    Result->SetStringField(TEXT("message"),
        FString::Printf(TEXT("Imported %dx%d %s as UTexture2D."),
            Imported->GetSizeX(), Imported->GetSizeY(), *Ext.ToUpper()));
    return Result;
```

Add the includes at the top of the file:

```cpp
#include "AssetImportTask.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Engine/Texture2D.h"
#include "Misc/ScopeExit.h"
#include "Modules/ModuleManager.h"
#include "PixelFormat.h"
```

- [ ] **Step 2: Build the editor**

Expected: clean build. If you see `LNK2019` for `UAssetImportTask` or `FAssetToolsModule`, double-check Task 2 added `UnrealEd` and `AssetTools` to Build.cs.

- [ ] **Step 3: Live integration smoke test**

Place a test PNG in `tests/fixtures/test_texture.png` (any small PNG works — e.g. a 256×256 stripe).

```python
import socket, json, os
fixture = os.path.abspath("tests/fixtures/test_texture.png")
s = socket.socket(); s.connect(('127.0.0.1', 18888))
s.send(json.dumps({
    "jsonrpc":"2.0","id":1,"method":"import_texture",
    "params":{
        "source_path": fixture,
        "dest_path": "/Game/_UnrealClaudeMCPSmoke",
        "dest_name": "T_TextureSmoke",
    }
}).encode())
print(s.recv(65536).decode())
```

Expected: `{"jsonrpc":"2.0","id":1,"result":{"ok":true,"asset_path":"/Game/_UnrealClaudeMCPSmoke/T_TextureSmoke.T_TextureSmoke","asset_name":"T_TextureSmoke","source_path":"...","width":256,"height":256,"format":"PF_B8G8R8A8","message":"Imported 256x256 PNG as UTexture2D."}}`.

In the editor's Content Browser, navigate to `/Game/_UnrealClaudeMCPSmoke/` and confirm the new `T_TextureSmoke` asset is present.

- [ ] **Step 4: Commit**

```bash
git add UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ImportTexture.cpp
git commit -m "$(cat <<'EOF'
feat(unreal): wire import_texture to UAssetImportTask + IAssetTools

Replaces the validation-only stub with the full canonical Epic-blessed
import path: build a UAssetImportTask, call IAssetTools::ImportAssetTasks,
read the imported texture out of the task results, return shape, format,
and asset path to the caller.

Verified live by importing a 256x256 PNG fixture into a smoke folder
and confirming the resulting UTexture2D has the expected dimensions
and PF_B8G8R8A8 pixel format.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Phase 4 — `configure_texture` handler (C++)

### Task 6: Create `configure_texture` skeleton + param parsing + LoadObject

**Files:**
- Create: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ConfigureTexture.cpp`
- Modify: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp`

- [ ] **Step 1: Write the handler with param parsing and asset lookup, but no mutation yet**

```cpp
// Copyright (c) 2026 HD Media (Kuwait). MIT License.

#include "MCP/MCPHandler.h"
#include "Dom/JsonObject.h"
#include "Engine/Texture.h"
#include "UObject/UObjectGlobals.h"

class FHandler_ConfigureTexture : public IUCMCPHandler
{
public:
    virtual FString GetMethodName() const override { return TEXT("configure_texture"); }

    virtual TSharedPtr<FJsonObject> Handle(
        const TSharedPtr<FJsonObject>& Params,
        FString& OutError) override
    {
        if (!Params.IsValid())
        {
            OutError = TEXT("configure_texture: missing params");
            return nullptr;
        }

        FString Path;
        if (!Params->TryGetStringField(TEXT("path"), Path) || Path.IsEmpty())
        {
            OutError = TEXT("configure_texture: 'path' is required");
            return nullptr;
        }

        const bool bHasSrgb        = Params->HasField(TEXT("srgb"));
        const bool bHasCompression = Params->HasField(TEXT("compression"));
        const bool bHasLodGroup    = Params->HasField(TEXT("lod_group"));
        const bool bHasFilter      = Params->HasField(TEXT("filter"));
        if (!bHasSrgb && !bHasCompression && !bHasLodGroup && !bHasFilter)
        {
            OutError = TEXT("configure_texture: no_changes_specified — "
                            "provide at least one of srgb / compression / lod_group / filter");
            return nullptr;
        }

        UTexture* Tex = LoadObject<UTexture>(nullptr, *Path);
        if (!Tex)
        {
            OutError = FString::Printf(TEXT("configure_texture: asset_not_found at %s"), *Path);
            return nullptr;
        }

        OutError = TEXT("configure_texture: lookup ok; mutation not yet wired");
        return nullptr;
    }
};

TSharedRef<IUCMCPHandler> Make_Handler_ConfigureTexture()
{
    return MakeShared<FHandler_ConfigureTexture>();
}
```

- [ ] **Step 2: Register in module**

In `UnrealClaudeMCPModule.cpp`, near the other extern + register lines:

```cpp
extern TSharedRef<IUCMCPHandler> Make_Handler_ConfigureTexture();
// in StartupModule:
Reg.Register(Make_Handler_ConfigureTexture());
```

- [ ] **Step 3: Build, then verify registration count is now 13**

Output Log should show 13 `Registered handler '...'` lines on editor startup.

- [ ] **Step 4: Live-test the validation paths**

```python
def call_cfg(params):
    s = socket.socket(); s.connect(('127.0.0.1', 18888))
    s.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"configure_texture","params":params}).encode())
    print(s.recv(65536).decode()); s.close()

call_cfg({})  # missing path
call_cfg({"path": "/Game/_UnrealClaudeMCPSmoke/T_TextureSmoke"})  # no_changes_specified
call_cfg({"path": "/Game/Nothing/Here", "srgb": False})  # asset_not_found
call_cfg({"path": "/Game/_UnrealClaudeMCPSmoke/T_TextureSmoke", "srgb": False})  # "lookup ok; mutation not yet wired"
```

- [ ] **Step 5: Commit**

```bash
git add UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ConfigureTexture.cpp UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/UnrealClaudeMCPModule.cpp
git commit -m "$(cat <<'EOF'
feat(unreal): scaffold configure_texture handler with validation

Adds the handler skeleton: param parsing, no_changes_specified guard,
LoadObject<UTexture>, asset_not_found error path. Mutation logic
follows in the next commit.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 7: Wire the four UTexture mutations + PreEditChange/PostEditChange dance

**Files:**
- Modify: `UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ConfigureTexture.cpp`

- [ ] **Step 1: Add string-to-enum helpers and the mutation block**

At the top of the file (above the class), add the enum mapping helpers:

```cpp
#include "Engine/TextureDefines.h"
#include "EditorAssetLibrary.h"

static bool ParseCompression(const FString& In, TextureCompressionSettings& Out)
{
    static const TMap<FString, TextureCompressionSettings> M = {
        {TEXT("Default"),                   TC_Default},
        {TEXT("Normalmap"),                 TC_Normalmap},
        {TEXT("Masks"),                     TC_Masks},
        {TEXT("Grayscale"),                 TC_Grayscale},
        {TEXT("Displacementmap"),           TC_Displacementmap},
        {TEXT("VectorDisplacementmap"),     TC_VectorDisplacementmap},
        {TEXT("HDR"),                       TC_HDR},
        {TEXT("UserInterface2D"),           TC_EditorIcon}, // verify enum name during impl
        {TEXT("BC7"),                       TC_BC7},
        {TEXT("HalfFloat"),                 TC_HalfFloat},
        {TEXT("SingleFloat"),               TC_SingleFloat},
        {TEXT("Alpha"),                     TC_Alpha},
        {TEXT("DistanceFieldFont"),         TC_DistanceFieldFont},
        {TEXT("HDR_Compressed"),            TC_HDR_Compressed},
        {TEXT("BC4"),                       TC_BC4},
        {TEXT("BC5"),                       TC_BC5},
    };
    if (const TextureCompressionSettings* V = M.Find(In)) { Out = *V; return true; }
    return false;
}

static bool ParseFilter(const FString& In, TextureFilter& Out)
{
    if (In == TEXT("Nearest"))    { Out = TF_Nearest;   return true; }
    if (In == TEXT("Bilinear"))   { Out = TF_Bilinear;  return true; }
    if (In == TEXT("Trilinear"))  { Out = TF_Trilinear; return true; }
    if (In == TEXT("Default"))    { Out = TF_Default;   return true; }
    return false;
}

static bool ParseLodGroup(const FString& In, TextureGroup& Out)
{
    // Implementation note: this list must be verified against
    // Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h
    // during this task. UE 5.7 may have added/removed entries.
    static const TMap<FString, TextureGroup> M = {
        {TEXT("World"),              TEXTUREGROUP_World},
        {TEXT("WorldNormalMap"),     TEXTUREGROUP_WorldNormalMap},
        {TEXT("WorldSpecular"),      TEXTUREGROUP_WorldSpecular},
        {TEXT("Character"),          TEXTUREGROUP_Character},
        {TEXT("CharacterNormalMap"), TEXTUREGROUP_CharacterNormalMap},
        {TEXT("CharacterSpecular"),  TEXTUREGROUP_CharacterSpecular},
        {TEXT("Weapon"),             TEXTUREGROUP_Weapon},
        {TEXT("WeaponNormalMap"),    TEXTUREGROUP_WeaponNormalMap},
        {TEXT("WeaponSpecular"),     TEXTUREGROUP_WeaponSpecular},
        {TEXT("Vehicle"),            TEXTUREGROUP_Vehicle},
        {TEXT("VehicleNormalMap"),   TEXTUREGROUP_VehicleNormalMap},
        {TEXT("VehicleSpecular"),    TEXTUREGROUP_VehicleSpecular},
        {TEXT("Cinematic"),          TEXTUREGROUP_Cinematic},
        {TEXT("Effects"),            TEXTUREGROUP_Effects},
        {TEXT("EffectsNotFiltered"), TEXTUREGROUP_EffectsNotFiltered},
        {TEXT("Skybox"),             TEXTUREGROUP_Skybox},
        {TEXT("UI"),                 TEXTUREGROUP_UI},
        {TEXT("Lightmap"),           TEXTUREGROUP_Lightmap},
        {TEXT("Shadowmap"),          TEXTUREGROUP_Shadowmap},
        {TEXT("RenderTarget"),       TEXTUREGROUP_RenderTarget},
        {TEXT("MobileFlattened"),    TEXTUREGROUP_MobileFlattened},
        {TEXT("IESLightProfile"),    TEXTUREGROUP_IESLightProfile},
        {TEXT("Bake"),               TEXTUREGROUP_Bake},
        {TEXT("Pixels2D"),           TEXTUREGROUP_Pixels2D},
        {TEXT("HierarchicalLOD"),    TEXTUREGROUP_HierarchicalLOD},
    };
    if (const TextureGroup* V = M.Find(In)) { Out = *V; return true; }
    return false;
}
```

Replace the stub line `OutError = TEXT("configure_texture: lookup ok; ...");` with:

```cpp
    // Pre-validate all enum values BEFORE we start mutating, so unknown_enum_value
    // doesn't leave the asset half-modified.
    bool bSrgb = false;
    TextureCompressionSettings Compression = TC_Default;
    TextureGroup LodGroup = TEXTUREGROUP_World;
    TextureFilter Filter = TF_Default;
    FString CompressionStr, LodGroupStr, FilterStr;

    if (bHasSrgb)        Params->TryGetBoolField(TEXT("srgb"), bSrgb);
    if (bHasCompression && Params->TryGetStringField(TEXT("compression"), CompressionStr))
    {
        if (!ParseCompression(CompressionStr, Compression))
        {
            OutError = FString::Printf(
                TEXT("configure_texture: unknown_enum_value 'compression'='%s'"), *CompressionStr);
            return nullptr;
        }
    }
    if (bHasLodGroup && Params->TryGetStringField(TEXT("lod_group"), LodGroupStr))
    {
        if (!ParseLodGroup(LodGroupStr, LodGroup))
        {
            OutError = FString::Printf(
                TEXT("configure_texture: unknown_enum_value 'lod_group'='%s'"), *LodGroupStr);
            return nullptr;
        }
    }
    if (bHasFilter && Params->TryGetStringField(TEXT("filter"), FilterStr))
    {
        if (!ParseFilter(FilterStr, Filter))
        {
            OutError = FString::Printf(
                TEXT("configure_texture: unknown_enum_value 'filter'='%s'"), *FilterStr);
            return nullptr;
        }
    }

    bool bCompress = true;
    Params->TryGetBoolField(TEXT("compress"), bCompress);

    // ---- Mutation: PreEdit / Modify / set / PostEdit ---------------------------
    Tex->PreEditChange(nullptr);
    Tex->Modify();

    TSharedPtr<FJsonObject> Applied = MakeShared<FJsonObject>();
    if (bHasSrgb)        { Tex->SRGB = bSrgb;                Applied->SetBoolField(TEXT("srgb"), bSrgb); }
    if (bHasCompression) { Tex->CompressionSettings = Compression; Applied->SetStringField(TEXT("compression"), CompressionStr); }
    if (bHasLodGroup)    { Tex->LODGroup = LodGroup;         Applied->SetStringField(TEXT("lod_group"), LodGroupStr); }
    if (bHasFilter)      { Tex->Filter = Filter;             Applied->SetStringField(TEXT("filter"), FilterStr); }

    FPropertyChangedEvent EmptyEvent(nullptr);
    Tex->PostEditChangeProperty(EmptyEvent);

    if (bCompress)
    {
        Tex->UpdateResource();
    }

    if (!UEditorAssetLibrary::SaveLoadedAsset(Tex))
    {
        OutError = TEXT("configure_texture: save_failed");
        return nullptr;
    }

    TSharedPtr<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetBoolField(TEXT("ok"), true);
    Result->SetStringField(TEXT("path"), Path);
    Result->SetObjectField(TEXT("applied"), Applied);
    Result->SetStringField(TEXT("message"), TEXT("Settings applied; resource rebuilt and saved."));
    return Result;
```

**Implementation note:** `TC_EditorIcon` may be the correct constant for `UserInterface2D`, or it may be a different name in UE 5.7. Verify against `Engine/Source/Runtime/Engine/Classes/Engine/TextureDefines.h` during this task and adjust the map. Same for any LOD group entries — the master list is in that header.

- [ ] **Step 2: Build the editor**

If you see `unresolved external symbol` for `UEditorAssetLibrary::SaveLoadedAsset`, the existing Build.cs already has `EditorScriptingUtilities` (per `ARCHITECTURE.md:128`), so it should link. If not, check the deps list.

- [ ] **Step 3: Live round-trip smoke test**

With the texture from Task 5 still present at `/Game/_UnrealClaudeMCPSmoke/T_TextureSmoke`:

```python
call_cfg({
    "path": "/Game/_UnrealClaudeMCPSmoke/T_TextureSmoke",
    "srgb": False,
    "compression": "Normalmap",
    "lod_group": "WorldNormalMap",
    "filter": "Default",
})
```

Expected: `{"jsonrpc":"2.0","id":1,"result":{"ok":true,"path":"...","applied":{"srgb":false,"compression":"Normalmap","lod_group":"WorldNormalMap","filter":"Default"},"message":"Settings applied..."}}`.

In the editor: open the texture asset and confirm in the Details panel that all four settings reflect the change.

Test the unknown_enum_value path:

```python
call_cfg({
    "path": "/Game/_UnrealClaudeMCPSmoke/T_TextureSmoke",
    "compression": "DefinitelyNotARealEnumValue",
})
```

Expected: error with `unknown_enum_value 'compression'='DefinitelyNotARealEnumValue'`.

- [ ] **Step 4: Commit**

```bash
git add UnrealClaudeMCP/Source/UnrealClaudeMCP/Private/MCP/Handlers/Handler_ConfigureTexture.cpp
git commit -m "$(cat <<'EOF'
feat(unreal): wire configure_texture mutations + save

Implements the SRGB / CompressionSettings / LODGroup / Filter mutation
path with the documented PreEditChange / Modify / set / PostEditChange
dance per Texture.h:1883. Calls UpdateResource() for GPU rebuild and
UEditorAssetLibrary::SaveLoadedAsset to persist.

All enum values are pre-validated before any mutation begins so
unknown_enum_value cannot leave the asset half-modified. The four
'applied' fields are reported back so the caller can verify what
actually changed.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Phase 5 — Smoke test + docs

### Task 8: Add live integration smoke test for both handlers

**Files:**
- Create: `tests/fixtures/test_texture.png` (binary, ~256×256 PNG)
- Modify: `examples/smoke_test.py` — bump existing `list_tools` count assertion 11→13, add new step for the texture round-trip

**Helper conventions (read first to match style):** `examples/smoke_test.py` already defines:
- `call(method: str, params: dict | None = None, request_id: int = 1) -> dict` — module-level globals `HOST` / `PORT` are read internally; **do NOT** pass host/port arguments.
- `header(name: str)` — prints a section banner.
- `step(label: str, fn)` — runs `fn`, captures `SmokeFailure`, appends to the local `failures` list.
- `assert_ok(resp: dict, label: str) -> dict` — returns `resp["result"]` on success, raises `SmokeFailure` otherwise.
- `assert_error_code(resp: dict, code: int, label: str)` — for negative cases.

The new smoke step must follow this pattern, not invent a new one.

- [ ] **Step 1: Place a small PNG fixture**

Any 256×256 (or smaller) PNG works. Create one via Python if needed:

```python
# Run once to generate the fixture
from PIL import Image
img = Image.new("RGBA", (256, 256), (255, 0, 0, 255))
for x in range(0, 256, 32):
    for y in range(256):
        img.putpixel((x, y), (0, 0, 0, 255))
img.save("tests/fixtures/test_texture.png")
```

Or supply your own. Keep it under 100 KB so it doesn't bloat the repo.

- [ ] **Step 2: Bump the existing `list_tools` count assertion from 11 to 13**

In `examples/smoke_test.py`, find the section currently labeled `header("1. list_tools (should list 11 tool names)")` (around line 143). Update both the header string and the count assertion:

```python
    header("1. list_tools (should list 13 tool names)")
    def t1():
        resp = call("list_tools")
        show(resp)
        result = assert_ok(resp, "list_tools")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise SmokeFailure(f"[list_tools] 'tools' not a list: {result}")
        if len(tools) != 13:
            raise SmokeFailure(f"[list_tools] expected 13 tools, got {len(tools)}: {tools}")
        if result.get("count") != len(tools):
            raise SmokeFailure(f"[list_tools] 'count' ({result.get('count')}) != len(tools) ({len(tools)})")
    step("list_tools", t1)
```

- [ ] **Step 3: Add a new step for the texture-pipeline round-trip**

Insert this block in `main()` after the existing `step("get_viewport_screenshot", t6)` and before the `if args.bp:` block:

```python
    header("7. texture pipeline round-trip (import + configure + cleanup)")
    def t_texture():
        fixture = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..",
                         "tests", "fixtures", "test_texture.png"))
        if not os.path.exists(fixture):
            raise SmokeFailure(f"[texture] fixture missing: {fixture}")

        smoke_dest = "/Game/_UnrealClaudeMCPSmoke"
        asset_name = "T_PipelineSmoke"
        asset_path = f"{smoke_dest}/{asset_name}.{asset_name}"

        # Import
        imp = call("import_texture", {
            "source_path": fixture,
            "dest_path": smoke_dest,
            "dest_name": asset_name,
            "replace_existing": True,
        })
        result = assert_ok(imp, "import_texture")
        if not (result.get("width") == 256 and result.get("height") == 256):
            raise SmokeFailure(f"[import_texture] unexpected dims: {result}")

        # Configure
        cfg = call("configure_texture", {
            "path": asset_path,
            "srgb": False,
            "compression": "Normalmap",
            "lod_group": "WorldNormalMap",
        })
        cfg_res = assert_ok(cfg, "configure_texture")
        applied = cfg_res.get("applied") or {}
        if applied.get("srgb") is not False:
            raise SmokeFailure(f"[configure_texture] srgb not applied: {cfg_res}")
        if applied.get("compression") != "Normalmap":
            raise SmokeFailure(f"[configure_texture] compression not applied: {cfg_res}")

        # Clean up via execute_unreal_python so we don't leave smoke assets behind
        cleanup = call("execute_unreal_python", {
            "code": (
                "import unreal\n"
                f"unreal.EditorAssetLibrary.delete_directory('{smoke_dest}')\n"
            )
        })
        assert_ok(cleanup, "texture_pipeline.cleanup")
    step("texture_pipeline", t_texture)
```

(Also bump the index numbers on later headers — `7. inspect_blueprint` becomes `8.`, etc. — so the sequential numbering reads cleanly.)

**Note:** the `assert_ok(imp, "import_texture")` call returns `imp["result"]`; the import handler's *result payload* is what contains `width` / `height` / `asset_path`. Don't dereference these off `imp` directly.

- [ ] **Step 3: Run the smoke (UE editor must be open)**

```bash
python examples/smoke_test.py
```

Expected: existing smoke prints stay green, plus a new `OK: texture-pipeline smoke passed` line.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/test_texture.png examples/smoke_test.py
git commit -m "$(cat <<'EOF'
test: add live smoke for texture-pipeline round-trip

Imports a 256x256 PNG fixture, configures the resulting asset
(srgb=false, compression=Normalmap, lod_group=WorldNormalMap),
verifies the response shape, then cleans up via execute_unreal_python.

Live test only; not part of CI (existing convention — requires
running UE editor on 127.0.0.1:18888).

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### Task 9: Update documentation

**Files:**
- Modify: `docs/TOOLS.md` (add 2 sections)
- Modify: `README.md` (table + count bumps)
- Modify: `docs/ARCHITECTURE.md` (scar collection entry)

- [ ] **Step 1: Append two sections to `docs/TOOLS.md`**

Following the existing format used for the 11 documented tools, append sections for `import_texture` and `configure_texture`. Each section should have: short prose intro, **Params** list, **Result** list, **Example** JSON-RPC request, plus any relevant notes (e.g. "the asset is saved synchronously to disk and will appear in Content Browser without a refresh").

For `import_texture`, the prose intro should explain that it uses the canonical `UAssetImportTask` path and link to the spec.

For `configure_texture`, the prose intro should mention the four settable properties and that the mutation is wrapped in `PreEditChange` / `PostEditChange` so the editor preview updates immediately.

- [ ] **Step 2: Update `README.md`**

In the tools table (currently rows for the 11 tools), add two rows:

```
| `import_texture` | Bring an image (PNG/JPG/EXR/...) into the project as a `UTexture2D` asset via UE's canonical import path. |
| `configure_texture` | Adjust SRGB / compression / LOD group / filter on an existing texture asset. |
```

Update the line `## Tools exposed (11)` → `## Tools exposed (13)`.
Update the line in **What's in the box** that says `Eleven generic tools exposed today` → `Thirteen generic tools exposed today`.
Update the **Status** section: bump `v0.1.0` → `v0.2.0`, change `2026-05-07` to today's date, change `11 tools live` → `13 tools live`.

- [ ] **Step 3: Append a scar-collection entry to `docs/ARCHITECTURE.md`**

After the existing bullets in the "UE 5.7 API gotchas" section (around line 169), add:

```markdown
- `UTexture` mutations require the full `PreEditChange(nullptr)` + `Modify()` + set property + `PostEditChangeProperty(emptyEvent)` + (`UpdateResource()` if you want the GPU side rebuilt before save) + `UEditorAssetLibrary::SaveLoadedAsset(...)` dance. Skipping `UpdateResource()` lets the in-editor preview keep showing the **old** texture even after the new settings are saved to disk; reopening the asset doesn't refresh it because the cached resource is still the pre-edit one. This caught us mid-implementation of `configure_texture` — fix is one extra line. Reference: [`Texture.h:1883`](https://github.com/EpicGames/UnrealEngine/blob/5.7/Engine/Source/Runtime/Engine/Classes/Engine/Texture.h).
```

- [ ] **Step 4: Run all bridge tests one more time as a sanity check**

```bash
pytest tests/ -v
```

Expected: all green. Doc changes don't touch Python, but it's a free 5-second sanity step.

- [ ] **Step 5: Commit**

```bash
git add docs/TOOLS.md README.md docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
docs: document import_texture and configure_texture; bump tool count to 13

- TOOLS.md: full sections for both new tools (params, result, example)
- README.md: tools table + count bumps + status block
- ARCHITECTURE.md: new scar-collection entry covering the texture
  mutation dance (PreEdit/Modify/set/PostEdit/UpdateResource/Save)
  and what breaks if you skip UpdateResource()

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

---

## Final acceptance gate

Before opening the PR for v0.2.0, confirm all of these:

- [ ] `pytest tests/ -v` — all green (33+ tests pass)
- [ ] UE editor build clean, no LNK errors, no warnings
- [ ] Editor Output Log shows 13 `Registered handler '...'` lines on startup
- [ ] `python examples/smoke_test.py` — all smoke functions pass, including the new texture-pipeline one
- [ ] `git log feat/v0.2.0-texture-pipeline ^main` — shows 9 clean commits (1 spec + 1 bridge + 1 build + 4 import + 2 configure + 1 smoke + 1 docs)
- [ ] No leftover `/Game/_UnrealClaudeMCPSmoke/` content in the project (smoke test cleans up)
- [ ] PR description summarizes the change, links the spec, lists the 13 tools, calls out the manifest+bridge+C++ trio of files for reviewers

---

## Out of scope reminders (do NOT add to this PR)

- `create_texture_from_bytes` — needs length-prefixed framing first; its own PR.
- Material authoring / instances / assignment — separate brainstorm cycles.
- Async import for large EXR files — current synchronous-on-game-thread pattern is documented as a known limitation.
- More than the four configure properties (e.g., MipGenSettings, MaxTextureSize) — keep v0.2.0 focused; add by-request later.
