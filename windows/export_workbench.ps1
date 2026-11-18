param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $Bundle).Path
$output = New-Item -ItemType Directory -Path $OutputPath
$payload = New-Item -ItemType Directory -Path (Join-Path $output.FullName 'payload')
$name = 'AutoContentFactory-Studio'
$archive = Join-Path $output.FullName "$name-WPF-Win64.zip"
$entries = @("$name.exe", "$name.exe.config", '_engine', 'browser_extension')
foreach ($entry in $entries) {
    Copy-Item -LiteralPath (Join-Path $source $entry) -Destination $payload.FullName -Recurse
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'first-run.txt') -Destination (Join-Path $payload.FullName 'START-HERE.txt')
$notices = Join-Path $payload.FullName 'THIRD-PARTY-NOTICES.txt'
@'
FFmpeg is a separate executable used for media processing.
This bundle uses the GPLv3 Gyan.dev Windows essentials build.
Build vendor and corresponding FFmpeg revision: https://www.gyan.dev/ffmpeg/builds/
GPLv3 terms: https://www.gnu.org/licenses/gpl-3.0.html
FFmpeg licensing and source information: https://ffmpeg.org/legal.html

The workbench also includes CPython and Python package dependencies, and uses
Microsoft WebView2 SDK components for the Douyin posting window.
CPython: https://docs.python.org/3/license.html
WebView2 SDK: https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3719.77

Bundled FFmpeg version and build settings:
'@ | Set-Content -LiteralPath $notices -Encoding utf8
$ffmpegDirectory = Join-Path $payload.FullName '_engine\_runtime\imageio_ffmpeg\binaries'
$ffmpeg = Get-ChildItem -LiteralPath $ffmpegDirectory -File -Filter 'ffmpeg*.exe' | Select-Object -First 1
if (!$ffmpeg) { throw 'The exported bundle is missing FFmpeg.' }
& $ffmpeg.FullName -version | Add-Content -LiteralPath $notices -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'The bundled FFmpeg did not report its version.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($payload.FullName, $archive, [IO.Compression.CompressionLevel]::Optimal, $false)

$unpacked = Join-Path $output.FullName 'unpacked'
[IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
$profile = Join-Path $output.FullName 'clean-profile'
$saved = @{}
foreach ($variable in 'LOCALAPPDATA', 'PATH', 'PYTHONPATH', 'IMAGEIO_FFMPEG_EXE') {
    $saved[$variable] = [Environment]::GetEnvironmentVariable($variable, 'Process')
}
try {
    $env:LOCALAPPDATA = $profile
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONPATH = $null
    $env:IMAGEIO_FFMPEG_EXE = $null
    $launcher = Join-Path $unpacked "$name.exe"
    $process = Start-Process -FilePath $launcher -ArgumentList '--check' -WindowStyle Hidden -PassThru
    if (!$process.WaitForExit(45000)) {
        Stop-Process -Id $process.Id
        throw 'Freshly unpacked release did not finish its self-check.'
    }
    $data = Join-Path $profile 'AutoContentFactory'
    if ($process.ExitCode -ne 0) {
        Get-Content -LiteralPath (Join-Path $data 'desktop-check-error.txt') -ErrorAction SilentlyContinue
        throw "Freshly unpacked release failed its self-check ($($process.ExitCode))."
    }
    Get-Content -LiteralPath (Join-Path $data 'desktop-check.txt')
} finally {
    foreach ($variable in $saved.Keys) {
        [Environment]::SetEnvironmentVariable($variable, $saved[$variable], 'Process')
    }
}
$checksum = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
Set-Content -Encoding ascii -LiteralPath (Join-Path $output.FullName "$name-WPF-Win64.sha256") -Value (
    "$checksum  $name-WPF-Win64.zip")
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'verify_team_client.ps1') -Destination $output.FullName
Get-Item -LiteralPath $archive | Select-Object FullName, Length
