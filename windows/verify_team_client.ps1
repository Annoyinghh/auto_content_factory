[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Package,
    [Parameter(Mandatory = $true)][string]$Checksum,
    [string]$ScratchRoot = [IO.Path]::GetTempPath(),
    [switch]$CheckOperations,
    [switch]$CheckReview
)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or ![Environment]::Is64BitOperatingSystem) {
    throw 'This acceptance check requires 64-bit Windows.'
}
$archive = (Resolve-Path -LiteralPath $Package).Path
$expected = ((Get-Content -Raw -LiteralPath $Checksum).Trim() -split '\s+')[0]
if ($expected -notmatch '\A[0-9a-fA-F]{64}\z') {
    throw 'The checksum file must start with the release SHA-256.'
}
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if ($actual -ne $expected) {
    throw 'The package does not match the release SHA-256. No application was started.'
}

$root = (Get-Item -LiteralPath $ScratchRoot).FullName
if (!(Test-Path -LiteralPath $root -PathType Container)) {
    throw 'ScratchRoot must be an existing directory.'
}
$scratchName = 'acf-client-check-' + [Guid]::NewGuid().ToString('N')
$scratch = (New-Item -ItemType Directory -Path (Join-Path $root $scratchName)).FullName
$originalEnvironment = @{}
foreach ($name in 'LOCALAPPDATA', 'PATH', 'PYTHONPATH', 'IMAGEIO_FFMPEG_EXE') {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Invoke-CheckedProcess {
    param([string]$Launcher, [string]$Arguments, [int]$TimeoutMs)

    $start = @{
        FilePath = $Launcher
        ArgumentList = $Arguments
        WorkingDirectory = Split-Path -Parent $Launcher
        WindowStyle = 'Hidden'
        PassThru = $true
    }
    $process = Start-Process @start
    try {
        if (!$process.WaitForExit($TimeoutMs)) {
            Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
            throw 'The workbench verification process timed out.'
        }
        return $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

function Test-ConfiguredTeamConnection {
    param([string]$Stage, [string]$Launcher, [string]$Scratch, [string]$LocalData)

    $request = Join-Path $Scratch "$Stage-request.json"
    $response = Join-Path $Scratch "$Stage-result.json"
    $output = Join-Path $LocalData 'AutoContentFactory\output'
    $command = @{
        stage = $Stage
        day = Get-Date -Format 'yyyy-MM-dd'
        platform = $null
        payload = $null
        output_dir = $output
        now = $null
    } | ConvertTo-Json -Compress
    [IO.File]::WriteAllText($request, $command, [Text.UTF8Encoding]::new($false))
    $arguments = '--worker "' + $request + '" "' + $response + '"'
    $exitCode = Invoke-CheckedProcess -Launcher $Launcher -Arguments $arguments -TimeoutMs 45000
    if ($exitCode -ne 0 -or !(Test-Path -LiteralPath $response -PathType Leaf)) {
        return 'unavailable'
    }
    $value = (Get-Content -LiteralPath $response -Raw -Encoding UTF8 | ConvertFrom-Json).value
    $address = $null
    if (!$value -or ![Uri]::TryCreate([string]$value.address, [UriKind]::Absolute, [ref]$address) -or
        $address.Scheme -ne 'https' -or $address.IsLoopback) {
        return 'not_connected'
    }
    if (($Stage -eq 'team_ops' -and $value.member) -or
        ($Stage -eq 'team_reviews' -and $value.PSObject.Properties.Name -contains 'items')) {
        return 'passed'
    }
    return 'unavailable'
}

try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $unpacked = Join-Path $scratch 'package'
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
    $launcher = Join-Path $unpacked 'AutoContentFactory-Studio.exe'
    $cleanProfile = Join-Path $scratch 'clean-profile'
    $env:LOCALAPPDATA = $cleanProfile
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONPATH = $null
    $env:IMAGEIO_FFMPEG_EXE = $null
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $exitCode = Invoke-CheckedProcess -Launcher $launcher -Arguments '--check' -TimeoutMs 45000
    $watch.Stop()
    $marker = Join-Path $cleanProfile 'AutoContentFactory\desktop-check.txt'
    if ($exitCode -ne 0 -or !(Test-Path -LiteralPath $marker -PathType Leaf) -or
        (Get-Content -LiteralPath $marker -Raw) -notmatch 'Native WPF, 7 workbench pages, local snapshot and bundled FFmpeg OK') {
        throw 'The clean Windows workbench self-check did not pass.'
    }

    $operations = 'not_checked'
    $review = 'not_checked'
    if ($CheckOperations -or $CheckReview) {
        $env:LOCALAPPDATA = $originalEnvironment['LOCALAPPDATA']
        if (!$env:LOCALAPPDATA) { throw 'The current Windows profile has no Local AppData directory.' }
        if ($CheckOperations) {
            $operations = Test-ConfiguredTeamConnection -Stage 'team_ops' -Launcher $launcher -Scratch $scratch -LocalData $env:LOCALAPPDATA
        }
        if ($CheckReview) {
            $review = Test-ConfiguredTeamConnection -Stage 'team_reviews' -Launcher $launcher -Scratch $scratch -LocalData $env:LOCALAPPDATA
        }
    }
    [ordered]@{
        package_sha256 = $actual
        verified_at_utc = [DateTime]::UtcNow.ToString('o')
        windows_version = [Environment]::OSVersion.Version.ToString()
        authenticode = (Get-AuthenticodeSignature -LiteralPath $launcher).Status.ToString()
        native_self_check = 'passed'
        self_check_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 2)
        team_operations = $operations
        team_review = $review
    } | ConvertTo-Json
    if (($CheckOperations -and $operations -ne 'passed') -or ($CheckReview -and $review -ne 'passed')) {
        throw 'A requested team connection did not pass. Check the workbench connection settings.'
    }
} finally {
    foreach ($name in $originalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
    $directory = Get-Item -LiteralPath $scratch
    if ($directory.FullName -eq $scratch -and $directory.Name -eq $scratchName -and
        $directory.Parent.FullName -eq $root -and
        !($directory.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        Remove-Item -LiteralPath $directory.FullName -Recurse -Force
    }
}
