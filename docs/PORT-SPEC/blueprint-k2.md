# PORT-SPEC — Blueprint K2 Graph Family (`UK2GraphToolset`)

Read [00-overview.md](00-overview.md) first. Designed in [ticket #298](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/298).

**Workflow served (locked):** full authoring loop — an agent builds working Blueprint logic from a prompt: scaffold class → declare variables/dispatchers → define whole functions and event handlers declaratively → compile → read node-keyed diagnostics → regenerate. **Whole-function declarative** model: a graph is always written as one atomic spec; fixing a graph means redefining it. No pin-level round-trips exist in this family.

**In scope:** class scaffold, variables, whole-function define, event-handler define, BP interfaces, event dispatchers, deep inspection (round-trippable), compile/diagnostics.
**Out of scope (decided):** anim BP and widget BP inspection (Epic ships `AnimationAssistantToolset` / `UMGToolSet` nearby); macros; timelines (revisit only if a Smoke run proves a workflow blocked without them).

**Donor code** (mine for graph-manipulation internals, NOT for tool shape): `UnrealAIConnection/Source/UnrealAIConnection/Private/MCP/Handlers/` — `Handler_CreateBlueprint.cpp`, `Handler_AddBlueprintVariable.cpp`, `Handler_AddBlueprintFunction.cpp`, `Handler_AddBlueprintNode.cpp`, `Handler_ConnectBlueprintPins.cpp`, `Handler_SetBlueprintNodePinDefault.cpp`, `Handler_CompileBlueprint.cpp`, `Handler_InspectBlueprint.cpp`. The donor's node-type registry and pin-resolution logic in `Handler_AddBlueprintNode.cpp` / `Handler_ConnectBlueprintPins.cpp` is the hard-won part — reuse it inside the graph materializer.

---

## Shared spec structs

Declared once in `K2GraphSpecTypes.h`. All reflected — these ARE the JSON schema the agent sees.

```cpp
/** A pin declaration: name + K2 type. Type strings use UE pin-category grammar:
 *  "bool", "int", "float", "string", "name", "text", "byte",
 *  "struct:/Script/CoreUObject.Vector", "object:/Script/Engine.Actor",
 *  "class:/Script/Engine.Actor", "enum:/Script/Engine.ECollisionChannel",
 *  and container wrappers "array:<inner>", "set:<inner>", "map:<key>,<value>". */
USTRUCT(BlueprintType)
struct FK2PinDecl
{
	GENERATED_BODY()

	UPROPERTY() FString Name;
	UPROPERTY() FString Type;
};

/** One node in a graph spec. `Id` is caller-chosen, unique per spec; diagnostics
 *  and inspection refer to nodes by this id. `Type` is one of the enumerated
 *  kinds below; `NodeClass` is the escape hatch for any other UK2Node subclass.
 *  Kinds: "call_function", "variable_get", "variable_set", "branch", "sequence",
 *  "for_each", "cast", "spawn_actor", "select", "make_struct", "break_struct",
 *  "make_array", "self", "dispatcher_call", "dispatcher_bind", "interface_message",
 *  "custom" (requires NodeClass). */
USTRUCT(BlueprintType)
struct FK2NodeSpec
{
	GENERATED_BODY()

	UPROPERTY() FString Id;
	UPROPERTY() FString Type;
	/** call_function: owning class + function name, e.g.
	 *  {"Class":"/Script/Engine.KismetSystemLibrary","Name":"PrintString"}. */
	UPROPERTY() FK2MemberRef Member;
	/** variable_get/set: variable name on this Blueprint (or Member for external). */
	UPROPERTY() FString Variable;
	/** cast/spawn_actor: target class. */
	UPROPERTY() FSoftClassPath TargetClass;
	/** Escape hatch: full UK2Node subclass path when Type=="custom". */
	UPROPERTY() FSoftClassPath NodeClass;
	/** Literal defaults for unconnected input pins, by pin name. Values in
	 *  UE export-text form (donor: Handler_SetBlueprintNodePinDefault.cpp). */
	UPROPERTY() TMap<FString, FString> PinDefaults;
};

/** Directed edge: "<node_id>.<pin_name>" -> "<node_id>.<pin_name>".
 *  Pseudo-nodes: "entry" (function/event entry pins) and "return" (function
 *  result pins). Exec pins are named "exec" (in) / "then" (out); Branch outs
 *  are "true"/"false"; ForEach outs are "loop_body"/"completed". */
USTRUCT(BlueprintType)
struct FK2EdgeSpec
{
	GENERATED_BODY()

	UPROPERTY() FString From;
	UPROPERTY() FString To;
};

/** A whole graph, applied atomically. */
USTRUCT(BlueprintType)
struct FK2GraphSpec
{
	GENERATED_BODY()

	UPROPERTY() TArray<FK2NodeSpec> Nodes;
	UPROPERTY() TArray<FK2EdgeSpec> Edges;
};

/** Function signature for define_function / interface declarations. */
USTRUCT(BlueprintType)
struct FK2FunctionSignature
{
	GENERATED_BODY()

	UPROPERTY() TArray<FK2PinDecl> Inputs;
	UPROPERTY() TArray<FK2PinDecl> Outputs;
	UPROPERTY() bool bPure = false;
	UPROPERTY() FString Category;
};

/** Compile outcome. Diagnostics carry the spec node id when the error maps to
 *  a spec-created node, else an empty NodeId + graph/asset context in Message. */
USTRUCT(BlueprintType)
struct FK2Diagnostic
{
	GENERATED_BODY()

	UPROPERTY() FString Severity;   // "error" | "warning"
	UPROPERTY() FString NodeId;
	UPROPERTY() FString Message;
};

USTRUCT(BlueprintType)
struct FK2CompileReport
{
	GENERATED_BODY()

	UPROPERTY() bool bSuccess = false;
	UPROPERTY() TArray<FK2Diagnostic> Diagnostics;
};
```

Variable / component / dispatcher / event declaration structs follow the same pattern (`FK2VariableDecl` {Name, Type, DefaultValue, bInstanceEditable, bExposeOnSpawn, Category, ReplicationMode("none"|"replicated"|"repnotify")}, `FK2ComponentDecl` {Name, ComponentClass, AttachParent}, `FK2DispatcherDecl` {Name, Inputs}, `FK2EventRef` {Kind("begin_play"|"tick"|"custom"|"input_action"|"interface"|"overridable"), Name}).

---

## Tools

All `static UFUNCTION(meta = (AICallable))` on `UK2GraphToolset : public UToolsetDefinition` (`UCLASS(BlueprintType, Hidden)`). All sync (game-thread budget fine for graph surgery). Errors raised via `RaiseScriptError`, never returned.

### 1. `inspect_blueprint`

```cpp
/**
 * Returns a structured digest of a Blueprint asset: parent class, variables,
 * components, event dispatchers, implemented interfaces, function signatures,
 * and (optionally) every graph in round-trippable FK2GraphSpec form — the
 * output graph spec is valid input for define_function / define_event_handler.
 *
 * @param Blueprint      The Blueprint asset to inspect.
 * @param bIncludeGraphs Include full graph specs (large); false = digest only.
 * @return The Blueprint digest.
 */
static FK2BlueprintDigest InspectBlueprint(UBlueprint* Blueprint, bool bIncludeGraphs);
```

Donor: `Handler_InspectBlueprint.cpp`. Round-trip property is the acceptance anchor: inspect → define with the same spec → identical recompile result.

**Smoke:** setup: any project BP (or one made by tool 2). Call with `bIncludeGraphs=true`. Assert: digest lists known variables/functions; a returned graph spec fed back into `define_function` compiles clean. Teardown: none.

### 2. `create_blueprint`

```cpp
/**
 * Creates a new Blueprint asset in one call: parent class, member variables,
 * and components (with attachment hierarchy). Idempotent on re-run with the
 * same AssetPath: existing asset is updated to match the declaration
 * (variables/components not listed are left untouched).
 *
 * @param AssetPath   Content path for the new asset, e.g. "/Game/BP/BP_Turret".
 * @param ParentClass Parent class of the Blueprint.
 * @param Variables   Member variables to declare.
 * @param Components  Components to add (SCS), with optional attach parents.
 * @return Reference to the created or updated Blueprint asset.
 */
static UBlueprint* CreateBlueprint(const FString& AssetPath, UClass* ParentClass,
	const TArray<FK2VariableDecl>& Variables, const TArray<FK2ComponentDecl>& Components);
```

Donor: `Handler_CreateBlueprint.cpp`, `Handler_AddBlueprintVariable.cpp`.

**Smoke:** call with `/Game/Smoke/BP_SmokeTurret`, parent `Actor`, 2 variables (`Health: float`, `Target: object:/Script/Engine.Actor`), 1 `StaticMeshComponent`. Assert: asset exists, `inspect_blueprint` shows both variables + component. Teardown: delete `/Game/Smoke`.

### 3. `declare_variables`

```cpp
/**
 * Declares (upserts) member variables on an existing Blueprint. Listed
 * variables are created or updated to match; unlisted variables are untouched.
 *
 * @param Blueprint The target Blueprint.
 * @param Variables Variable declarations to apply.
 * @return Updated digest (without graphs).
 */
static FK2BlueprintDigest DeclareVariables(UBlueprint* Blueprint,
	const TArray<FK2VariableDecl>& Variables);
```

Donor: `Handler_AddBlueprintVariable.cpp` (extend: donor is add-only; this is upsert incl. type change + rep settings).

**Smoke:** on BP from tool 2, upsert `Health` to `int` + add `Ammo: int`. Assert via inspect: type changed, new var present, `Target` untouched.

### 4. `define_function`

```cpp
/**
 * Creates or wholly replaces a function on a Blueprint: signature + graph,
 * applied atomically, then compiles. The graph spec is the complete function
 * body — any previous body is discarded. On compile failure the new graph is
 * kept (inspectable) and diagnostics identify offending nodes by spec id.
 *
 * @param Blueprint    The target Blueprint.
 * @param FunctionName Name of the function to create or replace.
 * @param Signature    Input/output pins, purity, category.
 * @param Graph        The complete function body.
 * @return Compile report with node-id-keyed diagnostics.
 */
static FK2CompileReport DefineFunction(UBlueprint* Blueprint, const FString& FunctionName,
	const FK2FunctionSignature& Signature, const FK2GraphSpec& Graph);
```

Donor: `Handler_AddBlueprintFunction.cpp` (function creation), `Handler_AddBlueprintNode.cpp` (node registry — the crown-jewel logic), `Handler_ConnectBlueprintPins.cpp` (pin resolution), `Handler_SetBlueprintNodePinDefault.cpp` (defaults), `Handler_CompileBlueprint.cpp`.

**Smoke:** on `BP_SmokeTurret`, define `TakeDamage(Amount: float) -> (Dead: bool)`: get `Health`, subtract, set `Health`, compare `<= 0`, return. Assert: `bSuccess=true`; second call with a broken spec (edge to nonexistent pin) raises/reports diagnostic naming the bad node id; redefine with fixed spec compiles clean.

### 5. `define_event_handler`

```cpp
/**
 * Creates or wholly replaces one event handler cluster in the event graph:
 * the event node (BeginPlay, Tick, custom event, input action, interface or
 * overridable event) plus its complete downstream graph. Other handlers in
 * the event graph are untouched. Compiles after applying.
 *
 * @param Blueprint The target Blueprint.
 * @param Event     Which event this handler is for.
 * @param Graph     Complete handler body ("entry" = the event node's outputs).
 * @return Compile report with node-id-keyed diagnostics.
 */
static FK2CompileReport DefineEventHandler(UBlueprint* Blueprint,
	const FK2EventRef& Event, const FK2GraphSpec& Graph);
```

Donor: same set as `define_function`.

**Smoke:** define BeginPlay: `PrintString("turret up")`. Assert compile clean; PIE via SceneTools/automation shows log line (manual eye ok). Redefine BeginPlay with different string — assert old cluster gone (inspect: one BeginPlay only).

### 6. `define_interface`

```cpp
/**
 * Creates or updates a Blueprint Interface asset with the given function
 * signatures. Listed functions are upserted; unlisted are untouched.
 *
 * @param AssetPath Content path, e.g. "/Game/BP/BPI_Damageable".
 * @param Functions Interface function declarations (name + signature).
 * @return Reference to the interface asset.
 */
static UBlueprint* DefineInterface(const FString& AssetPath,
	const TArray<FK2FunctionDecl>& Functions);
```

Donor: none direct (new capability); asset-creation plumbing from `Handler_CreateBlueprint.cpp`.

**Smoke:** create `BPI_Damageable` with `ApplyDamage(Amount: float)`. Assert asset exists, function listed on inspect.

### 7. `implement_interface`

```cpp
/**
 * Adds an interface to a Blueprint and defines the implementation graph for
 * each of its functions in one atomic call, then compiles.
 *
 * @param Blueprint       The target Blueprint.
 * @param Interface       The Blueprint Interface asset to implement.
 * @param Implementations Map of interface function name -> complete body.
 * @return Compile report with node-id-keyed diagnostics.
 */
static FK2CompileReport ImplementInterface(UBlueprint* Blueprint, UBlueprint* Interface,
	const TMap<FString, FK2GraphSpec>& Implementations);
```

**Smoke:** implement `BPI_Damageable` on `BP_SmokeTurret`, body calls `TakeDamage`. Assert compile clean; inspect lists interface; `interface_message` node from a second BP compiles against it.

### 8. `declare_dispatchers`

```cpp
/**
 * Declares (upserts) event dispatchers on a Blueprint. Binding and calling
 * happen inside graph specs via node kinds "dispatcher_bind" /
 * "dispatcher_call" — this tool only declares them.
 *
 * @param Blueprint   The target Blueprint.
 * @param Dispatchers Dispatcher declarations (name + input pins).
 * @return Updated digest (without graphs).
 */
static FK2BlueprintDigest DeclareDispatchers(UBlueprint* Blueprint,
	const TArray<FK2DispatcherDecl>& Dispatchers);
```

**Smoke:** declare `OnDied()`. Redefine `TakeDamage` graph adding `dispatcher_call` node on the `Dead` branch. Assert compile clean.

### 9. `compile_blueprint`

```cpp
/**
 * Compiles a Blueprint and returns diagnostics. Define-tools already compile;
 * this exists for post-hoc checks and for compiling after external changes.
 *
 * @param Blueprint The Blueprint to compile.
 * @return Compile report. Diagnostics carry node ids where the node originated
 *         from a spec in this session; otherwise node display names.
 */
static FK2CompileReport CompileBlueprint(UBlueprint* Blueprint);
```

Donor: `Handler_CompileBlueprint.cpp`.

**Smoke:** compile `BP_SmokeTurret` → clean. Break it externally (delete `Health` var via `declare_variables` upsert to different name — or manual) → compile reports error.

---

## Family-level acceptance

Run Smoke blocks 2 → 3 → 4 → 6 → 7 → 8 → 5 → 9 → 1 in one sitting against a live 5.8 editor via `call_tool` (project: `F:\ai\claude-work\McpSmoke\`). End state: `BP_SmokeTurret` is a compiling actor with variables, components, a function, an interface implementation, a dispatcher, and a BeginPlay handler — authored entirely over MCP with zero pin-level calls. Then delete `/Game/Smoke`.

## Build-session notes

- The graph materializer (spec → UK2Node instances → pin wiring → defaults) is the whole game. Donor's `Handler_AddBlueprintNode.cpp` node registry + `Handler_ConnectBlueprintPins.cpp` pin matcher contain the edge cases (hidden pins, wildcard pins, self pins, exec fan-out) already solved once — port their logic, not their granularity.
- Verify every UE API symbol above against 5.8 before writing bodies (`ue-api-verifier`); stubs were designed against 5.7-era donor knowledge plus the 5.8 primer, and `FK2MemberRef`/`FK2FunctionDecl`/digest structs are to be defined by the build session alongside the listed ones.
- `bIncludeGraphs` inspection must emit specs this family can consume — that round-trip is what makes regenerate-to-fix safe.
