<#
    run_centene_pch_tracker.ps1
    ---------------------------
    Wrapper to run centene_pch_file_tracker.py at 11:00 PM America/Chicago
    on a VM whose clock is UTC.

    Why a wrapper (and not just a raw scheduled time):
      11 PM Central is 05:00 UTC during CST (winter) but 04:00 UTC during
      CDT (summer). Windows Task Scheduler fires triggers in the VM's LOCAL
      time (UTC here) and does NOT DST-shift for other time zones, so a
      single hardcoded UTC time would drift by an hour half the year.

      Solution: the scheduled task fires at BOTH 04:00 and 05:00 UTC, and
      this wrapper checks the *actual current Central time* and only runs
      the Python job when it is the 11 PM Central hour. The non-matching
      trigger each day becomes a cheap no-op.

    Adjust $PythonExe and $ScriptDir below to match the VM.

    NOTE: This file is intentionally pure ASCII (no box-drawing chars, no
    em-dashes). Windows PowerShell 5.1 reads BOM-less files as the ANSI code
    page; multibyte Unicode in comments can corrupt on a bad re-save and break
    parsing. Keep it ASCII, or save as UTF-8 *with BOM*, to stay safe.
#>

$ErrorActionPreference = "Stop"

# --- Config - EDIT THESE to match the VM ------------------------------------
$PythonExe  = "C:\Users\myopsadmin\AppData\Local\Programs\Python\Python312\python.exe"
$ScriptDir  = "C:\Users\myopsadmin\Documents\RPA\pch"
$ScriptName = "centene_pch_file_tracker.py"
$LogDir     = Join-Path $ScriptDir "logs_tracker"
# ----------------------------------------------------------------------------

# Current time in Central, derived from UTC (DST handled by the OS tz database)
$centralTz  = [System.TimeZoneInfo]::FindSystemTimeZoneById("Central Standard Time")
$utcNow     = [System.DateTime]::UtcNow
$centralNow = [System.TimeZoneInfo]::ConvertTimeFromUtc($utcNow, $centralTz)

# Only proceed during the 11 PM Central hour. Both UTC triggers (04:00 and
# 05:00) reach this point; exactly one of them lands inside hour 23 Central
# on any given day, so the job runs once per day year-round.
if ($centralNow.Hour -ne 23) {
    Write-Output "$(Get-Date -Format o)  Central hour is $($centralNow.Hour), not 23 - skipping (no-op trigger)."
    exit 0
}

# Ensure log directory exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$stamp      = $centralNow.ToString("yyyy-MM-dd")
$logFile    = Join-Path $LogDir "tracker_$stamp.log"
$scriptPath = Join-Path $ScriptDir $ScriptName

Write-Output "$(Get-Date -Format o)  Central time $($centralNow.ToString('yyyy-MM-dd HH:mm')) - running $ScriptName" | Tee-Object -FilePath $logFile -Append

# Run from the script directory so the backfill/tracker's relative imports and
# CWD-relative paths resolve the same way they do when run by hand.
Push-Location $ScriptDir
try {
    & $PythonExe $scriptPath *>&1 | Tee-Object -FilePath $logFile -Append
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

Write-Output "$(Get-Date -Format o)  Python exited with code $exitCode" | Tee-Object -FilePath $logFile -Append
exit $exitCode