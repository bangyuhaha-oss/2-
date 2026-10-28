param(
    [switch]$InstallOnly,
    [string]$EnvironmentPath,
    [string]$PythonPath
)
# 只接受与离线 wheel 一致的 Windows CPython 3.12 64 位；不删除已有虚拟环境。
$ErrorActionPreference = 'Stop'
if ($EnvironmentPath) { $EnvironmentPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($EnvironmentPath) }
if ($PythonPath) { $PythonPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PythonPath) }
Set-Location -LiteralPath $PSScriptRoot
if (-not $EnvironmentPath) { $EnvironmentPath = Join-Path $PSScriptRoot '.venv' }
$EnvironmentPath = [IO.Path]::GetFullPath($EnvironmentPath)
$taskEnvironmentPython = Join-Path $EnvironmentPath 'Scripts\python.exe'
$taskProbe = "import sys,struct; raise SystemExit(0 if sys.implementation.name == 'cpython' and sys.platform == 'win32' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"

function Test-TaskPython([string]$Executable, [string[]]$PrefixArguments) {
    try {
        & $Executable @PrefixArguments -c $taskProbe 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

try {
    if (Test-Path -LiteralPath $taskEnvironmentPython) {
        if (-not (Test-TaskPython $taskEnvironmentPython @())) {
            throw 'Existing .venv is not usable Windows CPython 3.12 x64. Move it aside and run Setup.cmd again; it is not a portable runtime.'
        }
    } else {
        $taskCandidates = @()
        if ($PythonPath) { $taskCandidates += @{ File=$PythonPath; Arguments=@() } }
        if ($env:PYTHON312 -and -not $PythonPath) { $taskCandidates += @{ File=$env:PYTHON312; Arguments=@() } }
        if (-not $PythonPath) {
            $taskLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
            if ($taskLauncher) { $taskCandidates += @{ File=$taskLauncher.Source; Arguments=@('-3.12') } }
            $taskPathPython = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($taskPathPython -and $taskPathPython.Source -notlike '*\WindowsApps\*') {
                $taskCandidates += @{ File=$taskPathPython.Source; Arguments=@() }
            }
            # 仅本机便捷回退；新电脑仍需自行安装 CPython 3.12 x64。
            $taskBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
            if (Test-Path -LiteralPath $taskBundledPython) { $taskCandidates += @{ File=$taskBundledPython; Arguments=@() } }
        }
        $taskSelected = $null
        foreach ($taskCandidate in $taskCandidates) {
            if (Test-TaskPython $taskCandidate.File $taskCandidate.Arguments) { $taskSelected = $taskCandidate; break }
        }
        if (-not $taskSelected) { throw 'Install Windows CPython 3.12 x64 from python.org (with pip and venv). Or set PYTHON312 to its python.exe. This wheelhouse is not for Python 3.13/3.14, 32-bit, ARM64, macOS or Linux.' }
        $taskSelectedFile = $taskSelected.File
        $taskSelectedArguments = $taskSelected.Arguments
        & $taskSelectedFile @taskSelectedArguments -m venv $EnvironmentPath
        if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
    }
    # 禁用网络索引和缓存，只从随包 wheelhouse 安装完整锁定的运行依赖。
    $taskPipArguments = @('-m', 'pip', 'install', '--no-index', '--no-cache-dir', '--only-binary=:all:', '--find-links', (Join-Path $PSScriptRoot 'wheelhouse'), '-r', (Join-Path $PSScriptRoot 'requirements.txt'))
    Write-Host 'Installing the locked runtime from local wheelhouse...'
    & $taskEnvironmentPython @taskPipArguments
    if ($LASTEXITCODE -ne 0) { throw 'Offline installation failed. Check Python 3.12 x64 and the complete wheelhouse directory.' }
    & $taskEnvironmentPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency validation failed.' }
    & $taskEnvironmentPython -c "import sys,flask,sqlite3; print('Python',sys.version.split()[0],'/ SQLite',sqlite3.sqlite_version); print('Offline runtime ready.')"
    if ($LASTEXITCODE -ne 0) { throw 'Runtime import failed.' }
    if (-not $InstallOnly) { Write-Host 'Setup completed. Run Start.cmd for the website, or ..\scrum\Start_Board.cmd for the internal board.' }
    exit 0
} catch {
    Write-Host ('Setup failed: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
