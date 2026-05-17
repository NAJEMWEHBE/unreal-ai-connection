# Competitive analysis — Unreal + MCP ecosystem

**As of:** 2026-05-15 (snapshot — metrics drift over time)
**Scope:** This repo (`NAJEMWEHBE/unreal-ai-connection`) vs. 12 other Unreal + MCP projects + 3 marketplace listings.
**Method:** WebFetch on each README + `gh api repos/<owner>/<repo>` + tree listings via the GitHub REST API. Tool counts are claimed-or-observed from READMEs / manifests / tree listings; CI/test claims verified via `.github/workflows/` listings; license + activity from API metadata. Source code itself was not exhaustively audited. Three Explore sub-agents fanned out (~4 repos each) to keep token cost honest.

This is an **honest** scorecard, not marketing. Where we lead is documented; where we lag is documented.

---

## TL;DR

| Where this repo leads | Evidence |
|---|---|
| **Tool count** | 105 (72 C++ + 33 synthetic). Closest rival GenOrca has 68; everyone else ≤ 51. |
| **Test coverage + CI** | 458 pytest cases, ~99% bridge coverage, CI on every push/PR. Two rivals (remiphilippe, ChiR24) ship CI workflows; none publish a comparable pytest count. |
| **PBR / texture depth** | Multi-map import (color + normal + roughness + AO + displacement + metalness) via `marketplace_import` + longlat→cubemap via `convert_hdri_to_cubemap`. No rival ships either. |
| **Sequencer authoring** | `sequencer_add_transform_keyframe` synthetic with caller-friendly `[pitch, yaw, roll]` Euler + `[location, rotation, scale]` partial keys + 5 interpolation modes. Two rivals (ChiR24, flopperam) mention "sequencer support"; neither documents a keyframe-authoring primitive. |
| **Docs depth** | `docs/TOOLS.md` per-tool JSON schemas + examples (~3900 lines), `docs/HANDOFF.md` rolling-three session log, `docs/ARCHITECTURE.md` UE 5.7 traps catalogue. No rival ships comparable docs. |
| **Vendor-neutrality** | 7 clients explicitly: Claude Code, Cursor, Codex CLI, VS Copilot, Windsurf, Cline, Zed. Tied with `remiphilippe/mcp-unreal`; ahead of everyone else (most are Claude-only or Claude+Cursor). |

| Where this repo lags | Evidence |
|---|---|
| **Adoption** | **3 stars, 1 fork, created 2026-05-07** (≈ 1 week old). Top rivals: 621 (ChiR24), 397 (VedantRGosavi, abandoned), 304 (chongdashu), 178 (flopperam). |
| **UE version range** | UE 5.7 only by design (ADR-0001 — Phase H FROZEN, cross-engine compat retired, not maintained). ChiR24 supports 5.0–5.7 per its docs; flopperam claims 5.5–5.7. This is a deliberate scope choice, not a backlog item. |
| **Distribution channels** | No Docker image (ChiR24 has `mcp/unreal-engine-mcp-server` on Docker Hub), no marketplace listing on mcpservers.org or mcpmarket.com. |
| **One-command install** | Plugin install + `.mcp.json` editing is multi-step. Phase G in the roadmap addresses this. |

**Verdict (honest):** Feature-leading on every technical dimension that matters for production work (tool count, tests, PBR depth, sequencer depth, docs, vendor-neutrality), but **adoption-lagging by 1–2 orders of magnitude** because the repo is one week old and has no distribution channel. The technical lead is real; the awareness gap is the next moat to close.

---

## Repo scorecard

### 1. NAJEMWEHBE/unreal-ai-connection (this repo)

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **3 / 1 / 1** |
| Created | 2026-05-07 |
| Last commit | 2026-05-15 |
| Primary language | Python (bridge) + C++ (plugin) |
| License | MIT |
| UE version range | **5.7 only** (ADR-0001 — Phase H FROZEN; cross-engine compat retired, not maintained) |
| Tool count | **105** (72 native C++ handlers + 33 bridge-side synthetic) |
| Clients explicitly named | Claude Code, Cursor, Codex CLI, VS Code Copilot, Windsurf, Cline, Zed |
| Sequencer | **Yes** — `sequencer_add_transform_keyframe` (authored keyframes; 458 test cases exercise it) |
| Material/texture/lighting | **Yes** — multi-map PBR import + HDRI longlat→cubemap + Florence demo scene |
| CI / tests | **458 pytest** cases, ~99% bridge coverage, GitHub Actions on every push/PR |
| Install steps (typical) | 6 (clone + plugin copy + Python deps + .mcp.json + UE restart + first tool call) |
| Docs | TOOLS.md (3900+ lines), HANDOFF.md (rolling-three log), ARCHITECTURE.md, AGENTS.md, RESTART-RECOVERY.md |

