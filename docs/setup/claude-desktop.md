# Claude Desktop

Anthropic's desktop chat app. Loads servers from `claude_desktop_config.json`.

## 5-step setup

1. **Locate the config file.**
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Linux:** `~/.config/Claude/claude_desktop_config.json`
2. **Create or edit it.** If the file doesn't exist, create it with the snippet below.
3. **Replace the path** with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. **Quit and re-launch Claude Desktop.** A taskbar/menubar quit-and-restart is required — reload won't pick up the new server.
5. **First-call test.** In a new chat: *"Call get_engine_version on the unreal-ai-connection server."*

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

In Claude Desktop, click the 🔌 (plug) icon in the message input bar. You should see `unreal-ai-connection` listed with all 121 tools.

## Notes

- The path supports backslashes on Windows; remember to double-escape them in JSON (`\\`).
- If the server doesn't appear after restart, check the Claude Desktop logs at `%APPDATA%\Claude\logs\mcp*.log` (Windows) / equivalent on Mac.
- Claude Desktop has no `.env` injection — if your bridge needs env vars, set them at the OS level before launch.
