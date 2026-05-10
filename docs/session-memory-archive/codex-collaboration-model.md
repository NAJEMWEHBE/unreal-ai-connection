---
name: Codex co-developer collaboration model
description: User installed a Codex plugin in Claude Code and wants Codex used as a parallel co-developer (not just a reviewer); I delegate research/debug/solve/search/coding tasks to Codex, integrate its output back here, and ship via the existing bot-review + self-merge pipeline
type: feedback
originSessionId: 0b6e09bb-52da-45b6-a0ac-4502facb704d
---
**Rule:** When working on a feature with discrete subtasks, partition the work between Claude (me) and Codex. Delegate well-scoped pieces (research, debugging, solving a specific bug, searching for prior art, implementing a particular handler/module) to Codex. Do other work in parallel. When Codex returns its output, **review it as the integrator** — same standards I apply to my own code — then incorporate into the active branch and continue the workflow.

**Why:** Speed. The user said this directly: *"Why am I doing this? Because I wanna speed up the workflow."* Quality is already strong (codex+gemini bot review caught 8 real bugs across 7 PRs in the 2026-05-09 session); the bottleneck now is wall-clock time. The Tier 2 sprint took ~3 hours sequentially across 6 PRs, with significant dead-time waiting for bot reviews between PRs. Two-agent parallelism is the speed lever.

**How to apply (speed-first):**

- **On session start**, use `ToolSearch` (or check the Skill list) to identify what the Codex plugin exposes. Look for `mcp__codex__*` tools or a `codex:*` skill. Verify it works on a tiny task before committing to large delegations.

- **Three parallelism patterns, ranked by payoff:**

  1. **Sub-PR concurrency** (low risk, ~10-15 min savings per PR): partition a single PR's work — I write C++, Codex writes the bridge/tests/docs in parallel. We converge on one branch.

  2. **Pipeline concurrency** (medium risk, saves the bot-review-wait window): the moment PR N is pushed, start PR N+1 on a fresh branch. The bot review wait time (~5-10 min) becomes productive work. **Requires:** branches that don't conflict on common files (module.cpp, bridge.py, tests/test_bridge.py, manifest.json, TOOLS.md). For multi-handler work in the same PR area, expect rebase conflicts and budget for them.

  3. **Fix-while-write** (highest payoff, also highest coordination cost): when bot findings land on PR N, Codex addresses them while I'm deep in PR N+1. **Requires:** Codex knowing enough about the established discipline (cast-before-clamp, marker pattern, temp-file pattern for ExecuteFile, error-code prefix format) to land fixes that won't get re-flagged. Build trust here gradually — start with low-stakes fixes (typos, doc updates, count bumps) before delegating semantic ones (cursor logic, cancellation flags).

- **Partition work explicitly.** When picking a multi-part task, name what Codex will do AND what I'll do, in plain terms, before either starts. Example: *"Codex: implement Handler_X.cpp following the structure of Handler_PollEvents.cpp. Me: bridge entry, manifest, schema test, TOOLS.md section."* User sees the split before either runs.

- **Pass Codex enough context.** Codex doesn't see this conversation. Always include: the spec/design doc path, the file pattern to mirror (e.g. *"follow the structure of `Handler_PollEvents.cpp`"*), the project conventions to honor (HANDOFF directives #1-#6, the temp-file pattern for `ExecuteFile` mode, `FCriticalSection` discipline, error-code prefix format), and what "done" looks like (compiles in UE 5.7, passes pytest).

- **Review Codex output as the integrator** — same standards as my own pre-commit review. Common bug classes to check: cast-before-clamp on numeric inputs, off-by-one cursor semantics, marker-pattern fragility, missing temp-file pattern for `ExecuteFile`, missing input validation on JSON fields. These are the bugs the bots have been catching this session; pre-empting them in integration is faster than letting bots catch them post-push.

- **Keep the existing bot-review + self-merge pipeline.** Codex co-development is *upstream* of PR creation. After integration, the PR still goes through codex+gemini review (yes, including reviewing Codex's own code from a different angle) and self-merge per HANDOFF directive #4. The bots are the safety net; co-development is the speedup.

- **Don't be precious about ownership.** If Codex writes something better than I would have, ship it. If something needs fixing, fix it without performative attribution.

- **If Codex tooling is unclear**, ask the user how to invoke it before guessing. They'll tell me the command shape.
