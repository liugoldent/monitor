[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$services = @('monitor-mxf', 'webhook-server', 'h3-ef-012-strategy', 'cloudflared')

Push-Location $projectDir
try {
    & docker compose --profile windows stop @services
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose stop failed with exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
