# Claude Code (CLI + VS Code / JetBrains extensions)

Anthropic's official coding agent. Reads `.mcp.json` from your project root.

## 5-step setup

1. **Open the project where you want the tools available.** This is usually your UE project root, but it can be any directory — Claude Code searches up the tree for `.mcp.json`.
2. **Copy the example.** From this repo:
   ```bash
   cp examples/.mcp.json.example <your-project>/.mcp.json
   ```
   Or create `.mcp.json` from scratch with the snippet below.
3. **Edit the path.** Open `.mcp.json` and replace the placeholder with your full path to `bridge/unreal_claude_mcp_bridge.py`.
4. **Restart Claude Code.** In the CLI, exit and re-launch. In VS Code, reload the window (Ctrl/Cmd+Shift+P → *Reload Window*).
5. **First-call test.** Ask Claude: *"Use the unreal-claude-mcp server and call get_engine_version."* Expected reply: `5.7.x`.

## `.mcp.json` snippet

**Windows:**
```json
{
  "$schema": "https://json.schemastore.org/mcp.json",
  "mcpServers": {
    "unreal-claude-mcp": {
      "command": "py",
      "args": ["C:\\full\\path\\to\\UnrealClaudeMCP\\bridge\\unreal_claude_mcp_bridge.py"]
    }
  }
}
```

**macOS / Linux:**
```json
{
  "$schema": "https://json.schemastore.org/mcp.json",
  "mcpServers": {
    "unreal-claude-mcp": {
      "command": "python3",
      "args": ["/full/path/to/UnrealClaudeMCP/bridge/unreal_claude_mcp_bridge.py"]
    }
  }
}
```

## Approval flow

First time Claude Code launches with this server in `.mcp.json`, it prompts you to approve the new MCP server. Click **Approve** (or use `/mcp` and select `unreal-claude-mcp` → *Approve*). The approval is sticky per-project.

## Verification

```text
> /mcp
```

Should show `unreal-claude-mcp` connected with the full tool list (currently 104; expect this number to grow over time). Run `get_engine_version` as the canonical first call.

## Notes

- The `.mcp.json` file is project-scoped. To make the tools available everywhere, place a copy in your home directory and Claude Code will inherit it (precedence: project > user).
- VS Code extension uses the same `.mcp.json`. Reload the window after editing.
