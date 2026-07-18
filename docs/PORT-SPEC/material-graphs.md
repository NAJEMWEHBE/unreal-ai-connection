# PORT-SPEC — Material Graphs Family (`UMaterialGraphToolset`)

Read [00-overview.md](00-overview.md) first. Designed in [ticket #302](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/302).

**Port-vs-extend verdict (locked):** Epic 5.8 ships `MaterialTools` (21 tools, Python, `Engine/Plugins/Experimental/Toolsets/EditorToolset/Content/Python/editor_toolset/toolsets/material.py`) with **full granular node-graph authoring** — add/delete/connect/disconnect expressions, wire to material output slots, pin-name discovery, layout, recompile — plus `MaterialInstanceTools` (9 tools) covering the whole instance/parameter surface. The ticket's original premise ("Epic covers instances only") is false. This family is therefore a **gap-fill extension, not a port**: it layers workflow-shaped atomics over Epic's granular surface and duplicates none of it. The gap is shape, not capability — building a 20-node material through Epic's tools costs ~50 serial MCP calls (game-thread serial, meta-tool dispatch per call) and Epic has no single-call graph read, no expression property setting at creation, and no bulk assignment.

**Workflow served (locked):** an agent authors a complete working material (or material function) from a prompt in one call — declarative whole-graph spec → nodes with properties + wiring + output connections + recompile, atomically; reads any material's full graph back in one call (round-trippable); bulk-assigns the result across a level. **Whole-graph declarative** model, same as blueprint-k2: fixing a graph means redefining it. No pin-level round-trips in this family — those exist in Epic's `MaterialTools` when an agent wants surgical edits.

**In scope:** atomic whole-graph build for `UMaterial` and `UMaterialFunction` (create-or-replace, expression properties inline, output wiring, recompile, diagnostics), single-call round-trippable graph snapshot, bulk material assignment across actors by label/folder/regex.
**Out of scope (decided):** granular node tools (Epic's `MaterialTools` covers: `add_expression`, `connect_expressions`, `connect_to_output`, `delete_expression`, `layout_expressions`, `recompile`, …); everything instance/parameter (Epic's `MaterialInstanceTools`); donor `material_blend_override` (runtime dynamic-MID overrides, no asset persistence — dropped at the re-grill); material layers/MPCs beyond what a graph spec references.

**Donor code** (mine for internals, NOT for tool shape): `UnrealAIConnection/Source/UnrealAIConnection/Private/MCP/Handlers/` — `Handler_AddMaterialExpression.cpp` (class resolution via `LoadClass<UMaterialExpression>`, `CreateMaterialExpression` usage), `Handler_ConnectMaterialExpression.cpp` (property-name→`EMaterialProperty` string table, `ConnectMaterialProperty` / `ConnectMaterialExpressions` usage, name-based node lookup), `Handler_BatchMaterialAssign.cpp` (target selectors + slot assignment), `Handler_InspectMaterial.cpp` (parameter-name enumeration — snapshot supersedes it). Epic's read path to imitate for the snapshot: `material.py:358-423` (`get_expression_inputs`, `get_property_input`).

---

## Shared spec structs

Declared once in `MaterialGraphSpecTypes.h`. All reflected — these ARE the JSON schema the agent sees.

```cpp
/** One expression node in a graph spec. `Id` is caller-chosen, unique per spec;
 *  diagnostics and snapshots refer to nodes by this id. `ExpressionClass` is a
 *  short name ("Multiply", "TextureSample", "ScalarParameter", "FunctionInput")
 *  resolved as /Script/Engine.MaterialExpression<Name>, or a full class path
 *  for expressions living outside /Script/Engine. */
USTRUCT(BlueprintType)
struct FMatNodeSpec
{
	GENERATED_BODY()

	UPROPERTY() FString Id;
	UPROPERTY() FString ExpressionClass;
	UPROPERTY() int32 PosX = 0;
	UPROPERTY() int32 PosY = 0;
	/** Expression properties by name, values in UE export-text form. Examples:
	 *  Constant → {"R":"0.5"}; ScalarParameter → {"ParameterName":"Roughness",
	 *  "DefaultValue":"0.3"}; TextureSample → {"Texture":"/Game/T_Rock.T_Rock"}.
	 *  Applied via FProperty::ImportText before wiring. */
	UPROPERTY() TMap<FString, FString> Properties;
};

/** Directed edge: "<node_id>.<output_name>" -> "<node_id>.<input_name>".
 *  Output name may be omitted ("<node_id>") for the expression's default
 *  output. Material output slots use the pseudo-node "output":
 *  "mul1" -> "output.BaseColor" (property names: BaseColor, Metallic,
 *  Specular, Roughness, EmissiveColor, Opacity, OpacityMask, Normal,
 *  AmbientOcclusion, WorldPositionOffset). Function graphs use real
 *  FunctionInput/FunctionOutput expression nodes, never the pseudo-node. */
USTRUCT(BlueprintType)
struct FMatEdgeSpec
{
	GENERATED_BODY()

	UPROPERTY() FString From;
	UPROPERTY() FString To;
};

/** A whole material graph, applied atomically. */
USTRUCT(BlueprintType)
struct FMatGraphSpec
{
	GENERATED_BODY()

	UPROPERTY() TArray<FMatNodeSpec> Nodes;
	UPROPERTY() TArray<FMatEdgeSpec> Edges;
};

/** Build outcome. Diagnostics carry the spec node id when the problem maps to
 *  a spec node (bad class, bad property, bad pin), else empty NodeId. */
USTRUCT(BlueprintType)
struct FMatDiagnostic
{
	GENERATED_BODY()

	UPROPERTY() FString Severity;   // "error" | "warning"
	UPROPERTY() FString NodeId;
	UPROPERTY() FString Message;
};

USTRUCT(BlueprintType)
struct FMatBuildReport
{
	GENERATED_BODY()

	UPROPERTY() bool bSuccess = false;
	/** Ref to the built asset (created or replaced). */
	UPROPERTY() UObject* Asset = nullptr;
	UPROPERTY() TArray<FMatDiagnostic> Diagnostics;
};

/** Actor selectors for bulk assignment. All provided selectors AND together;
 *  at least one must be set. */
USTRUCT(BlueprintType)
struct FMatActorTargets
{
	GENERATED_BODY()

	/** Exact actor label match (empty = ignore). */
	UPROPERTY() FString ByLabel;
	/** Outliner folder path prefix (empty = ignore). */
	UPROPERTY() FString ByFolder;
	/** Regex over actor names (empty = ignore). */
	UPROPERTY() FString ByNameRegex;
};

USTRUCT(BlueprintType)
struct FMatAssignReport
{
	GENERATED_BODY()

	UPROPERTY() int32 ActorsMatched = 0;
	UPROPERTY() int32 SlotsChanged = 0;
	/** Labels of modified actors (for the agent to confirm targeting). */
	UPROPERTY() TArray<FString> ModifiedActors;
};
```

Snapshot return struct `FMatGraphSnapshot` {Graph: FMatGraphSpec, AssetType: FString ("material"|"material_function"), Settings: TMap<FString,FString> (see tool 1), ParameterNames: TArray<FString>} — node `Id`s in a snapshot are the expressions' object names, so a snapshot is valid `build_material_graph` input.

---

## Tools

All `static UFUNCTION(meta = (AICallable))` on `UMaterialGraphToolset : public UToolsetDefinition` (`UCLASS(BlueprintType, Hidden)`). All sync (graph surgery + recompile fit the game-thread budget; renders don't live here). Errors raised via `RaiseScriptError`, never returned.

### 1. `build_material_graph`

```cpp
/**
 * Creates or wholly replaces a material or material function graph in one
 * atomic call: asset (created if missing), asset settings, all expression
 * nodes with their properties, all wiring including material output slots,
 * then recompile. Any previous expression graph is discarded — this tool
 * regenerates, it does not patch (use Epic's MaterialTools for surgical
 * single-node edits). On failure the partial graph is kept (inspectable via
 * snapshot_material_graph) and diagnostics identify offending spec node ids.
 *
 * @param AssetPath Content path, e.g. "/Game/Materials/M_Rock".
 * @param AssetType "material" or "material_function".
 * @param Settings  Asset-level properties in export-text form, applied before
 *                  the graph (e.g. {"BlendMode":"BLEND_Translucent",
 *                  "TwoSided":"true"}). Empty = leave defaults/current.
 * @param Graph     The complete graph: nodes, properties, wiring.
 * @return Build report: success flag, asset ref, node-id-keyed diagnostics.
 */
static FMatBuildReport BuildMaterialGraph(const FString& AssetPath, const FString& AssetType,
	const TMap<FString, FString>& Settings, const FMatGraphSpec& Graph);
```

Donor: `Handler_AddMaterialExpression.cpp` (expression class resolution — keep its `LoadClass` path but add the short-name convenience), `Handler_ConnectMaterialExpression.cpp` (the property string table and both `UMaterialEditingLibrary` connect calls). New over donor AND Epic: inline `Properties` import, asset `Settings`, atomicity, diagnostics.

**Smoke:** call with `/Game/Smoke/M_SmokeRock`, type `material`, graph: `TextureSample` (any engine texture) → `Multiply` ← `ScalarParameter("Tint", 1.0)`, multiply → `output.BaseColor`, constant `0.8` → `output.Roughness`. Assert: `bSuccess=true`; asset exists; snapshot shows 4 nodes wired as specified; `ScalarParameter` visible to Epic's `MaterialInstanceTools.list_parameters`. Re-call with a bad edge (`"mul1" -> "output.Nonexistent"`) — assert diagnostic names the edge/property, prior good graph replaced by kept-partial semantics documented above. Re-call with fixed spec — clean. Teardown: delete `/Game/Smoke`.

**Smoke (function):** call with `/Game/Smoke/MF_SmokeDesat`, type `material_function`, graph: `FunctionInput`(`Properties: {"InputName":"In","InputType":"FunctionInput_Vector3"}`) → `Desaturation` → `FunctionOutput`. Assert: asset exists; a second `build_material_graph` on a material referencing it via `MaterialFunctionCall` (`Properties: {"MaterialFunction":"/Game/Smoke/MF_SmokeDesat"}`) compiles clean.

### 2. `snapshot_material_graph`

```cpp
/**
 * Returns the complete graph of a material or material function in one call,
 * in round-trippable form: the returned spec is valid build_material_graph
 * input. Includes asset settings, every expression with its non-default
 * properties, all wiring including material output slots, and declared
 * parameter names.
 *
 * @param Asset The UMaterial or UMaterialFunction to read.
 * @return Snapshot: asset type, settings, graph spec, parameter names.
 */
static FMatGraphSnapshot SnapshotMaterialGraph(UObject* Asset);
```

Donor: none (donor `inspect_material` lists parameter names only — superseded). Epic's granular read path (`get_expression_inputs` / `get_property_input`, `material.py:358-423`) shows the traversal; this tool does it whole-graph in one call. Serialize each expression's non-default properties via `FProperty::ExportTextItem` diff against the class CDO.

**Smoke:** snapshot `M_SmokeRock` from tool 1. Assert: 4 nodes, edges match the build spec, `Tint` in ParameterNames. Feed the returned Graph straight back into `build_material_graph` — assert `bSuccess=true` and a second snapshot is identical (round-trip anchor). Also snapshot any Epic-authored or hand-authored engine material — assert no error (read path can't assume our tool built it).

### 3. `batch_material_assign`

```cpp
/**
 * Assigns one material to mesh slots across many actors in the current level,
 * selected by label, outliner folder, and/or name regex. Persists to the
 * level (component overrides), transacted as one undo step.
 *
 * @param Material The material or material instance to assign.
 * @param SlotName Slot to assign by name; empty = all slots on each mesh.
 * @param Targets  Actor selectors (ANDed; at least one required).
 * @return Assignment report: match/change counts, modified actor labels.
 */
static FMatAssignReport BatchMaterialAssign(UMaterialInterface* Material,
	const FString& SlotName, const FMatActorTargets& Targets);
```

Donor: `Handler_BatchMaterialAssign.cpp` — port its selector semantics and slot resolution; upgrade: donor's `slot` was index-based, this resolves by slot *name* with empty = all; wrap in a single `FScopedTransaction`. Slot-name resolution (5.8-verified): static meshes via `UStaticMesh::GetMaterialIndex(FName)` (`StaticMesh.h:2230`); skeletal via `USkinnedMeshComponent::GetMaterialIndex(FName)` (`SkinnedMeshComponent.h:1371`) — **`USkeletalMesh` has no such method**; asset-side fallback = iterate `GetMaterials()` matching `FSkeletalMaterial::MaterialSlotName`. Write path stays `UPrimitiveComponent::SetMaterial(int32, UMaterialInterface*)`.

**Smoke:** level with 3 labeled cube actors under folder `Smoke/`, one nonmatching sphere. Assign `M_SmokeRock` with `Targets.ByFolder="Smoke"`. Assert: report says 3 actors matched, sphere untouched (check its component material), viewport shows the change, single Ctrl+Z reverts all 3. Teardown: revert + delete actors.

---

## Family-level acceptance

Run Smoke blocks 1 (material) → 1 (function) → 2 → 3 in one sitting against a live 5.8 editor via `call_tool` (project: `F:\ai\claude-work\McpSmoke\`). End state: a material + function authored declaratively over MCP in single calls, snapshot round-trip proven, bulk-assigned across a level with one undo step. Interop assertion along the way: Epic's `MaterialTools.get_expressions` lists our nodes and our snapshot reads a graph Epic's tools touched — the two surfaces compose. Then delete `/Game/Smoke`.

## Build-session notes

- The graph materializer (spec → expressions → property import → wiring → recompile) mirrors blueprint-k2's; the material one is simpler (no exec pins, no wildcard pins). Wipe-then-rebuild for the regenerate path: `UMaterialEditingLibrary::DeleteAllMaterialExpressions(UMaterial*)` (`MaterialEditingLibrary.h:150`) / `DeleteAllMaterialExpressionsInFunction(UMaterialFunction*)` (`:377`). Beware: `DeleteMaterialExpression` guards `GetOuter() == Material` and silently skips in-function expressions (`MaterialEditingLibrary.cpp:592`) — always use the `InFunction` variants for function graphs.
- All APIs below **verified against 5.8 source** (ticket #302 verifier pass; key file `Editor/MaterialEditor/Public/MaterialEditingLibrary.h`). Exact-signature gotchas for the build session:
  - `RecompileMaterial(UMaterial*)` returns `TArray<FString>` (compiler error strings — fold them into `FMatBuildReport.Diagnostics`), not void (`MaterialEditingLibrary.h:267`). Function path: `UpdateMaterialFunction(UMaterialFunctionInterface*, UMaterial* PreviewMaterial = nullptr)` (`:388`).
  - `CreateMaterialExpression` (`:168`) / `CreateMaterialExpressionInFunction` (`:373`) return the new `UMaterialExpression*`; `ConnectMaterialExpressions` (`:242`) / `ConnectMaterialProperty` (`:232`) return `bool` — a false is a spec-edge diagnostic.
  - `UMaterial::GetExpressions()` / `UMaterialFunction::GetExpressions()` return `TConstArrayView<TObjectPtr<UMaterialExpression>>` (`Material.h:1479`, `MaterialFunction.h:206`), NOT `TArray<UMaterialExpression*>`; `FunctionExpressions` is `_DEPRECATED`.
  - `UMaterialExpressionMaterialFunctionCall::MaterialFunction` is `TObjectPtr<UMaterialFunctionInterface>` (`MaterialExpressionMaterialFunctionCall.h:86`); `UMaterialExpressionFunctionInput::InputType` is `TEnumAsByte<EFunctionInputType>`, `InputName`/`OutputName` are `FName`.
  - `EMaterialProperty` is a `UENUM` in `Runtime/Engine/Public/SceneTypes.h:146` — all ten pseudo-node property names map to `MP_*` members; donor's string table stays valid.
  - Snapshot read helpers all exist on `UMaterialEditingLibrary`: `GetMaterialPropertyInputNode` (`:317`), `GetInputsForMaterialExpression` (`:337`), `GetInputsForMaterialFunctionExpression` (`:341`), `GetMaterialExpressionInputNames`/`InputTypes`/`OutputNames` (`:325-333`).
- **Factory trap check passed** (unlike pro-viz OCIO / MRQ ProRes): `UMaterialFactoryNew` and `UMaterialFunctionFactoryNew` both live under `Editor/UnrealEd/Classes/Factories/` — UHT-public, `#include "Factories/MaterialFactoryNew.h"` is safe. `FScopedTransaction`: `UnrealEd/Public/ScopedTransaction.h`, editor-only.
- Connecting to function outputs happens through `ConnectMaterialExpressions` targeting the `FunctionOutput` expression's input pin — the `output.*` pseudo-node is materials-only; reject it in function specs with a node-id diagnostic.
- Snapshot property serialization: diff each expression against its class CDO, export only differing properties — otherwise round-trip specs bloat with every default.
- `FMatBuildReport.Asset` rides the `FToolsetReferenceConverter` (`{"refPath": ...}`) per overview conventions.
