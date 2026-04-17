param(
    [string]$PythonExe = "python",
    [int]$RequiredConsecutive = 10,
    [string]$RequiredEvidenceReports = "",
    [string]$EvidenceBundleFile = "artifacts\\p0-release-evidence-bundle-release-gate.json",
    [string]$BurnInFile = "artifacts\\p0-burnin-consecutive-green-release-gate.json",
    [string]$RunbookContractFile = "artifacts\\p0-runbook-contract-check-release-gate.json",
    [string]$OutputFile = "artifacts\\p0-closure-report-release-gate.json",
    [switch]$AllowNotReady
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$arguments = @(
    ".\\scripts\\p0-closure-report.py",
    "--label", "manual",
    "--required-consecutive", [string]$RequiredConsecutive,
    "--evidence-bundle-file", $EvidenceBundleFile,
    "--burnin-file", $BurnInFile,
    "--runbook-contract-file", $RunbookContractFile,
    "--output-file", $OutputFile
)
if (-not [string]::IsNullOrWhiteSpace($RequiredEvidenceReports)) {
    $arguments += @("--required-evidence-reports", $RequiredEvidenceReports)
}
if ($AllowNotReady) {
    $arguments += "--allow-not-ready"
}

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "P0 closure report failed with exit code $LASTEXITCODE."
}

Write-Host "P0 closure report check passed." -ForegroundColor Green
