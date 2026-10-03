[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [string]$DataDir = "",
    [switch]$SmokeTest,
    [switch]$StartHidden,
    [double]$QuitAfter = 0
)

$ErrorActionPreference = "Stop"
$ClipNestRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ClipNestRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "找不到 Python：$PythonPath。请按 README 安装依赖。"
}
$ClipNestRunArgs = @((Join-Path $ClipNestRoot "run_clipnest.py"))
if (-not [string]::IsNullOrWhiteSpace($DataDir)) { $ClipNestRunArgs += @("--data-dir", $DataDir) }
if ($SmokeTest) { $ClipNestRunArgs += "--smoke-test" }
if ($StartHidden) { $ClipNestRunArgs += "--start-hidden" }
if ($QuitAfter -gt 0) { $ClipNestRunArgs += @("--quit-after", $QuitAfter.ToString([Globalization.CultureInfo]::InvariantCulture)) }

Push-Location -LiteralPath $ClipNestRoot
try {
    & $PythonPath @ClipNestRunArgs
    if ($LASTEXITCODE -ne 0) { throw "ClipNest 退出，状态码：$LASTEXITCODE。" }
} finally {
    Pop-Location
}
