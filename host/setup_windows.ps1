[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$VenvDir = Join-Path $PSScriptRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $PyLauncher) {
        & $PyLauncher.Source -3 -m venv $VenvDir
    } else {
        $Python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $Python) {
            throw "Python 3.10 or newer is required. Install Python, then rerun this script."
        }
        & $Python.Source -m venv $VenvDir
    }
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install State Notifier host dependencies."
}
& $VenvPython -m pip install --disable-pip-version-check --no-deps --force-reinstall $PSScriptRoot
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the State Notifier VFD Python API."
}

Write-Host "Windows host environment is ready: $VenvPython"
Write-Host "Next: .\host\install_hooks.ps1 install"
