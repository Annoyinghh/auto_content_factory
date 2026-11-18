param([Parameter(Mandatory = $true)][string]$OutputPath)

$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'studio'
$root = Split-Path -Parent $PSScriptRoot
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
$wpf = Join-Path $framework 'WPF'
$out = New-Item -ItemType Directory -Force -Path $OutputPath
$compile = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/codepage:65001',
    '/main:Acf.Studio.Program',
    "/out:$(Join-Path $out.FullName 'AutoContentFactory-Studio.exe')",
    "/win32icon:$(Join-Path $root 'assets\screening\app.ico')",
    "/win32manifest:$(Join-Path $PSScriptRoot 'workbench.manifest')",
    "/resource:$(Join-Path $source 'Theme.xaml'),Studio.Theme.xaml",
    "/resource:$(Join-Path $source 'Brand.xaml'),Studio.Brand.xaml",
    "/resource:$(Join-Path $source 'DashboardIllustration.jpg'),Studio.DashboardIllustration.jpg",
    "/resource:$(Join-Path $source 'WorksIllustration.jpg'),Studio.WorksIllustration.jpg",
    "/reference:$(Join-Path $framework 'System.Xaml.dll')",
    '/reference:System.Web.Extensions.dll', '/reference:System.Security.dll',
    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
    "/reference:$(Join-Path $wpf 'WindowsBase.dll')",
    "/reference:$(Join-Path $wpf 'PresentationCore.dll')",
    "/reference:$(Join-Path $wpf 'PresentationFramework.dll')"
)
$compile += @(Get-ChildItem -LiteralPath $source -File -Filter '*.cs' | ForEach-Object FullName)
& $compiler @compile
if ($LASTEXITCODE -ne 0) { throw "WPF compilation failed ($LASTEXITCODE)." }
Copy-Item -LiteralPath (Join-Path $source 'App.config') -Destination (Join-Path $out.FullName 'AutoContentFactory-Studio.exe.config') -Force
