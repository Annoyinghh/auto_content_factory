param(
    [Parameter(Mandatory = $true)][string]$LegacyExe,
    [Parameter(Mandatory = $true)][string]$Candidate,
    [ValidateRange(1, 10)][int]$Runs = 3
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$study = New-Item -ItemType Directory -Force -Path (Join-Path $root ('build\startup-' + [Guid]::NewGuid().ToString('N')))
$old = New-Item -ItemType Directory -Path (Join-Path $study.FullName 'legacy')
$new = New-Item -ItemType Directory -Path (Join-Path $study.FullName 'native')
$name = 'AutoContentFactory-Studio.exe'
Copy-Item -LiteralPath (Resolve-Path -LiteralPath $LegacyExe).Path -Destination (Join-Path $old.FullName $name)
$bundle = (Resolve-Path -LiteralPath $Candidate).Path
foreach ($entry in $name, "$name.config", '_engine', 'browser_extension') {
    Copy-Item -LiteralPath (Join-Path $bundle $entry) -Destination $new.FullName -Recurse
}
$temp = New-Item -ItemType Directory -Path (Join-Path $study.FullName 'tmp')
$oldAppData = $env:LOCALAPPDATA
$oldTemp = $env:TEMP
$oldTmp = $env:TMP

function Measure-Workbench([string]$edition, [string]$path, [int]$run) {
    $env:LOCALAPPDATA = Join-Path $study.FullName "$edition-$run"
    $env:TEMP = $temp.FullName
    $env:TMP = $temp.FullName
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process -FilePath $path -ArgumentList '--check' -WindowStyle Hidden -PassThru
    $first = $null
    while (!$process.HasExited -and $clock.Elapsed.TotalSeconds -lt 75) {
        if ($null -eq $first) {
            $window = Get-Process -Name 'AutoContentFactory-Studio' -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -eq $path -and $_.MainWindowTitle -match '^热采放映室' } |
                Select-Object -First 1
            if ($window) { $first = [math]::Round($clock.Elapsed.TotalSeconds, 2) }
        }
        Start-Sleep -Milliseconds 40
    }
    if (!$process.HasExited) {
        Get-Process -Name 'AutoContentFactory-Studio' -ErrorAction SilentlyContinue |
            Where-Object Path -eq $path | Stop-Process
        throw "$edition did not finish the self-check in 75 seconds."
    }
    $proof = Join-Path $env:LOCALAPPDATA 'AutoContentFactory\desktop-check.txt'
    if ($process.ExitCode -ne 0 -or !(Test-Path -LiteralPath $proof -PathType Leaf)) {
        throw "$edition did not complete its startup checks."
    }
    [pscustomobject]@{
        Edition = $edition
        Run = $run
        WindowSeconds = $first
        CheckSeconds = [math]::Round($clock.Elapsed.TotalSeconds, 2)
    }
}

try {
    $results = for ($run = 1; $run -le $Runs; $run++) {
        if ($run % 2) {
            Measure-Workbench 'Tk' (Join-Path $old.FullName $name) $run
            Measure-Workbench 'WPF' (Join-Path $new.FullName $name) $run
        } else {
            Measure-Workbench 'WPF' (Join-Path $new.FullName $name) $run
            Measure-Workbench 'Tk' (Join-Path $old.FullName $name) $run
        }
    }
    $results | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $study.FullName 'startup.json')
    $results | Format-Table -AutoSize
    Write-Output (Join-Path $study.FullName 'startup.json')
} finally {
    $env:LOCALAPPDATA = $oldAppData
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
}
