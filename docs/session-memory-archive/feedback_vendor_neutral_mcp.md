---
name: Vendor-neutral MCP server — supports all MCP-compliant clients, not just Claude
description: User explicitly clarified 2026-05-10 that this plugin should work for all AI models (Codex, Cursor, Gemini CLI, etc.), not only Claude Code; keep tool descriptions and docs vendor-neutral
type: feedback
originSessionId: 886e5f4a-65a1-4dd0-a038-703f0c903a63
---
User said (2026-05-10): "This MCP or plugin that we are making here, just keep in mind that I don't want only to use it only for Claude code or Claude. I want to use it for all of the AI models like Codex."

**Reality check:** The MCP protocol itself is vendor-neutral (open spec; Codex, Cursor, Gemini CLI, Copilot CLI, Continue, and any conforming client work today). The bridge speaks standard JSON-RPC over stdio + length-prefixed JSON over TCP. The 57 tool implementations are pure UE editor automation — no client-specific assumptions in the protocol or the handlers. **Architecturally this is already universal.** Where "Claude" leaks in is naming and documentation framing.

**Going-forward rules for any new PR:**

1. **Tool descriptions** in `mcp_manifest.json` and `bridge/unreal_claude_mcp_bridge.py`'s `TOOLS` list must use vendor-neutral language:
   - ❌ "for Claude to use", "Claude can call", "the Claude agent"
   - ✅ "for the LLM client", "the AI agent", "the calling agent", or just describe what the tool does without naming the consumer
2. **Docs in `docs/TOOLS.md`** — same neutralization. Describe what tools do, not who uses them.
3. **Spec / plan docs in `docs/superpowers/`** — same neutralization for the same reasons.
4. **HANDOFF.md** — internal doc, more latitude here, but still default to neutral.

**Heavy refactors NOT to do (unless user explicitly says so):**

- DON'T rename the repo or plugin folder (`UnrealClaudeMCP` → something neutral). Multi-PR churn, touches every import path, every test, every CI workflow. No functional benefit — the protocol is already universal.
- DON'T rename `bridge/unreal_claude_mcp_bridge.py`. Same reason.
- DON'T rename the manifest file or change the `mcp_manifest.json`'s top-level `name` field. Brand identity, decorative not functional.

**Small wins worth doing when convenient:**

- **`docs/CLIENTS.md`** (NEW): a single doc page covering connection setup for Codex CLI, Cursor, Gemini CLI, Continue, and any generic MCP stdio client. Mirror the existing Claude Code example. ~30 min of work, real onboarding value for non-Claude users. Good candidate for a sprint-cleanup PR or a quick standalone PR when the cleanup queue is being processed.
- **Audit pass** on existing TOOLS.md and bridge descriptions for Claude-specific language. Fold into the next cleanup PR; don't do as a standalone PR.
- **README** can mention "MCP-compliant clients (Claude Code, Codex CLI, Cursor, Gemini CLI, ...)" instead of just Claude Code. One-line edit.

**How to apply:**

When writing any new tool description, doc section, or spec, **default to vendor-neutral language unless there's a specific reason to mention a particular client** (e.g., a known client-specific quirk). When reviewing my own work, flag any Claude-specific phrasing as a finding to fix before push.
