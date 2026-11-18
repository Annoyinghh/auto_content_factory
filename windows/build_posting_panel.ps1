param([Parameter(Mandatory = $true)][string]$OutputPath)

$ErrorActionPreference = 'Stop'
$version = '1.0.3719.77'
$packageName = "microsoft.web.webview2.$version.nupkg"
$root = Split-Path -Parent $PSScriptRoot
$cache = New-Item -ItemType Directory -Force -Path (Join-Path $root 'build\webview2-sdk')
$out = New-Item -ItemType Directory -Force -Path $OutputPath
$package = Join-Path $cache.FullName $packageName
if (-not (Test-Path -LiteralPath $package)) {
    Invoke-WebRequest -UseBasicParsing -Uri "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/$packageName" -OutFile $package
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $package).Hash -ne '2F6BE3A10A1A8D6D1FDE986AF4131DAB344F8181FFFE75590824E0F4B037ED73') {
    throw "WebView2 SDK checksum did not match: $package"
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($package)
try {
    foreach ($name in @('lib/net462/Microsoft.Web.WebView2.Core.dll',
                        'lib/net462/Microsoft.Web.WebView2.WinForms.dll',
                        'runtimes/win-x64/native/WebView2Loader.dll')) {
        $entry = $archive.GetEntry($name)
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $out.FullName $entry.Name), $true)
    }
} finally {
    $archive.Dispose()
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$compile = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
    "/out:$(Join-Path $out.FullName 'PostingPanel.exe')",
    "/win32icon:$(Join-Path $root 'assets\screening\app.ico')",
    '/reference:System.Drawing.dll', '/reference:System.Windows.Forms.dll', '/reference:System.Web.Extensions.dll',
    "/reference:$(Join-Path $out.FullName 'Microsoft.Web.WebView2.Core.dll')",
    "/reference:$(Join-Path $out.FullName 'Microsoft.Web.WebView2.WinForms.dll')",
    (Join-Path $PSScriptRoot 'PostingPanel.cs')
)
& $compiler @compile
if ($LASTEXITCODE -ne 0) { throw "Posting panel compilation failed ($LASTEXITCODE)." }
foreach ($readerName in 'SourceReader', 'CaseReader') {
    $readerCompile = $compile | ForEach-Object {
        if ($_ -eq "/out:$(Join-Path $out.FullName 'PostingPanel.exe')") { "/out:$(Join-Path $out.FullName "$readerName.exe")" }
        elseif ($_ -eq (Join-Path $PSScriptRoot 'PostingPanel.cs')) { Join-Path $PSScriptRoot "$readerName.cs" }
        else { $_ }
    }
    & $compiler @readerCompile
    if ($LASTEXITCODE -ne 0) { throw "$readerName compilation failed ($LASTEXITCODE)." }
}
