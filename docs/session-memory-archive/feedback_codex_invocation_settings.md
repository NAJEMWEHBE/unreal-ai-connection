---
name: Codex invocation settings — xhigh effort, GPT-5.5, generous usage
description: When delegating to Codex (codex:rescue agent), pass --effort xhigh and --model gpt-5.5; user authorized aggressive usage; "1.5 speed" has no CLI mapping
type: feedback
originSessionId: 886e5f4a-65a1-4dd0-a038-703f0c903a63
---
When invoking the `codex:codex-rescue` subagent on this project, the user wants the maximum-quality settings:

**Definite mappings (verified against `codex --help`):**
- **`--effort xhigh`** — user said "extra high"; `xhigh` is the documented top tier. Possible values are `none / minimal / low / medium / high / xhigh`.
- **`--model gpt-5.5`** — user said "GPT 5.5". Try this literal name first; if Codex rejects it, fall back to the strongest available codex-tuned model (the skill mentions `gpt-5.3-codex-spark` via the `spark` shortcut, but that's a different model line).
- **Usage budget: generous, do not throttle** — user said "Increase usage. No problem."
- **`--write`** is the codex-rescue default unless user explicitly says read-only.

**No CLI mapping (acknowledge but skip):**
- **"1.5 speed"** — no `--speed` flag, no throughput multiplier, no "1.5x" tier exists in `codex --help`. The user may be confusing this with a different product's UI (ChatGPT audio/video has playback-speed). When invoking, mention this in the user-facing update so they can correct if it meant something specific.

**Why:** the user is speed-oriented (directive #7) and explicitly authorized aggressive Codex usage. Codex co-development (directive #8) only pays off when Codex's output is high-enough quality that Claude doesn't have to redo the work — under-specced Codex calls waste the parallelism we get from running Claude + Codex concurrently.

**How to apply:**
1. Every `codex:rescue` dispatch passes `--effort xhigh` and `--model gpt-5.5` (or the strongest fallback if 5.5 is rejected).
2. Don't downgrade unless the user explicitly says so.
3. Mention the chosen flags briefly in the user-facing update so the user can correct.
4. The codex-rescue subagent forwards to the `task` helper; the rescue subagent is a forwarder, not an orchestrator (per the codex-cli-runtime skill).
