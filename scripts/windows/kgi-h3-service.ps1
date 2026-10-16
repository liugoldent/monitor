[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Check', 'Paper', 'Diagnose')][string]$Action = 'Paper',
    [switch]$StartupSimulationBuyOne,
    [switch]$StartupProductionBuyOne
)
$ErrorActionPreference = 'Stop'
if ($StartupSimulationBuyOne -and $Action -notin @('Start', 'Check')) {
    throw '-StartupSimulationBuyOne is supported only with -Action Start or Check.'
}
if ($StartupProductionBuyOne -and $Action -notin @('Start', 'Check')) {
    throw '-StartupProductionBuyOne is supported only with -Action Start or Check.'
}
if ($StartupSimulationBuyOne -and $StartupProductionBuyOne) {
    throw 'Choose only one startup-test environment.'
}
$startupTestArgs = @()
if ($StartupSimulationBuyOne) { $startupTestArgs += '--startup-simulation-buy-one' }
if ($StartupProductionBuyOne) { $startupTestArgs += '--startup-production-buy-one' }
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$strategyDir = Join-Path $projectDir 'backend-futures-py\h3-kgi-flatten-reentry-strategy'
$pythonPath = Join-Path $strategyDir '.venv\Scripts\python.exe'
$scriptPath = Join-Path $strategyDir 'kgi_monitor.py'
$runtimeDir = Join-Path $strategyDir 'runtime'
$pidPath = Join-Path $runtimeDir 'windows-service.json'
function Get-OwnedProcesses {
    # Windows venv Python may be a launcher with a separate base-Python worker.
    # Include an orphaned worker so Stop/Start cannot leave the old trader alive.
    $basePythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
        $_.ExecutablePath -in @($pythonPath, $basePythonPath) -and
        $_.CommandLine -like "*$scriptPath*" -and $_.CommandLine -like '*--service*' -and
        $_.CommandLine -like '*--live*'
    })
}
$ownedProcesses = @(Get-OwnedProcesses)
$ownedProcess = $ownedProcesses | Select-Object -First 1
if ($Action -eq 'Stop') {
    $ownedIds = @($ownedProcesses | ForEach-Object { $_.ProcessId })
    foreach ($owned in ($ownedProcesses | Sort-Object { if ($_.ParentProcessId -in $ownedIds) { 0 } else { 1 } })) {
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$owned.ProcessId)" -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.CreationDate -eq $owned.CreationDate -and
            $candidate.ExecutablePath -eq $owned.ExecutablePath -and $candidate.CommandLine -eq $owned.CommandLine) {
            Stop-Process -Id $candidate.ProcessId -ErrorAction Stop
            Wait-Process -Id $candidate.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
        }
    }
    if (@(Get-OwnedProcesses).Count) { throw 'H3 KGI still has an owned worker; do not launch a second trader.' }
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
    & $pythonPath $scriptPath --check-config @startupTestArgs
    if ($LASTEXITCODE -ne 0) { throw 'H3 KGI configuration validation failed.' }
    if ($Action -eq 'Check') { return }
    if ($ownedProcess) {
        Write-Host "H3 KGI service is already running (PID $($ownedProcess.ProcessId))."
        return
    }
    if ($StartupProductionBuyOne) {
        # This path stays in the human operator's foreground console. Never pass
        # --service or pre-fill the confirmation, and never auto-restart it.
        Write-Host 'Manual production test: foreground only; review the account/contract and type the confirmation yourself.'
        & $pythonPath -u $scriptPath --live @startupTestArgs
        if ($LASTEXITCODE -ne 0) { throw 'Manual production test stopped; review the broker response before retrying.' }
        return
    }
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    $stdoutPath = Join-Path $runtimeDir 'windows-service.log'
    $stderrPath = Join-Path $runtimeDir 'windows-service-error.log'
    $serviceArgs = @('-u', ('"{0}"' -f $scriptPath), '--live', '--service')
    $serviceArgs += $startupTestArgs
    $process = Start-Process -FilePath $pythonPath -ArgumentList $serviceArgs -WorkingDirectory $strategyDir `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    @{ pid = $process.Id; script = $scriptPath; startedAt = (Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
    Start-Sleep -Seconds 2
    $process.Refresh()
    if ($process.HasExited) { throw "H3 KGI service exited; see $stderrPath" }
    Write-Host "H3 KGI launched (PID $($process.Id)); broker startup is recorded in $stdoutPath"
} finally { $env:PYTHONUTF8 = $previousUtf8 }
