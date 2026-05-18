# ADR-0001: UE 5.7 is the Officially Supported Version; Other Versions are Best-Effort (Community)

> **Note on the slug.** This file's name still contains
> `ue57-only-freeze-cross-engine-compat` for historical/no-churn reasons.
> The **title above supersedes the slug**: the policy is *not* a hard
> 5.7-only freeze — it is "5.7 officially supported & tested; every other
> UE version open / best-effort / community, scaffold kept available."

## Status

Accepted (2026-05-18)

## Context

Phase H was a cross-engine scaffold intended to let the plugin build and run
across a wide UE version range (UE 4.27 through 5.8). Of the three release
tiers it defined:

- **T1 = UE 5.7** — host-proven historically (cold build + live-editor smoke).
- **T2 = UE 5.1** — host-proven historically (`RunUAT BuildPlugin` `ExitCode=0`
  plus a live-editor smoke: socket bind, all handlers registered, a real
  screenshot).
- **T3 = UE 4.27** — never verified on a real engine. The 4.27 code paths are
  source-authored by inspection only and have never compiled.

The project should remain usable on **any** UE version by **anyone** — their
own choice, at their own risk — while the team only officially supports and
tests **one** version. The compatibility machinery (per-handler version gating
via `#if UCMCP_ENGINE_AT_LEAST(...)` branches, the `UCMCPCompat.h` shim header,
the T1/T2/T3 bucket model, and the per-bucket `.uplugin` generator
`scripts/gen_uplugin_variants.py`) is the **basics** that makes that possible.
It carries a maintenance cost on each new handler, so the team does not
actively maintain it — but it is the enabling scaffold for community use and
is therefore kept.

## Decision

**UE 5.7 is the sole officially supported & tested version.**

All other UE versions are **best-effort / community / use-at-your-own-risk**:

- The cross-engine compat scaffold — the `UCMCPCompat.h` shims and every
  per-handler version-gated branch, the T1/T2/T3 release-tier model, and the
  per-bucket `.uplugin` generator (`scripts/gen_uplugin_variants.py`) with its
  bucket `EngineVersion` variants — is **kept and available** so anyone can
  build/run on their chosen UE version **from source**.
- That scaffold is **not certified** outside UE 5.7 and is **not actively
  maintained**, but it is **not removed** and contributions to it are
  **welcome**.
- Upgrading to a newer UE version later is **welcome on request**.
- Fully **reversible / expandable**: because the scaffold stays in the tree
  rather than being deleted, cross-version support can be re-prioritised or
  widened later without reconstructing it from scratch.

Future feature work targets UE 5.7 APIs and is not expected to add or update
cross-version branches — but the existing branches are not torn out.

## Consequences

- Users on UE ≤5.3 / UE 4.27 / future versions may build from source on a
  **best-effort** basis (no guarantee, uncertified, not actively maintained,
  PR-welcome).
- The historical UE 5.1 (T2) host-verification is a useful data point for
  anyone attempting a non-5.7 build — kept as a record, not an active
  support promise.
- Team bandwidth focuses on UE 5.7; the scaffold stays in-tree as the enabling
  "basics" for community/best-effort use rather than being archived or deleted.
- Handler development for UE 5.7 is simpler — no version-gating branch, shim,
  or per-bucket variant is required for new 5.7 work — without closing the
  door on the existing cross-version paths.

## References

- `docs/PHASE-H-COMPAT.md` — the cross-engine compatibility scaffold (kept and
  available as a best-effort / community path).
- `docs/HANDOFF.md` — 33rd consecutive closing-note records this scope policy.
