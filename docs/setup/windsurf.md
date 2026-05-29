# Windsurf (Codeium)

Codeium's AI-native editor. MCP servers in `~/.codeium/windsurf/mcp_config.json`.

## 5-step setup

1. **Locate the config.**
   - **Windows:** `%USERPROFILE%\.codeium\windsurf\mcp_config.json`
   - **macOS / Linux:** `~/.codeium/windsurf/mcp_config.json`
2. **Create or open it.** If new, paste the snippet below as the entire file contents.
3. **Replace the path** with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. **Refresh in-app.** Open Windsurf's Cascade chat → click the hammer/tools icon → **Refresh**. Or restart Windsurf.
5. **First-call test.** In Cascade: *"Call get_engine_version through unreal-ai-connection."*

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

Cascade panel → 🔨 tools icon → expand `unreal-ai-connection`. Tool count should match all 129 tools.

## Notes

- Windsurf's MCP tooltip shows green dot when the bridge process is alive — red dot if it crashed at startup.
- Cascade has a per-conversation tool budget; if a long session reports "tool limit reached," start a new conversation.
