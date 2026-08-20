[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$backendDir = Join-Path $projectDir 'backend-futures-py'
$sessionPath = Join-Path $backendDir 'session_monitor_six_strategy.session'
$requiredFiles = @(
    (Join-Path $backendDir '.env'),
    (Join-Path $backendDir 'monitor_and_trade_six_strategy.py')
)

foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker command was not found. Install and start Docker Desktop first.'
}

if (-not (Test-Path -LiteralPath $sessionPath -PathType Leaf)) {
    [System.IO.File]::WriteAllBytes($sessionPath, [byte[]]@())
}

Push-Location $projectDir
try {
    Write-Host 'Stopping only the six-strategy service...' -ForegroundColor Yellow
    & docker compose stop six-strategy

    Write-Host ''
    Write-Host 'Telegram will ask for phone, login code, and possibly the 2FA password.' -ForegroundColor Cyan
    Write-Host 'Use an international phone number such as +8869xxxxxxxx.' -ForegroundColor Cyan
    Write-Host ''

    & docker compose run --rm --no-deps --entrypoint python six-strategy /app/scripts/initialize_telegram_session.py
    if ($LASTEXITCODE -ne 0) {
        throw "Telegram login failed with exit code: $LASTEXITCODE"
    }

    Write-Host 'Starting six-strategy with the saved Telegram session...' -ForegroundColor Green
    & docker compose up --detach six-strategy
    if ($LASTEXITCODE -ne 0) {
        throw "six-strategy restart failed with exit code: $LASTEXITCODE"
    }

    Write-Host 'Done. Follow logs with: docker compose logs -f six-strategy' -ForegroundColor Green
} finally {
    Pop-Location
}
