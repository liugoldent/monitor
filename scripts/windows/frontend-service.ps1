[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Start', 'Stop')][string]$Action,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$frontendDir = Join-Path $projectDir 'tx-trading-system'
$serverPath = Join-Path $frontendDir 'server\index.mjs'
$runtimeDir = Join-Path $frontendDir '.runtime'
$statePath = Join-Path $runtimeDir 'service.json'
$url = 'http://127.0.0.1:5173'

function Get-OwnedFrontendProcess {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    try {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $process = Get-Process -Id ([int]$state.ProcessId) -ErrorAction SilentlyContinue
        if (-not $process) { return $null }
        # Verify both creation time and absolute command path; a reused PID is not ownership.
        if ($process.StartTime.ToUniversalTime().ToString('o') -ne $state.StartedAt) { return $null }
        $details = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $process.Id)
        if ($details.Name -ne 'node.exe' -or
            $details.CommandLine -notmatch [regex]::Escape($serverPath) -or
            $details.CommandLine -notmatch '--production') { return $null }
        return $process
    } catch { throw "Could not verify the frontend process. No process was stopped: $($_.Exception.Message)" }
}

function Stop-Frontend {
    $process = Get-OwnedFrontendProcess
    if ($process) {
        Stop-Process -Id $process.Id -ErrorAction Stop
        $process.WaitForExit(10000) | Out-Null
        if (-not $process.HasExited) { throw 'TX frontend did not stop within 10 seconds.' }
        Write-Host 'TX Trading frontend stopped.' -ForegroundColor Green
    } else {
        Write-Host 'TX Trading frontend is not running under this launcher.' -ForegroundColor Cyan
    }
    if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
}

if ($Action -eq 'Stop') { Stop-Frontend; return }
if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) { throw "Frontend entry point is missing: $serverPath" }
$node = Get-Command node.exe -ErrorAction SilentlyContinue
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $node -or -not $npm) { throw 'Node.js and npm are required for TX Trading. Install Node.js 22 LTS first.' }
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

# A running managed frontend is replaced after building succeeds, so failed builds
# leave the existing viewer available.
$ownedProcess = Get-OwnedFrontendProcess
$listener = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)
if ($listener.Count -and @($listener | Where-Object { -not $ownedProcess -or $_.OwningProcess -ne $ownedProcess.Id }).Count) {
    throw 'Port 5173 is already used by another process. Stop a manually launched TX frontend (Ctrl+C), then run again. No unrelated process was stopped.'
}

Push-Location $frontendDir
try {
    $lockPath = Join-Path $frontendDir 'package-lock.json'
    $dependencyStamp = Join-Path $runtimeDir 'dependencies.sha256'
    if (-not $NoBuild) {
        if (-not (Test-Path -LiteralPath $lockPath)) { throw 'Frontend package-lock.json is missing.' }
        $lockHash = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash
        $previousHash = if (Test-Path -LiteralPath $dependencyStamp) { (Get-Content -LiteralPath $dependencyStamp -Raw).Trim() } else { '' }
        if ($previousHash -ne $lockHash -or -not (Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules\vite\bin\vite.js'))) {
            Write-Host 'Installing TX Trading dependencies...' -ForegroundColor Cyan
            & $npm.Source ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed: $LASTEXITCODE" }
            Set-Content -LiteralPath $dependencyStamp -Value $lockHash -Encoding ASCII
        }
        Write-Host 'Building TX Trading frontend...' -ForegroundColor Cyan
        & $npm.Source run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed: $LASTEXITCODE" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendDir 'dist\index.html'))) {
        throw 'Frontend dist/index.html is missing. Run the launcher without -NoBuild first.'
    }
    Stop-Frontend
    $previousPort = $env:PORT
    try {
        $env:PORT = '5173'
        $process = Start-Process -FilePath $node.Source -ArgumentList @(('"{0}"' -f $serverPath), '--production') `
            -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runtimeDir 'stdout.log') `
            -RedirectStandardError (Join-Path $runtimeDir 'stderr.log')
    } finally { $env:PORT = $previousPort }
    $state = @{ ProcessId = $process.Id; StartedAt = $process.StartTime.ToUniversalTime().ToString('o') }
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    $ready = $false
    foreach ($attempt in 1..30) {
        if ($process.HasExited) { break }
        try {
            $health = Invoke-RestMethod "$url/api/health" -TimeoutSec 1
            if ($health.app -eq 'tx-trading-system') { $ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 300
    }
    if (-not $ready) {
        Stop-Frontend
        throw "TX frontend failed to start. Check $runtimeDir\stderr.log"
    }
    Write-Host "TX Trading frontend ready: $url/strategies" -ForegroundColor Green
    Write-Host "Frontend logs: $runtimeDir" -ForegroundColor Cyan
} finally { Pop-Location }
