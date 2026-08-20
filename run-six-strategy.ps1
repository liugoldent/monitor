[CmdletBinding()]
param(
    [switch]$Background,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$backendDir = Join-Path $projectDir 'backend-futures-py'
$requiredFiles = @(
    (Join-Path $backendDir '.env'),
    (Join-Path $backendDir 'Sinopac.pfx'),
    (Join-Path $backendDir 'session_monitor.session')
)

foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker command was not found. Install Docker Desktop first.'
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Engine is not running. Start Docker Desktop and wait until it reports Running.'
}

Push-Location $projectDir
try {
    $composeArgs = @('compose', 'up')
    if ($Background) {
        $composeArgs += '-d'
    }
    if (-not $NoBuild) {
        $composeArgs += '--build'
    }
    $composeArgs += 'six-strategy'

    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code: $LASTEXITCODE"
    }

    if ($Background) {
        Write-Host 'Six-strategy monitor is running in the background.'
        Write-Host 'Logs: docker compose logs -f six-strategy'
        Write-Host 'CSV: backend-futures-py\tv_doc\six_strategy_signal_events.csv'
    }
} finally {
    Pop-Location
}
