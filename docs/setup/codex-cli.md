# Codex CLI (OpenAI)

OpenAI's coding CLI. Configured via TOML at `~/.codex/config.toml`, NOT JSON.

## 5-step setup

1. **Install Codex CLI** if you haven't: `npm install -g @openai/codex` or via Brew.
2. **Use the built-in `mcp add` command** (preferred) — it edits TOML for you:
   ```bash
   codex mcp add unreal-ai-connection -- py "C:\\full\\path\\to\\UnrealAIConnection\\bridge\\unreal_ai_connection_bridge.py"
   ```
   On macOS/Linux:
   ```bash
   codex mcp add unreal-ai-connection -- python3 /full/path/to/UnrealAIConnection/bridge/unreal_ai_connection_bridge.py
   ```
3. **Or edit TOML manually** — open `~/.codex/config.toml` (Windows: `%USERPROFILE%\.codex\config.toml`) and append the snippet below.
4. **Restart any active Codex session.**
5. **First-call test.** `codex` → in the prompt: *"Call get_engine_version via the unreal-ai-connection MCP server."*

## TOML snippet

**Windows:**
```toml
[mcp_servers.unreal-ai-connection]
command = "py"
args = ["C:\\full\\path\\to\\UnrealAIConnection\\bridge\\unreal_ai_connection_bridge.py"]
```

**macOS / Linux:**
```toml
[mcp_servers.unreal-ai-connection]
command = "python3"
args = ["/full/path/to/UnrealAIConnection/bridge/unreal_ai_connection_bridge.py"]
```

## Verification

```bash
codex mcp list
```

Should show `unreal-ai-connection` → connected with the tool count.

## Notes

- Codex uses TOML *not* JSON — easy mistake. Use `codex mcp add` to avoid hand-editing.
- Path quoting on Windows: forward slashes also work (`"C:/full/path/..."`) and avoid double-escape.
- If `codex mcp list` shows the server but tool calls fail, run `codex --debug` and inspect the bridge stderr.
