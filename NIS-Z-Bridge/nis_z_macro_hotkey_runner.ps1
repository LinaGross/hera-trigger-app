param(
    [string]$CommandDir = "E:\Jiayi\NISZBridge\commands",
    [string]$ResponseDir = "E:\Jiayi\NISZBridge\responses",
    [string]$WindowTitleContains = "NIS-Elements",
    [string]$RunHotkey = "{F4}",
    [string]$StopFile = "E:\Jiayi\NISZBridge\stop_hotkey_runner.txt",
    [int]$PollMilliseconds = 250,
    [int]$DebounceMilliseconds = 1000,
    [int]$RetrySeconds = 3,
    [int]$MaxCommandAgeSeconds = 180,
    [string[]]$ExcludeTitleContains = @(
        "Visual Studio Code",
        "Windows PowerShell",
        "PowerShell",
        "Windows Terminal",
        "Command Prompt",
        "cmd.exe"
    )
)

Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Windows.Forms

$lastSent = @{}
$lastHotkeyAt = Get-Date "2000-01-01"

Write-Host "Starting macro hotkey runner. WindowTitleContains='$WindowTitleContains' Hotkey='$RunHotkey'"
Write-Host "Watching: $CommandDir"
Write-Host "Responses: $ResponseDir"
Write-Host "Stop file: $StopFile"

function Get-ResponsePathForCommand {
    param([string]$CommandName)

    $stem = [System.IO.Path]::GetFileNameWithoutExtension($CommandName)
    switch ($stem) {
        "current_getz" { return Join-Path $ResponseDir "current_getz_response.txt" }
        "current_move_rel_custom" { return Join-Path $ResponseDir "current_move_rel_custom_response.txt" }
        "current_move_abs_4100_4050_7000" { return Join-Path $ResponseDir "current_move_abs_4100_4050_7000_response.txt" }
        "current_move_abs_4200_4000_8100" { return Join-Path $ResponseDir "current_move_abs_4200_4000_8100_response.txt" }
        "current_stop" { return Join-Path $ResponseDir "current_stop_response.txt" }
        default { return $null }
    }
}

function Get-NisWindowProcess {
    $matches = Get-Process |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            $_.MainWindowTitle -like "*$WindowTitleContains*"
        }

    $eligible = $matches | Where-Object {
        $title = $_.MainWindowTitle
        -not ($ExcludeTitleContains | Where-Object { $title -like "*$_*" })
    }

    $selected = $eligible | Sort-Object @{
        Expression = {
            if ($_.MainWindowTitle -like "*NIS-Elements*") { 0 } else { 1 }
        }
    }, MainWindowTitle | Select-Object -First 1

    if (-not $selected -and $matches) {
        $ignored = ($matches | Select-Object -ExpandProperty MainWindowTitle) -join " | "
        Write-Host "$(Get-Date -Format s) Ignored matching non-NIS windows: $ignored"
    }

    return $selected
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

        $ageSeconds = ((Get-Date).ToUniversalTime() - $command.LastWriteTimeUtc).TotalSeconds
        if ($ageSeconds -gt $MaxCommandAgeSeconds) {
            continue
        }

        $responsePath = Get-ResponsePathForCommand -CommandName $command.Name
        if ($responsePath -and (Test-Path -LiteralPath $responsePath)) {
            continue
        }

        if ($lastSent.ContainsKey($key)) {
            $retryElapsed = ((Get-Date) - $lastSent[$key]).TotalSeconds
            if ($retryElapsed -lt $RetrySeconds) {
                continue
            }
        }

        $elapsed = ((Get-Date) - $lastHotkeyAt).TotalMilliseconds
        if ($elapsed -lt $DebounceMilliseconds) {
            Start-Sleep -Milliseconds ([int]($DebounceMilliseconds - $elapsed))
        }

        if (Send-NisRunHotkey -CommandName $command.Name) {
            $now = Get-Date
            $lastSent[$key] = $now
            $lastHotkeyAt = $now
        }
    }

    Start-Sleep -Milliseconds $PollMilliseconds
}
