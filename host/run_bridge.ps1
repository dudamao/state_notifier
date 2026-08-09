[CmdletBinding()]
param(
    [string] $Port,
    [string] $SerialNumber,
    [ValidateSet("codex", "demo")]
    [string] $Mode = "codex",
    [switch] $Demo,
    [double] $Fps = 30,
    [string] $CodexHome,
    [string] $CodexBinary
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
if ($CodexHome) { $BridgeArgs += @("--codex-home", $CodexHome) }
if ($CodexBinary) { $BridgeArgs += @("--codex-binary", $CodexBinary) }
if ($PSBoundParameters.ContainsKey("Verbose")) { $BridgeArgs += "--verbose" }

& $Python (Join-Path $PSScriptRoot "bridge.py") @BridgeArgs
exit $LASTEXITCODE
