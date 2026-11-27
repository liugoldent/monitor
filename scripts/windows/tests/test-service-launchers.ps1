# Offline launcher integration check: every Docker/native/window command is a fake.
$ErrorActionPreference = 'Stop'
$sourceDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$fixture = Join-Path $PSScriptRoot ('fixture-' + [guid]::NewGuid().ToString('N'))
$global:launcherTestCommands = [Collections.Generic.List[object]]::new()
$global:launcherTestWindows = [Collections.Generic.List[object]]::new()
$global:launcherTestFrontendActions = [Collections.Generic.List[string]]::new()
$global:launcherTestFailKey3 = $false
function Assert-That($condition, $message) {
    if (-not $condition) { throw $message }
}
function global:docker {
    $global:launcherTestCommands.Add(@($args))
    $global:LASTEXITCODE = if ($global:launcherTestFailKey3 -and $args -contains 'up' -and $args -contains 'api-key3-follow') { 42 } else { 0 }
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
    New-Item -ItemType Directory -Path (Join-Path $backendDir 'h3-shane-use') | Out-Null
    Set-Content -LiteralPath (Join-Path $backendDir 'h3-shane-use\.env') -Value '# fake'
    Set-Content -LiteralPath (Join-Path $scriptsDir 'watch-service.ps1') -Value '# fake'
    Set-Content -LiteralPath (Join-Path $scriptsDir 'frontend-service.ps1') -Value 'param($Action, [switch]$NoBuild); $global:launcherTestFrontendActions.Add($Action)'
    foreach ($name in @('capital-h3-service.ps1', 'kgi-h3-service.ps1')) {
        Set-Content -LiteralPath (Join-Path $scriptsDir $name) -Value 'param($Action); if ($Action -ne "Stop") { throw "Wrong native action" }'
    }

    & (Join-Path $scriptsDir 'start-services.ps1') -NoBuild
    Assert-That ($global:launcherTestFrontendActions[0] -eq 'Start') 'Frontend was not started.'
    $ups = @($global:launcherTestCommands | Where-Object { $_ -contains 'up' })
    Assert-That ($ups.Count -eq 2) 'Expected the main Compose up, then an isolated API_KEY3 up.'
    $strategyServices = @('h3-flatten-reentry-strategy', 'h3-profit-protection-strategy', 'h3-shane-use')
    Assert-That ($ups[0] -contains 'h3-signal-router') 'Startup omitted the H3 signal router.'
    Assert-That ($ups[0] -contains 'h2-signal-router') 'Startup omitted the H2 signal router.'
    Assert-That ($ups[0] -contains 'h3-protection') 'Missing API_KEY2 profile.'
    Assert-That ($ups[0] -contains 'h3-shane') 'Missing Shane profile.'
    Assert-That ($global:launcherTestWindows.Count -eq 9) 'Expected nine log tabs.'
    Assert-That (-not ($ups[0] -contains 'api-key3-follow')) 'API_KEY3 must not be part of the main up.'
    Assert-That ($ups[1] -contains 'api-key3-follow') 'Startup omitted API_KEY3.'
    Assert-That ($ups[1] -contains '--no-deps') 'Starting API_KEY3 must not restart dependencies.'
    Assert-That (@($global:launcherTestWindows | Where-Object { $_ -contains 'api-key3-follow' }).Count -eq 1) 'Missing API_KEY3 log tab.'
    $paused = @($global:launcherTestCommands | Where-Object { $_ -contains 'stop' })
    Assert-That ($paused.Count -eq 1) 'Expected legacy strategy stop before startup.'
    foreach ($service in $strategyServices) {
        Assert-That ($ups[0] -contains $service) "Startup omitted $service"
        Assert-That (@($global:launcherTestWindows | Where-Object { $_ -contains $service }).Count -eq 1) "Missing log tab for $service"
        Assert-That (-not ($paused[0] -contains $service)) "Startup paused $service"
    }

    $global:launcherTestCommands.Clear()
    $global:launcherTestFrontendActions.Clear()
    $global:launcherTestFailKey3 = $true
    & (Join-Path $scriptsDir 'start-services.ps1') -NoBuild
    Assert-That ($global:launcherTestFrontendActions -contains 'Start') 'API_KEY3 failure blocked the frontend.'
    $ups = @($global:launcherTestCommands | Where-Object { $_ -contains 'up' })
    foreach ($service in $strategyServices) {
        Assert-That ($ups[0] -contains $service) "API_KEY3 failure interfered with $service startup."
        Assert-That (@($global:launcherTestCommands | Where-Object { $_ -contains 'stop' -and $_ -contains $service }).Count -eq 0) "API_KEY3 failure stopped $service."
    }
    $global:launcherTestFailKey3 = $false
    $global:launcherTestCommands.Clear()
    & (Join-Path $scriptsDir 'stop-services.ps1')
    Assert-That ($global:launcherTestFrontendActions -contains 'Stop') 'Frontend was not stopped.'
    $stops = @($global:launcherTestCommands | Where-Object { $_ -contains 'stop' })
    Assert-That ($stops.Count -eq 1) 'Expected one Compose stop.'
    foreach ($service in (@('h3-signal-router', 'h2-signal-router', 'api-key3-follow') + $strategyServices)) {
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
    Set-Content -LiteralPath (Join-Path $scriptsDir 'frontend-service.ps1') -Value 'throw "fake frontend stop failure"'
    $global:launcherTestCommands.Clear()
    try { & (Join-Path $scriptsDir 'stop-services.ps1') } catch { }
    Assert-That (@($global:launcherTestCommands | Where-Object { $_ -contains 'stop' }).Count -eq 1) 'Frontend error prevented Docker shutdown.'
    Write-Host 'PASS: all four strategies start and stop, nine log tabs open, and API_KEY3 failure leaves the other services running.'
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