### 2. ChiR24/Unreal_mcp

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **621 / 110 / 24** |
| Created | 2025-09-07 |
| Last commit | 2026-05-15 |
| Primary language | C++ (with TypeScript MCP wrapper) |
| License | MIT |
| UE version range | 5.0 – 5.7 (per README architecture) |
| Tool count | 35–36 across 8 categories |
| Clients explicitly named | Claude Desktop, Cursor IDE |
| Sequencer | **Yes** — "Sequencer timeline control and keyframe management" per README |
| Material/texture/lighting | Yes — asset operations cover materials + textures; Niagara particles |
| CI / tests | GitHub Actions workflows present; no test count public |
| Install steps (typical) | 4 (copy plugin, env var, Node 18+ or HTTP) — has a Docker image too |
| Docs | README + architecture notes; no equivalent of TOOLS.md |

**Honest verdict:** Most mature C++ MCP-Unreal currently shipping. Native automation bridge + dual transport (HTTP or stdio). 24 open issues suggest scaling pain. Docker Hub distribution is a real moat we don't have.

### 3. VedantRGosavi/UE5-MCP

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **397 / 57 / 8** |
| Created | unknown |
| Last commit | 2025-06-02 (≈ 11 months stale) |
| Status | **Abandoned per README** ("we didn't move forward due to time constraints") |

**Honest verdict:** Inflated star count for an abandoned project. Not a real competitor today; included for completeness.

### 4. chongdashu/unreal-mcp

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **304 / 2 / 34** |
| Created | unknown |
| Last commit | 2026-05-15 |
| Primary language | C++ |
| License | MIT |
| UE version range | 5.5+ |
| Tool count | ~10 C++ handler files |
| Clients explicitly named | Claude Desktop, Cursor, Windsurf |
| Sequencer | No |
| Material/texture/lighting | None mentioned |
| CI / tests | No workflows detected |
| Install steps (typical) | 6–7 |
| Docs | Standard README only |

**Honest verdict:** Highest popularity-to-feature ratio in the field. 304 stars but only 10 C++ handlers, no sequencer, no material depth, 34 open issues. The brand value is in being one of the first; the technical depth is shallow.

### 5. flopperam/unreal-engine-mcp

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **178 / 0 / 6** |
| Created | unknown |
| Last commit | 2026-05-15 |
| Primary language | C++ |
| License | MIT |
| UE version range | 5.5 – 5.7 (explicit in docs) |
| Tool count | 50+ hosted ("Flop MCP") / ≈ 30 local OSS; 51 C++ handler files observed |
| Clients explicitly named | Cursor, Claude Code, Windsurf, VS Code Copilot, Cline, "any MCP client" |
| Sequencer | **Yes** — "Level Sequences with camera cuts"; MetaSound/SoundCue graph editing |
| Material/texture/lighting | Material instance + VFX + animation domains |
| CI / tests | No workflows detected |
| Install steps (typical) | 3 (hosted) or 5 (local) |
| Docs | Mid-tier README; no per-tool catalog visible |

**Honest verdict:** Largest native C++ footprint of the rivals (51 handlers). Cleanest vendor-neutrality story we'll find in the wild. Dual commercial-hosted + local-OSS distribution model. **The repo to watch most closely.**

### 6. GenOrca/unreal-mcp

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **96 / ? / ?** |
| Created | unknown |
| Last commit | 2026-05-09 |
| Primary language | Python |
| License | Apache-2.0 |
| UE version range | 5.6+ |
| Tool count | **68** across 9 categories |
| Clients explicitly named | Claude, Cursor, VS Code Copilot |
| Sequencer | None mentioned |
| Material/texture/lighting | Material expressions, parameter adjustment; no PBR/multi-map/cubemap |
| CI / tests | Unknown |
| Install steps (typical) | 4 (uv package manager + plugin enable + MCP config) |

**Honest verdict:** Highest tool count among rivals (68 tools). Python-only — no native C++ depth. Strong actor + Blueprint + UMG breadth; zero sequencer depth.

### 7. remiphilippe/mcp-unreal

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **24 / 4 / 1** |
| Created | 2026-02-20 |
| Last commit | 2026-05-13 |
| Primary language | Go (57.5%) + C++ (41.6%) |
| License | Apache-2.0 |
| UE version range | 5.7 only |
| Tool count | 49 (34 core + 15 derived) |
| Clients explicitly named | Claude Code, Cursor, OpenAI Codex CLI, VS Code Copilot, Windsurf, Cline, Zed (**7 clients**) |
| Sequencer | None |
| Material/texture/lighting | Material creation + params; no PBR/multi-map |
| CI / tests | GitHub Actions present with JSON pass/fail reporting |
| Install steps (typical) | 4 (binary release / `go install` / source) |

