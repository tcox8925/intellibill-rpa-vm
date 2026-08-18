param(
    [string]$ProjectDir = $PSScriptRoot,
    [string]$PatientsFile = "$PSScriptRoot\practice_fusion_patients.csv",
    [string]$QueueJson = "$PSScriptRoot\pf_appointment_queue.json",
    [string]$DownloadsDir = "$PSScriptRoot\pf_encounter_pdfs",
    [string]$ChromeUserDataDir = "C:\Users\poorn\pf_rpa_chrome",
    [string]$SourceUserDataDir = "C:\Users\poorn\AppData\Local\Google\Chrome\User Data",
    [string]$SourceProfile = "Profile 11",
    [string]$Practice = "NWARK Internal Medicine",
    [string]$StartDate = "2026-07-25",
    [string]$EndDate = "2026-07-30",
    [int]$Limit = 10,
    [switch]$SkipReportPull,
    [string]$AppointmentsFile = "",
    [switch]$GeneratePdfs
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectDir

$Worker = Join-Path $ProjectDir "pf_soap_sync_v5_16.py"
$PdfConfig = Join-Path $ProjectDir "config\pf_pdf_sync_config.json"
$ReportConfig = Join-Path $ProjectDir "config\pf_appointment_report_config.json"
$ExpectedBuild = "PF-SOAP-SYNC-v5.16.0-batch-appointment-metadata"

if (-not (Test-Path $Worker)) {
    throw "Worker not found: $Worker"
}
if (Select-String -Path $Worker -SimpleMatch "After the dashboard is visible, press ENTER" -Quiet) {
    throw "A legacy worker with the terminal ENTER prompt is still present: $Worker"
}
$ActualBuild = ((& python $Worker version 2>&1) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $ActualBuild -ne $ExpectedBuild) {
    throw "Wrong/stale worker. Expected '$ExpectedBuild' but found '$ActualBuild' at $Worker"
}
Write-Host "Verified worker build: $ActualBuild"

if ([string]::IsNullOrWhiteSpace($env:PF_USERNAME)) {
    throw "PF_USERNAME is not set. Example: `$env:PF_USERNAME='your-login@email.com'"
}
if ([string]::IsNullOrWhiteSpace($env:PF_PASSWORD)) {
    throw "PF_PASSWORD is not set. Set it in this PowerShell session before running."
}
Write-Host "Practice Fusion automated login configured for: $env:PF_USERNAME"

Write-Host "`n[1] Local self-test"
python $Worker self-test
if ($LASTEXITCODE -ne 0) { throw "Self-test failed" }

Write-Host "`n[2] Doctor"
# v5.4: resolve the appointments file before doctor runs so column mapping is validated
# here rather than surfacing two steps later at ingest.
if ($SkipReportPull) {
    if (-not $AppointmentsFile) {
        throw "Supply -AppointmentsFile when -SkipReportPull is used."
    }
} elseif (-not $AppointmentsFile) {
    $AppointmentsFile = Join-Path $ProjectDir "appointments_${StartDate}_to_${EndDate}.csv"
}

$DoctorArgs = @(
    "doctor",
    "--config-json", $PdfConfig,
    "--report-config-json", $ReportConfig,
    "--patients-file", $PatientsFile,
    "--queue-json", $QueueJson,
    "--chrome-user-data-dir", $ChromeUserDataDir
)
if (Test-Path $AppointmentsFile) {
    $DoctorArgs += @("--appointments-file", $AppointmentsFile)
} else {
    Write-Host "Appointment report not present yet; column mapping will be checked after the pull."
}
python $Worker @DoctorArgs
if ($LASTEXITCODE -ne 0) { throw "Doctor failed" }

if (-not $SkipReportPull) {
    Write-Host "`n[3] Appointment report pull"
    Write-Host "Close all normal Chrome windows before the first profile clone."
    python $Worker pull-report `
      --report-config-json $ReportConfig `
      --start-date $StartDate `
      --end-date $EndDate `
      --output-csv $AppointmentsFile `
      --chrome-user-data-dir $ChromeUserDataDir `
      --source-user-data-dir $SourceUserDataDir `
      --source-profile $SourceProfile
    if ($LASTEXITCODE -ne 0) { throw "Report pull failed" }

    Write-Host "`n[3b] Re-check column mapping against the pulled report"
    python $Worker doctor `
      --config-json $PdfConfig `
      --report-config-json $ReportConfig `
      --appointments-file $AppointmentsFile
    if ($LASTEXITCODE -ne 0) { throw "Pulled report failed column mapping" }
}

Write-Host "`n[4] Ingest"
python $Worker ingest `
  --appointments-file $AppointmentsFile `
  --queue-json $QueueJson `
  --practice $Practice `
  --config-json $PdfConfig
if ($LASTEXITCODE -ne 0) { throw "Ingest failed" }

# v5.4: this check used to assert only appointment_date, then merely *print* status,
# type and provider. A blank appointment_status is the most dangerous silent failure in
# the pipeline -- status_matches() returns false on an empty string, so the ignored gate
# never fires and cancelled/no-show appointments get driven through the browser.
# Every field the report carries is now asserted.
$QueueData = Get-Content $QueueJson -Raw | ConvertFrom-Json
$NonIgnored = @($QueueData.rows | Where-Object { $_.status -ne "ignored" })
foreach ($Field in @("appointment_date", "appointment_status", "appointment_type", "provider", "patient_name", "patient_dob")) {
    $Blank = @($NonIgnored | Where-Object { [string]::IsNullOrWhiteSpace([string]$_.$Field) })
    if ($Blank.Count -gt 0) {
        throw "Ingest validation failed: $($Blank.Count) of $($NonIgnored.Count) non-ignored queue rows have blank $Field."
    }
}

# At least one row must have been recognized as cancelled/no-show. If the status column
# ever stops mapping, every row silently becomes actionable; this catches that.
$IgnoredRows = @($QueueData.rows | Where-Object { $_.status -eq "ignored" })
Write-Host ("Ingest: {0} rows total, {1} actionable, {2} ignored by status." -f `
    $QueueData.rows.Count, $NonIgnored.Count, $IgnoredRows.Count)
if ($IgnoredRows.Count -eq 0) {
    Write-Host "WARNING: no rows were ignored by appointment status. Confirm the report genuinely contains no cancellations or no-shows."
}

$SampleRow = $NonIgnored | Select-Object -First 1
if ($null -ne $SampleRow) {
    Write-Host ("Mapped appointment sample: {0} | {1} | {2} | {3}" -f `
        $SampleRow.appointment_date, $SampleRow.appointment_status, `
        $SampleRow.appointment_type, $SampleRow.provider)
}

Write-Host "`n[5] Match patients"
python $Worker match-patients `
  --queue-json $QueueJson `
  --patients-file $PatientsFile
if ($LASTEXITCODE -ne 0) { throw "Patient matching failed" }

# v5.4: chart navigation needs the GUID, not the PRN. Any row that reached "ready"
# without a GUID would fail inside process(), so catch it here instead.
$QueueData = Get-Content $QueueJson -Raw | ConvertFrom-Json
$ReadyNoGuid = @(
    $QueueData.rows | Where-Object {
        $_.status -eq "ready" -and [string]::IsNullOrWhiteSpace([string]$_.ehr_patient_guid)
    }
)
if ($ReadyNoGuid.Count -gt 0) {
    throw "Match validation failed: $($ReadyNoGuid.Count) rows are ready but have no ehr_patient_guid."
}
$ReadyNoPrn = @(
    $QueueData.rows | Where-Object {
        $_.status -eq "ready" -and [string]::IsNullOrWhiteSpace([string]$_.patient_id)
    }
)
if ($ReadyNoPrn.Count -gt 0) {
    Write-Host "NOTE: $($ReadyNoPrn.Count) ready rows have no PRN. These process on the GUID; their PDFs are named using the GUID."
}

Write-Host "`n[6] Queue status"
python $Worker status --queue-json $QueueJson

Write-Host "`n[7] One-record UI dry run"
python $Worker process `
  --queue-json $QueueJson `
  --config-json $PdfConfig `
  --downloads-dir $DownloadsDir `
  --chrome-user-data-dir $ChromeUserDataDir `
  --limit 1 `
  --dry-run
if ($LASTEXITCODE -ne 0) { throw "UI dry run failed" }

if ($GeneratePdfs) {
    Write-Host "`n[8] Generate PDFs for up to $Limit records"
    python $Worker process `
      --queue-json $QueueJson `
      --config-json $PdfConfig `
      --downloads-dir $DownloadsDir `
      --chrome-user-data-dir $ChromeUserDataDir `
      --limit $Limit
    if ($LASTEXITCODE -ne 0) { throw "PDF processing failed" }
}

Write-Host "`nTest sequence completed."
