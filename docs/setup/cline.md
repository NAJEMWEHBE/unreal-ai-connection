# Cline (VS Code extension)

Open-source coding agent for VS Code (formerly Claude Dev). Config: `cline_mcp_settings.json`.

## 5-step setup

1. **Locate the settings file.**
   - **Windows:** `%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json`
   - **macOS:** `~/Library/Application Support/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
   - **Linux:** `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`
2. **Or use the in-app editor.** Open Cline panel → ⚙️ → **MCP Servers** → **Edit Configuration**.
3. **Paste / replace** with the snippet below, swapping the path to your bridge.
4. **Cline auto-reloads** on file save. No window reload required.
5. **First-call test.** In the Cline chat: *"Use unreal-claude-mcp to call get_engine_version."*

## Config snippet

**Windows:**
```json
{
  "mcpServers": {
    "unreal-claude-mcp": {
      "command": "py",
      "args": ["C:\\full\\path\\to\\UnrealClaudeMCP\\bridge\\unreal_claude_mcp_bridge.py"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

**macOS / Linux:**
```json
{
  "mcpServers": {
    "unreal-claude-mcp": {
      "command": "python3",
      "args": ["/full/path/to/UnrealClaudeMCP/bridge/unreal_claude_mcp_bridge.py"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

## Verification

Cline panel → **MCP Servers** tab → look for `unreal-claude-mcp` with a green dot. Click it to see all 104 tools.

## Notes

- `autoApprove` accepts a list of tool names to skip the per-call approval prompt — handy for `get_log_lines`, `get_viewport_screenshot`, etc.
- The "Always allow read-only tools" toggle in Cline settings is a coarser version of `autoApprove`.
- Cline will surface bridge stderr inline in the chat if the server crashes — useful for debugging.
