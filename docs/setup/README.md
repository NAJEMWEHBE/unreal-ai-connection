# MCP client setup recipes

Unreal AI Connection speaks the open Model Context Protocol over stdio. Every MCP-compliant AI client can drive it — the only thing that differs is *where the config file lives* and *what JSON/TOML shape it expects*. Pick your client below.

## Prerequisites (all clients)

1. **Unreal Engine 5.7** with the `UnrealAIConnection` plugin built into your project (see [the main README quick-start](../../README.md#quick-start)).
2. **Python 3.11+** on PATH (`py --version` should print `3.11.x` or newer on Windows; `python3 --version` on macOS/Linux).
3. **This repo cloned somewhere on disk.** You'll point the client at `bridge/unreal_ai_connection_bridge.py`.
4. **UE editor running** with the plugin loaded — look for `[LogUCMCP] Listening on 127.0.0.1:18888` in the Output Log.

## Pick your client

| Client | Recipe | Config location |
|---|---|---|
| Claude Code (CLI / VS Code / JetBrains) | [claude-code.md](claude-code.md) | `.mcp.json` at project root |
| Claude Desktop | [claude-desktop.md](claude-desktop.md) | `claude_desktop_config.json` |
| Cursor | [cursor.md](cursor.md) | `.cursor/mcp.json` |
| Codex CLI (OpenAI) | [codex-cli.md](codex-cli.md) | `~/.codex/config.toml` |
| Windsurf | [windsurf.md](windsurf.md) | `~/.codeium/windsurf/mcp_config.json` |
| Continue | [continue.md](continue.md) | `~/.continue/config.yaml` |
| Cline (VS Code extension) | [cline.md](cline.md) | `cline_mcp_settings.json` |
| Zed | [zed.md](zed.md) | `~/.config/zed/settings.json` |
| Gemini CLI | [gemini-cli.md](gemini-cli.md) | `~/.gemini/settings.json` |
| VS Code Copilot | [vscode-copilot.md](vscode-copilot.md) | `.vscode/mcp.json` |

## Shape of every recipe

Each file is a 5-step copy-paste:

1. Locate / create the client config file.
2. Paste the MCP server entry (we provide the snippet).
3. Replace the placeholder path with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. Reload / restart the client.
5. Ask the client to call `get_engine_version` — first-call verification.

## "Just give me the JSON"

Stdio command shared by JSON-based clients (Claude Code, Cursor, Cline, VS Copilot, Gemini, Windsurf):

```json
{
  "command": "py",
  "args": ["C:\\full\\path\\to\\UnrealAIConnection\\bridge\\unreal_ai_connection_bridge.py"]
}
```

On macOS/Linux use `python3` instead of `py` and a POSIX path:

```json
{
  "command": "python3",
  "args": ["/full/path/to/UnrealAIConnection/bridge/unreal_ai_connection_bridge.py"]
}
```

Codex CLI uses TOML — see its recipe.

## Troubleshooting (applies to every client)

- **Client lists 0 tools / "server disconnected"** — UE editor not running, or plugin failed to load. Check `Saved/Logs/<Project>.log` for `[LogUCMCP] Listening on 127.0.0.1:18888`.
- **`py: command not found`** — Windows Python launcher missing. Install from python.org or use full path: `"command": "C:\\Python312\\python.exe"`.
- **Tool call hangs forever** — bridge is waiting on UE. Confirm port 18888 is listening: `netstat -ano | findstr 18888` (Windows) or `lsof -i :18888` (macOS/Linux).
- **First call returns `connection refused`** — UE plugin loaded but server didn't bind. Restart the editor; it usually binds within ~2 minutes of launch.

For per-client gotchas (paths, schema variants, reload commands), see each recipe.
