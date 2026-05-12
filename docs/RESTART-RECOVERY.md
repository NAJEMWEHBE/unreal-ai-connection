# Restart-recovery procedure

**Use this doc when:** the development machine has been wiped (C: formatted, OS reinstalled, all software gone) and you need to bring the project back to a working state with the same multi-agent workflow.

The repo (this folder you're reading) is the **only thing that survives a format**, because it's pushed to GitHub. Everything else — Claude Code session memory, Codex auth, `.mcp.json`, npm globals, locally installed tools — needs to be re-set-up. This doc covers all of it.

---

## What survived (zero action needed)

- **The repo source** — every PR through #85 is on `origin/main` at `github.com/NAJEMWEHBE/UnrealClaudeMCP`
- **Session memory files** — preserved in [`docs/session-memory-archive/`](session-memory-archive/) (this is the safety net for the format)
- **HANDOFF.md** — reflects the current state (86 tools, 69 C++ handlers + 17 bridge synthetic, all directives, all traps)
- **CLAUDE.md** at repo root — auto-loaded by Claude Code on session start; tells the agent to read HANDOFF.md
- **The Unreal Engine install** — if it's on `F:\UE_5.7\` (different drive from the formatted C:), it survives. If you also wipe F:, follow step 7 below.

---

## What was wiped (action needed)

| What | Where | Survives format? |
|---|---|---|
| Repo working tree | `F:\UnrealClaudeMCP\` | ❌ — re-clone from GitHub |
| Claude Code session memory | `~/.claude/projects/.../memory/*.md` | ❌ — restore from `docs/session-memory-archive/` |
| Codex CLI auth | `~/.codex/auth.json` | ❌ — re-login via `codex login` |
| Codex CLI install | npm global (`@openai/codex`) | ❌ — `npm install -g @openai/codex` |
| `.mcp.json` (Claude Code MCP config) | repo root, gitignored | ❌ — copy from `examples/.mcp.json.example` |
| Node.js + npm | `C:\Program Files\nodejs\` | ❌ |
| Python 3 | various | ❌ |
| Git + GitHub CLI | various | ❌ |
| Visual Studio with C++ workload | `C:\Program Files\Microsoft Visual Studio\` | ❌ — required to build the UE plugin |
| Unreal Engine 5.7 | `F:\UE_5.7\` | ✅ if F: not formatted; ❌ otherwise |
| Claude Code | `C:\Users\<user>\AppData\Local\Programs\claude\` | ❌ |

---

## Recovery sequence

Do these in order. Each step builds on the previous.

### 1. Install foundational tools

In order of dependency:

1. **Git** — `https://git-scm.com/download/win`
2. **GitHub CLI (`gh`)** — `winget install --id GitHub.cli` or `https://cli.github.com/`
3. **Node.js LTS** (includes npm) — `https://nodejs.org/`
4. **Python 3.11+** — `https://www.python.org/downloads/` (check "Add to PATH")
5. **Visual Studio 2022** with the **"Game development with C++"** workload — required by UE 5.7's UnrealBuildTool. `https://visualstudio.microsoft.com/`
6. **Claude Code** — `https://claude.ai/download` (or whichever distribution applies)
7. **Optional: VS Code** — `https://code.visualstudio.com/` if you want the editor with Codex extension

Verify each:
```powershell
git --version          # 2.40+
gh --version           # 2.30+
node --version         # v20 or v22
npm --version          # 10+
py -3 --version        # Python 3.11 or later
```

### 2. Authenticate to GitHub

```powershell
gh auth login
# Choose: GitHub.com → HTTPS → Yes to auth git → Login with a web browser
```

### 3. Re-clone the repo

```powershell
cd $env:USERPROFILE\Desktop
gh repo clone NAJEMWEHBE/UnrealClaudeMCP
cd UnrealClaudeMCP
git status   # should be clean, on main
```

### 4. Install Codex CLI + authenticate

```powershell
npm install -g @openai/codex
codex login
# Browser opens for ChatGPT login → return to terminal
```

Verify:
```powershell
codex --version    # 0.130 or later
node "$env:USERPROFILE\.claude\plugins\cache\openai-codex\codex\1.0.4\scripts\codex-companion.mjs" setup --json
# Should report ready: true, codex.available: true, auth.loggedIn: true
```

(The path above is from the prior install. If the plugin location is different, find it via the Claude Code plugin manager.)

### 5. Restore session memory

The 8 session memory files live in [`docs/session-memory-archive/`](session-memory-archive/) — copy them back to the Claude Code project memory directory:

```powershell
$src = "F:\UnrealClaudeMCP\docs\session-memory-archive"
$dst = "$env:USERPROFILE\.claude\projects\F--UnrealClaudeMCP\memory"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item "$src\*.md" $dst -Force
```

After this, the next Claude Code session in this project will see the same directives, conventions, and lessons as before the format. The `MEMORY.md` index file is included.

### 6. Restore `.mcp.json` (Claude Code MCP config)

```powershell
cd F:\UnrealClaudeMCP
Copy-Item examples\.mcp.json.example .mcp.json
```

Edit `.mcp.json` to match your local Python path if needed (the example uses `py -3`).

### 7. (Optional) Reinstall Unreal Engine 5.7

If F: drive was also wiped:
- Install Epic Games Launcher → install Unreal Engine 5.7
- Verify: `F:\UE_5.7\Engine\Build\BatchFiles\Build.bat` exists

If F: drive survived: skip this step.

### 8. Re-sync the UE project's plugin

Open the test project (e.g. `<host-project>\<HostProjectName>.uproject`). Per HANDOFF runbook step 3:

```powershell
robocopy "F:\UnrealClaudeMCP\UnrealClaudeMCP" "<host-project>\Plugins\UnrealClaudeMCP" /MIR /XD Binaries Intermediate .vs /NFL /NDL /NJH /NJS /NP
```

Then build:

```powershell
& "F:\UE_5.7\Engine\Build\BatchFiles\Build.bat" <HostProjectName>Editor Win64 Development -project="<full path to .uproject>"
```

Must end with `Result: Succeeded`.

### 9. Verify the test suite still passes

```powershell
cd F:\UnrealClaudeMCP
py -3 -m pip install pytest   # if pytest isn't already installed
py -3 -m pytest tests/ -q
```

Expected: **162 passing** (matches the state at end of the 2026-05-09/05-10 sprint — verify against the count assertions in `tests/test_bridge.py:26` and `tests/test_manifest_sync.py:45`).

### 10. Open Claude Code in the repo

Claude Code will auto-load `CLAUDE.md` (which references `docs/HANDOFF.md`). Send the agent: *"Read `docs/HANDOFF.md` and continue from where the previous session left off. Verify Codex tooling is reachable."*

---

## What the next session should know

After recovery, the project state is:

- **86 tools shipped** (69 UE C++ handlers + 17 bridge-side synthetic)
- **Latest commit on main:** check via `git log -1 --oneline` — this file is necessarily behind on the SHA after every merge.
- **Live verification on host machine:** **PASSING** as of the 2026-05-10 sprint (cold compile + editor + bridge round-trip proven on `HDMediaVirtualStudio` host project). New C++ handlers follow the cold-compile-before-merge cadence (HANDOFF.md "Session 2026-05-10").
- **Next deferred handlers:** `inspect_data_asset`, `inspect_sound_class`, `inspect_metasound`, bulk delete/move, Sequencer keyframe authoring, Movie Render Queue — see HANDOFF.md "What to watch in the next session".
- **Copilot reviewer: deferred (no subscription).** `gh api user/copilot` returns 404 on the owner account. `.github/copilot-instructions.md` stays in tree as a no-op until/unless a Copilot Pro subscription is added independently. Bot review fleet is Codex + Gemini only. See HANDOFF.md "Session 2026-05-11 (third micro-session)".

The session memory files (now restored to `~/.claude/projects/.../memory/`) document:
- Multi-agent workflow (Codex C++ + Sonnet helpers + Opus integrator/reviewer) — `feedback_multi_agent_workflow.md`
- Vendor-neutral MCP framing — `feedback_vendor_neutral_mcp.md`
- Codex invocation settings (`gpt-5.5` / `xhigh`) — `feedback_codex_invocation_settings.md`
- The Codex collaboration model (parallelism patterns) — `codex-collaboration-model.md`
- Operational gotchas: `dotnet.exe` UBT crash, Codex usage limits — `reference_*.md`

---

## If something goes wrong

- **Memory files don't load** — Claude Code reads them via the `MEMORY.md` index. Make sure the destination path matches the project: `~/.claude/projects/F--UnrealClaudeMCP/memory/`. The folder name encodes the absolute repo path with `\` → `-`.
- **Codex CLI not on PATH after install** — close and reopen the terminal; npm globals need a fresh shell to pick up. Or check `npm config get prefix` to find the install location and add it to PATH.
- **Codex CLI says "usage limit"** — this happens occasionally on heavy sprints. Reset is on a clock (typically 4-6h). See `docs/session-memory-archive/reference_codex_usage_limits.md`.
- **`dotnet.exe` Application Error popup during Codex run** — known issue; covered in `docs/session-memory-archive/reference_codex_dotnet_ubt_crash.md`. The fix is the no-UBT instruction baked into Codex prompts.
- **UE plugin won't compile** — confirm Visual Studio C++ workload is installed AND that UE 5.7 is on PATH (check `Build.bat` exists). Both are required.
- **pytest count mismatch** — main may have moved since this doc was written. Run `git log -1` and check `docs/HANDOFF.md` for the current expected count.

---

## Format checklist (run BEFORE the format)

Verify everything is on GitHub before you wipe:

```powershell
cd F:\UnrealClaudeMCP
git status                # should be clean
git push origin main      # should report "Everything up-to-date"
git log --oneline -5      # should match what `gh repo view --web` shows
```

If `git status` reports uncommitted work, commit and push it first. **Don't format until `git push` reports "everything up-to-date."**
