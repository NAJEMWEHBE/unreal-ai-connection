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
5. **First-call test.** In the Cline chat: *"Use unreal-ai-connection to call get_engine_version."*

## Config snippet

**Windows:**
```json
{
  "mcpServers": {
    "unreal-ai-connection": {
      "command": "py",
      "args": ["C:\\full\\path\\to\\UnrealAIConnection\\bridge\\unreal_ai_connection_bridge.py"],
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
    "unreal-ai-connection": {
      "command": "python3",
      "args": ["/full/path/to/UnrealAIConnection/bridge/unreal_ai_connection_bridge.py"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

## Verification

Cline panel → **MCP Servers** tab → look for `unreal-ai-connection` with a green dot. Click it to see the published tool list. As a single-tool smoke test, ask Cline to call `get_engine_version`.

## Notes

- `autoApprove` accepts a list of tool names to skip the per-call approval prompt — handy for `get_log_lines`, `get_viewport_screenshot`, etc.
- The "Always allow read-only tools" toggle in Cline settings is a coarser version of `autoApprove`.
- Cline will surface bridge stderr inline in the chat if the server crashes — useful for debugging.
