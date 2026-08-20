[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Title,

    [Parameter(Mandatory = $true)]
    [string]$ServiceNames,

    [Parameter(Mandatory = $true)]
    [string]$ProjectDir
)

$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = $Title
$services = $ServiceNames.Split(',', [System.StringSplitOptions]::RemoveEmptyEntries)

Set-Location -LiteralPath $ProjectDir
Write-Host "[$Title] Live logs. Closing this window does not stop background services." -ForegroundColor Cyan
Write-Host "Services: $($services -join ', ')" -ForegroundColor DarkGray
Write-Host ''

& docker compose logs --follow --tail 100 @services
