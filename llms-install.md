# Unreal AI Connection — agent install guide

You are an AI agent configuring the **Unreal AI Connection** MCP server for a user. Follow these sections in order.

## 1. PREREQUISITE — cannot be skipped, no registry can do this

The MCP server is only a thin stdio bridge. It is useless until the Unreal Engine plugin is installed and the editor is running. You (or the user) must:

1. Clone/copy this repo: `git clone https://github.com/NAJEMWEHBE/unreal-ai-connection`
2. Copy the `UnrealClaudeMCP/` plugin folder from the repo into the user's UE project: `<UEProject>/Plugins/UnrealClaudeMCP/`
3. Build the UE 5.7 editor for that project (regenerate project files → build `Development Editor`).
4. Launch the UE editor. The plugin's TCP server binds `127.0.0.1:18888` within ~2 minutes of launch. Confirm via the Output Log: `[LogUCMCP] Listening on 127.0.0.1:18888`.

If the editor is not running, every tool call returns a clear "MCP server not running" error — that is expected, not a misconfiguration.

## 2. Wire the MCP server

Register a stdio MCP server in the user's client config:

- **Server key:** `unreal-ai-connection`
- **Command:** `python3` (macOS/Linux) or `py` (Windows)
- **Args:** the absolute path to `bridge/unreal_ai_connection_bridge.py` in the cloned repo
- **Env:** `PYTHONUNBUFFERED=1`

Canonical JSON (`.mcp.json` / client-equivalent):

```json
{
  "mcpServers": {
    "unreal-ai-connection": {
      "command": "python3",
      "args": ["/absolute/path/to/unreal-ai-connection/bridge/unreal_ai_connection_bridge.py"],
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

Per-client config locations and snippet shapes are in `docs/setup/` (one file per client: claude-code, claude-desktop, cursor, codex-cli, windsurf, continue, cline, zed, gemini-cli, vscode-copilot). Python 3.11+ required; the bridge has zero third-party dependencies.

## 3. Verify

With the UE editor running, ask the model to call the `get_engine_version` tool. Expected result: `5.7.x`. If you get a connection error, the editor isn't running or hasn't bound the port yet (see §1).
