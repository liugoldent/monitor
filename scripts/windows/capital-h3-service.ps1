[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Check')][string]$Action = 'Check')
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$strategyDir = Join-Path $projectDir 'backend-futures-py\h3-capital-flatten-reentry-strategy'
$pythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$scriptPath = Join-Path $strategyDir 'h_monitor.py'
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
    Write-Host 'H3 Capital service stopped. Existing broker positions are unchanged.'
    return
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'H3 Capital Python runtime is missing.' }
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw 'H3 Capital strategy script is missing.' }
& $pythonPath $scriptPath --check-config
if ($LASTEXITCODE -ne 0) { throw 'H3 Capital configuration validation failed.' }
if ($Action -eq 'Check') { return }
if ($ownedProcess) {
    Write-Host "H3 Capital service is already running (PID $($ownedProcess.ProcessId))."
    return
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$stdoutPath = Join-Path $runtimeDir 'windows-service.log'
$stderrPath = Join-Path $runtimeDir 'windows-service-error.log'
$previousUtf8 = $env:PYTHONUTF8
try {
    $env:PYTHONUTF8 = '1'
    $serviceArgs = @('-u', ('"{0}"' -f $scriptPath), '--live', '--service')
    $process = Start-Process -FilePath $pythonPath -ArgumentList $serviceArgs -WorkingDirectory $strategyDir `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    @{ pid = $process.Id; script = $scriptPath; startedAt = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) { throw "H3 Capital service exited; see $stderrPath" }
    Write-Host "H3 Capital service launched (PID $($process.Id)); broker startup is recorded in $stdoutPath"
} finally { $env:PYTHONUTF8 = $previousUtf8 }
