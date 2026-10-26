# Builds the portable Windows app into dist\JobSearchHelper.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 [-Smoke]
#
# Safe to run when you have been using the built app: a data\ folder from the previous build
# (your resume, listings and settings) is kept, restored, and checked byte-for-byte. The build
# refuses to run while the app is open, because Windows locks its files.
param([switch]$Smoke)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Missing .venv. Create it first: python -m venv .venv ; .venv\Scripts\pip install -e .[dev]" }
if (Get-Process JobSearchHelper -ErrorAction SilentlyContinue) {
    throw "JobSearchHelper is running. Close it first so its files can be replaced."
}

$dist = Join-Path $root "dist\JobSearchHelper"
$data = Join-Path $dist "data"
$park = Join-Path ([IO.Path]::GetTempPath()) ("jobhelper_data_" + [guid]::NewGuid().ToString("N"))

function Get-DataFingerprint($folder) {
    Get-ChildItem $folder -Recurse -File | Get-FileHash |
        ForEach-Object { "$($_.Hash) $($_.Path.Substring($folder.Length))" } | Sort-Object
}

$before = $null
if (Test-Path $data) {
    $before = Get-DataFingerprint $data
    Copy-Item $data $park -Recurse          # safety copy outside dist, kept until the restore is verified
    Write-Host "Kept your data folder ($($before.Count) file(s)) at $park"
}

Remove-Item (Join-Path $root "dist"), (Join-Path $root "build") -Recurse -Force -ErrorAction SilentlyContinue

# PIL and tkinter are excluded because nothing in the app uses them (PIL only gets bundled if a dev
# tool installed it; tkinter was needed only by the removed clipboard code).
& $py -m PyInstaller --noconfirm --clean --name JobSearchHelper --windowed --paths src `
    --collect-submodules webview --exclude-module PIL --exclude-module tkinter `
    --add-data "src/jobhelper/models;jobhelper/models" `
    --add-data "src/jobhelper/web;jobhelper/web" `
    --add-data "src/jobhelper/data;jobhelper/data" `
    src/jobhelper/__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed. Your data is safe at $park" }

if ($before) {
    Copy-Item $park $data -Recurse
    $after = Get-DataFingerprint $data
    if (Compare-Object $before $after) { throw "Restored data differs from the original! The original is at $park" }
    Remove-Item $park -Recurse -Force
    Write-Host "Your data folder was restored and verified identical."
}

$size = [math]::Round(((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Built $dist ($size MB)"

if ($Smoke) {
    # Opens the real window against a throwaway data folder, checks the Python bridge, and exits.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("jobhelper_smoke_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory $tmp | Out-Null
    $env:JOBHELPER_DATA = Join-Path $tmp "data"
    $env:JOBHELPER_SMOKE_LOG = Join-Path $tmp "smoke.log"
    try {
        $proc = Start-Process (Join-Path $dist "JobSearchHelper.exe") -ArgumentList "--smoke" -PassThru -Wait
        Write-Host "Smoke test exit code: $($proc.ExitCode)"
        if (Test-Path $env:JOBHELPER_SMOKE_LOG) { Get-Content $env:JOBHELPER_SMOKE_LOG }
        if ($proc.ExitCode -ne 0) { throw "Smoke test failed." }
    } finally {
        Remove-Item Env:JOBHELPER_DATA, Env:JOBHELPER_SMOKE_LOG -ErrorAction SilentlyContinue
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
