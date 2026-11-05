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

## PyPI package — [IN-REPO DONE] build, [MAINTAINER ACTION] upload

`pyproject.toml` builds the PyPI package `unreal-ai-connection` with hatchling: a wheel holding the single stdlib-only module `unreal_ai_connection_bridge` plus a console script `unreal-ai-connection` (its `main()`). The version is read from `SERVER_VERSION` in the bridge; `tests/test_packaging.py` keeps `server.json` and the `.uplugin` on the same version, and the CI `package` job builds the wheel, installs it in a clean venv and answers an MCP `initialize` through the console script.

Build and check locally:

```bash
python -m pip install build
python -m build                # dist/unreal_ai_connection-<version>-py3-none-any.whl + .tar.gz
```

Publish (human, needs a PyPI account + API token): `python -m pip install twine && twine upload dist/*`. Once it is on PyPI, `uvx unreal-ai-connection` runs the bridge with no checkout and no absolute script path.

## Official MCP Registry — [MAINTAINER ACTION], after the PyPI upload

`server.json` ships in the repo (`io.github.najemwehbe/unreal-ai-connection`) and points at the PyPI package above, run with `uvx`. Publishing it feeds VS Code's MCP gallery, mcp.so and PulseMCP. The registry checks two things the repo already carries: the `description` fits the schema's 100-character limit, and the package README contains `mcp-name: io.github.najemwehbe/unreal-ai-connection` (an HTML comment at the top of `README.md`). One-time, after the PyPI upload:

```bash
# install the publisher CLI from github.com/modelcontextprotocol/registry releases
mcp-publisher login github     # GitHub device-flow; the io.github.najemwehbe namespace needs that GitHub account
mcp-publisher publish          # reads ./server.json
```

Re-run on each release, after the matching PyPI upload (can be a GitHub Actions step).

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

1. **PyPI upload, then MCP Registry publish** — one action feeds VS Code gallery + mcp.so + PulseMCP.
2. **Claude Code marketplace** — already live from the repo; just document the two `/plugin` commands.
3. **Cline marketplace** — one GitHub issue + a logo, large built-in audience.
4. Cursor/VS Code badges — already in README, zero submission.
5. Smithery + directory forms — opportunistic.
