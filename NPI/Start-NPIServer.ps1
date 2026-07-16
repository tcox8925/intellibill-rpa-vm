<#
.SYNOPSIS
    Launches the NPI FastAPI server (uvicorn on port 8000) with auto-restart and logging.
    Designed to be triggered by Windows Task Scheduler on VM startup.

.DESCRIPTION
    - Changes to the NPI project directory
    - Starts uvicorn in a loop so it auto-restarts on crash
    - Logs stdout/stderr to a daily rotating log file
    - Waits 10 seconds between restarts to avoid tight crash loops
#>

# ── CONFIG ───────────────────────────────────────────────────────────────────
$ProjectDir   = "C:\Users\myopsadmin\Documents\RPA\NPI"
$PythonExe    = "python"
$Host_         = "0.0.0.0"
$Port          = 8000
$LogDir        = "$ProjectDir\logs"
$RestartDelay  = 10
$MaxRestarts   = 50
# ─────────────────────────────────────────────────────────────────────────────

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

Set-Location $ProjectDir

$restartCount = 0

while ($restartCount -lt $MaxRestarts) {
    $timestamp = Get-Date -Format "yyyy-MM-dd"
    $logFile   = Join-Path $LogDir "npi_server_$timestamp.log"

    $startMsg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Starting NPI server (restart #$restartCount)..."
    Add-Content -Path $logFile -Value $startMsg
    Write-Host $startMsg

    try {
        $process = Start-Process -FilePath $PythonExe `
            -ArgumentList "-m", "uvicorn", "server:app", "--host", $Host_, "--port", $Port `
            -WorkingDirectory $ProjectDir `
            -NoNewWindow `
            -RedirectStandardOutput "$LogDir\npi_stdout_$timestamp.log" `
            -RedirectStandardError  "$LogDir\npi_stderr_$timestamp.log" `
            -PassThru

        $process.WaitForExit()

        $exitCode = $process.ExitCode
        $exitMsg  = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Server exited with code $exitCode"
        Add-Content -Path $logFile -Value $exitMsg
        Write-Host $exitMsg
    }
    catch {
        $errMsg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Failed to start: $_"
        Add-Content -Path $logFile -Value $errMsg
        Write-Host $errMsg
    }

    $restartCount++

    if ($restartCount -lt $MaxRestarts) {
        $waitMsg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Restarting in $RestartDelay seconds..."
        Add-Content -Path $logFile -Value $waitMsg
        Write-Host $waitMsg
        Start-Sleep -Seconds $RestartDelay
    }
}

$finalMsg = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | Max restarts ($MaxRestarts) reached. Stopping."
$logFile  = Join-Path $LogDir "npi_server_$(Get-Date -Format 'yyyy-MM-dd').log"
Add-Content -Path $logFile -Value $finalMsg
Write-Host $finalMsg
