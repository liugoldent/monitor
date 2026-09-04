[CmdletBinding()]
param(
    [double]$IntervalSeconds = 60,
    [switch]$Once,
    [switch]$NoSave
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'

$projectDir = $PSScriptRoot
$monitorScript = Join-Path $projectDir 'backend-futures-py\options-level-monitor\monitor.py'
$venvPython = Join-Path $projectDir 'backend-futures-py\.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $monitorScript -PathType Leaf)) {
    throw "Monitor script is missing: $monitorScript"
}

if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $python = $venvPython
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $python = 'py.exe'
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    $python = 'python.exe'
} else {
    throw 'Python was not found. Install Python or create backend-futures-py\.venv first.'
}

$arguments = @($monitorScript, '--interval', $IntervalSeconds)
if ($Once) { $arguments += '--once' }
if ($NoSave) { $arguments += '--no-save' }

Write-Host 'Starting TX options support/resistance monitor...' -ForegroundColor Green
& $python @arguments
exit $LASTEXITCODE
