---
name: dotnet.exe Application Error popup is Codex's UBT compile attempt crashing
description: When Codex tries to run RunUAT/UBT for "compile verification" inside its sandbox, dotnet.exe crashes with CLR exception 0xe0434352 — Windows shows Application Error popup. Suppress the build step in Codex prompts.
type: reference
originSessionId: 886e5f4a-65a1-4dd0-a038-703f0c903a63
---
**Symptom:** Windows pops up "dotnet.exe - Application Error: The exception unknown software exception (0xe0434352) occurred in the application at location 0x00007FFDA694FE0A." Sometimes appears mid-Codex-task, sometimes after.

**Root cause:** UE 5.x's `UnrealBuildTool` (UBT) runs as a .NET app via `dotnet.exe`. Codex's default workflow includes a "structural-correctness" final step that attempts `RunUAT BuildPlugin` or similar compile verification. The Codex sandbox blocks writes under `C:\Users\<user>\AppData\Local\UnrealEngine\...` (where UBT writes intermediate artifacts), causing the .NET runtime to throw an unhandled CLR exception → `dotnet.exe` crashes → Windows error popup.

The exception code `0xe0434352` is the canonical CLR unhandled-exception code — its hex bytes literally encode `\xe0CCR` (CCR = original CLR codename). Confirms it's .NET, not Codex's Node-based CLI.

**Confirmed instances:**
- PR #51: Codex's own report said "attempted RunUAT BuildPlugin for a full compile check but the sandbox blocked writes under `%LOCALAPPDATA%\UnrealEngine\...`"
- PR #53: dotnet.exe popup appeared during Codex dispatch (the agent was also killed for shell-quoting issues, separate failure)
- Earlier in the session before PR #50: same popup appeared

**Fix going forward — bake into every Codex prompt:**

Add an explicit out-of-scope instruction:

> **DO NOT run UBT, RunUAT, BuildPlugin, or any C++ compilation step.** Structural correctness is verified by `git diff --check` and `git diff --numstat` on the touched files; code-correctness is verified by Opus's synthesis review post-return. Compilation is run by the user on the host machine per the HANDOFF.md runbook, not by you. Skipping compilation also avoids the known sandbox crash on `dotnet.exe` (UBT runs as a .NET app — when blocked from writing AppData/Local/UnrealEngine, it throws an unhandled CLR exception).

**Why skipping is safe:**

- pytest matrix on CI verifies the bridge / Python side
- Opus's synthesis review catches structural / contract / trap-table issues post-return
- The user manually runs UBT on the host post-merge per the canonical verification runbook (HANDOFF.md:42-52)
- Codex's structural verification (file diffs, header citations, contract conformance, "no other files touched") is already comprehensive

**How to apply:** include the block above (or a tightened version) in the "Out of scope — DO NOT TOUCH" section of every Codex prompt for this project.
