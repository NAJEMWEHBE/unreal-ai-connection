# Cursor

AI-first code editor. MCP servers configured via `.cursor/mcp.json` (project-scope) or `~/.cursor/mcp.json` (global).

## 5-step setup

1. **Pick a scope.**
   - **Project-scope** (recommended for UE projects): `.cursor/mcp.json` at the project root.
   - **Global**: `~/.cursor/mcp.json` (Windows: `%USERPROFILE%\.cursor\mcp.json`).
2. **Create the file** with the snippet below.
3. **Replace the path** with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. **Refresh the tool list.** On Cursor v0.43+ the change is picked up automatically on file save (see Notes). On older versions, `Cmd/Ctrl+Shift+P` → *Reload Window*.
5. **First-call test.** Open Cursor Chat (`Cmd/Ctrl+L`) and ask: *"Use the unreal-ai-connection tool to call get_engine_version."*

## Config snippet

**Windows:**
```json
{
  "mcpServers": {
    "unreal-ai-connection": {
      "command": "py",
      "args": ["C:\\full\\path\\to\\UnrealAIConnection\\bridge\\unreal_ai_connection_bridge.py"]
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
      "args": ["/full/path/to/UnrealAIConnection/bridge/unreal_ai_connection_bridge.py"]
    }
  }
}
```

## Verification

`Cmd/Ctrl+Shift+J` → **Cursor Settings** → **Features** → **MCP**. You should see `unreal-ai-connection` with a green dot and the tool count.

## Notes

- Cursor lists each tool individually — you can toggle per-tool access if you want to restrict the scope.
- The agent calling pattern is via Composer (`Cmd/Ctrl+I`) or Chat (`Cmd/Ctrl+L`) — Composer is faster for multi-tool sequences.
- Cursor refreshes the tool list on file save of `mcp.json`, no explicit reload needed in v0.43+.
