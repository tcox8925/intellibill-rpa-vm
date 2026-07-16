<#
    register_centene_pch_task.ps1
    -----------------------------
    Registers the "Centene PCH File Tracker" Windows Scheduled Task.

    Fires at 04:00 AND 05:00 UTC daily. The wrapper (run_centene_pch_tracker.ps1)
    then runs the Python job only during the 11 PM Central hour, so the job
    executes exactly once per day at 11 PM Central year-round — no manual
    change needed at DST transitions.

    Run this ONCE, in an ELEVATED PowerShell (Run as Administrator), on the VM.
    Edit $WrapperPath if you place the wrapper somewhere other than the script dir.
#>

$ErrorActionPreference = "Stop"

# ─── Config — EDIT to match the VM ──────────────────────────────────────────
$TaskName    = "Centene PCH File Tracker"
$WrapperPath = "C:\Users\myopsadmin\Documents\RPA\pch\run_centene_pch_tracker.ps1"
# Account to run under. SYSTEM has no network creds for Azure; use the account
# that owns the Azure CLI / DefaultAzureCredential login. Adjust as needed.
$RunAsUser   = "$env:USERDOMAIN\$env:USERNAME"
# ────────────────────────────────────────────────────────────────────────────

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WrapperPath`""

# Two daily triggers in the VM's local (UTC) time — covers both CST and CDT.
$trigger0400 = New-ScheduledTaskTrigger -Daily -At 4:00AM
$trigger0500 = New-ScheduledTaskTrigger -Daily -At 5:00AM

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

# -RunLevel Highest if the account needs elevation; drop if not required.
$principal = New-ScheduledTaskPrincipal `
    -UserId $RunAsUser `
    -LogonType S4U `
    -RunLevel Limited

# Remove any prior version so this is idempotent.
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger @($trigger0400, $trigger0500) `
    -Settings $settings `
    -Principal $principal `
    -Description "Runs centene_pch_file_tracker.py at 11 PM Central (dual UTC triggers + wrapper time-guard for DST safety)."

Write-Output "Registered task '$TaskName'."
Write-Output "Triggers: 04:00 and 05:00 UTC daily. Wrapper runs the job only during the 11 PM Central hour."
Write-Output "Test now with:  Start-ScheduledTask -TaskName `"$TaskName`""
