---
name: Codex CLI hits usage limits — reset times matter for sprint planning
description: Codex usage is metered; when limit hits, the next dispatch fails with "try again at HH:MM" message. Reset is on a clock, not on demand. Plan multi-PR sprints accordingly.
type: reference
originSessionId: 886e5f4a-65a1-4dd0-a038-703f0c903a63
---
**Symptom:** A `codex:codex-rescue` dispatch returns immediately with an error message: *"You've hit your usage limit. Upgrade to Pro / visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at HH:MM AM."* The agent's task does NOT execute; no files are written.

**First confirmed instance:** 2026-05-10 sprint, after PR #55 (~8 successful Codex dispatches over ~6 hours of sprinting). Reset was reported as 3:14 AM.

**Practical implications:**

1. **Codex is not infinite.** A heavy multi-PR sprint (~5+ Codex C++ handlers per ~3-4 hours) can exhaust the daily quota. Plan accordingly — if user wants 10 PRs in a row, partition some to Opus-only work to spread Codex usage.

2. **Reset is on a clock, not on demand.** Re-dispatching immediately after the limit message just hits the limit again. Wait until the reset time, OR upgrade tier, OR have the user purchase credits.

3. **Recovery options when limit hits mid-sprint:**
   - **Wait** — if reset is <1 hour away and user accepts the pause
   - **Opus does the C++** — if I have the explorer brief and a clear contract, I can write the C++ myself (per the user's directive "if you wanna do some part of the coding, no problem"). Slower than Codex on novel C++ but viable for established patterns (e.g. another `Inspect*` handler mirroring `Handler_InspectStaticMesh.cpp`).
   - **Pivot to non-Codex work** — cleanup PR, docs PR, vendor-neutral language audit, synthetic tool (no C++ needed) — keeps the sprint moving without burning more Codex credits.

4. **Detect the limit early.** If a Codex dispatch returns within ~30 seconds with no completion notification, suspect a limit hit. Check the agent's task notification. The error pattern is recognizable: short duration_ms (~280s in the observed case) + result text mentioning "usage limit" / "Upgrade to Pro" / "purchase more credits".

5. **Symptoms NOT to confuse with usage limit:**
   - **Shell-quoting kill** (PR #54): agent reports "shell quoting issues" + tries to retry with temp file. Different error message.
   - **Sandbox UBT crash** (`dotnet.exe` popup): handled by the no-UBT instruction now.
   - **Auto-commit by VS-Codex environment**: produces a commit, not a usage error. Different.

**How to apply:** when a Codex dispatch fails with usage-limit messaging, surface it explicitly to the user (don't silently retry), offer the three recovery options, and default to "Opus does the C++ for the next PR" if the user is velocity-oriented and the work is bounded (well-spec'd contract, explorer brief in context, sibling pattern available).