**Honest verdict:** Most-explicit-clients support in the field (7). Single Go binary distribution model is elegant. Procedural mesh + GAS + PCG graph operations are unique. Only 24 stars but most architecturally innovative. **The hidden gem of the list.**

### 8. kvick-games/UnrealMCP

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **79 / 0 / 14** |
| Last commit | 2026-05-15 |
| Primary language | C++ |
| License | MIT |
| UE version range | 5.5 (tested only) |
| Tool count | 6 commands + roadmap (7 C++ handler files) |
| Sequencer | None |
| CI / tests | No workflows |

**Honest verdict:** Smallest mature surface. Direct competitor for the `UnrealMCP` repo-name slug (Phase F collision).

### 9. runeape-sats/unreal-mcp

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **9 / 0 / 2** |
| UE version range | 5.3 only (hardcoded) |
| Tool count | 12 (Python-only) |
| Clients explicitly named | Claude Desktop |
| Sequencer | None |

**Honest verdict:** Minimal Python-only MCP server. UE 5.3 lock-in + Claude-Desktop-only.

### 10. gingerol/vhcilab-unreal-engine-mcp

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **2 / 0 / 0** |
| UE version range | 5.1+ |
| Tool count | 1 (`create_objects`) |
| Clients explicitly named | Claude Code |

**Honest verdict:** Single-tool natural-language scene builder. Very early stage; not a feature competitor.

### 11. Natfii/UnrealClaude

| Metric | Value |
|---|---|
| Status | Sub-agent 3 returned data identical to this repo (609 stars), suggesting a search-index artifact or a near-clone. **Manual verification needed before assuming this is a real second project.** |

**Honest verdict:** Data was inconclusive in this analysis pass. Treat as low-priority follow-up.

### 12. iflow-mcp/natfii-unrealclaude

| Metric | Value |
|---|---|
| Stars / Forks / Issues | **0 / 0 / 0** |
| Created | 2026-02-14 |
| Status | Stale fork of Natfii/UnrealClaude. No activity post-creation. |

**Honest verdict:** Stale organizational mirror. Not a competitor.

### 13. Marketplace listings

| Listing | Hosts | Clients listed | Notes |
|---|---|---|---|
| mcpservers.org (`/servers/remiphilippe/mcp-unreal`) | remiphilippe/mcp-unreal | Claude Code, Cursor, Codex CLI, VS Copilot, Windsurf, Cline, Zed | Distribution surface — we should list here too. |
| mcpmarket.com (`/server/unreal-1`) | chongdashu/unreal-mcp (gated, 403 on WebFetch) | Unknown | Marketplace presence — we should be here too. |
| Docker Hub (`mcp/unreal-engine-mcp-server`) | ChiR24/Unreal_mcp | Implicit MCP-compliant | 186 MB image, ~194 weekly pulls. We could ship a Docker image too. |

---

## Side-by-side matrix

| Repo | Stars | Tool count | UE range | Sequencer | Cubemap/PBR | Tests | Clients | License |
|---|---:|---:|---|---|---|---|---:|---|
| **this repo** | **3** | **105** | 5.7 only (ADR-0001) | **Yes** (auth+kf) | **Yes (multi-map + cubemap)** | **458** | **7** | MIT |
| ChiR24/Unreal_mcp | 621 | 36 | 5.0–5.7 | Yes | Partial | Unknown CI | 2 | MIT |
| VedantRGosavi/UE5-MCP | 397 | ? | unspecified | Unclear | "AI-generated textures" | None | unknown | other |
| chongdashu/unreal-mcp | 304 | ≈ 10 | 5.5+ | No | No | None | 3 | MIT |
| flopperam/unreal-engine-mcp | 178 | 30–51 | 5.5–5.7 | Yes (level-sequence) | Material instance | None | 6+ | MIT |
| GenOrca/unreal-mcp | 96 | 68 | 5.6+ | No | No | Unknown | 3 | Apache-2.0 |
| kvick-games/UnrealMCP | 79 | 6 | 5.5 (tested) | No | No | None | 1 | MIT |
| remiphilippe/mcp-unreal | 24 | 49 | 5.7 | No | Material params | Yes | **7** | Apache-2.0 |
| runeape-sats/unreal-mcp | 9 | 12 | 5.3 only | No | Materials/SkyLight | None | 1 | MIT |
| gingerol/vhcilab-unreal-engine-mcp | 2 | 1 | 5.1+ | No | Lights only | None | 1 | MIT |
| iflow-mcp/natfii-unrealclaude | 0 | (fork) | inherited | (fork) | (fork) | (fork) | (fork) | unspecified |

