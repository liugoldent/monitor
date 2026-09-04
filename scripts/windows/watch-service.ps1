[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Title,
    [Parameter(Mandatory = $true)][string]$ServiceNames,
    [Parameter(Mandatory = $true)][string]$ProjectDir,
    [string]$ClearMarker
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$Host.UI.RawUI.WindowTitle = $Title
$services = $ServiceNames.Split(',', [System.StringSplitOptions]::RemoveEmptyEntries)

Set-Location -LiteralPath $ProjectDir
Write-Host "[$Title] Live Docker logs." -ForegroundColor Cyan
Write-Host "Closing this window does not stop: $($services -join ', ')" -ForegroundColor DarkGray
if ([string]::IsNullOrWhiteSpace($ClearMarker)) {
    & docker compose logs --follow --tail 100 @services
} else {
    & docker compose logs --no-color --follow --tail 100 @services | ForEach-Object {
        if ($_ -like "*$ClearMarker*") {
            Clear-Host
        } else {
            Write-Host $_
        }
    }
}
