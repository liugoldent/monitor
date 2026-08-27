[CmdletBinding()]
param([switch]$NoBuild)

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backendDir = Join-Path $projectDir 'backend-futures-py'
$rootEnvPath = Join-Path $projectDir '.env'
$watchScript = Join-Path $PSScriptRoot 'watch-service.ps1'
$services = @(
    'telegram-signal-relay',
    'monitor-mxf',
    'webhook-server',
    'cloudflared'
)
$retiredStrategyServices = @(
    'six-strategy-listener',
    'h3-ef-012-strategy',
    'ef-strong-consensus-morning-flat-strategy',
    'ef-rsi60-filter-strategy'
)

function Get-RootEnvValue([string]$Name) {
    if (-not (Test-Path -LiteralPath $rootEnvPath -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $rootEnvPath) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Save-CloudflaredToken {
    $secureToken = Read-Host 'Paste the Cloudflare tunnel token (input is hidden)' -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ([string]::IsNullOrWhiteSpace($token)) { throw 'Cloudflare token cannot be empty.' }

    $lines = @()
    if (Test-Path -LiteralPath $rootEnvPath -PathType Leaf) {
        $lines = @(Get-Content $rootEnvPath | Where-Object { $_ -notmatch '^\s*CLOUDFLARED_TOKEN\s*=' })
    }
    $lines += "CLOUDFLARED_TOKEN=$token"
    [IO.File]::WriteAllLines($rootEnvPath, $lines, [Text.UTF8Encoding]::new($false))
}

function Test-DockerEngine {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & docker info *> $null
        return $LASTEXITCODE -eq 0
    } finally { $ErrorActionPreference = $previousPreference }
}

foreach ($requiredFile in @(
    (Join-Path $backendDir '.env'),
    (Join-Path $backendDir 'Sinopac.pfx'),
    $watchScript
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker command was not found. Install Docker Desktop first.'
}
if ([string]::IsNullOrWhiteSpace((Get-RootEnvValue 'CLOUDFLARED_TOKEN'))) {
    Save-CloudflaredToken
}
if (-not (Test-DockerEngine)) {
    $dockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $dockerDesktop)) { throw 'Docker Desktop was not found.' }
    Start-Process $dockerDesktop -WindowStyle Hidden
    $ready = $false
    foreach ($attempt in 1..60) {
        Start-Sleep -Seconds 2
        if (Test-DockerEngine) { $ready = $true; break }
    }
    if (-not $ready) { throw 'Docker did not become ready within 120 seconds.' }
}
Push-Location $projectDir
try {
    # Pure recording mode: make sure no old restart-enabled strategy container
    # can run beside the Telegram relay.
    & docker compose --profile strategies stop @retiredStrategyServices
    if ($LASTEXITCODE -ne 0) { throw "Could not stop retired strategy services: $LASTEXITCODE" }
    $composeArgs = @('compose', '--profile', 'tunnel', 'up', '--detach')
    if (-not $NoBuild) { $composeArgs += '--build' }
    $composeArgs += $services
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $LASTEXITCODE" }
} finally { Pop-Location }

$logWindows = @(
    @{ Title = 'Telegram H-EF Relay'; Service = 'telegram-signal-relay' },
    @{ Title = 'MXF Market Monitor'; Service = 'monitor-mxf' },
    @{ Title = 'Webhook Server'; Service = 'webhook-server' },
    @{ Title = 'Cloudflare Tunnel'; Service = 'cloudflared' }
)
$terminal = Get-Command wt.exe -ErrorAction SilentlyContinue
foreach ($item in $logWindows) {
    $watchArgs = @(
        '-NoLogo', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $watchScript,
        '-Title', $item.Title, '-ServiceNames', $item.Service, '-ProjectDir', $projectDir
    )
    if ($terminal) {
        $terminalArgs = @(
            '--window', 'monitor-services', 'new-tab',
            '--title', $item.Title, 'powershell.exe'
        ) + $watchArgs
        # Invoke wt.exe directly so PowerShell preserves arguments containing
        # spaces. Start-Process flattens ArgumentList into a single string and
        # caused titles such as "MXF Market Monitor" to be parsed as commands.
        & $terminal.Source @terminalArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Windows Terminal could not open the $($item.Title) log tab."
        }
    } else {
        # Start-Process also needs explicit quotes in its flattened argument
        # string when Windows Terminal is unavailable.
        $quotedWatchArgs = @(
            '-NoLogo', '-NoExit', '-ExecutionPolicy', 'Bypass',
            '-File', ('"{0}"' -f $watchScript),
            '-Title', ('"{0}"' -f $item.Title),
            '-ServiceNames', ('"{0}"' -f $item.Service),
            '-ProjectDir', ('"{0}"' -f $projectDir)
        )
        Start-Process powershell.exe -ArgumentList $quotedWatchArgs
    }
    Start-Sleep -Milliseconds 300
}

Write-Host 'Windows services are running. Closing log tabs does not stop Docker.' -ForegroundColor Green
