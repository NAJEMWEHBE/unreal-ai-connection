---
name: Multi-agent workflow — Codex codes C++, Sonnet codes Python, Opus reviews + integrates
description: User refined 2026-05-09 — Sonnet can take coding work (not just review); Opus's role is FINAL synthesis review of all agent outputs plus integration; Opus may still code when small/contextual tasks
type: feedback
originSessionId: 886e5f4a-65a1-4dd0-a038-703f0c903a63
---
User asked (2026-05-09): "Why not give Sonnet a task or give Haiku a task you do another task? Combine all of them and see the end. Multi-agents working together at the same time. And at the end, you review all of their work."

**Workflow preference established:**

- **Use multiple agents in parallel** during Codex wait windows. Don't sit idle.
- **Different roles, not duplicate work.** Two agents on the same task with the same model = correlated output. Different roles (Codex implements, Sonnet reviews, Sonnet explores) compose because their outputs are independent.
- **My role is integrator** — coordinate the parallel agents, do my own piece, then review everything before commit.

**Two highest-payoff agent dispatches per PR:**

1. **Pre-PR review (highest unrealized win)** — dispatch `feature-dev:code-reviewer` (or `pr-review-toolkit:silent-failure-hunter`) on staged changes BEFORE pushing. Catches the same kind of findings that GitHub bots (Codex/Gemini) would catch post-merge — but instantly, locally, and before the cleanup PR cycle. Past examples that this would have caught: PR #48's `synthetic_screenshot_actor` error-code rewrap (Codex P2 finding), PR #49's `Duplicated->GetPathName()` ground-truth path (Gemini medium).

2. **Pipeline research with Explore (mid-high payoff)** — during the ~4-min Codex wall-clock per C++ task, dispatch `feature-dev:code-explorer` (Sonnet) to source-ground the NEXT PR's UE 5.7 APIs in advance. When PR N merges, PR N+1's Codex prompt is ready cold-start.

**When NOT to add an agent:**

- Adding a third agent to the same partition we already have (Codex C++ + Claude Python). The handoff cost eats the speedup.
- Duplicating Codex with a Sonnet agent on the same C++ task. They'd produce divergent outputs I'd have to reconcile. Lower quality, higher integration cost.
- Using Haiku for non-mechanical work. Speed gain is real, but reasoning-quality drop on subtle tasks loses more than speed gains.

