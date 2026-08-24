[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backendDir = Join-Path $projectDir 'backend-futures-py'
$strategyDir = Join-Path $backendDir 'h3-ef-012-strategy'
$runtimeDir = Join-Path $strategyDir 'runtime'
$sessionPath = Join-Path $runtimeDir 'session_h3_ef_012.session'
$markerPath = Join-Path $runtimeDir 'session_h3_ef_012.authorized'

foreach ($requiredFile in @(
    (Join-Path $backendDir '.env'),
    (Join-Path $strategyDir 'monitor_and_trade.py')
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker command was not found. Install and start Docker Desktop first.'
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
Push-Location $projectDir
try {
    & docker compose --profile windows stop h3-ef-012-strategy
    & docker compose --profile windows build h3-ef-012-strategy
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed with exit code: $LASTEXITCODE"
    }

    Write-Host 'Telegram will ask for phone, login code, and possibly 2FA.' -ForegroundColor Cyan
    & docker compose --profile windows run --rm --no-deps `
        --entrypoint python `
        -e TELEGRAM_SESSION_PATH=/app/backend-futures-py/h3-ef-012-strategy/runtime/session_h3_ef_012 `
        -e TELEGRAM_SESSION_MARKER=/app/backend-futures-py/h3-ef-012-strategy/runtime/session_h3_ef_012.authorized `
        h3-ef-012-strategy `
        /app/scripts/initialize_telegram_session.py
    if ($LASTEXITCODE -ne 0) {
        throw "Telegram login failed with exit code: $LASTEXITCODE"
    }
    if (-not (Test-Path $sessionPath) -or -not (Test-Path $markerPath)) {
        throw 'Telegram initialization did not create the expected session files.'
    }
    & docker compose --profile windows up --detach h3-ef-012-strategy
    if ($LASTEXITCODE -ne 0) {
        throw "H3+EF service restart failed with exit code: $LASTEXITCODE"
    }
    Write-Host 'Telegram session is ready and H3+EF is running.' -ForegroundColor Green
} finally {
    Pop-Location
}
