<#
.SYNOPSIS
    One-time setup: registers the NPI server as a Windows Scheduled Task
    that launches automatically at system startup.

.DESCRIPTION
    Run this script once (as Administrator) to create the task.
    After that, the server will auto-start on every boot.

    To remove:  Unregister-ScheduledTask -TaskName "NPI FastAPI Server" -Confirm:$false
    To disable: Disable-ScheduledTask  -TaskName "NPI FastAPI Server"
    To re-enable: Enable-ScheduledTask -TaskName "NPI FastAPI Server"
#>

# ── CONFIG ───────────────────────────────────────────────────────────────────
$TaskName    = "NPI FastAPI Server"
$ScriptPath  = "C:\Users\myopsadmin\Documents\RPA\NPI\Start-NPIServer.ps1"
$RunAsUser   = "myopsadmin"
# ─────────────────────────────────────────────────────────────────────────────

$trigger = New-ScheduledTaskTrigger -AtStartup

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`""

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 3 `
    -ExecutionTimeLimit (New-TimeSpan -Days 365)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $trigger `
    -Action $action `
    -Settings $settings `
    -User $RunAsUser `
    -RunLevel Highest `
    -Description "Auto-starts the NPI FastAPI server (uvicorn port 8000) on VM boot with auto-restart on crash."

Write-Host ""
Write-Host "Task '$TaskName' registered successfully." -ForegroundColor Green
Write-Host "The server will now start automatically on every VM boot."
Write-Host ""
Write-Host "Manual controls:"
Write-Host "  Start now:   Start-ScheduledTask  -TaskName '$TaskName'"
Write-Host "  Stop:        Stop-ScheduledTask   -TaskName '$TaskName'"
Write-Host "  Disable:     Disable-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Remove:      Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
