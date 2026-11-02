# Offline ownership checks. No real process is launched or stopped.
$ErrorActionPreference = 'Stop'
$fixture = Join-Path $PSScriptRoot ('frontend-fixture-' + [guid]::NewGuid().ToString('N'))
$global:frontendTestStopped = [Collections.Generic.List[int]]::new()
$global:frontendTestProcess = $null
$global:frontendTestDetails = $null
function global:Get-Process { param($Id, $ErrorAction); return $global:frontendTestProcess }
function global:Get-CimInstance { param($ClassName, $Filter); return $global:frontendTestDetails }
function global:Stop-Process { param($Id, $ErrorAction); $global:frontendTestStopped.Add($Id) }
function Assert-That($Condition, $Message) { if (-not $Condition) { throw $Message } }
try {
    $scriptsDir = Join-Path $fixture 'scripts\windows'
    $runtimeDir = Join-Path $fixture 'tx-trading-system\.runtime'
    New-Item -ItemType Directory -Path $scriptsDir, $runtimeDir -Force | Out-Null
    $launcher = Join-Path $scriptsDir 'frontend-service.ps1'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\frontend-service.ps1') -Destination $launcher
    $statePath = Join-Path $runtimeDir 'service.json'
    $started = [datetime]'2026-11-02T00:00:00Z'
    $state = @{ ProcessId = 12345; StartedAt = $started.ToUniversalTime().ToString('o') }

    # No record must be an idempotent no-op.
    & $launcher -Action Stop
    Assert-That ($global:frontendTestStopped.Count -eq 0) 'Stopped a process without an ownership record.'
    $global:frontendTestProcess = [pscustomobject]@{ Id = 12345; StartTime = $started; HasExited = $true }
    $global:frontendTestProcess | Add-Member ScriptMethod WaitForExit { param($Timeout); return $true }
    $server = Join-Path $fixture 'tx-trading-system\server\index.mjs'
    $global:frontendTestDetails = [pscustomobject]@{ Name = 'node.exe'; CommandLine = ('node.exe "{0}" --production' -f $server) }

    # A reused PID must never be stopped.
    @{ ProcessId = 12345; StartedAt = $started.AddMinutes(-1).ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath $statePath
    & $launcher -Action Stop
    Assert-That ($global:frontendTestStopped.Count -eq 0) 'Stopped a reused PID.'

    # Matching PID and time still do not authorize another Node project.
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath
    $global:frontendTestDetails.CommandLine = 'node.exe C:\other-project\server\index.mjs --production'
    & $launcher -Action Stop
    Assert-That ($global:frontendTestStopped.Count -eq 0) 'Stopped an unrelated Node project.'

    # Only the matching process, timestamp and absolute entry point are stopped.
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath
    $global:frontendTestDetails.CommandLine = ('node.exe "{0}" --production' -f $server)
    & $launcher -Action Stop
    Assert-That ($global:frontendTestStopped.Count -eq 1) 'Owned frontend was not stopped exactly once.'
    Assert-That (-not (Test-Path -LiteralPath $statePath)) 'Stale runtime record was retained.'
    & $launcher -Action Stop
    Assert-That ($global:frontendTestStopped.Count -eq 1) 'Repeated stop was not idempotent.'
    Write-Host 'PASS: frontend ownership, PID reuse, unrelated Node protection, and repeated stop.'
} finally {
    foreach ($name in @('Get-Process', 'Get-CimInstance', 'Stop-Process')) { Remove-Item -LiteralPath "Function:\$name" -ErrorAction SilentlyContinue }
    $resolvedFixture = [IO.Path]::GetFullPath($fixture)
    $allowedParent = [IO.Path]::GetFullPath($PSScriptRoot) + [IO.Path]::DirectorySeparatorChar
    if ($resolvedFixture.StartsWith($allowedParent, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedFixture)) {
        Remove-Item -LiteralPath $resolvedFixture -Recurse -Force
    }
}
