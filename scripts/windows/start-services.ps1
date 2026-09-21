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
    'cloudflared',
    'ef-strong-consensus-morning-flat-strategy',
    'ef-morning-weekend-hedge-strategy'
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

function Enable-ProjectBuilder {
    $builderName = 'monitor-builder'
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & docker buildx inspect $builderName *> $null
        $builderExists = $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if (-not $builderExists) {
        Write-Host "Creating the project Docker builder ($builderName)..." -ForegroundColor Cyan
        & docker buildx create --name $builderName --driver docker-container | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Could not create Docker builder: $builderName" }
    }
    Write-Host "Preparing the project Docker builder ($builderName)..." -ForegroundColor Cyan
    & docker buildx inspect $builderName --bootstrap | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Docker builder is not ready: $builderName" }
    return $builderName
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
$previousBuilder = $env:BUILDX_BUILDER
$projectBuilder = $null
if (-not $NoBuild) {
    # Docker Desktop's default embedded BuildKit can be severely throttled while
    # RUN steps download packages.  The container driver uses the normal Docker
    # network path and is dramatically faster on affected Windows installations.
    $projectBuilder = Enable-ProjectBuilder
    $env:BUILDX_BUILDER = $projectBuilder
}
Push-Location $projectDir
try {
    if (-not $NoBuild) {
        # Every application service uses monitor-app:local. Build and import it
        # once; asking Compose to build every service repeats the large export.
        & docker compose --progress plain build telegram-signal-relay
        if ($LASTEXITCODE -ne 0) { throw "Docker image build failed: $LASTEXITCODE" }
    }
    $composeArgs = @('compose', '--progress', 'plain', '--profile', 'tunnel', 'up', '--detach', '--no-build')
    $composeArgs += @($services | Where-Object { $_ -ne 'ef-morning-weekend-hedge-strategy' })
    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed: $LASTEXITCODE" }
    # Always start a fresh account-2 process, even when its image is unchanged.
    # Other services retain their ordinary compose lifecycle.
    $efArgs = @('compose', '--progress', 'plain', 'up', '--detach', '--no-build', '--no-deps', '--force-recreate')
    $efArgs += 'ef-morning-weekend-hedge-strategy'
    & docker @efArgs
    if ($LASTEXITCODE -ne 0) { throw "EF account 2 startup failed: $LASTEXITCODE" }
} finally {
    Pop-Location
    if ($projectBuilder) {
        & docker buildx stop $projectBuilder | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not stop the idle Docker builder: $projectBuilder"
        }
    }
    $env:BUILDX_BUILDER = $previousBuilder
}

$logWindows = @(
    @{ Title = 'Telegram H-EF Relay'; Service = 'telegram-signal-relay' },
    @{ Title = 'MXF Market Monitor'; Service = 'monitor-mxf' },
    @{ Title = 'Webhook Server'; Service = 'webhook-server' },
    @{ Title = 'Cloudflare Tunnel'; Service = 'cloudflared' },
    @{ Title = 'EF Hysteresis Consensus LIVE - API KEY'; Service = 'ef-strong-consensus-morning-flat-strategy' },
    @{ Title = 'EF Pure Morning Flat - API KEY2'; Service = 'ef-morning-weekend-hedge-strategy' }
)
$terminal = Get-Command wt.exe -ErrorAction SilentlyContinue
foreach ($item in $logWindows) {
    $watchArgs = @(
        '-NoLogo', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', $watchScript,
        '-Title', $item.Title, '-ServiceNames', $item.Service, '-ProjectDir', $projectDir
    )
    if ($item.ClearMarker) {
        $watchArgs += @('-ClearMarker', $item.ClearMarker)
    }
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
        if ($item.ClearMarker) {
            $quotedWatchArgs += @('-ClearMarker', ('"{0}"' -f $item.ClearMarker))
        }
        Start-Process powershell.exe -ArgumentList $quotedWatchArgs
    }
    Start-Sleep -Milliseconds 300
}

Write-Host 'Windows services are running. Closing log tabs does not stop Docker.' -ForegroundColor Green
