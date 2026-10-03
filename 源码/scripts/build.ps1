[CmdletBinding()]
param(
    [string]$PythonPath = "",
    [switch]$Console
)

$ErrorActionPreference = "Stop"
$ClipNestRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ClipNestRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python 不存在：$PythonPath。请按 README 创建 .venv，或用 -PythonPath 指定解释器。"
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$ClipNestPreviousPath = $env:Path
$ClipNestBuild = Join-Path $ClipNestRoot "build"
$ClipNestDist = Join-Path $ClipNestRoot "dist"
$ClipNestIcon = Join-Path $ClipNestBuild "assets\ClipNest.ico"

Push-Location -LiteralPath $ClipNestRoot
try {
    & $PythonPath -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12 or newer is required'"
    if ($LASTEXITCODE -ne 0) { throw "Python 版本检查失败。" }
    $ClipNestPythonBase = (& $PythonPath -c "import sys; print(sys.base_prefix)").Trim()
    if ($LASTEXITCODE -ne 0) { throw "读取 Python 运行库位置失败。" }
    # Qt 6 使用 Windows 系统 ICU；不能从其他软件的 PATH 收集同名的不兼容 DLL。
    $env:Path = @(
        (Split-Path -Parent $PythonPath),
        $ClipNestPythonBase,
        (Join-Path $ClipNestPythonBase "DLLs"),
        [Environment]::SystemDirectory,
        $env:SystemRoot
    ) -join ";"
    & $PythonPath (Join-Path $PSScriptRoot "generate_icon.py") $ClipNestIcon
    if ($LASTEXITCODE -ne 0) { throw "生成图标失败。" }

    $ClipNestBuildArgs = @(
        "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--noupx",
        "--name", "ClipNest", "--icon", $ClipNestIcon,
        "--distpath", $ClipNestDist,
        "--workpath", (Join-Path $ClipNestBuild "pyinstaller"),
        "--specpath", $ClipNestBuild,
        "--paths", $ClipNestRoot,
        "--additional-hooks-dir", (Join-Path $PSScriptRoot "hooks"),
        "--collect-submodules", "pygments.lexers",
        "--collect-submodules", "pygments.styles"
    )
    if ($Console) { $ClipNestBuildArgs += "--console" } else { $ClipNestBuildArgs += "--windowed" }
    $ClipNestBuildArgs += Join-Path $ClipNestRoot "run_clipnest.py"
    & $PythonPath @ClipNestBuildArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败。" }
    $ClipNestExecutable = Join-Path $ClipNestDist "ClipNest\ClipNest.exe"
    if (-not (Test-Path -LiteralPath $ClipNestExecutable -PathType Leaf)) {
        throw "构建结束，但未发现 $ClipNestExecutable。"
    }
    Copy-Item -LiteralPath (Join-Path $ClipNestRoot "README.md") -Destination (Join-Path $ClipNestDist "ClipNest\README.md") -Force
    New-Item -ItemType Directory -Path (Join-Path $ClipNestDist "ClipNest\docs") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $ClipNestRoot "docs\RELEASE.md") -Destination (Join-Path $ClipNestDist "ClipNest\docs\RELEASE.md") -Force
    $ClipNestLicenseSource = Join-Path $ClipNestRoot "docs\licenses"
    $ClipNestLicenseTarget = Join-Path $ClipNestDist "ClipNest\docs\licenses"
    New-Item -ItemType Directory -Path $ClipNestLicenseTarget -Force | Out-Null
    Get-ChildItem -LiteralPath $ClipNestLicenseSource | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $ClipNestLicenseTarget -Recurse -Force
    }
    Write-Host "构建完成：$ClipNestExecutable"
    Write-Host "请分发整个 dist\ClipNest 目录，包括 _internal。"
} finally {
    $env:Path = $ClipNestPreviousPath
    Pop-Location
}