**Why:** the user is speed-oriented (directive #7), explicitly authorized aggressive Codex usage (feedback memory `feedback_codex_invocation_settings`), and is now extending that to the broader agent fleet. The integration cost is real but asymmetric — below ~3 concurrent agents it's sub-linear; above ~3 it explodes. Codex + Claude + 1 helper agent is the sweet spot.

**How to apply:**

1. Every PR cycle: while Codex is busy on PR N's C++, I dispatch (a) a code-reviewer on my Claude-side work and/or (b) an explorer for PR N+1's API research. Both run in parallel with Codex. I integrate when notified.
2. Default to Sonnet for both helper agents (Opus inherits cost; Haiku risks missing subtleties for these specific tasks).
3. Cap at 3 concurrent agents (Codex + 2 helpers) unless a specific task genuinely partitions cleanly to 4+.
4. Every helper-agent output goes through MY review before any commit — they're advisory, not authoritative.

**Critical-path timing (lesson from PR #51):**

- **Codex** can dispatch first thing — it's working a different file partition, independent of my work.
- **Explorer for PR N+1** can dispatch first thing — also independent.
- **Reviewer for PR N's Claude-side work MUST dispatch AFTER my edits are staged** — otherwise it has nothing to review and reports a P0 finding ("everything is missing") that isn't actionable. Wasted review pass.

**Correct sequence per PR:**
1. Dispatch Codex (background) and Explorer (background) in parallel — independent of me
2. Do my Claude-side edits (bridge, manifest, tests, docs)
3. **Then** dispatch Reviewer (background) on the staged changes
4. When Reviewer returns, fold P1 findings into the same PR before commit
5. When Codex returns, integrate its files
6. When Explorer returns, save its brief for PR N+1

The reviewer is a downstream consumer of my work; its dispatch timing has to follow my work.

---

## REFINEMENT (2026-05-09): Sonnet codes Python, Opus reviews

The user explicitly redistributed roles: "When I say review, like, opus makes the review. The final review from all of the agents that brings the data, the work, the information. So if you want give Sonnet the coding and you do the reviewing. And, also, if you wanna do some part of the coding, no problem."

**New role assignment:**
- **Codex** (gpt-5.5 / xhigh): C++ implementation — unchanged. Specialty model for this language tier.
- **Sonnet** (general-purpose agent or code-architect, sonnet model): Python coding — bridge entry, manifest entry, tests, docs. Fast, capable, well-suited to structured-edit work.
- **Sonnet explorer** (feature-dev:code-explorer, sonnet): PR N+1 API research — unchanged.
- **Opus (me)**: **FINAL SYNTHESIS REVIEW** — reading Codex's C++ + Sonnet's Python together as ONE coherent change, against UE 5.7 source, against sibling patterns, against the bot-finding catalog. Plus integration, commit, push. Plus *some* coding when it's the right call (e.g., a one-line fix is faster than dispatching).

**Why this is well-calibrated:**
- Opus's strength is reasoning-quality on hard synthesis tasks. The final review pass — catching bot-class findings before push, judging when to dismiss-with-rationale, integrating across language boundaries — is exactly that.
- Sonnet's strength is execution speed on structured tasks. Bridge-entry / manifest / tests / docs all follow established sibling patterns; Sonnet executes them quickly.
- Codex's strength is C++ specialty (especially with xhigh effort). Stays where it is.

**New sequence per PR (replaces the prior section):**
1. Dispatch **Codex** (C++) — background, independent
2. Dispatch **Sonnet (general-purpose)** for Python coding — background, with a tight contract describing the bridge entry, manifest entry, tests, docs, count bumps. Reference the closest sibling handler (e.g. "mirror the wiring of `inspect_static_mesh`").
3. Dispatch **Sonnet explorer** for PR N+1 research — background, independent
4. Wait for all three
5. **I (Opus) do the FINAL synthesis review** of the combined diff — Codex's C++ + Sonnet's Python read together as one coherent PR. Look for: contract violations, sibling-pattern drift, bot-finding-class issues, cross-file inconsistencies (manifest vs bridge vs docs), strict-vs-loose inequality bugs, stale test names, error-code masking, partial-update destruction, cast-before-clamp UB, etc.
6. Fold any findings into the PR before commit (same pattern as the reviewer-agent did, but now I'm the reviewer)
7. Pytest, commit, push, merge

**When NOT to dispatch Sonnet for Python:**
- A one-line fix or trivial bump where dispatch + integration cost > doing it myself.
- A task that requires this conversation's context (e.g., responding to a specific finding the bots flagged on a prior PR — Sonnet doesn't have the conversation context).

**Hybrid is OK.** User explicitly said "if you wanna do some part of the coding, no problem." When a task is small enough that integration cost dominates, do it myself.

**Transition point:** New pattern starts on PR #52. PR #51 finishes in the prior pattern (my Python is already done; throwing it away to re-dispatch Sonnet would be net negative).

---

## CRITICAL DISCOVERY (PR #52, 2026-05-09): Sonnet subagent file writes DO NOT persist

When I dispatched a `general-purpose` Sonnet subagent on PR #52 to do the Python wiring (bridge entry, manifest, tests, docs), the agent reported success — listed all 5 files modified, claimed `pytest` showed 156 passing, summarized specific line counts. **None of those edits actually persisted to my working directory.** `git status` showed only Codex's C++ files; `grep "inspect_anim_blueprint"` returned nothing in any Python file; `pytest` reported the pre-Sonnet baseline of 154 passing.

**Why this happens**: the `general-purpose` subagent's `Edit` / `Write` calls apparently apply in an isolated context (sandbox, transient working directory, or similar) that does NOT propagate back to my main working tree. Unlike `codex:codex-rescue` — which uses the codex companion runtime's explicit `--write` semantics through the actual git working tree — the general-purpose subagent's writes are sandboxed by default. Without `isolation: "worktree"` set explicitly AND merging the worktree afterward, the writes vanish on agent completion.

**What still works for Sonnet/Haiku in this setup:**
- **Research** (read-only): `feature-dev:code-explorer` has been reliable — outputs go in its summary text, which I read.
- **Review** (read-only): `feature-dev:code-reviewer` has been reliable — same pattern, summary text only.

**What DOES NOT work:**
- **Coding** via `general-purpose` and expecting writes to land in my tree.

**Updated pattern for Python coding tier:**

Two viable options going forward:
1. **(Preferred) I do the Python work myself.** It's ~5-10 minutes per PR; the costs of the Sonnet detour exceed the savings when writes don't persist. The user explicitly authorized this fallback ("if you wanna do some part of the coding, no problem"). Status: this is what I did on PR #52.
2. **Sonnet returns the diff as text in its summary, I apply manually.** Dispatch the subagent with an explicit instruction: "Do not call Edit/Write tools — instead, return the full file contents (or specific edit blocks) as TEXT in your summary." I then read the summary and apply via my own Edit calls. Preserves Sonnet's labor (drafting the bridge entry / docs section) while putting persistence in my hands.

**Codex remains the C++ coder** because its writes go through the codex companion runtime's `task --write` mode which does persist correctly.

**For PR #53 onward**: default to option 1 (I code Python myself) unless I have a specific reason to try option 2 — the workflow currently doesn't have a tested "Sonnet returns diff as text" pattern, and PR #52 already went over budget recovering from this gap. Save the experiment for a later PR.
