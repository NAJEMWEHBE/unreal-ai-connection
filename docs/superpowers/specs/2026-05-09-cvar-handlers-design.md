# UnrealClaudeMCP v0.10.x — CVar handlers design

**Date:** 2026-05-09
**Status:** Proposed (autonomy mode — spec stands as the design contract)
**Author:** Claude
**Target release:** v0.10.1 (closes Tier 1 ergonomics roadmap)

---

## Goal

Add the last two Tier 1 ergonomics handlers — `get_console_variable` and `set_console_variable` — bringing the project's tool count from 36 → 38 and closing the Tier 1 surface called out in `docs/HANDOFF.md` ("Autonomy roadmap"). After this PR, every Tier 1 sibling proposed in the autonomy brainstorm is shipped, and the next strategic move is Tier 2 (editor event push, long-running task tracking, persistent Python REPL).

## Scope

**In scope:**
- `get_console_variable(name)` — read a single Console Variable by name, returning all four representations (int / float / bool / string) plus its detected type and read-only flag.
- `set_console_variable(name, value)` — mutate a Console Variable. `value` is polymorphic (string / number / bool); the handler coerces to a string and routes through UE's `IConsoleVariable::Set`. Pre-rejects read-only CVars and post-verifies the change landed.

**Out of scope (deferred):**
- Console *commands* (no associated value, executed not set). Already covered by `execute_console_command` — use that for `r.RestartRenderer` etc.
- Bulk read (`get_console_variables(prefix)`) — pure ergonomic sugar over a single call; no code path different from looping `get_console_variable`. Defer until concrete demand.
- Auto-completion / search by prefix — same reason; would belong in a separate `find_console_variables(prefix)` handler.
- CVar change subscriptions — fits in Tier 2 (editor event push) alongside `FConsoleVariableMulticastDelegate`. Don't fragment that work.

## Why two handlers, not one combined `console_variable(op, name, value)`

We considered three shapes:

1. **Two handlers** — `get_console_variable` + `set_console_variable` (this proposal).
2. **One combined handler** with an `op: "get" | "set"` discriminator and conditional `value` param.
3. **Two handlers + a `find_console_variables(prefix)` discovery helper** as a third tool.

**Chosen Option 1.** Reasons:

- Mirrors every other paired handler in the codebase — `inspect_*` vs `set_*`, `inspect_material_instance` vs `set_mi_parameter`, `inspect_blueprint` vs `compile_blueprint`. Discriminator-based handlers don't exist in this project (only `edit_widget_tree` uses an op enum, and that's because the underlying graph mutations have *different schemas per op*).
- Symmetric handlers have simpler test surfaces: each tests one path. Discriminator handlers couple the get-path test to the set-path test through shared input validation.
- The `set` handler can have a *required* `value` field (cleaner contract); the discriminator approach has to make `value` optional and rely on runtime checks.
- Forward-compatible: a future `find_console_variables(prefix)` discovery handler can be added later without re-shaping these two.

Option 3's discovery helper is genuinely useful but is its own bundle — defer until the user asks for CVar tab-completion semantics.

---

## API surface (UE 5.7, source-grounded)

All facts cited against `F:\UE_5.7\Engine\Source\Runtime\Core\Public\HAL\IConsoleManager.h`:

