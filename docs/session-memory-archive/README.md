# Session memory archive

This folder is a snapshot of Claude Code's project-specific memory files for this repo, taken at the end of the 2026-05-09 / 2026-05-10 Tier 3 sprint and updated on session pause for `restart-recovery` purposes.

**Why this exists:** Claude Code stores per-project session memory under `~/.claude/projects/<project-path-encoded>/memory/*.md`. That directory lives on the developer's home filesystem — a C: format wipes it. To survive a format/reinstall, the files are mirrored here in the repo.

**See also:** [`../RESTART-RECOVERY.md`](../RESTART-RECOVERY.md) for the step-by-step procedure to restore these files into Claude Code's expected location after a fresh OS install.

## Files in this archive

| File | Purpose |
|---|---|
| [`MEMORY.md`](MEMORY.md) | Index of all memory files; Claude Code loads this first |
| [`codex-collaboration-model.md`](codex-collaboration-model.md) | Three-pattern Codex+Claude parallelism model (sub-PR concurrency, pipeline concurrency, fix-while-write) |
| [`feedback_codex_invocation_settings.md`](feedback_codex_invocation_settings.md) | User's Codex defaults: `--effort xhigh --model gpt-5.5`, generous usage budget |
| [`feedback_multi_agent_workflow.md`](feedback_multi_agent_workflow.md) | 4-agent workflow (Codex + Sonnet explorer + Sonnet reviewer + Opus integrator); critical-path timing; sandbox-isolation gotcha for `general-purpose` subagent |
| [`feedback_vendor_neutral_mcp.md`](feedback_vendor_neutral_mcp.md) | Plugin supports all MCP clients (Codex CLI, Cursor, Gemini CLI, …); use vendor-neutral language in tool descriptions |
| [`pr-46-language-shim-in-flight.md`](pr-46-language-shim-in-flight.md) | Notes from the language-shim experiment (PR #46) — context for the synthetic-tool decision flow in `LANGUAGE-CHOICE-RETROSPECTIVE.md` |
| [`reference_codex_dotnet_ubt_crash.md`](reference_codex_dotnet_ubt_crash.md) | `dotnet.exe` Application Error popup is Codex's UBT compile-verify step crashing in its sandbox; bake "DO NOT run UBT" into every Codex prompt |
| [`reference_codex_usage_limits.md`](reference_codex_usage_limits.md) | Codex CLI is metered; ~5+ heavy C++ dispatches can exhaust quota; reset is on a clock; recovery options listed |

## Restoring after a format

```powershell
$src = "$env:USERPROFILE\Desktop\UnrealClaudeMCP\docs\session-memory-archive"
$dst = "$env:USERPROFILE\.claude\projects\C--Users-<USERNAME>-Desktop-UnrealClaudeMCP\memory"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item "$src\*.md" $dst -Force
# Don't copy this README.md — it's repo-only documentation
Remove-Item "$dst\README.md" -ErrorAction SilentlyContinue
```

After restore, the next Claude Code session for this project will see the same directives, conventions, and operational lessons as before the format.

## When to update this archive

These files are **snapshots**, not live links. They get stale as Claude Code's actual memory files evolve. Update this archive when:

1. **Before any major OS reinstall / format** — the whole reason this archive exists.
2. **After a sprint that adds new directives or trap-table entries** — the fresh learnings should be captured before they're at risk.
3. **When transferring the project to a new machine** — copy the archive to the new machine's memory directory after clone.

To update: copy from the live memory directory back into this folder, commit, push.

```powershell
$src = "$env:USERPROFILE\.claude\projects\C--Users-<USERNAME>-Desktop-UnrealClaudeMCP\memory"
$dst = "$env:USERPROFILE\Desktop\UnrealClaudeMCP\docs\session-memory-archive"
Copy-Item "$src\*.md" $dst -Force
cd $env:USERPROFILE\Desktop\UnrealClaudeMCP
git add docs/session-memory-archive
git commit -m "docs(memory): refresh session memory archive snapshot"
git push
```
