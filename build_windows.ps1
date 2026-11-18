param([string]$DistPath = (Join-Path $PSScriptRoot 'dist'))

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$build = Join-Path $root 'build'
$python = Join-Path $root '.venv\Scripts\python.exe'
$stage = Join-Path $build ('windows-' + [Guid]::NewGuid().ToString('N'))
$bundle = Join-Path $stage 'bundle'
$workerName = 'AutoContentFactory-Worker'
$postingPanel = Join-Path $build 'post_panel'

& (Join-Path $root 'windows\build_posting_panel.ps1') -OutputPath $postingPanel
$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--noupx', '--onedir', '--contents-directory', '_runtime', '--windowed',
    '--name', $workerName, '--workpath', $build, '--specpath', $build, '--distpath', $stage, '--paths', $root,
    '--manifest', (Join-Path $root 'windows\workbench.manifest'),
    '--add-data', "$(Join-Path $root 'assets\bgm_ambient.wav'):assets",
    '--add-data', "$(Join-Path $root 'assets\copywriting_cases.json'):assets",
    '--add-data', "$(Join-Path $root 'assets\video_styles.json'):assets",
    '--add-data', "$(Join-Path $root 'assets\screening\studio.png'):assets/screening",
    '--collect-data', 'imageio_ffmpeg', '--collect-data', 'faster_whisper',
    '--collect-binaries', 'ctranslate2',
    '--exclude-module', 'desktop', '--exclude-module', 'studio_reader',
    '--exclude-module', 'tkinter', '--exclude-module', 'PySide6',
    (Join-Path $root 'windows\worker_entry.py')
)
foreach ($file in 'PostingPanel.exe', 'SourceReader.exe', 'CaseReader.exe', 'Microsoft.Web.WebView2.Core.dll',
                 'Microsoft.Web.WebView2.WinForms.dll', 'WebView2Loader.dll') {
    $arguments += @('--add-binary', "$(Join-Path $postingPanel $file):post_panel")
}
Push-Location -LiteralPath $root
try {
    $index = Join-Path $build "$workerName\PYZ-00.toc"
    if (Test-Path -LiteralPath $index) { Remove-Item -LiteralPath $index }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Worker packaging failed ($LASTEXITCODE)." }
} finally {
    Pop-Location
}

& (Join-Path $root 'windows\build_workbench.ps1') -OutputPath $bundle
$engine = (Resolve-Path -LiteralPath (Join-Path $stage $workerName)).Path
if ((Split-Path -Parent $engine) -ne (Resolve-Path -LiteralPath $stage).Path) { throw 'Unexpected worker output path.' }
Move-Item -LiteralPath $engine -Destination (Join-Path $bundle '_engine')
$extension = New-Item -ItemType Directory -Path (Join-Path $bundle 'browser_extension')
foreach ($file in 'manifest.json', 'background.js', 'post.js') {
    Copy-Item -LiteralPath (Join-Path $root "browser_extension\$file") -Destination $extension.FullName
}
& (Join-Path $root 'windows\install_workbench.ps1') -Bundle $bundle -DistPath $DistPath
