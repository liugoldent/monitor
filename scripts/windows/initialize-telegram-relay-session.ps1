[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backendDir = Join-Path $projectDir 'backend-futures-py'
$runtimeDir = Join-Path $backendDir 'telegram-relay-runtime'
$sessionPath = Join-Path $runtimeDir 'session_h_ef_relay.session'
$markerPath = Join-Path $runtimeDir 'session_h_ef_relay.authorized'

foreach ($requiredFile in @(
    (Join-Path $backendDir '.env'),
    (Join-Path $backendDir 'telegram_signal_relay.py')
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
    & docker compose stop telegram-signal-relay
    & docker compose build telegram-signal-relay
    if ($LASTEXITCODE -ne 0) {
        throw "Docker build failed with exit code: $LASTEXITCODE"
    }

    Write-Host 'Telegram will ask for phone, login code, and possibly 2FA.' -ForegroundColor Cyan
    & docker compose --profile windows run --rm --no-deps `
        --entrypoint python `
        -e TELEGRAM_SESSION_PATH=/app/backend-futures-py/telegram-relay-runtime/session_h_ef_relay `
        -e TELEGRAM_SESSION_MARKER=/app/backend-futures-py/telegram-relay-runtime/session_h_ef_relay.authorized `
        telegram-signal-relay `
        /app/scripts/initialize_telegram_session.py
    if ($LASTEXITCODE -ne 0) {
        throw "Telegram login failed with exit code: $LASTEXITCODE"
    }
    if (-not (Test-Path $sessionPath) -or -not (Test-Path $markerPath)) {
        throw 'Telegram initialization did not create the expected session files.'
    }
    & docker compose up --detach telegram-signal-relay
    if ($LASTEXITCODE -ne 0) {
        throw "Telegram relay restart failed with exit code: $LASTEXITCODE"
    }
    Write-Host 'Telegram session is ready and the signal relay is running.' -ForegroundColor Green
} finally {
    Pop-Location
}
