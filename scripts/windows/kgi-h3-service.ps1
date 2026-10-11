[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Check', 'Paper', 'Diagnose')][string]$Action = 'Paper')
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$strategyDir = Join-Path $projectDir 'backend-futures-py\h3-kgi-flatten-reentry-strategy'
$pythonPath = Join-Path $strategyDir '.venv\Scripts\python.exe'
$scriptPath = Join-Path $strategyDir 'kgi_monitor.py'
$runtimeDir = Join-Path $strategyDir 'runtime'
$pidPath = Join-Path $runtimeDir 'windows-service.json'
function Get-OwnedProcess {
    if (-not (Test-Path -LiteralPath $pidPath)) { return $null }
    $record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)" -ErrorAction SilentlyContinue
    if ($candidate -and $candidate.ExecutablePath -eq $pythonPath -and
        $candidate.CommandLine -like "*$scriptPath*" -and $candidate.CommandLine -like '*--service*' -and
        $candidate.CommandLine -like '*--live*') { return $candidate }
    return $null
}
$ownedProcess = Get-OwnedProcess
if ($Action -eq 'Stop') {
    if ($ownedProcess) { Stop-Process -Id $ownedProcess.ProcessId -ErrorAction Stop }
    Write-Host 'H3 KGI service stopped. Existing broker positions and orders are unchanged.'
    return
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Run scripts\windows\setup-kgi-h3.ps1 to install the isolated KGI runtime first.'
}
$previousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    if ($Action -eq 'Paper') { & $pythonPath -u $scriptPath; exit $LASTEXITCODE }
    if ($Action -eq 'Diagnose') { & $pythonPath -u $scriptPath --diagnose; exit $LASTEXITCODE }
    & $pythonPath $scriptPath --check-config
    if ($LASTEXITCODE -ne 0) { throw 'H3 KGI configuration validation failed.' }
    if ($Action -eq 'Check') { return }
    if ($ownedProcess) {
        Write-Host "H3 KGI service is already running (PID $($ownedProcess.ProcessId))."
        return
    }
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $stdoutPath = Join-Path $runtimeDir 'windows-service.log'
    $stderrPath = Join-Path $runtimeDir 'windows-service-error.log'
    $serviceArgs = @('-u', ('"{0}"' -f $scriptPath), '--live', '--service')
    $process = Start-Process -FilePath $pythonPath -ArgumentList $serviceArgs -WorkingDirectory $strategyDir `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    @{ pid = $process.Id; script = $scriptPath; startedAt = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) { throw "H3 KGI service exited; see $stderrPath" }
    Write-Host "H3 KGI launched (PID $($process.Id)); broker startup is recorded in $stdoutPath"
} finally { $env:PYTHONUTF8 = $previousUtf8 }
