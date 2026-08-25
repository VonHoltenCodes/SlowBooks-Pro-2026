# ============================================================================
# SlowBooks Pro - Server Edition install (Windows)
#
# Registers a scheduled task that runs the server at machine startup as
# SYSTEM (no login required), stores books machine-wide under
# C:\ProgramData\SlowBooksPro, and opens the firewall port. Run from an
# elevated PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File serveredition-install.ps1
#
# Undo everything with serveredition-uninstall.ps1.
# ============================================================================
#Requires -RunAsAdministrator
param(
    [int]$Port = 3001,
    [string]$ExePath = "",
    [string]$DataDir = "$env:ProgramData\SlowBooksPro"
)

$ErrorActionPreference = "Stop"
$TaskName = "SlowBooksProServer"
$RuleName = "SlowBooks Pro Server Edition"

# The script ships inside the bundle at _internal\scripts\windows\ -
# the exe is three levels up. Explicit -ExePath overrides.
if (-not $ExePath) {
    $ExePath = Join-Path $PSScriptRoot "..\..\..\SlowBooksPro.exe"
}
# Existence check must come first: Resolve-Path throws its own opaque
# error on a missing path under ErrorActionPreference=Stop, which aborted
# the whole install before the firewall/task steps with no guidance (#65).
if (-not (Test-Path $ExePath)) {
    throw ("SlowBooksPro.exe not found at $ExePath - run this script from " +
        "the installed app's _internal\scripts\windows\ folder, or pass -ExePath")
}
$ExePath = (Resolve-Path $ExePath).Path

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# Bring existing desktop-mode books along (docs promised this; the script
# previously only created an empty folder, #65). Copies company files, the
# manifest, the .env encryption key (without it, stored credentials cannot
# be decrypted), uploads, and backups - not the webview cache or logs.
# Only runs when the server data dir has no company files yet, so it can
# never clobber an active server's books.
$DesktopDir = Join-Path $env:LOCALAPPDATA "SlowBooksPro"
# Company files live in the data home's companies\ subfolder (root-level
# .db checked too, for older layouts).
function Test-HasBooks([string]$dir) {
    if (-not (Test-Path $dir)) { return $false }
    $roots = @(Get-ChildItem -Path $dir -Filter *.db -ErrorAction SilentlyContinue)
    $comps = @(Get-ChildItem -Path (Join-Path $dir "companies") -Filter *.db -ErrorAction SilentlyContinue)
    return ($roots.Count + $comps.Count) -gt 0
}
$serverHasBooks = Test-HasBooks $DataDir
$desktopHasBooks = Test-HasBooks $DesktopDir
if (-not $serverHasBooks -and $desktopHasBooks) {
    Write-Host ">> Copying your desktop books from $DesktopDir"
    Get-ChildItem -Path $DesktopDir -File -Force |
        Where-Object { $_.Extension -in ".db", ".json" -or $_.Name -like ".env*" } |
        ForEach-Object {
            Copy-Item $_.FullName -Destination $DataDir -Force
            Write-Host ("   " + $_.Name)
        }
    foreach ($sub in "companies", "uploads", "backups") {
        $src = Join-Path $DesktopDir $sub
        if (Test-Path $src) {
            Copy-Item $src -Destination $DataDir -Recurse -Force
            Write-Host ("   " + $sub + "\")
        }
    }
    Write-Host ">> Desktop copies are untouched; the server now uses $DataDir"
} elseif ($serverHasBooks) {
    Write-Host ">> $DataDir already has company files - leaving them as-is"
}

Write-Host ">> Opening firewall port $Port (rule: $RuleName)"
netsh advfirewall firewall delete rule name="$RuleName" | Out-Null
netsh advfirewall firewall add rule name="$RuleName" dir=in action=allow `
    protocol=TCP localport=$Port | Out-Null

Write-Host ">> Registering startup task $TaskName (runs as SYSTEM, no login needed)"
# PowerShell 5.1 mangles embedded double quotes when handing arguments to
# native commands: with the app in "C:\Program Files\SlowBooks Pro 2026",
# schtasks saw /TR split at the first space and rejected it (Invalid
# argument/option - 'Files\SlowBooks'), so the task was never created from
# a normal installed location. Backslash-quote is the one form PS passes
# through literally.
$TaskCmd = "\`"$ExePath\`" --serve-lan --port $Port --data-dir \`"$DataDir\`""
schtasks /Create /TN $TaskName /SC ONSTART /RU SYSTEM /RL HIGHEST /F /TR $TaskCmd | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "schtasks could not register the $TaskName task (exit $LASTEXITCODE)"
}

Write-Host ">> Starting the server now"
schtasks /Run /TN $TaskName | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "schtasks could not start the $TaskName task (exit $LASTEXITCODE)"
}
Start-Sleep -Seconds 8

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Host ""
Write-Host "SlowBooks Pro Server Edition is installed." -ForegroundColor Green
Write-Host "Books live in: $DataDir"
Write-Host "Your team connects at:"
Write-Host "    http://$($env:COMPUTERNAME):$Port"
foreach ($ip in $ips) { Write-Host "    http://${ip}:$Port" }
Write-Host ""
Write-Host "It starts automatically with Windows (before anyone logs in)."
Write-Host "Plain HTTP - trusted networks only."
