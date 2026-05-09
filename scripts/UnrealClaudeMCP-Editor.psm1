# UnrealClaudeMCP-Editor.psm1
#
# PowerShell module for UnrealClaudeMCP editor lifecycle automation.
# Use during the verification runbook (docs/HANDOFF.md) to launch the editor,
# wait for the MCP TCP server to bind, count registered handlers in the log,
# and shut the editor down cleanly.
#
# Usage:
#   Import-Module .\scripts\UnrealClaudeMCP-Editor.psm1
#   Stop-UCMCPEditor
#   $proc = Start-UCMCPEditor -ProjectPath "C:\path\to\Host.uproject"
#   $ready = Wait-UCMCPReady -TimeoutSeconds 540
#   $check = Test-UCMCPHandlers -LogPath "C:\path\to\Host\Saved\Logs\Host.log"
#   if ($check.Pass) { "verified $($check.Actual)/$($check.Expected) handlers" }
#
# Cross-platform: works on Windows PowerShell 5.1 and PowerShell 7+ (Linux/Mac
# usable with -EditorBinary pointing at the Linux/Mac UE binary). The TCP
# port-poll uses [System.Net.Sockets.TcpClient] rather than Test-NetConnection
# (which is Windows-only) so the readiness loop is portable.

Set-StrictMode -Version Latest

# ----------------------------------------------------------------------------
# Start-UCMCPEditor
# ----------------------------------------------------------------------------
# Launch the UE editor with a host project as a detached background process.
# Returns the System.Diagnostics.Process object so the caller can capture the
# PID for later Stop-UCMCPEditor or kill-on-failure cleanup.
#
# The editor stays running until Stop-UCMCPEditor (or another taskkill).
# Step 5 of the verification runbook depends on this — the MCP TCP server
# binds 127.0.0.1:18888 only after the editor finishes loading the plugin
# module and registering all 32 handlers.

function Start-UCMCPEditor {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [ValidateScript({ Test-Path $_ -PathType Leaf -ErrorAction Stop })]
        [string]$ProjectPath,

        [Parameter()]
        [string]$EditorBinary = 'F:\UE_5.7\Engine\Binaries\Win64\UnrealEditor.exe'
    )

    if (-not (Test-Path $EditorBinary -PathType Leaf)) {
        throw "Editor binary not found at '$EditorBinary'. Override with -EditorBinary <path>."
    }

    $proc = Start-Process -FilePath $EditorBinary `
        -ArgumentList "`"$ProjectPath`"" `
        -PassThru

    Write-Verbose "Started UnrealEditor PID $($proc.Id) loading $ProjectPath"
    return $proc
}

# ----------------------------------------------------------------------------
# Stop-UCMCPEditor
# ----------------------------------------------------------------------------
# Idempotently stop any running UnrealEditor.exe instances. Safe to call when
# no editor is running. The verification runbook step 2 calls this before a
# rebuild because Live Coding holds the plugin DLL open and would prevent UBT
# from overwriting it.
#
# Returns the count of processes stopped (0 if none were running).

function Stop-UCMCPEditor {
    [CmdletBinding()]
    param()

    $procs = Get-Process UnrealEditor -ErrorAction SilentlyContinue
    if ($null -eq $procs) {
        Write-Verbose "No UnrealEditor process running"
        return 0
    }

    $count = 0
    foreach ($p in $procs) {
        try {
            Stop-Process -Id $p.Id -Force -ErrorAction Stop
            Write-Verbose "Stopped UnrealEditor PID $($p.Id)"
            $count++
        }
        catch {
            Write-Warning "Failed to stop UnrealEditor PID $($p.Id): $_"
        }
    }
    return $count
}

# ----------------------------------------------------------------------------
# Wait-UCMCPReady
# ----------------------------------------------------------------------------
# Block until the MCP TCP server is listening on the given port, or until
# TimeoutSeconds elapses. Polls every PollIntervalSeconds.
#
# This is the canonical "editor is ready" signal: the plugin module's
# StartupModule (UnrealClaudeMCPModule.cpp:91) calls
# FUCMCPServer::Get().Start(kMCPDefaultPort) only AFTER all handlers have
# registered. So a successful TCP connect implies all 32 handlers are
# already registered.
#
# Returns a PSCustomObject with Ready (bool), Port (int), and ElapsedSeconds.

function Wait-UCMCPReady {
    [CmdletBinding()]
    param(
        [Parameter()]
        [int]$Port = 18888,

        [Parameter()]
        [int]$TimeoutSeconds = 540,

        [Parameter()]
        [int]$PollIntervalSeconds = 3,

        [Parameter()]
        [string]$Hostname = '127.0.0.1'
    )

    $startTime = Get-Date
    $deadline = $startTime.AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync($Hostname, $Port)
            # 1 s connect attempt; ConnectAsync returns immediately on
            # connection-refused (closed port). If the port IS listening,
            # Wait returns true and Connected is true.
            if ($task.Wait(1000) -and $client.Connected) {
                $elapsed = ((Get-Date) - $startTime).TotalSeconds
                Write-Verbose "MCP port $Port responsive after $([math]::Round($elapsed, 1))s"
                return [PSCustomObject]@{
                    Ready          = $true
                    Port           = $Port
                    ElapsedSeconds = [math]::Round($elapsed, 1)
                }
            }
        }
        catch {
            # AggregateException on connection refused / timeout — not ready yet.
        }
        finally {
            $client.Dispose()
        }
        Start-Sleep -Seconds $PollIntervalSeconds
    }

    $elapsed = ((Get-Date) - $startTime).TotalSeconds
    Write-Verbose "MCP port $Port did not become ready within ${TimeoutSeconds}s"
    return [PSCustomObject]@{
        Ready          = $false
        Port           = $Port
        ElapsedSeconds = [math]::Round($elapsed, 1)
    }
}

# ----------------------------------------------------------------------------
# Test-UCMCPHandlers
# ----------------------------------------------------------------------------
# Count "Registered handler" lines in the host project's UE log. The plugin
# emits one such line per handler via LogUCMCPHandler (MCPHandler.cpp:17),
# so a successful run produces exactly ExpectedCount lines.
#
# Returns a PSCustomObject with Pass (bool), Actual, Expected, and the
# list of handler Names that were found (useful for diff against expected
# when the count is wrong).

function Test-UCMCPHandlers {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [ValidateScript({ Test-Path $_ -PathType Leaf -ErrorAction Stop })]
        [string]$LogPath,

        [Parameter()]
        [int]$ExpectedCount = 32
    )

    $matches = Select-String -Path $LogPath -Pattern "Registered handler '([^']+)'" -AllMatches
    $names = @()
    foreach ($match in $matches) {
        foreach ($m in $match.Matches) {
            $names += $m.Groups[1].Value
        }
    }

    return [PSCustomObject]@{
        Pass     = ($names.Count -eq $ExpectedCount)
        Actual   = $names.Count
        Expected = $ExpectedCount
        LogPath  = $LogPath
        Names    = $names
    }
}

Export-ModuleMember -Function `
    Start-UCMCPEditor, `
    Stop-UCMCPEditor, `
    Wait-UCMCPReady, `
    Test-UCMCPHandlers
