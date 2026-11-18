param([string]$DistPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\tools'))

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $root '.venv\Scripts\python.exe'
$build = Join-Path $root 'build\team-host'
$source = Join-Path $root 'core\team_service.py'

& $projectPython -m PyInstaller --noconfirm --noupx --onefile --console `
    --name AutoContentFactory-Team-Host --workpath $build --specpath $build --distpath $DistPath $source
if ($LASTEXITCODE -ne 0) { throw "Team host build failed ($LASTEXITCODE)." }
