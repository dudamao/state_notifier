[CmdletBinding()]
param(
    [ValidateSet("install", "uninstall", "status")]
    [string] $Action = "install"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Windows environment is missing. Run .\host\setup_windows.ps1 first."
}

& $Python (Join-Path $PSScriptRoot "install_hooks.py") $Action
exit $LASTEXITCODE
