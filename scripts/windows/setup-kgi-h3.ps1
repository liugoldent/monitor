[CmdletBinding()]
param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$strategyDir = Join-Path $projectDir 'backend-futures-py\h3-kgi-flatten-reentry-strategy'
if (-not $PythonPath) {
    $PythonPath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Specify -PythonPath with a 64-bit Python 3.9-3.14 executable.'
}
& $PythonPath -c 'import struct,sys; assert struct.calcsize("P")==8 and (3,9)<=sys.version_info[:2]<=(3,14), "64-bit Python 3.9-3.14 required"'
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python runtime.' }
$venvDir = Join-Path $strategyDir '.venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $PythonPath -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw 'KGI virtual environment creation failed.' }
}
& $venvPython -m pip install --no-compile -r (Join-Path $strategyDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'KGI dependencies installation failed.' }
& $venvPython -c 'import kgisuperpy; import importlib.metadata; print("KGI SDK import OK:", importlib.metadata.version("kgisuperpy"))'
if ($LASTEXITCODE -ne 0) { throw 'KGI SDK failed its import check; see the error above.' }
foreach ($pair in @(@('.env.example', '.env'), @('h-config.example.json', 'h-config.json'))) {
    $targetPath = Join-Path $strategyDir $pair[1]
    if (-not (Test-Path -LiteralPath $targetPath)) {
        Copy-Item -LiteralPath (Join-Path $strategyDir $pair[0]) -Destination $targetPath
    }
}
Write-Host 'KGI runtime installed. Fill in the strategy .env, then run -Action Check / Diagnose.'
