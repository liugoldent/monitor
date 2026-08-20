[CmdletBinding()]
param(
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$backendDir = Join-Path $projectDir 'backend-futures-py'
$rootEnvPath = Join-Path $projectDir '.env'
$watchScript = Join-Path $projectDir 'scripts\watch-windows-service.ps1'
$serviceNames = @('six-strategy', 'monitor-mxf', 'webhook-server', 'cloudflared')
$requiredFiles = @(
    (Join-Path $backendDir '.env'),
    (Join-Path $backendDir 'Sinopac.pfx'),
    (Join-Path $backendDir 'session_monitor_six_strategy.session')
)

function Get-RootEnvValue {
    param([string]$Name)

    if (-not (Test-Path -LiteralPath $rootEnvPath -PathType Leaf)) {
        return $null
    }

    foreach ($line in Get-Content -LiteralPath $rootEnvPath) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return $null
}

function Save-CloudflaredToken {
    $secureToken = Read-Host 'Paste the NEW Cloudflare tunnel token (input is hidden)' -AsSecureString
    $tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    try {
        $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }

    if ([string]::IsNullOrWhiteSpace($token)) {
        throw 'Cloudflare tunnel token cannot be empty.'
    }

    $lines = @()
    if (Test-Path -LiteralPath $rootEnvPath -PathType Leaf) {
        $lines = @(Get-Content -LiteralPath $rootEnvPath | Where-Object { $_ -notmatch '^\s*CLOUDFLARED_TOKEN\s*=' })
    }
    $lines += "CLOUDFLARED_TOKEN=$token"
    [System.IO.File]::WriteAllLines($rootEnvPath, $lines, [System.Text.UTF8Encoding]::new($false))
}

function Test-DockerEngine {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell 5 converts native stderr into ErrorRecord objects.
        # Silence that expected output while Docker Desktop is still starting.
        $ErrorActionPreference = 'SilentlyContinue'
        & docker info *> $null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required file is missing: $requiredFile"
    }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw 'Docker command was not found. Install Docker Desktop first.'
}

if ([string]::IsNullOrWhiteSpace((Get-RootEnvValue -Name 'CLOUDFLARED_TOKEN'))) {
    Save-CloudflaredToken
    Write-Host 'Cloudflare token was saved to the Git-ignored root .env file.' -ForegroundColor Green
}

if (-not (Test-DockerEngine)) {
    $dockerDesktop = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path -LiteralPath $dockerDesktop -PathType Leaf)) {
        throw 'Docker Engine is not running and Docker Desktop was not found.'
    }

    Write-Host 'Starting Docker Desktop...' -ForegroundColor Yellow
    Start-Process -FilePath $dockerDesktop -WindowStyle Hidden
    $ready = $false
    foreach ($attempt in 1..60) {
        Start-Sleep -Seconds 2
        if (Test-DockerEngine) {
            $ready = $true
            break
        }
    }
    if (-not $ready) {
        throw 'Docker Engine did not become ready within 120 seconds. Check Docker Desktop.'
    }
}

Push-Location $projectDir
try {
    $composeArgs = @('compose', 'up', '--detach')
    if (-not $NoBuild) {
        $composeArgs += '--build'
    }
    $composeArgs += $serviceNames

    & docker @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code: $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$windows = @(
    @{ Title = 'Six Strategy CSV Monitor'; Services = 'six-strategy' },
    @{ Title = 'MXF Market Monitor'; Services = 'monitor-mxf' },
    @{ Title = 'Webhook Server'; Services = 'webhook-server' },
    @{ Title = 'Cloudflare Tunnel'; Services = 'cloudflared' }
)

$windowsTerminal = Get-Command 'wt.exe' -ErrorAction SilentlyContinue
if ($windowsTerminal) {
    $terminalWindowName = 'monitor-services'
    foreach ($window in $windows) {
        $arguments = @(
            '--window', $terminalWindowName,
            'new-tab',
            '--title', ('"{0}"' -f $window.Title),
            'powershell.exe',
            '-NoLogo',
            '-NoExit',
            '-ExecutionPolicy', 'Bypass',
            '-File', ('"{0}"' -f $watchScript),
            '-Title', ('"{0}"' -f $window.Title),
            '-ServiceNames', ('"{0}"' -f $window.Services),
            '-ProjectDir', ('"{0}"' -f $projectDir)
        )
        Start-Process -FilePath $windowsTerminal.Source -ArgumentList $arguments
        Start-Sleep -Milliseconds 300
    }
    Write-Host 'All services are running. One Windows Terminal window with four tabs was opened.' -ForegroundColor Green
} else {
    foreach ($window in $windows) {
        $arguments = @(
            '-NoLogo',
            '-NoExit',
            '-ExecutionPolicy', 'Bypass',
            '-File', ('"{0}"' -f $watchScript),
            '-Title', ('"{0}"' -f $window.Title),
            '-ServiceNames', ('"{0}"' -f $window.Services),
            '-ProjectDir', ('"{0}"' -f $projectDir)
        )
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments
    }
    Write-Warning 'Windows Terminal (wt.exe) was not found. Opened four PowerShell windows instead.'
}

Write-Host 'Closing a log window does not stop a service. Docker restart policy remains active.'
