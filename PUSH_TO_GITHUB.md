# Push this repo to GitHub

**Status:** repo initialized, all files committed locally on the `main` branch. Ready to push.

The local repo is at: `C:\Users\NINOH\Desktop\UnrealClaudeMCP\`

This is the **clean public** repo — generic Unreal-Claude MCP infrastructure with NO HD Media plugin code (no Studio Operator panel, no chroma key, no camera/billboard spawn buttons). Eleven generic tools, MIT licensed.

## Easiest path — GitHub web UI + one paste

1. Go to https://github.com/new while logged in as **najmwahba.98@gmail.com** (the email Git is configured with on this machine).
2. Set:
   - **Repository name:** `UnrealClaudeMCP` (or whatever you prefer)
   - **Description:** `Drive Unreal Engine 5.7 from Claude (or any MCP client) over a local TCP socket. Eleven generic editor-automation tools, MIT license.`
   - **Visibility:** Public (this is the generic / open-source version) or Private (your call — flip later in Settings)
   - **DO NOT check** "Add a README", "Add .gitignore", or "Add a license" — the local repo already has these.
3. Click **Create repository**. GitHub shows a page with a few one-line setup commands.
4. Copy the URL of the new repo (looks like `https://github.com/<your-username>/UnrealClaudeMCP.git`).
5. Paste this in PowerShell or Git Bash, replacing `<URL>` with what you copied:

```bash
cd "C:/Users/NINOH/Desktop/UnrealClaudeMCP"
git remote add origin <URL>
git push -u origin main
```

If GitHub asks you to authenticate, paste a Personal Access Token (PAT) — see https://github.com/settings/tokens. Tick the `repo` scope. The token IS your password for HTTPS pushes.

That's it. The repo is live.

## Alternative — GitHub CLI

If you have `gh` installed:

```bash
cd "C:/Users/NINOH/Desktop/UnrealClaudeMCP"
gh repo create UnrealClaudeMCP --public --source=. --remote=origin --push
```

This creates the repo on GitHub AND pushes the local commits in one command. Pass `--private` instead of `--public` to start private.

If `gh` is not installed:

```powershell
winget install --id GitHub.cli
```

Then in a NEW shell (the install doesn't update PATH for the current one), run `gh auth login` once, then the `gh repo create` command above.

## After push, verify

Open the new repo URL in your browser. You should see:
- README rendered on the homepage with the 11-tool table
- 3 docs in `docs/` (INSTALLATION, ARCHITECTURE, TOOLS)
- 1 license (MIT), 1 .gitignore
- Plugin source in `UnrealClaudeMCP/Source/UnrealClaudeMCP/`
- Bridge in `bridge/unreal_claude_mcp_bridge.py`
- Example smoke test + `.mcp.json.example` in `examples/`

If anything looks off, the local repo is the source of truth — push corrections from here.

## What this repo is NOT

This repo intentionally does NOT contain:

- **HD Media's HDMediaCamPlugin** — that's the proprietary plugin with the Studio Operator panel, the camera/billboard/CompShot spawn buttons, the chroma-key MPC, and the BP_HDMediaAxCamera setup. That code stays in the private HD Media project.
- **`set_chroma_key` tool** — HD Media-specific (writes to MPC_HDMediaAx). Not in the generic 11.
- **HD Media setup scripts** — `setup_plugin.py` and friends.

If you ever want to publish the HD Media plugin separately, the unmodified copy is at `C:\Users\NINOH\Desktop\HDMediaUnrealMCP\` (also locally committed, not pushed).

## License note

MIT, copyright HD Media. Anyone can clone, modify, and redistribute with attribution.
