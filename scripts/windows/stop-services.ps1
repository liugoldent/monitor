[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$nativeErrors = @()
foreach ($nativeScript in @('capital-h3-service.ps1', 'kgi-h3-service.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $nativeScript))) { continue }
    try { & (Join-Path $PSScriptRoot $nativeScript) -Action Stop }
    catch { $nativeErrors += "${nativeScript}: $($_.Exception.Message)" }
}
$services = @(
    'h3-signal-router',
    'telegram-signal-relay',
    'monitor-mxf',
    'webhook-server',
    'ef-strong-consensus-morning-flat-strategy',
    'ef-morning-weekend-hedge-strategy',
    'ef-hysteresis-again-strategy',
    'h3-flatten-reentry-strategy',
    'h3-profit-protection-strategy',
    'ef-hysteresis-total-breakout-strategy',
    'cloudflared'
)

Push-Location $projectDir
try {
    & docker compose --profile '*' stop @services
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose stop failed with exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
Write-Host 'Docker services stopped, including API_KEY and API_KEY2. Existing positions are unchanged.' -ForegroundColor Green
if ($nativeErrors.Count) {
    throw ("Docker services stopped, but native service shutdown needs review: " + ($nativeErrors -join '; '))
}