| Symbol | Location | Notes |
|---|---|---|
| `IConsoleManager::Get()` | `IConsoleManager.h:1270` | Inline static singleton accessor. |
| `IConsoleManager::FindConsoleVariable(const TCHAR* Name, bool bTrackFrequentCalls = true)` | `IConsoleManager.h:1170` | Returns `IConsoleVariable*` or `nullptr`. Returns null for console *commands* (those use `FindConsoleObject` → `IsConsoleCommand()`). |
| `IConsoleVariable::IsVariableBool/Int/Float/String()` | `IConsoleManager.h:478-481` | Type introspection — exactly one returns true on a typed CVar. |
| `IConsoleVariable::GetBool/GetInt/GetFloat/GetString()` | `IConsoleManager.h:628-637` | All four work on any CVar type via UE's internal coercion. |
| `IConsoleVariable::Set<T>(T Value, EConsoleVariableFlags Flags = ECVF_SetByCode, FName Tag = NAME_None)` | `IConsoleManager.h:750` | Templated convenience — accepts `bool/int32/float/const TCHAR*`. Routes through `Set(TCHAR*, FSetContext&)` at line 721, then through `Set(TCHAR*, FResolvedContext&)` at line 615 (the pure virtual). |
| `IConsoleVariable::GetFlags() const` | `IConsoleManager.h:410` | Returns `EConsoleVariableFlags` for ReadOnly / SetBy / etc. testing. |
| `ECVF_ReadOnly = 0x4` | `IConsoleManager.h:71` | Static attribute. Set on CVars that can only change during early init. |
| `ECVF_SetByConsole = 0x0F000000` | `IConsoleManager.h:175` | Highest SetBy priority. Matches user-typed-in-console semantics. |
| `ECVF_SetByCode = 0x0E000000` | `IConsoleManager.h:173` | Default SetBy priority. |
| `ECVF_SetByMask = 0xff000000` | `IConsoleManager.h:140` | Mask for the upper SetBy byte. |

### Decision: `ECVF_SetByConsole` for our `Set` calls

UE's CVar system has SetBy *priorities*: a `Set` with priority lower than the current setter is silently ignored. The four common priorities (low → high): `Constructor < Scalability < ProjectSetting < SystemSettingsIni < DeviceProfile < Code < Console`. If a CVar was set by `DefaultEngine.ini`, calling `Set(value, ECVF_SetByCode)` won't take effect.

We pass `ECVF_SetByConsole` so our calls behave like a user typing the command in the editor's console — they always take effect (matching the user's mental model and pairing naturally with `execute_console_command`'s implicit semantics).

### Decision: pre-reject read-only CVars

`ECVF_ReadOnly` CVars (`r.RHIThreadEnable`, `r.SkinCache.CompileShaders`, etc.) only accept changes during very early initialization. After that, `Set` calls silently no-op. We pre-check `(GetFlags() & ECVF_ReadOnly) != 0` and return a `read_only` error rather than letting the call disappear into the void.

### Decision: post-verify the Set

After calling `Set`, we read the value back via `GetString()` and include both the requested value and the actual post-set value in the response. Mismatch is logged in the response (`note` field) but not flagged as an error — the priority semantics could legitimately reject the change against a higher-priority prior setter. This matches the post-verify discipline established for material instance parameter sets (HANDOFF.md "UE 5.7 traps already mapped" table).

---

## Tool 1: `get_console_variable`

Read a CVar's current value plus its type metadata.

### Params

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Exact CVar name, case-sensitive (e.g. `r.ScreenPercentage`, `Slate.bAllowToolTips`). |

### Result

```json
{
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
}
```

- `type` — one of `int`, `float`, `bool`, `string`, `unknown`. Derived from `IsVariable*()`.
- `read_only` — `(GetFlags() & ECVF_ReadOnly) != 0`.
- `set_by` — humanized last setter (one of `Constructor`, `Scalability`, `GameSetting`, `ProjectSetting`, `SystemSettingsIni`, `DeviceProfile`, `Code`, `Console`, `Commandline`, etc.).
- `value_string/int/float/bool` — all four representations always populated via UE's coercing getters; clients pick whichever fits.
- `help` — `IConsoleObject::GetHelp()` text. Empty string if none.

### Errors

| Code | Trigger |
|---|---|
| `missing_required_field` | `name` was missing or empty. |
| `cvar_not_found` | `IConsoleManager::FindConsoleVariable` returned null. Distinguishes "wrong name" from "is a console command" — the latter still returns null via this lookup, so the user-facing message points to `execute_console_command`. |

---

## Tool 2: `set_console_variable`

