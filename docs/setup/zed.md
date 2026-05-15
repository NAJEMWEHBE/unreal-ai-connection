# Zed

High-performance code editor. MCP support via the `context_servers` block in `~/.config/zed/settings.json`.

## 5-step setup

1. **Open Zed settings.** `Cmd/Ctrl+,` opens `~/.config/zed/settings.json` (Windows: `%APPDATA%\Zed\settings.json`).
2. **Add a `context_servers` block** at the top level. Snippet below.
3. **Replace the path** with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. **Save the file.** Zed picks up changes immediately — no restart required.
5. **First-call test.** Open the assistant panel (`Cmd/Ctrl+R Cmd/Ctrl+A` or `agent: new conversation`) and ask: *"Call get_engine_version using unreal-ai-connection."*

## Settings snippet

**Windows:**
```json
{
  "context_servers": {
    "unreal-ai-connection": {
      "command": {
        "path": "py",
        "args": ["C:\\full\\path\\to\\UnrealClaudeMCP\\bridge\\unreal_ai_connection_bridge.py"],
        "env": {}
      }
    }
  }
}
```

**macOS / Linux:**
```json
{
  "context_servers": {
    "unreal-ai-connection": {
      "command": {
        "path": "python3",
        "args": ["/full/path/to/UnrealClaudeMCP/bridge/unreal_ai_connection_bridge.py"],
        "env": {}
      }
    }
  }
}
```

## Verification

Open the Agent panel → click the model name in the top-right → **Tools**. `unreal-ai-connection` should appear with all 104 tools.

## Notes

- Zed uses `context_servers` (with an underscore), not `mcpServers`. Easy to mistype.
- The wrapper `command: { path, args, env }` shape is Zed-specific. Other clients use flat `command` + `args`.
- Zed will silently fail to load a malformed snippet — check `~/.local/share/zed/logs/Zed.log` if the server doesn't show up.