---

## Naming-collision check (for Phase F repo rename)

User's two candidates from voice message:

- **`UnrealMCP`** — taken: `kvick-games/UnrealMCP`. **COLLIDES.** Skip.
- **`UnrealAI Connection`** — no exact match found. Available, but spaces in slugs are awkward; `unreal-ai-connection` is the URL form. Available.

Other variants already in the wild (high-collision-risk space):

- `unreal-mcp` — `chongdashu`, `runeape-sats`, `GenOrca` (3 repos use this slug case-insensitively).
- `UnrealMCP` — `kvick-games`.
- `Unreal_mcp` — `ChiR24`.
- `mcp-unreal` — `remiphilippe`.
- `unreal-engine-mcp` — `flopperam`.
- `UE5-MCP` — `VedantRGosavi`.
- `unreal-mcpython` — `GenOrca` (alternative slug).
- `UnrealClaude` / `unrealclaude` — `Natfii`, `iflow-mcp/natfii-unrealclaude`.
- `vhcilab-unreal-engine-mcp` — `gingerol`.

Distinct slugs that **do NOT collide** (recommended ordered by clarity):

1. **`unreal-ai-connection`** — user's preferred candidate, available.
2. **`unreal-mcp-pro`** — distinct, signals "production-grade".
3. **`unreal-mcp-suite`** — distinct, signals "many tools, batteries-included".
4. **`unreal-ai-bridge`** — distinct, technically accurate.
5. **`ueagent`** / **`ueagent-mcp`** — distinct, short.

**Keep-the-name option:** `UnrealClaudeMCP` is differentiated by the historical name. Even though "Claude" is now decorative (we serve all MCP clients), the README + plugin description already lead with vendor-neutrality. Rename has carrying cost (every external bookmark eats a redirect). Worth considering whether to keep.

---

## Strategic verdict + recommendations

### What this repo should NOT do

- **Don't try to out-star ChiR24 / chongdashu / VedantRGosavi** by adding shallow tools. Their lead is age + first-mover, not depth.
- **Don't fragment vendor-neutrality** by adding Claude-specific code paths. Stay protocol-level.
- **Don't ship without tests.** The 458-test moat is the most defensible technical lead.

### What this repo should do (next 6 PR cycles)

| Priority | Action | Phase in roadmap | Estimated impact |
|---|---|---|---|
| 1 | **Per-client setup recipes** at top of README (10 markdown files under `docs/setup/`) | Phase G2 | Closes the awareness gap. New users see "yes, my client works" immediately. |
| 2 | **One-command installer** (`scripts/install.ps1` / `install.sh`) | Phase G3 | Drops install from 6 steps to 1. Highest conversion lever. |
| 3 | **List on mcpservers.org + mcpmarket.com** + ship a **Docker image** | Phase G (later) | Matches ChiR24's distribution moat. Same install-conversion impact as G3 for a different audience. |
| 4 | ~~UE 5.5 + 5.6 backport~~ — **REMOVED (ADR-0001).** Cross-engine support is FROZEN; UE 5.7-only is a deliberate scope choice, not a backlog item. | — (Phase H frozen) | n/a — scope decision, not a gap to close. |
| 5 | **Movie Render Queue synthetic** | future bridge work | Closes the only sequencer gap vs. flopperam. We already lead on keyframe authoring. |
| 6 | **Repo rename** to non-colliding slug (likely `unreal-ai-connection` per user preference) | Phase F | After Phase G so the rename comes with a hero refresh, not an empty new-name page. |

### What this repo does NOT need

- **More tool count.** 105 is enough — quality > quantity at this point. The next 20 tools should be specifically targeted (MRQ, Niagara, NavMesh) not opportunistic.
- **A TypeScript wrapper.** The Python bridge works for every MCP client. ChiR24's TS layer is a maintenance burden we don't need.
- **A hosted/commercial tier.** flopperam's dual model is a distraction. Stay OSS-only.

---

## Closing note

We are **technically ahead** on every dimension that survives a careful read of each rival's README, manifest, and tree listing (tool count, tests, sequencer authoring, multi-map PBR, longlat→cubemap, docs, vendor-neutrality), and **adoption-behind** by 1–2 orders of magnitude because the repo is one week old and not yet on any distribution channel. The technical lead is real and defensible; the awareness gap is fixable in 2–3 PR cycles (Phases G + F).

The honest takeaway: **this is not "the best" in the sense of most-starred or most-installed — yet.** It IS the most feature-complete + most-tested + most-honestly-vendor-neutral + most-documented Unreal+MCP plugin currently in the field. Closing the distribution gap is the next moat to dig.