Mutate a CVar's value. Polymorphic input — accepts JSON string / number / bool — coerced to a string and forwarded to UE.

### Params

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `name` | string | yes | — | Exact CVar name, case-sensitive. |
| `value` | string \| number \| bool | yes | — | New value. Number / bool are converted to their canonical string form (`"42"`, `"1.5"`, `"1"`/`"0"`) before being passed to `IConsoleVariable::Set`. UE's underlying parser handles type coercion against the CVar's declared type. |

### Result

```json
{
  "ok": true,
  "name": "r.ScreenPercentage",
  "type": "float",
  "requested_value": "75",
  "value_string": "75",
  "value_int": 75,
  "value_float": 75.0,
  "value_bool": true,
  "set_by": "Console"
}
```

If the value didn't change because of priority semantics or rejection, the response includes a `note` field:

```json
{
  "ok": true,
  "name": "r.X",
  ...
  "note": "Set was accepted by IConsoleVariable but the post-set value ('5') differs from the requested value ('10'). The CVar may have a higher-priority setter (see set_by) or its parser rejected the input."
}
```

### Errors

| Code | Trigger |
|---|---|
| `missing_required_field` | `name` or `value` was missing or empty. |
| `cvar_not_found` | `FindConsoleVariable` returned null. |
| `read_only` | The CVar has the `ECVF_ReadOnly` flag set. The error message names the flag and points to `DefaultEngine.ini` as the legitimate setting site for read-only CVars. |
| `invalid_value_type` | `value` is a JSON object or array. Only string / number / bool are accepted. |

---

## Implementation shape

Two files of ~120 LoC combined:

```
Source/UnrealClaudeMCP/Private/MCP/Handlers/
  Handler_GetConsoleVariable.cpp     # ~70 LoC
  Handler_SetConsoleVariable.cpp     # ~110 LoC (pre/post-verify path)
```

Both:
- `#include "HAL/IConsoleManager.h"` (no Build.cs change — already in `Core` which is already a dep).
- Implement `IUCMCPHandler` (no new patterns).
- Use the standard error-code prefix format `"<tool>: <code>: <detail>"`.

Module registration:
- 2 new `extern TSharedRef<IUCMCPHandler> Make_Handler_*();` lines.
- 2 new `Reg.Register(Make_Handler_*());` calls.

Bridge / manifest / docs / tests follow the established vertical-slice pattern (see HANDOFF.md "Vertical-slice task decomposition" §).

## Test plan

Unit (pytest, no editor):
- `test_get_console_variable_in_tools_catalog` — bridge schema shape (required `name`).
- `test_set_console_variable_in_tools_catalog` — bridge schema shape (required `name`, `value`).
- Bump `len(bridge.TOOLS) == 38` in `test_tools_list_has_thirtysix_entries` (and rename function), `test_handle_tools_list_returns_all_tools`, and `test_manifest_tool_count_matches_bridge`.
- Add the two new names to the expected set in `test_tool_names_are_unique_and_match_handlers`.

Smoke (live, manual — runbook step 5/6):
- Editor registers 38 handlers (was 36).
- `get_console_variable r.ScreenPercentage` returns the current value.
- `set_console_variable r.ScreenPercentage 75` mutates it, then `get_console_variable` confirms.
- `set_console_variable` against a read-only CVar (e.g. `r.SkinCache.CompileShaders`) returns `read_only` error.

`examples/smoke_test.py` will get a new optional section at the end exercising `get/set` against a CVar that's safe to mutate at runtime (e.g. `r.ScreenPercentage` — very low blast radius, easy to restore).

## Risk

LOW. The CVar surface is well-trodden, the API is small (4 functions plus 1 enum), and we've already grounded every call site against the header. The only nontrivial decisions (SetBy priority, ReadOnly handling) have explicit decisions documented above with rationale.

The only live-only risk is the post-set verification semantics — if UE's parser silently coerces (e.g. `"true"` for a float CVar setting it to 0), our `note` field will surface that to the caller. That's a feature, not a bug.
