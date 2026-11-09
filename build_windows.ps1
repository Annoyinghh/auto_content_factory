param([string]$DistPath = (Join-Path $PSScriptRoot 'dist'))

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$projectPython = Join-Path $root '.venv\Scripts\python.exe'
$build = Join-Path $root 'build'
$buildArgs = @(
    '-m', 'PyInstaller', '--noconfirm', '--noupx', '--onefile', '--windowed',
    '--name', 'AutoContentFactory', '--workpath', $build, '--specpath', $build, '--distpath', $DistPath,
    '--icon', (Join-Path $root 'assets\screening\app.ico'),
    '--add-data', "$(Join-Path $root 'assets\bgm_ambient.wav'):assets",
    '--add-data', "$(Join-Path $root 'assets\copywriting_cases.json'):assets",
    '--add-data', "$(Join-Path $root 'assets\screening'):assets/screening",
    '--add-data', "$(Join-Path $root 'workbench_prototype.html'):.",
    '--collect-data', 'imageio_ffmpeg', '--exclude-module', 'numpy', (Join-Path $root 'desktop.py')
)
try {
    Push-Location -LiteralPath $root
    # Rebuild the Python archive when module contents change without changing the module list.
    $pythonArchiveIndex = Join-Path $build 'AutoContentFactory\PYZ-00.toc'
    if (Test-Path -LiteralPath $pythonArchiveIndex) { Remove-Item -LiteralPath $pythonArchiveIndex }
    & $projectPython @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)." }
} finally {
    Pop-Location
}
