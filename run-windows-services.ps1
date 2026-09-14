[CmdletBinding()]
param([switch]$NoBuild, [switch]$NoPull)

$ErrorActionPreference = 'Stop'
try {
    if (-not $NoPull) {
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw 'Git was not found. Install Git for Windows, or use -NoPull for local code.'
        }
        Push-Location $PSScriptRoot
        try {
            & git pull --ff-only
            if ($LASTEXITCODE -ne 0) {
                throw 'Git update failed. Resolve the reported conflict or connection issue, then run again. Services were not restarted.'
            }
        } finally { Pop-Location }
    }
    $script = Join-Path $PSScriptRoot 'scripts\windows\start-services.ps1'
    & $script -NoBuild:$NoBuild
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
