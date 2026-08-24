[CmdletBinding()]
param()

& (Join-Path $PSScriptRoot 'scripts\windows\initialize-h3-session.ps1')
exit $LASTEXITCODE
