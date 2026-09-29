[CmdletBinding()]
param(
    [string] $Port,
    [string] $SerialNumber,
    [ValidateSet("clock", "demo")]
    [string] $Mode = "clock",
    [switch] $Demo,
    [double] $Fps = 30
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Windows environment is missing. Run .\host\setup_windows.ps1 first."
}

$BridgeArgs = @()
if ($Port) { $BridgeArgs += @("--port", $Port) }
if ($SerialNumber) { $BridgeArgs += @("--serial-number", $SerialNumber) }
if ($Demo) { $Mode = "demo" }
$BridgeArgs += @("--mode", $Mode, "--fps", $Fps)
if ($PSBoundParameters.ContainsKey("Verbose")) { $BridgeArgs += "--verbose" }

& $Python (Join-Path $PSScriptRoot "bridge.py") @BridgeArgs
exit $LASTEXITCODE
