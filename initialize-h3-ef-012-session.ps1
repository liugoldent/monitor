[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$backendDir = Join-Path $projectDir 'backend-futures-py'
$strategyDir = Join-Path $backendDir 'h3-ef-012-strategy'
$runtimeDir = Join-Path $strategyDir 'runtime'
$sessionPath = Join-Path $runtimeDir 'session_h3_ef_012.session'
$sessionMarkerPath = Join-Path $runtimeDir 'session_h3_ef_012.authorized'
$requiredFiles = @(
    (Join-Path $backendDir '.env'),
    (Join-Path $strategyDir 'monitor_and_trade.py')
)

foreach ($requiredFile in $requiredFiles) {
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
    Write-Host 'Stopping only the H3+EF 0/U/2U service...' -ForegroundColor Yellow
    & docker compose stop h3-ef-012-strategy

    Write-Host 'Building the H3+EF Docker image...' -ForegroundColor Yellow
    & docker compose build h3-ef-012-strategy
    if ($LASTEXITCODE -ne 0) {
        throw "h3-ef-012-strategy build failed with exit code: $LASTEXITCODE"
    }

    Write-Host ''
    Write-Host 'Telegram will ask for phone, login code, and possibly the 2FA password.' -ForegroundColor Cyan
    Write-Host 'Use an international phone number such as +8869xxxxxxxx.' -ForegroundColor Cyan
    Write-Host ''

    & docker compose run --rm --no-deps `
        --entrypoint python `
        -e TELEGRAM_SESSION_PATH=/app/backend-futures-py/h3-ef-012-strategy/runtime/session_h3_ef_012 `
        -e TELEGRAM_SESSION_MARKER=/app/backend-futures-py/h3-ef-012-strategy/runtime/session_h3_ef_012.authorized `
        h3-ef-012-strategy `
        /app/scripts/initialize_telegram_session.py
    if ($LASTEXITCODE -ne 0) {
        throw "Telegram login failed with exit code: $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath $sessionPath -PathType Leaf)) {
        throw "Telegram login did not create the expected session: $sessionPath"
    }
    if (-not (Test-Path -LiteralPath $sessionMarkerPath -PathType Leaf)) {
        throw "Telegram login did not create the authorization marker: $sessionMarkerPath"
    }

    Write-Host 'Starting H3+EF 0/U/2U with the saved Telegram session...' -ForegroundColor Green
    & docker compose up --detach h3-ef-012-strategy
    if ($LASTEXITCODE -ne 0) {
        throw "h3-ef-012-strategy restart failed with exit code: $LASTEXITCODE"
    }

    Write-Host 'Done. Follow logs with: docker compose logs -f h3-ef-012-strategy' -ForegroundColor Green
} finally {
    Pop-Location
}
