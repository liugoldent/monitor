[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$ServiceNames,
    [Parameter(Mandatory = $true)][string]$ProjectDir
)

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = $Title
$services = $ServiceNames.Split(',', [System.StringSplitOptions]::RemoveEmptyEntries)

Set-Location -LiteralPath $ProjectDir
Write-Host "[$Title] Live Docker logs." -ForegroundColor Cyan
Write-Host "Closing this window does not stop: $($services -join ', ')" -ForegroundColor DarkGray
& docker compose --profile windows logs --follow --tail 100 @services
