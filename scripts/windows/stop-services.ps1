[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$services = @(
    'telegram-signal-relay',
    'monitor-mxf',
    'webhook-server',
    'ef-strong-consensus-morning-flat-strategy',
    'ef-morning-weekend-hedge-strategy',
    'ef-hysteresis-again-strategy',
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
