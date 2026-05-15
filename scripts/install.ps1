<#
.SYNOPSIS
    One-command installer for UnrealClaudeMCP on Windows.

.DESCRIPTION
    Drops the UnrealClaudeMCP plugin into a target UE project's Plugins/ folder,
    verifies the Python launcher (`py`) is on PATH, and (optionally) writes a
    starter .mcp.json for Claude Code / Cursor / VS Code Copilot in the project
    root. Does NOT regenerate VS project files or build the editor -- that's
    still a manual step the user runs once after install.

.PARAMETER ProjectPath
    Full path to the UE project root (the directory containing the .uproject).
    Required.

.PARAMETER Client
    Optional. One of: claude-code, claude-desktop, cursor, codex-cli, windsurf,
    continue, cline, zed, gemini-cli, vscode-copilot. If supplied, the script
    writes the matching MCP config snippet for that client. Project-scoped
    snippets (claude-code, cursor, vscode-copilot) land in the project; user-
    scoped snippets print the target path for the user to paste manually.

.PARAMETER DryRun
    Print every action without writing anything.

.EXAMPLE
    .\scripts\install.ps1 -ProjectPath "F:\MyGame" -Client claude-code

.EXAMPLE
    .\scripts\install.ps1 -ProjectPath "F:\MyGame" -DryRun
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [ValidateSet(
        'claude-code', 'claude-desktop', 'cursor', 'codex-cli', 'windsurf',
        'continue', 'cline', 'zed', 'gemini-cli', 'vscode-copilot'
    )]
    [string]$Client,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PluginSource = Join-Path $RepoRoot 'UnrealClaudeMCP'
$BridgePath = Join-Path $RepoRoot 'bridge\unreal_ai_connection_bridge.py'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "    SKIP: $msg" -ForegroundColor Yellow }

# 1. Validate ProjectPath
Write-Step "Validating project path: $ProjectPath"
if (-not (Test-Path $ProjectPath -PathType Container)) {
    throw "ProjectPath '$ProjectPath' does not exist or is not a directory."
}
$uproject = Get-ChildItem -Path $ProjectPath -Filter '*.uproject' -File | Select-Object -First 1
if (-not $uproject) {
    throw "No .uproject file found in '$ProjectPath'. Pass the UE project root."
}
Write-Ok "Found $($uproject.Name)"

# 2. Verify Python launcher
Write-Step "Checking Python 3.11+"
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    throw "Python launcher 'py' not on PATH. Install Python 3.11+ from python.org."
}
$pyVer = (& py -3 --version 2>&1).ToString()
if ($pyVer -notmatch 'Python 3\.(1[1-9]|[2-9][0-9])') {
    throw "Need Python 3.11+, got '$pyVer'."
}
Write-Ok $pyVer.Trim()

# 3. Verify plugin source + bridge
Write-Step "Verifying repo layout"
if (-not (Test-Path $PluginSource -PathType Container)) {
    throw "Plugin source missing: $PluginSource"
}
if (-not (Test-Path $BridgePath -PathType Leaf)) {
    throw "Bridge missing: $BridgePath"
}
Write-Ok "Plugin source: $PluginSource"
Write-Ok "Bridge:        $BridgePath"

# 4. Copy plugin into <project>/Plugins/UnrealClaudeMCP
$PluginDest = Join-Path $ProjectPath 'Plugins\UnrealClaudeMCP'
Write-Step "Installing plugin to $PluginDest"
if ($DryRun) {
    Write-Skip "DryRun -- would copy '$PluginSource' -> '$PluginDest'"
}
else {
    if (Test-Path $PluginDest) {
        Write-Host "    Target exists; removing previous install." -ForegroundColor Yellow
        Remove-Item -Recurse -Force $PluginDest
    }
    $PluginsDir = Split-Path $PluginDest -Parent
    if (-not (Test-Path $PluginsDir)) {
        New-Item -ItemType Directory -Path $PluginsDir | Out-Null
    }
    Copy-Item -Recurse -Path $PluginSource -Destination $PluginDest
    Write-Ok "Plugin installed."
}

# 5. Optional: write client config snippet
function New-StdioConfig {
    param([string]$BridgePath, [bool]$IsCopilot = $false)
    $serverEntry = [ordered]@{
        command = 'py'
        args    = @($BridgePath)
    }
    if ($IsCopilot) {
        $serverEntry = [ordered]@{
            type    = 'stdio'
            command = 'py'
            args    = @($BridgePath)
        }
        $cfg = [ordered]@{ servers = [ordered]@{ 'unreal-ai-connection' = $serverEntry } }
    }
    else {
        $cfg = [ordered]@{ mcpServers = [ordered]@{ 'unreal-ai-connection' = $serverEntry } }
    }
    return ($cfg | ConvertTo-Json -Depth 6)
}

if ($Client) {
    Write-Step "Writing $Client MCP config"
    $jsonSnippet = New-StdioConfig -BridgePath $BridgePath -IsCopilot:$false
    switch ($Client) {
        'claude-code' {
            $target = Join-Path $ProjectPath '.mcp.json'
            if ($DryRun) { Write-Skip "Would write $target" }
            else { Set-Content -Path $target -Value $jsonSnippet -Encoding UTF8; Write-Ok "Wrote $target" }
        }
        'cursor' {
            $cursorDir = Join-Path $ProjectPath '.cursor'
            $target = Join-Path $cursorDir 'mcp.json'
            if ($DryRun) { Write-Skip "Would write $target" }
            else {
                if (-not (Test-Path $cursorDir)) { New-Item -ItemType Directory -Path $cursorDir | Out-Null }
                Set-Content -Path $target -Value $jsonSnippet -Encoding UTF8
                Write-Ok "Wrote $target"
            }
        }
        'vscode-copilot' {
            $vscodeDir = Join-Path $ProjectPath '.vscode'
            $target = Join-Path $vscodeDir 'mcp.json'
            $copilotJson = New-StdioConfig -BridgePath $BridgePath -IsCopilot:$true
            if ($DryRun) { Write-Skip "Would write $target" }
            else {
                if (-not (Test-Path $vscodeDir)) { New-Item -ItemType Directory -Path $vscodeDir | Out-Null }
                Set-Content -Path $target -Value $copilotJson -Encoding UTF8
                Write-Ok "Wrote $target"
            }
        }
        default {
            Write-Host "    User-scope config for $Client. See: docs\setup\$Client.md" -ForegroundColor Yellow
            Write-Host "    Bridge path to paste: $BridgePath"
        }
    }
}

# 6. Next-step instructions
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host " Plugin installed. Manual next steps:"
Write-Host "============================================================" -ForegroundColor Green
Write-Host " 1. Right-click '$($uproject.FullName)' -> Generate Visual Studio project files"
Write-Host " 2. Open the .sln in Visual Studio, build 'Development Editor | Win64'"
Write-Host " 3. Launch UE; the server binds 127.0.0.1:18888 within ~2 min."
Write-Host " 4. Verify in your client's MCP panel that 'unreal-ai-connection' is connected."
Write-Host " 5. First test: ask your AI to call get_engine_version"
if (-not $Client) {
    Write-Host ""
    Write-Host " No -Client supplied. See docs\setup\README.md for per-client recipes." -ForegroundColor Yellow
}
