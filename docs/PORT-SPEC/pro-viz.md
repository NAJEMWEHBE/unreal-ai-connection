# PORT-SPEC — Pro-Viz Family (`UDmxToolset`, `UNDisplayToolset`, `UOcioToolset`)

Read [00-overview.md](00-overview.md) first. Designed in [ticket #299](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/299).

**Convention deviation (decided):** this family file specs **three** toolset classes, not one. DMX, nDisplay and OCIO are unrelated domains; one `UProVizToolset` would muddy the AI-facing toolset description and force `describe_toolset` to return unrelated tools together. Each class self-describes its domain; all three live in this one family file and one build session.

**Moat status:** Epic's live 5.8 `list_toolsets` (55 toolsets, verified in [#296](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/296)) ships **no DMX, nDisplay, or OCIO toolsets**. Strong moat, unchanged from the overlap scan.

**Workflows served (locked):**

- **DMX** — full rig-from-scratch + live output: an agent starts from an empty project, declares library + fixture types + patches in one atomic call, then streams channel values live over Art-Net. Donor only covered patch-into-existing-library; the rig tool is the redesign.
- **nDisplay** — inspect + targeted edit of an existing cluster config: read topology, tweak viewport regions/cameras/GPU/projection params and node hosts/windows. No from-scratch cluster authoring (config-editor territory, decided out).
- **OCIO** — inspect + curate: read a config asset (curated sets + full `.ocio` enumeration), create/update the asset declaratively (file pointer, desired color spaces, display-views, context). The `.ocio` file itself stays external. Viewport apply/wiring decided out.

**Donor code** (mine for internals, NOT tool shape):

- `UnrealAIConnectionDMX/Source/UnrealAIConnectionDMX/Private/Handlers/Handler_CreateDmxPatch.cpp` — the C++ fixture-type bind (`FDMXEntityFixtureTypeRef(UDMXEntityFixtureType*)` ctor bypasses the protected-GUID / Python-edit-gate problem), the display-name-vs-UObject-name trap, and the 5.6-verified factory bug: `CreateFixturePatchInLibrary` ignores `UniverseID`/`StartingAddress` in construction params — apply via `SetUniverseID()` / `SetStartingChannel()` after creation. Assume the bug until proven fixed in 5.8.
- `.../Handlers/Handler_DmxStream.cpp` — the crown jewel: `FTSTicker::GetCoreTicker()` game-thread re-send loop. `FDMXOutputPort::SendDMX` asserts `IsInGameThread()`; a background thread crashes. Ticker fires whenever the editor ticks; an idle editor holds the last frame — correct Art-Net hold-last behaviour. Port this design wholesale.
- `UnrealAIConnectionNDisplay/.../Handlers/Handler_InspectNdisplayConfig.cpp` — load-as-UObject-then-cast pattern, `GetOrLoadConfig()` null on uncompiled assets, sorted-ids-for-stable-output, projection policy = Type string + free-form param map.
- `UnrealAIConnectionOCIO/.../Handlers/Handler_InspectOcioConfig.cpp` — curated `Desired*` UPROPERTYs always readable; full enumeration needs `WITH_OCIO` + `GetOrCreateConfigWrapper()`; degrade gracefully with a note, never fail the call.

---

## Shared spec structs

Declared in `ProVizSpecTypes.h`. All reflected — these ARE the JSON schemas the agent sees.

```cpp
/** One logical DMX function (attribute) inside a fixture mode.
 *  Channel is the 1-based offset within the mode's footprint. */
USTRUCT(BlueprintType)
struct FDmxFunctionSpec
{
	GENERATED_BODY()

	UPROPERTY() FString Name;                       // e.g. "Dimmer", "Pan", "Red"
	UPROPERTY(meta=(ClampMin=1, ClampMax=512)) int32 Channel = 1;
	UPROPERTY(meta=(ClampMin=8, ClampMax=16)) int32 BitDepth = 8;   // 8 or 16 (16 = coarse+fine pair)
};

/** One mode of a fixture type (e.g. "8ch", "16ch extended"). */
USTRUCT(BlueprintType)
struct FDmxModeSpec
{
	GENERATED_BODY()

	UPROPERTY() FString Name;
	UPROPERTY() TArray<FDmxFunctionSpec> Functions;
};

/** A fixture type declaration. Upserted by Name (display name). */
USTRUCT(BlueprintType)
struct FDmxFixtureTypeSpec
{
	GENERATED_BODY()

	UPROPERTY() FString Name;
	UPROPERTY() TArray<FDmxModeSpec> Modes;
};

/** A patch declaration. Upserted by Name. FixtureType refers to a
 *  FDmxFixtureTypeSpec.Name in the same rig spec, or an existing
 *  fixture type's display name in the library. */
USTRUCT(BlueprintType)
struct FDmxPatchSpec
{
	GENERATED_BODY()

	UPROPERTY() FString Name;
	UPROPERTY() FString FixtureType;
	UPROPERTY(meta=(ClampMin=0)) int32 Universe = 1;
	UPROPERTY(meta=(ClampMin=1, ClampMax=512)) int32 StartingAddress = 1;
	UPROPERTY(meta=(ClampMin=0)) int32 ActiveMode = 0;
};

/** Whole rig, applied declaratively. Empty arrays = inspect-only call. */
USTRUCT(BlueprintType)
struct FDmxRigSpec
{
	GENERATED_BODY()

	UPROPERTY() TArray<FDmxFixtureTypeSpec> FixtureTypes;
	UPROPERTY() TArray<FDmxPatchSpec> Patches;
};

/** One channel value for streaming. */
USTRUCT(BlueprintType)
struct FDmxChannelValue
{
	GENERATED_BODY()

	UPROPERTY(meta=(ClampMin=1, ClampMax=512)) int32 Channel = 1;
	UPROPERTY(meta=(ClampMin=0, ClampMax=255)) int32 Value = 0;
};

/** Stream status. Universes lists {Universe, ChannelsHeld} pairs. */
USTRUCT(BlueprintType)
struct FDmxStreamStatus
{
	GENERATED_BODY()

	UPROPERTY() bool bRunning = false;
	UPROPERTY() int64 Frames = 0;
	UPROPERTY() int32 OutputPorts = 0;
	UPROPERTY() TArray<FDmxUniverseStatus> Universes;
};
```

Digest structs mirror inspection output and follow the same pattern: `FDmxRigDigest` {Library (`UDMXLibrary*` — reflects as `{"refPath"}`), FixtureTypes (name, modes with function name/channel/bit-depth), Patches (name, fixture type, universe, starting address, active mode), OutputPorts (name, protocol, destination address, enabled)}; `FDmxUniverseStatus` {Universe, ChannelsHeld}; `FNDisplayConfigDigest` {Name, PrimaryNodeId, Nodes (id, host, window rect, viewports: id, enabled, camera, gpu index, region rect, projection type, projection params map)}; `FOcioConfigDigest` {Name, ConfigFile, DesiredColorSpaces (name, family), DesiredDisplayViews (display, view), Context map, AllColorSpaces, AllDisplays, AllViews (display → view list), Note (empty unless enumeration degraded)}. Edit-spec structs for nDisplay: `FNDisplayNodeEdit` {Id, Host (empty = leave), bSetWindow, Window rect}, `FNDisplayViewportEdit` {NodeId, ViewportId, optional-field pattern: bSetEnabled/bEnabled, Camera, GpuIndex (-1 = leave), bSetRegion/Region, ProjectionParams (empty map = leave; else merged key-by-key)}. OCIO curation structs: `FOcioColorSpaceRef` {Name, Family}, `FOcioDisplayViewRef` {Display, View}.

---

## `UDmxToolset`

All `static UFUNCTION(meta = (AICallable))`, `UCLASS(BlueprintType, Hidden)`, deriving `UToolsetDefinition`. Errors via `RaiseScriptError`, never returned. All sync — streaming is fire-and-forget into the module-owned ticker, no call blocks.

**Streamer state note (build):** the donor's `FUCMCPDmxStreamer` singleton moves to the plugin **module class** (editor-only), not the toolset class — toolset methods are static and stateless; the module owns lifecycle and calls `StopAndClear()` on shutdown.

### 1. `setup_dmx_rig`

```cpp
/**
 * Declaratively creates or updates a DMX rig in one call: ensures the
 * DMXLibrary asset exists at LibraryPath, upserts the declared fixture types
 * (modes and functions) and patches (universe/address/mode), and reports the
 * resulting rig including configured DMX output ports. Existing entities not
 * named in the spec are left untouched. Calling with an empty spec inspects
 * the library without changing it.
 *
 * @param LibraryPath Content path of the DMXLibrary, e.g. "/Game/DMX/MyRig".
 * @param Rig         Fixture types and patches to declare (may be empty).
 * @param bSave       Save the library asset after applying. Default true.
 * @return Full rig digest: fixture types, patches, and output-port status.
 */
static FDmxRigDigest SetupDmxRig(const FString& LibraryPath, const FDmxRigSpec& Rig, bool bSave = true);
```

Donor: `Handler_CreateDmxPatch.cpp` (FT-ref bind, name trap, factory-param bug — all three carry over). New ground, **all 5.8-source-verified**: library creation via `UDMXLibraryFactory` (public header, `DMXEditor/Public/Factories/DMXLibraryFactory.h:13`) + `IAssetTools::CreateAsset`; fixture-type creation via `UDMXEntityFixtureType::CreateFixtureTypeInLibrary(FDMXEntityFixtureTypeConstructionParams, DesiredName, bMarkDMXLibraryDirty)` (`DMXEntityFixtureType.h:370` — exact parallel to the patch factory; params carry `ParentDMXLibrary` + `DMXCategory`); mode/function population via `UDMXEntityFixtureType::Modes` → `FDMXFixtureMode::Functions` → `FDMXFixtureFunction` {`Channel` int32 (h:116), `Attribute` FDMXAttributeName (h:102), `DataType` (h:120)}; 16-bit = `EDMXFixtureSignalFormat::E16Bit` (`DMXProtocolTypes.h:91`). Output ports are **report-only**: enumerate `FDMXPortManager::Get().GetOutputPorts()` (returns `TArray<FDMXOutputPortSharedRef>` — shared refs, not raw pointers); if zero, the digest says so — the tool never edits project settings (Protocol Settings ports are user config).

**Smoke:** setup (one-time, manual): McpSmoke project has DMX plugins enabled and one Art-Net output port ("SmokeOut", 127.0.0.1) in Project Settings → DMX. Call `setup_dmx_rig` with `/Game/Smoke/DMX_SmokeRig`, one fixture type "SmokePar" (mode "3ch": Dimmer@1, Red@2, Green@3), two patches ("Par1"@universe 1 addr 1, "Par2"@universe 1 addr 4). Assert: digest lists both patches at declared addresses, output port present. Re-run same call — assert identical digest (idempotent, no duplicate entities). Call with empty spec — assert digest unchanged (pure inspect). Teardown: delete `/Game/Smoke`.

### 2. `stream_dmx_channels`

```cpp
/**
 * Starts or updates continuous DMX output. Held channel values are re-sent to
 * every configured DMX output port each editor frame (Art-Net hold-last-frame
 * behaviour), without blocking. Values persist until overwritten or the
 * stream is stopped.
 *
 * @param Universe Local universe id to write into.
 * @param Channels Channel/value pairs to set (1-512, 0-255).
 * @param bMerge   true = merge into held values; false = replace the universe.
 * @return Stream status after applying.
 */
static FDmxStreamStatus StreamDmxChannels(int32 Universe, const TArray<FDmxChannelValue>& Channels, bool bMerge = true);
```

Donor: `Handler_DmxStream.cpp` (`dmx_stream_set`) — port the ticker design wholesale (see streamer state note). Channels move from a JSON string-key map to a typed array for clean schema reflection. Raise on zero output ports (donor's `no_output_ports`).

**Smoke:** after tool 1's rig: stream universe 1 {1:255, 2:128}. Assert: `bRunning=true`, `OutputPorts>=1`. Poll `get_dmx_stream_status` twice ~1s apart — assert `Frames` increased (editor ticking). Wire assertion optional: Art-Net capture on 127.0.0.1 shows ch1=255 (manual eye ok, matches K2 precedent).

### 3. `stop_dmx_stream`

```cpp
/**
 * Stops continuous DMX output and clears all held channel values.
 *
 * @return Stream status after stopping (bRunning=false, empty universes).
 */
static FDmxStreamStatus StopDmxStream();
```

Donor: `Handler_DmxStream.cpp` (`dmx_stream_stop`).

**Smoke:** call after tool 2. Assert `bRunning=false`, `Universes` empty; status poll agrees.

### 4. `get_dmx_stream_status`

```cpp
/**
 * Reports whether the DMX streamer is running, frames sent, configured output
 * ports, and which universes hold values.
 *
 * @return Current stream status.
 */
static FDmxStreamStatus GetDmxStreamStatus();
```

Donor: `Handler_DmxStream.cpp` (`dmx_stream_status`).

**Smoke:** covered inside tools 2/3 smokes (poll pattern).

---

## `UNDisplayToolset`

### 5. `inspect_ndisplay_config`

```cpp
/**
 * Returns the cluster topology of an nDisplay configuration asset: primary
 * node id and, per cluster node, host address, window rectangle, and
 * viewports (region, camera, GPU index, enabled flag, projection policy type
 * and parameters).
 *
 * @param Config The nDisplay configuration asset (Display Cluster Blueprint).
 * @return Cluster topology digest.
 */
static FNDisplayConfigDigest InspectNDisplayConfig(UDisplayClusterBlueprint* Config);
```

Donor: `Handler_InspectNdisplayConfig.cpp` — direct port. `GetOrLoadConfig()` null → raise (donor's `no_config_data`). Typed param replaces the donor's path string (reference converter).

**Smoke:** setup (one-time, manual): create an nDisplay config asset in McpSmoke from the editor's nDisplay template (2 nodes or the default single-node). Call inspect. Assert: primary node id non-empty, node count matches template, each viewport reports a projection type. Teardown: none (asset kept for tool 6).

### 6. `edit_ndisplay_config`

```cpp
/**
 * Applies targeted edits to an existing nDisplay configuration asset: primary
 * node, per-node host and window rectangle, per-viewport enabled/camera/GPU
 * index/region and projection policy parameters (merged key-by-key). Only
 * referenced nodes and viewports are touched; ids must already exist — this
 * tool edits clusters, it does not create them. Saves and recompiles the
 * asset, then returns the updated topology.
 *
 * @param Config        The nDisplay configuration asset to edit.
 * @param PrimaryNodeId New primary node id; empty = leave unchanged.
 * @param NodeEdits     Per-node edits (host, window rect).
 * @param ViewportEdits Per-viewport edits (enabled, camera, GPU, region, projection params).
 * @return Updated cluster topology digest.
 */
static FNDisplayConfigDigest EditNDisplayConfig(UDisplayClusterBlueprint* Config,
	const FString& PrimaryNodeId, const TArray<FNDisplayNodeEdit>& NodeEdits,
	const TArray<FNDisplayViewportEdit>& ViewportEdits);
```

Donor: inspector (read side). Write path **5.8-source-verified** against the `DisplayClusterConfigurator` module (its viewport view-models are the sanctioned pattern): per config object `Modify()` → mutate → `MarkPackageDirty()`, then blueprint-level `FDisplayClusterConfiguratorUtils::MarkDisplayClusterBlueprintAsModified(Object, bIsStructuralChange)` (`DisplayClusterConfiguratorUtils.cpp:197`) which routes to `FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified` / `MarkBlueprintAsModified` — the structural variant triggers the recompile, no manual `FKismetEditorUtilities::CompileBlueprint` needed for edits. Then save. Unknown node/viewport id → raise with the sorted list of valid ids in the message.

**Smoke:** on the tool-5 asset: set viewport region of node's first viewport to x=0,y=0,w=1280,h=720 and add projection param `test_key=test_val`. Assert: returned digest shows both. Close/reopen the asset (or re-inspect after `LoadAsset` fresh) — assert edits persisted. Edit with bogus viewport id — assert raised error names valid ids. Teardown: delete the smoke config asset.

---

## `UOcioToolset`

### 7. `inspect_ocio_config`

```cpp
/**
 * Returns an OpenColorIO configuration asset's curated color spaces,
 * display-views and context map, plus the full set of color spaces, displays
 * and views enumerated from the underlying .ocio file when the OCIO library
 * is available. If enumeration is unavailable the curated fields still
 * return and Note explains why.
 *
 * @param Config The OpenColorIO configuration asset.
 * @return Configuration digest.
 */
static FOcioConfigDigest InspectOcioConfig(UOpenColorIOConfiguration* Config);
```

Donor: `Handler_InspectOcioConfig.cpp` — direct port, including the `WITH_OCIO` / null-wrapper graceful-degrade + Note behaviour. Typed param replaces path string.

**Smoke:** setup: minimal hand-written `.ocio` file checked into McpSmoke (`Content/Smoke/smoke_config.ocio`, 2 color spaces, 1 display/view) + a config asset made by tool 8. Call inspect. Assert: `ConfigFile` matches, `AllColorSpaces` lists both spaces (or Note explains degrade — then curated fields still assert). Teardown: with tool 8's.

### 8. `define_ocio_config`

```cpp
/**
 * Creates or updates an OpenColorIO configuration asset declaratively: points
 * it at a .ocio file and sets the curated (Desired) color spaces,
 * display-views and context map. The given lists REPLACE the curated sets;
 * the context map replaces the existing map. The .ocio file itself is never
 * written. Saves the asset and returns its digest.
 *
 * @param AssetPath        Content path, e.g. "/Game/Smoke/OCIO_Smoke".
 * @param ConfigFile       Path to the .ocio file (project-relative or absolute).
 * @param DesiredColorSpaces Curated color spaces (name + family).
 * @param DesiredDisplayViews Curated display-views (display + view).
 * @param Context          OCIO context key-value map.
 * @return Digest of the created or updated asset.
 */
static FOcioConfigDigest DefineOcioConfig(const FString& AssetPath, const FString& ConfigFile,
	const TArray<FOcioColorSpaceRef>& DesiredColorSpaces,
	const TArray<FOcioDisplayViewRef>& DesiredDisplayViews,
	const TMap<FString, FString>& Context);
```

Donor: inspector (read side + struct shapes: `FOpenColorIOColorSpace.ColorSpaceName/FamilyName`, `FOpenColorIODisplayView.Display/View` — donor-verified public). New ground, **5.8-source-verified with two gotchas**: (1) `ConfigurationFile` is a **private** UPROPERTY in 5.8 (`OpenColorIOConfiguration.h:165`, `AllowPrivateAccess`) — set it via `SetConfigurationFile(const FFilePath&)` (h:146), never direct member access; the setter already calls `ReloadExistingColorspaces()` internally, so no explicit reload needed after pointing at the file (`ReloadExistingColorspaces(bool bForce=false)` is public BlueprintCallable, h:71, if state is mutated another way). `DesiredColorSpaces` / `DesiredDisplayViews` / `Context` remain public UPROPERTYs. (2) The create-new factory is `UOpenColorIOConfigurationFactoryNew`, but its header lives under the editor module's **Private/Factories/** — not exported, cannot be `#include`d from our module. Create via `NewObject<UOpenColorIOConfiguration>` into a new package (+ `FAssetRegistryModule::AssetCreated`, mark dirty, save) or look the factory `UClass` up by name; do not spec a factory include. Nonexistent `.ocio` file → raise (`config_file_not_found`) rather than creating a broken asset.

**Smoke:** create `/Game/Smoke/OCIO_Smoke` pointing at `smoke_config.ocio`, curate 1 color space + 1 display-view + context `{shot: "sm01"}`. Assert: digest echoes all three; tool-7 inspect on a fresh load agrees. Re-run with a different curated list — assert lists replaced, not merged. Teardown: delete `/Game/Smoke`, remove the `.ocio` file.

---

## Family-level acceptance

Run Smoke blocks 1 → 2 → 4 → 3 → 5 → 6 → 8 → 7 in one sitting against a live 5.8 editor via `call_tool` (project: `F:\ai\claude-work\McpSmoke\` + the one-time manual setups: Art-Net port, nDisplay template asset, `smoke_config.ocio`). End state: a from-scratch DMX rig streamed live and stopped clean, an nDisplay cluster inspected and edited with persistence proven, an OCIO asset created, curated, and round-tripped through inspect. Then delete `/Game/Smoke`.

## Build-session notes

- **Plugin dependencies (locked at [#305](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/305)):** the module needs `DMXRuntime`, `DMXProtocol`, `DMXEditor` (verify split), `DisplayCluster`, `DisplayClusterConfiguration`, `OpenColorIO` — all editor-only usage; keep the plugin editor-only per overview. These are **hard `Plugins` dependencies in the .uplugin** — no per-domain compile-out, no `#if` guards; this is a pro-viz plugin by definition. Sole soft dependency in the whole spec stays MRQ's ProRes runtime class lookup (see mrq.md).
- **Every UE API named above was verified against 5.8 source at design time** (`F:\UE_5.8`, ue-api-verifier pass on [#299](https://github.com/NAJEMWEHBE/unreal-ai-connection/issues/299), 11/11 symbols confirmed with file:line evidence). The spec's own structs (`FDmx*Spec`, digests, edit specs) are ours to define — verify nothing there, just keep them reflected. `RaiseScriptError` is `DevelopmentOnly` meta — compiles out of shipping; fine, plugin is editor-only.
- The patch-factory param bug (`UniverseID`/`StartingAddress` ignored) was host-build-verified on 5.6. Re-test on 5.8 during the first Smoke run; keep the post-create setter calls either way — they're idempotent.
- Streamer singleton lives in the module class, never in toolset statics; module `ShutdownModule()` must `StopAndClear()` or the ticker outlives the plugin on unload.
- `TMap<FString, FString>` (OCIO context, projection params) schema reflection: confirm the reflected JSON shape early — if maps reflect poorly, fall back to a `TArray<FKeyValue>` pair struct like the DMX channels decision.
