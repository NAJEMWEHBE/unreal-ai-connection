# VS Code Copilot (GitHub Copilot in Agent Mode)

GitHub Copilot's agent mode in VS Code reads MCP servers from `.vscode/mcp.json` (workspace) or user settings.

## 5-step setup

1. **Enable Agent mode** if you haven't — Copilot Chat → mode picker (top-left of chat) → **Agent**. Requires Copilot subscription.
2. **Create the workspace config.** In your project root: `.vscode/mcp.json`.
3. **Paste the snippet below** and replace the path with your full path to `bridge/unreal_claude_mcp_bridge.py`.
4. **Reload VS Code.** Command palette → *Developer: Reload Window*.
5. **First-call test.** Copilot Chat (Agent mode) → *"Use the unreal-claude-mcp server and call get_engine_version."*

## Config snippet

**Windows:**
```json
{
  "servers": {
    "unreal-claude-mcp": {
      "type": "stdio",
      "command": "py",
      "args": ["C:\\full\\path\\to\\UnrealClaudeMCP\\bridge\\unreal_claude_mcp_bridge.py"]
    }
  }
}
```

**macOS / Linux:**
```json
{
  "servers": {
    "unreal-claude-mcp": {
      "type": "stdio",
      "command": "python3",
      "args": ["/full/path/to/UnrealClaudeMCP/bridge/unreal_claude_mcp_bridge.py"]
    }
  }
}
```

## Verification

Copilot Chat (Agent mode) → 🛠️ tools button (input bar) → expand **MCP: unreal-claude-mcp**. All 104 tools should appear with checkboxes.

## Notes

- VS Code uses `servers` (not `mcpServers`) and requires the `"type": "stdio"` field — a different shape from every other client. Easy to mis-copy.
- For user-scope (every workspace inherits), put the same JSON in `User Settings (JSON)` under the `"mcp": { "servers": { ... } }` key.
- The Agent mode is gated by Copilot subscription tier; chat-only ("Ask" mode) cannot call MCP tools.
- Tool permission is per-call by default — toggle "Always allow" in the tool dropdown to skip prompts.
