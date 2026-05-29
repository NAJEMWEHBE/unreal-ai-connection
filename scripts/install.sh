#!/usr/bin/env bash
# One-command installer for UnrealAIConnection on macOS / Linux.
#
# Drops the UnrealAIConnection plugin into a target UE project's Plugins/ folder,
# verifies python3 3.11+, and optionally writes a starter MCP config snippet
# for the chosen client. Does NOT regenerate project files or build the editor
# — that remains a manual step.
#
# Usage:
#   ./scripts/install.sh --project-path /path/to/MyGame [--client claude-code] [--dry-run]
#
# Supported clients: claude-code, claude-desktop, cursor, codex-cli, windsurf,
#                    continue, cline, zed, gemini-cli, vscode-copilot

set -euo pipefail

PROJECT_PATH=""
CLIENT=""
DRY_RUN=0

usage() {
    cat <<EOF
Usage: $0 --project-path <path> [--client <name>] [--dry-run]

  --project-path <path>   UE project root (directory containing .uproject)
  --client <name>         Optional. One of: claude-code, claude-desktop,
                          cursor, codex-cli, windsurf, continue, cline, zed,
                          gemini-cli, vscode-copilot
  --dry-run               Print every action without writing anything
  -h, --help              Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --project-path) PROJECT_PATH="$2"; shift 2 ;;
        --client) CLIENT="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

if [ -z "$PROJECT_PATH" ]; then
    echo "ERROR: --project-path is required." >&2
    usage
    exit 2
fi

case "$CLIENT" in
    ""|claude-code|claude-desktop|cursor|codex-cli|windsurf|continue|cline|zed|gemini-cli|vscode-copilot) ;;
    *) echo "ERROR: unknown --client value: $CLIENT" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PLUGIN_SOURCE="$REPO_ROOT/UnrealAIConnection"
BRIDGE_PATH="$REPO_ROOT/bridge/unreal_ai_connection_bridge.py"

step()  { printf '\033[1;36m==> %s\033[0m\n' "$1"; }
ok()    { printf '    \033[1;32mOK:   %s\033[0m\n' "$1"; }
skip()  { printf '    \033[1;33mSKIP: %s\033[0m\n' "$1"; }
warn()  { printf '    \033[1;33m%s\033[0m\n' "$1"; }

# 1. Validate project path
step "Validating project path: $PROJECT_PATH"
if [ ! -d "$PROJECT_PATH" ]; then
    echo "ERROR: '$PROJECT_PATH' is not a directory." >&2
    exit 1
fi
UPROJECT=$(find "$PROJECT_PATH" -maxdepth 1 -type f -name '*.uproject' | head -n 1 || true)
if [ -z "$UPROJECT" ]; then
    echo "ERROR: no .uproject file in '$PROJECT_PATH'." >&2
    exit 1
fi
ok "Found $(basename "$UPROJECT")"

# 2. Verify python3 3.11+
step "Checking Python 3.11+"
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not on PATH. Install Python 3.11+." >&2
    exit 1
fi
PY_VER=$(python3 --version 2>&1)
if ! echo "$PY_VER" | grep -qE 'Python 3\.(1[1-9]|[2-9][0-9])'; then
    echo "ERROR: need Python 3.11+, got '$PY_VER'." >&2
    exit 1
fi
ok "$PY_VER"

# 3. Verify repo layout
step "Verifying repo layout"
if [ ! -d "$PLUGIN_SOURCE" ]; then
    echo "ERROR: plugin source missing: $PLUGIN_SOURCE" >&2
    exit 1
fi
if [ ! -f "$BRIDGE_PATH" ]; then
    echo "ERROR: bridge missing: $BRIDGE_PATH" >&2
    exit 1
fi
ok "Plugin source: $PLUGIN_SOURCE"
ok "Bridge:        $BRIDGE_PATH"

# 4. Copy plugin
PLUGIN_DEST="$PROJECT_PATH/Plugins/UnrealAIConnection"
step "Installing plugin to $PLUGIN_DEST"
if [ "$DRY_RUN" -eq 1 ]; then
    skip "DryRun — would copy '$PLUGIN_SOURCE' -> '$PLUGIN_DEST'"
else
    if [ -d "$PLUGIN_DEST" ]; then
        warn "Target exists; removing previous install."
        rm -rf "$PLUGIN_DEST"
    fi
    mkdir -p "$(dirname "$PLUGIN_DEST")"
    cp -R "$PLUGIN_SOURCE" "$PLUGIN_DEST"
    ok "Plugin installed."
fi

# 5. Optional client config
if [ -n "$CLIENT" ]; then
    step "Writing $CLIENT MCP config"
    JSON_SNIPPET=$(cat <<EOF
{
  "mcpServers": {
    "unreal-ai-connection": {
      "command": "python3",
      "args": ["$BRIDGE_PATH"]
    }
  }
}
EOF
)
    write_file() {
        local target="$1"
        local content="$2"
        if [ "$DRY_RUN" -eq 1 ]; then
            skip "Would write $target"
        else
            mkdir -p "$(dirname "$target")"
            printf '%s\n' "$content" > "$target"
            ok "Wrote $target"
        fi
    }
    case "$CLIENT" in
        claude-code) write_file "$PROJECT_PATH/.mcp.json" "$JSON_SNIPPET" ;;
        cursor)      write_file "$PROJECT_PATH/.cursor/mcp.json" "$JSON_SNIPPET" ;;
        vscode-copilot)
            VSCODE_SNIPPET=$(cat <<EOF
{
  "servers": {
    "unreal-ai-connection": {
      "type": "stdio",
      "command": "python3",
      "args": ["$BRIDGE_PATH"]
    }
  }
}
EOF
)
            write_file "$PROJECT_PATH/.vscode/mcp.json" "$VSCODE_SNIPPET"
            ;;
        *)
            warn "User-scope config for $CLIENT. See: docs/setup/$CLIENT.md"
            warn "Bridge path to paste: $BRIDGE_PATH"
            ;;
    esac
fi

# 6. Next-step instructions
echo ""
echo "============================================================"
echo " Plugin installed. Manual next steps:"
echo "============================================================"
echo " 1. Open '$UPROJECT' and regenerate project files (right-click > Services menu)."
echo " 2. Build the Development Editor target (Xcode on macOS; make on Linux)."
echo " 3. Launch UE; the server binds 127.0.0.1:18888 within ~2 min."
echo " 4. Verify in your client's MCP panel that 'unreal-ai-connection' is connected."
echo " 5. First test: ask your AI to call get_engine_version"
if [ -z "$CLIENT" ]; then
    echo ""
    warn "No --client supplied. See docs/setup/README.md for per-client recipes."
fi
