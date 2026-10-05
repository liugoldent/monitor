$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runtimeDir = Join-Path $projectDir 'backend-futures-py\h3-capital-flatten-reentry-strategy\runtime'
$Host.UI.RawUI.WindowTitle = 'H3 Capital LIVE'
$stdoutPath = Join-Path $runtimeDir 'windows-service.log'
$stderrPath = Join-Path $runtimeDir 'windows-service-error.log'
Write-Host 'H3 Capital LIVE. Closing this log window does not stop the strategy.'
Write-Host "Error log: $stderrPath"
Get-Content -LiteralPath $stdoutPath -Encoding UTF8 -Tail 80 -Wait
