# ADR-0001: Support UE 5.7 Only — Freeze Cross-Engine Compatibility Scaffold

## Status

Accepted (2026-05-18)

## Context

Phase H was experimental cross-engine scaffolding intended to let the plugin
build and run across a wide UE version range (UE 4.27 through 5.8). Of the three
release tiers it defined, only two were ever proven on a real engine and the
third never was:

- **T1 = UE 5.7** — host-proven historically (cold build + live-editor smoke).
- **T2 = UE 5.1** — host-proven historically (`RunUAT BuildPlugin` `ExitCode=0`
  plus a live-editor smoke: socket bind, all handlers registered, a real
  screenshot).
- **T3 = UE 4.27** — never verified on a real engine. Certifying it would
  require an actual UE 4.27 install plus a build + smoke pass; the 4.27 code
  paths are source-authored by inspection only and have never compiled.

The compatibility machinery — per-handler version gating (`#if
UCMCP_ENGINE_AT_LEAST(...)` branches), the `UCMCPCompat.h` shim header, the
T1/T2/T3 bucket model, and the per-bucket `.uplugin` generator
(`scripts/gen_uplugin_variants.py`) — is a recurring maintenance tax on every
new handler. There is no real user base on UE ≤5.3 or UE 4.27 to justify that
tax, and the 4.27 bucket never compiled on a real engine, so the cost is paid
without a corresponding benefit.

## Decision

UE 5.7 is the sole supported version.

All cross-engine compatibility scaffolding is **frozen as-is and left inert in
the codebase**, not maintained going forward:

- the `UCMCPCompat.h` shims and every per-handler version-gated branch,
- the T1/T2/T3 release-tier model,
- the per-bucket `.uplugin` generator (`scripts/gen_uplugin_variants.py`) and
  its bucket `EngineVersion` variants.

The inert code is not deleted — it stays in the tree as historical reference and
is not actively maintained. Future feature work targets UE 5.7 APIs only and is
not expected to add or update cross-version branches.

This decision is reversible: because the scaffold is left in place rather than
removed, cross-version support can be re-expanded later on an explicit user
request without reconstructing it from scratch.

## Consequences

- UE ≤5.3 and UE 4.27 are no longer supported targets.
- The precompiled UE 5.1 drop-in distribution and the per-bucket build recipe
  are retired; releases target UE 5.7 only.
- The remaining Phase H compatibility clusters stay unverified and
  unmaintained — they are no longer tracked toward a "supported" state.
- The historical UE 5.1 (T2) host-verification is kept as a record only; it no
  longer implies an actively supported target.
- Handler development for UE 5.7 is simpler and faster — no version-gating
  branch, no shim, and no per-bucket variant step per new handler.

## References

- `docs/PHASE-H-COMPAT.md` — the historical cross-engine compatibility scaffold
  (kept for reference, now frozen).
- `docs/HANDOFF.md` — 33rd consecutive closing-note records this scope decision.
