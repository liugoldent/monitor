# Offline launcher integration check: every Docker/native/window command is a fake.
$ErrorActionPreference = 'Stop'
$sourceDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixture = Join-Path $PSScriptRoot ('fixture-' + [guid]::NewGuid().ToString('N'))
$global:launcherTestCommands = [Collections.Generic.List[object]]::new()
$global:launcherTestWindows = [Collections.Generic.List[object]]::new()
function Assert-That($condition, $message) {
    if (-not $condition) { throw $message }
}
function global:docker {
    $global:launcherTestCommands.Add(@($args))
    $global:LASTEXITCODE = 0
}
function global:wt.exe {
    $global:launcherTestWindows.Add(@($args))
    $global:LASTEXITCODE = 0
}
function global:Start-Sleep { param($Seconds, $Milliseconds) }
function global:Start-Process { throw 'Unexpected real process launch requested.' }
function global:Get-Command {
    param($Name, $ErrorAction)
    if ($Name -eq 'wt.exe') { return [pscustomobject]@{ Source = 'wt.exe' } }
    Microsoft.PowerShell.Core\Get-Command @PSBoundParameters
}
try {
    $scriptsDir = Join-Path $fixture 'scripts\windows'
    $backendDir = Join-Path $fixture 'backend-futures-py'
    New-Item -ItemType Directory -Path $scriptsDir, $backendDir | Out-Null
    foreach ($name in @('start-services.ps1', 'stop-services.ps1')) {
        Copy-Item -LiteralPath (Join-Path $sourceDir $name) -Destination $scriptsDir
    }
    Set-Content -LiteralPath (Join-Path $fixture '.env') -Value 'CLOUDFLARED_TOKEN=fake'
    Set-Content -LiteralPath (Join-Path $backendDir '.env') -Value '# fake'
    Set-Content -LiteralPath (Join-Path $backendDir 'Sinopac.pfx') -Value 'fake'
    Set-Content -LiteralPath (Join-Path $scriptsDir 'watch-service.ps1') -Value '# fake'
    foreach ($name in @('capital-h3-service.ps1', 'kgi-h3-service.ps1')) {
        Set-Content -LiteralPath (Join-Path $scriptsDir $name) -Value 'param($Action); if ($Action -ne "Stop") { throw "Wrong native action" }'
    }

    & (Join-Path $scriptsDir 'start-services.ps1') -NoBuild
    $ups = @($global:launcherTestCommands | Where-Object { $_ -contains 'up' })
    Assert-That ($ups.Count -eq 1) 'Expected exactly one Compose up.'
    foreach ($service in @('h3-signal-router', 'h3-flatten-reentry-strategy', 'h3-profit-protection-strategy')) {
        Assert-That ($ups[0] -contains $service) "Startup omitted $service"
    }
    Assert-That ($ups[0] -contains 'h3-protection') 'Missing H3 simulation profile.'
    Assert-That ($global:launcherTestWindows.Count -eq 7) 'Expected seven log tabs.'
    Assert-That (@($global:launcherTestWindows | Where-Object { $_ -contains 'h3-profit-protection-strategy' }).Count -eq 1) 'Missing API_KEY2 log tab.'
    $paused = @($global:launcherTestCommands | Where-Object { $_ -contains 'stop' })
    Assert-That ($paused.Count -eq 1) 'Expected legacy strategy stop before startup.'
    Assert-That (-not ($paused[0] -contains 'h3-profit-protection-strategy')) 'Startup must not pause API_KEY2.'

    $global:launcherTestCommands.Clear()
    & (Join-Path $scriptsDir 'stop-services.ps1')
    $stops = @($global:launcherTestCommands | Where-Object { $_ -contains 'stop' })
    Assert-That ($stops.Count -eq 1) 'Expected one Compose stop.'
    foreach ($service in @('h3-signal-router', 'h3-flatten-reentry-strategy', 'h3-profit-protection-strategy')) {
        Assert-That ($stops[0] -contains $service) "Shutdown omitted $service"
    }
    Assert-That ($stops[0] -contains '*') 'Shutdown must include every profile.'

    # Native shutdown failures must never prevent the Docker stop attempt.
    Set-Content -LiteralPath (Join-Path $scriptsDir 'capital-h3-service.ps1') -Value 'throw "fake native stop failure"'
    $global:launcherTestCommands.Clear()
    $reportedFailure = $false
    try { & (Join-Path $scriptsDir 'stop-services.ps1') }
    catch { $reportedFailure = $_.Exception.Message -like '*fake native stop failure*' }
    Assert-That $reportedFailure 'Native stop error must be reported.'
    Assert-That (@($global:launcherTestCommands | Where-Object { $_ -contains 'stop' }).Count -eq 1) 'Native error prevented Docker shutdown.'
    Write-Host 'PASS: router and both strategies start/stop; seven log tabs; native failure still stops Docker.'
} finally {
    foreach ($name in @('docker', 'wt.exe', 'Start-Sleep', 'Start-Process', 'Get-Command')) {
        Remove-Item -LiteralPath "Function:\$name" -ErrorAction SilentlyContinue
    }
    $resolvedFixture = [IO.Path]::GetFullPath($fixture)
    $allowedParent = [IO.Path]::GetFullPath($PSScriptRoot) + [IO.Path]::DirectorySeparatorChar
    if ($resolvedFixture.StartsWith($allowedParent, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolvedFixture)) {
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
    }
}
