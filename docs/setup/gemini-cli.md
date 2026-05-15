# Gemini CLI

Google's MCP-capable CLI. Servers configured in `~/.gemini/settings.json`.

## 5-step setup

1. **Locate the config.** `~/.gemini/settings.json` (Windows: `%USERPROFILE%\.gemini\settings.json`).
2. **Create or open it** and add an `mcpServers` block as in the snippet below.
3. **Replace the path** with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. **Restart any active Gemini CLI session.**
5. **First-call test.** `gemini` → in prompt: *"Use unreal-ai-connection to call get_engine_version."*

## Config snippet

**Windows:**
```json
{
  "mcpServers": {
    "unreal-ai-connection": {
      "command": "py",
      "args": ["C:\\full\\path\\to\\UnrealClaudeMCP\\bridge\\unreal_ai_connection_bridge.py"]
    }
  }
}
```

**macOS / Linux:**
```json
{
  "mcpServers": {
    "unreal-ai-connection": {
      "command": "python3",
      "args": ["/full/path/to/UnrealClaudeMCP/bridge/unreal_ai_connection_bridge.py"]
    }
  }
}
```

## Verification

```bash
gemini mcp list
```

Should show `unreal-ai-connection` → ready + tool count.

## Notes

- The Gemini CLI follows the canonical MCP JSON shape — same as Claude Code / Claude Desktop / Cursor.
- If you also use Gemini in Google AI Studio, the web client cannot consume local stdio MCP servers; that's a CLI-only feature.
- For project-scoped config, drop a `settings.json` at `.gemini/settings.json` in your project directory; CLI uses project-scope over user-scope.
