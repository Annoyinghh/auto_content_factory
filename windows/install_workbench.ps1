param(
    [Parameter(Mandatory = $true)][string]$Bundle,
    [Parameter(Mandatory = $true)][string]$DistPath
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = (Resolve-Path -LiteralPath $Bundle).Path
$destination = (New-Item -ItemType Directory -Force -Path $DistPath).FullName
if ($source -eq $destination) { throw 'Bundle and destination must be different directories.' }

$appName = 'AutoContentFactory-Studio'
$exeName = "$appName.exe"
$configName = "$exeName.config"
$launcher = Join-Path $destination $exeName
$engine = Join-Path $destination '_engine'
$previousRuntime = Join-Path $destination '_studio'
$extensions = Join-Path $destination 'browser_extension'
$expected = @($exeName, $configName, '_engine\AutoContentFactory-Worker.exe', 'browser_extension\manifest.json')
foreach ($file in $expected) {
    if (!(Test-Path -LiteralPath (Join-Path $source $file) -PathType Leaf)) { throw "Incomplete bundle: $file" }
}
$prefix = $destination.TrimEnd('\') + '\'
$active = Get-Process -Name $appName,'AutoContentFactory-Worker','PostingPanel' -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }
if ($active) { throw 'Close the workbench and its posting windows before updating.' }

$incoming = New-Item -ItemType Directory -Path (Join-Path $destination ('.update-' + [Guid]::NewGuid().ToString('N')))
$archive = $null
$moved = New-Object 'System.Collections.Generic.List[string]'
$installed = New-Object 'System.Collections.Generic.List[string]'
try {
    foreach ($file in $exeName, $configName, '_engine', 'browser_extension') {
        Copy-Item -LiteralPath (Join-Path $source $file) -Destination $incoming.FullName -Recurse
    }
    foreach ($file in $expected) {
        if ((Get-FileHash -LiteralPath (Join-Path $source $file)).Hash -ne
            (Get-FileHash -LiteralPath (Join-Path $incoming.FullName $file)).Hash) {
            throw "Copied bundle does not match the source: $file"
        }
    }
    if ((Test-Path -LiteralPath $launcher) -or (Test-Path -LiteralPath $engine) -or
        (Test-Path -LiteralPath $previousRuntime) -or (Test-Path -LiteralPath $extensions)) {
        $archive = New-Item -ItemType Directory -Force -Path (Join-Path $root (
            'build\windows-release-archive\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' +
            [Guid]::NewGuid().ToString('N').Substring(0, 8)))
        if (Test-Path -LiteralPath $launcher) {
            Copy-Item -LiteralPath $launcher -Destination $archive.FullName
        }
        foreach ($name in '_engine', '_studio', 'browser_extension', $configName) {
            $path = Join-Path $destination $name
            if (Test-Path -LiteralPath $path) {
                $resolved = Get-Item -LiteralPath $path
                if ($resolved.FullName -ne $path -or ($resolved.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Unexpected runtime path: $path"
                }
                Move-Item -LiteralPath $resolved.FullName -Destination $archive.FullName
                $moved.Add($name)
            }
        }
    }
    foreach ($name in '_engine', 'browser_extension', $configName) {
        $path = Join-Path $incoming.FullName $name
        if ((Split-Path -Parent (Resolve-Path -LiteralPath $path).Path) -ne $incoming.FullName) {
            throw "Unexpected staged runtime path: $path"
        }
        Move-Item -LiteralPath $path -Destination $destination
        $installed.Add($name)
    }
    $pending = Join-Path $incoming.FullName $exeName
    if (Test-Path -LiteralPath $launcher) {
        [IO.File]::Replace($pending, $launcher, [NullString]::Value)
    } else {
        [IO.File]::Move($pending, $launcher)
    }
} catch {
    foreach ($name in $installed) {
        Move-Item -LiteralPath (Join-Path $destination $name) -Destination $incoming.FullName
    }
    foreach ($name in $moved) {
        Move-Item -LiteralPath (Join-Path $archive.FullName $name) -Destination $destination
    }
    throw
} finally {
    if (!(Get-ChildItem -LiteralPath $incoming.FullName -Force)) {
        Remove-Item -LiteralPath $incoming.FullName
    }
}
Write-Output $launcher
