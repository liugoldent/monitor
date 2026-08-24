[CmdletBinding()]
param([switch]$NoBuild)

$script = Join-Path $PSScriptRoot 'scripts\windows\start-services.ps1'
& $script -NoBuild:$NoBuild
exit $LASTEXITCODE
