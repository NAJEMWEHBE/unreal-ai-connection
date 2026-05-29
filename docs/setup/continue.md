# Continue

Open-source IDE assistant. Configured via YAML at `~/.continue/config.yaml`.

## 5-step setup

1. **Open the config.** `~/.continue/config.yaml` (Windows: `%USERPROFILE%\.continue\config.yaml`). If you only have `config.json` (pre-v0.9.x), upgrade Continue first.
2. **Add an `mcpServers` block** under the top level. Snippet below.
3. **Replace the path** with your full path to `bridge/unreal_ai_connection_bridge.py`.
4. **Reload Continue.** Command palette → *Continue: Reload Window*.
5. **First-call test.** Open the Continue chat panel and type: *"Use the unreal-ai-connection tool, call get_engine_version."*

## YAML snippet

**Windows:**
```yaml
mcpServers:
  - name: unreal-ai-connection
    command: py
    args:
      - C:\full\path\to\UnrealAIConnection\bridge\unreal_ai_connection_bridge.py
```

**macOS / Linux:**
```yaml
mcpServers:
  - name: unreal-ai-connection
    command: python3
    args:
      - /full/path/to/UnrealAIConnection/bridge/unreal_ai_connection_bridge.py
```

## Verification

In the Continue panel, click the gear icon → **Tools**. You should see `unreal-ai-connection` listed; clicking it shows all 112 tools.

## Notes

- YAML is indentation-sensitive — use 2 spaces, no tabs.
- Continue uses string values for paths, no escape needed in YAML; single backslashes work on Windows in a YAML scalar.
- If Continue uses `config.json` instead of YAML on your install, the same structure applies — just use JSON: `{"mcpServers": [{"name": "...", "command": "...", "args": [...]}]}`.
