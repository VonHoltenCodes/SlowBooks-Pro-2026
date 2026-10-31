# ============================================================================
# SlowBooks Pro - Server Edition in-place updater (Windows)
#
# One command to swap the server's program files from a GitHub Actions
# artifact and restart the scheduled task. Data is never touched.
#
#   # with GitHub CLI available (recommended):
#   powershell -ExecutionPolicy Bypass -File update-server-edition.ps1 -RunId 31651056187
#
#   # or with a manually downloaded artifact zip:
#   powershell -ExecutionPolicy Bypass -File update-server-edition.ps1 -ZipPath "$env:USERPROFILE\Downloads\SlowBooksPro-windows-x64.zip"
#
# ASCII only in this file - Windows PowerShell 5.1 parses BOM-less
# scripts as ANSI and em-dashes break the parser.
# ============================================================================
#Requires -RunAsAdministrator
param(
    [string]$RunId = "",
    [string]$ZipPath = "",
    [string]$InstallDir = "C:\SlowBooksServer\SlowBooksPro-windows-x64",
    [string]$Repo = "VonHoltenCodes/SlowBooks-Pro-2026",
    [int]$Port = 3001
)

$ErrorActionPreference = "Stop"
$stage = Join-Path $env:TEMP ("sb-update-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $stage | Out-Null

try {
    # ---- Acquire the build ------------------------------------------------
    if ($RunId) {
        if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
            throw "GitHub CLI (gh) not found - download the artifact manually and use -ZipPath"
        }
        Write-Host ">> Downloading artifact from run $RunId"
        gh run download $RunId -R $Repo -n SlowBooksPro-windows-x64 -D $stage
        if ($LASTEXITCODE -ne 0) { throw "gh run download failed" }
    }
    elseif ($ZipPath) {
        Write-Host ">> Extracting $ZipPath"
        Expand-Archive $ZipPath $stage -Force
    }
    else {
        throw "Pass -RunId <actions run id> or -ZipPath <downloaded zip>"
    }

    # Artifacts arrive as zip-in-zip; keep extracting until an exe appears.
    foreach ($round in 1..3) {
        $exe = Get-ChildItem $stage -Recurse -Filter SlowBooksPro.exe -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($exe) { break }
        $inner = Get-ChildItem $stage -Recurse -Filter *.zip | Select-Object -First 1
        if (-not $inner) { break }
        Expand-Archive $inner.FullName (Join-Path $stage "x$round") -Force
    }
    if (-not $exe) { throw "SlowBooksPro.exe not found in the artifact" }

    $srcDir = Split-Path $exe.FullName
    if (-not (Test-Path (Join-Path $srcDir "_internal"))) {
        throw "Extracted layout is wrong: no _internal beside the exe at $srcDir"
    }

    # ---- Swap -------------------------------------------------------------
    Write-Host ">> Stopping server"
    Get-Process -Name SlowBooksPro -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 2

    Write-Host ">> Swapping program files in $InstallDir"
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Remove-Item "$InstallDir\*" -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item "$srcDir\*" $InstallDir -Recurse

    $check = Get-ChildItem $InstallDir
    if (-not (Test-Path "$InstallDir\SlowBooksPro.exe") -or -not (Test-Path "$InstallDir\_internal")) {
        throw "Post-swap layout check failed - expected SlowBooksPro.exe + _internal"
    }

    Write-Host ">> Restarting server task"
    schtasks /Run /TN SlowBooksProServer | Out-Null

    # ---- Verify -----------------------------------------------------------
    Write-Host ">> Waiting for /health"
    $ok = $false
    foreach ($i in 1..30) {
        Start-Sleep -Seconds 2
        try {
            $h = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 3
            Write-Host ("UPDATED OK - server healthy, version " + $h.version) -ForegroundColor Green
            $ok = $true
            break
        } catch { }
    }
    if (-not $ok) {
        throw "Server did not become healthy within 60s - check C:\ProgramData\SlowBooksPro\data\launcher.log"
    }
}
finally {
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
}
