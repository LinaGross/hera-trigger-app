param(
    [string]$CommandDir = "E:\Jiayi\NISZBridge\commands",
    [string]$WindowTitleContains = "NIS",
    [string]$RunHotkey = "{F4}",
    [string]$StopFile = "E:\Jiayi\NISZBridge\stop_hotkey_runner.txt",
    [int]$PollMilliseconds = 250,
    [int]$DebounceMilliseconds = 1000
)

Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms

$seen = @{}
$lastHotkeyAt = Get-Date "2000-01-01"

Write-Host "Starting macro hotkey runner. WindowTitleContains='$WindowTitleContains' Hotkey='$RunHotkey'"
Write-Host "Watching: $CommandDir"
Write-Host "Stop file: $StopFile"

function Get-NisWindowProcess {
    Get-Process |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            $_.MainWindowTitle -like "*$WindowTitleContains*"
        } |
        Select-Object -First 1
}

function Send-NisRunHotkey {
    param([string]$CommandName)

    $process = Get-NisWindowProcess
    if (-not $process) {
        Write-Host "$(Get-Date -Format s) Command '$CommandName' is waiting, but no NIS window containing '$WindowTitleContains' was found."
        return $false
    }

    [Microsoft.VisualBasic.Interaction]::AppActivate($process.Id) | Out-Null
    Start-Sleep -Milliseconds 200
    [System.Windows.Forms.SendKeys]::SendWait($RunHotkey)
    Write-Host "$(Get-Date -Format s) Command '$CommandName' found. Sent $RunHotkey to '$($process.MainWindowTitle)'."
    return $true
}

while ($true) {
    if (Test-Path -LiteralPath $StopFile) {
        Write-Host "$(Get-Date -Format s) Stop file found. Exiting macro hotkey runner."
        break
    }

    if (-not (Test-Path -LiteralPath $CommandDir)) {
        Start-Sleep -Milliseconds $PollMilliseconds
        continue
    }

    $commands = Get-ChildItem -LiteralPath $CommandDir -Filter "*.txt" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc, Name

    foreach ($command in $commands) {
        $key = $command.FullName
        $stamp = $command.LastWriteTimeUtc.Ticks
        if ($seen.ContainsKey($key) -and $seen[$key] -eq $stamp) {
            continue
        }

        $elapsed = ((Get-Date) - $lastHotkeyAt).TotalMilliseconds
        if ($elapsed -lt $DebounceMilliseconds) {
            Start-Sleep -Milliseconds ([int]($DebounceMilliseconds - $elapsed))
        }

        if (Send-NisRunHotkey -CommandName $command.Name) {
            $seen[$key] = $stamp
            $lastHotkeyAt = Get-Date
        }
    }

    Start-Sleep -Milliseconds $PollMilliseconds
}
