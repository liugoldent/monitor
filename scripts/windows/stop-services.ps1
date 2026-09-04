[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$services = @(
    'telegram-signal-relay',
    'monitor-mxf',
    'webhook-server',
    'six-strategy-listener',
    # Keep the retired service here so Stop always catches an old container.
    'h3-ef-012-strategy',
    'regression-mean-reversion-morning-flat-strategy',
    'ef-strong-consensus-morning-flat-strategy',
    'ef-dual-session-guard-strategy',
    'options-level-monitor',
    'cloudflared'
)

Push-Location $projectDir
try {
    & docker compose --profile windows stop @services
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose stop failed with exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
