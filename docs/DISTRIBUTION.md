# Distribution playbook

How "Unreal AI Connection" reaches users across MCP clients. Tags:

- **[IN-REPO DONE]** — the artifact ships in this repo; no further action.
- **[MAINTAINER ACTION]** — needs an account/token an automated agent cannot use; a human runs it once.

Cross-cutting caveat: **no channel can install the UE 5.7 plugin or launch the editor.** Every "install" only wires the stdio bridge. The UE plugin prerequisite is stated in `llms-install.md`, `server.json`'s description, and `docs/setup/README.md`.

## Claude Code plugin marketplace — [IN-REPO DONE]

`.claude-plugin/marketplace.json` + `.claude-plugin/plugin.json` + `.claude-plugin/mcp-config.json` ship in the repo. End users run:

```text
/plugin marketplace add NAJEMWEHBE/unreal-ai-connection
/plugin install unreal-ai-connection@unreal-ai-connection
```

Nothing else to publish — Claude Code reads these straight from the GitHub repo.

## Official MCP Registry — [MAINTAINER ACTION]

`server.json` ships in the repo (`io.github.NAJEMWEHBE/unreal-ai-connection`). Publishing it feeds VS Code's MCP gallery, mcp.so, and PulseMCP automatically. One-time:

```bash
# install the publisher CLI from github.com/modelcontextprotocol/registry releases
mcp-publisher login github     # GitHub device-flow; the io.github.NAJEMWEHBE namespace requires you own that GitHub account
mcp-publisher publish          # reads ./server.json
```

Re-run on each release (can be a GitHub Actions step).

> **Blocking follow-up TODO (not done yet):** `server.json` references a PyPI package `unreal-ai-connection` that does **not exist yet**. Until the PyPI publish below lands, the registry entry will not resolve to an installable package. Track this as the next distribution PR.

## PyPI package — [MAINTAINER ACTION] + small packaging follow-up

The bridge **already exposes a `main()` entrypoint with an `if __name__ == "__main__":` guard** (`bridge/unreal_ai_connection_bridge.py`). No code refactor is needed. The only follow-up to make `uvx unreal-ai-connection` / `pip install unreal-ai-connection` work is packaging config:

1. Add to `pyproject.toml` (the bridge lives in `bridge/`, so include it as a py-module via the build backend's source config — e.g. hatchling `[tool.hatch.build.targets.wheel] only-include = ["bridge"]`, or move the module onto the package path):
   ```toml
   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [tool.hatch.build.targets.wheel]
   only-include = ["bridge/unreal_ai_connection_bridge.py"]
   sources = ["bridge"]

   [project.scripts]
   unreal-ai-connection = "unreal_ai_connection_bridge:main"

   [tool.mcp]
   name = "io.github.NAJEMWEHBE/unreal-ai-connection"
   ```
2. Then (human): `python -m build` && `twine upload dist/*` (needs a PyPI account + token).

This is packaging-only (no behaviour change) and can ride in the same PR as the registry publish.

This refactor is intentionally NOT bundled with the marketplace metadata PR — it touches code and needs its own review + test pass.

## Cline MCP Marketplace — [MAINTAINER ACTION]

`llms-install.md` ships in the repo (the agent-readable install guide Cline consumes). One-time: open an issue at <https://github.com/cline/mcp-marketplace> with: the repo URL, a 400×400 PNG logo, and confirmation you tested Cline configuring it from `llms-install.md` alone. Review SLA ~ a couple of days.

## Smithery — [MAINTAINER ACTION]

Sign in at <https://smithery.ai> with GitHub and connect/claim the `NAJEMWEHBE/unreal-ai-connection` repo so it is indexed. (A `smithery.yaml` can be added later for the legacy stdio listing form; the MCP Registry entry is the higher-leverage path.)

## Cursor & VS Code one-click — [IN-REPO DONE]

Deeplink badges/templates are in `README.md` → "Install (one paste, any client)". No submission; the user clicks and approves. These become path-independent once the PyPI package lands (command becomes `uvx unreal-ai-connection` instead of an absolute script path).

## Directory listings — [MAINTAINER ACTION] (low priority)

- mcpservers.org: submit form at <https://mcpservers.org/submit>
- mcpmarket.com: submit form at <https://mcpmarket.com/submit>
- mcp.so + PulseMCP: auto-crawl from the official MCP Registry — nothing to do once the registry publish lands.

## Priority order (most reach per effort)

1. **MCP Registry publish** (+ the PyPI follow-up that unblocks it) — one action feeds VS Code gallery + mcp.so + PulseMCP.
2. **Claude Code marketplace** — already live from the repo; just document the two `/plugin` commands.
3. **Cline marketplace** — one GitHub issue + a logo, large built-in audience.
4. Cursor/VS Code badges — already in README, zero submission.
5. Smithery + directory forms — opportunistic.
