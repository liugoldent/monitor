[CmdletBinding()]
param()

& (Join-Path $PSScriptRoot 'scripts\windows\initialize-telegram-relay-session.ps1')
exit $LASTEXITCODE
