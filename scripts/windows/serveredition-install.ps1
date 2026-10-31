# ============================================================================
# SlowBooks Pro - Server Edition install (Windows)
#
# Registers a scheduled task that runs the server at machine startup as
# SYSTEM (no login required), keeps the books machine-wide under
# C:\ProgramData\SlowBooksPro (readable by administrators only), and opens
# the firewall port on private and domain networks. Run from an elevated
# PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File serveredition-install.ps1
#
# Run it again after an update: it stops the server, re-registers the task
# against the installed exe and starts the server. Undo everything with
# serveredition-uninstall.ps1.
#
# ASCII only in this file - Windows PowerShell 5.1 parses BOM-less scripts
# as ANSI and em-dashes break the parser.
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

# The layout the app uses everywhere: $DataDir is the home, the books live
# in $DataDir\data (companies\, companies.json, backups\, launcher.log) and
# the .env with the encryption and session keys sits beside data\, at
# $DataDir\.env. Up to 2.31.2 the task passed --data-dir $DataDir itself,
# which put the books one level up and the .env at C:\ProgramData\.env;
# an install from then is moved into place below.
$ServerData = Join-Path $DataDir "data"

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

function Test-HasBooks([string]$dir) {
    if (-not (Test-Path $dir)) { return $false }
    $roots = @(Get-ChildItem -Path $dir -Filter *.db -File -ErrorAction SilentlyContinue)
    $comps = @(Get-ChildItem -Path (Join-Path $dir "companies") -Filter *.db -ErrorAction SilentlyContinue)
    return ($roots.Count + $comps.Count) -gt 0
}

# Nothing is moved or copied while a server holds the files open.
Write-Host ">> Stopping any running server"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-Process -Name SlowBooksPro -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

New-Item -ItemType Directory -Force -Path $ServerData | Out-Null

# A 2.31.2-or-earlier server install: books directly under $DataDir and
# the .env at C:\ProgramData\.env. Move them into the layout above once;
# a later run finds nothing to move.
if ((Test-HasBooks $DataDir) -and -not (Test-HasBooks $ServerData)) {
    Write-Host ">> Moving the books from $DataDir into $ServerData"
    foreach ($name in "companies", "uploads", "backups", "companies.json", "connect-urls.txt", "launcher.log") {
        $src = Join-Path $DataDir $name
        if (Test-Path $src) {
            Move-Item $src -Destination $ServerData -Force
            Write-Host ("   " + $name)
        }
    }
    Get-ChildItem -Path $DataDir -Filter *.db -File | ForEach-Object {
        Move-Item $_.FullName -Destination $ServerData -Force
        Write-Host ("   " + $_.Name)
    }
    $oldEnv = Join-Path $env:ProgramData ".env"
    $newEnv = Join-Path $DataDir ".env"
    if ((Test-Path $oldEnv) -and -not (Test-Path $newEnv)) {
        Move-Item $oldEnv -Destination $newEnv
        Write-Host "   .env (the encryption and session keys; it was at $oldEnv)"
    }
    if (Test-Path "C:\SlowBooksServer") {
        Write-Host "   (C:\SlowBooksServer holds only the old program now; delete it when you like)"
    }
}

# Bring existing desktop-mode books along (docs promised this; the script
# previously only created an empty folder, #65, and then looked one folder
# too high, so it copied nothing). Copies the company files, the manifest,
# the .env (without it, payroll bank numbers and stored credentials cannot
# be decrypted), uploads and backups - not the webview cache or logs. Only
# runs when the server has no company files yet, so it can never clobber
# an active server's books.
$DesktopHome = Join-Path $env:LOCALAPPDATA "SlowBooksPro"
$DesktopData = Join-Path $DesktopHome "data"
if (-not (Test-HasBooks $ServerData) -and (Test-HasBooks $DesktopData)) {
    Write-Host ">> Copying your desktop books from $DesktopData"
    foreach ($name in "companies", "uploads", "backups", "companies.json") {
        $src = Join-Path $DesktopData $name
        if (Test-Path $src) {
            Copy-Item $src -Destination $ServerData -Recurse -Force
            Write-Host ("   " + $name)
        }
    }
    Get-ChildItem -Path $DesktopData -Filter *.db -File | ForEach-Object {
        Copy-Item $_.FullName -Destination $ServerData -Force
        Write-Host ("   " + $_.Name)
    }
    $desktopEnv = Join-Path $DesktopHome ".env"
    $newEnv = Join-Path $DataDir ".env"
    if ((Test-Path $desktopEnv) -and -not (Test-Path $newEnv)) {
        Copy-Item $desktopEnv -Destination $newEnv
        Write-Host "   .env"
    }
    Write-Host ">> Desktop copies are untouched; the server now uses $ServerData"
} elseif (Test-HasBooks $ServerData) {
    Write-Host ">> $ServerData already has company files - leaving them as-is"
}

# C:\ProgramData lets every local account read what is created under it,
# which here would be every company file and the .env with the keys. The
# server runs as SYSTEM; administrators keep access for backups.
Write-Host ">> Restricting $DataDir to SYSTEM and Administrators"
# The grant goes on the folder only: (OI)(CI) entries apply to what is under
# it by inheritance. A /T grant with /inheritance:r stripped every existing
# file's entries and gave it none (a file takes no (OI)(CI) entry), so the
# server could not read companies.json. The /reset then makes every existing
# file and folder inherit the two entries.
icacls $DataDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" /Q | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "icacls could not set $DataDir (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
if (Get-ChildItem $DataDir -Force -ErrorAction SilentlyContinue | Select-Object -First 1) {
    icacls "$DataDir\*" /reset /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "icacls could not reset the files under $DataDir (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
}

# Private and domain networks only: a laptop host on a public network
# (netsh's default is every profile) would serve the sign-in screen, the
# company list and the usernames to it.
Write-Host ">> Opening firewall port $Port on private and domain networks (rule: $RuleName)"
netsh advfirewall firewall delete rule name="$RuleName" | Out-Null
netsh advfirewall firewall add rule name="$RuleName" dir=in action=allow `
    protocol=TCP localport=$Port profile=domain,private | Out-Null

# The task: at startup, as SYSTEM, with no time limit (schtasks /Create left
# Task Scheduler's defaults, which stop a task after 72 hours and stop or
# refuse it on battery), restarted if it dies, one instance at a time.
# Register-ScheduledTask takes the exe path and its arguments separately,
# so the quoting that broke schtasks /TR on "C:\Program Files" is gone.
Write-Host ">> Registering startup task $TaskName (runs as SYSTEM, no login needed)"
$Action = New-ScheduledTaskAction -Execute $ExePath `
    -Argument "--serve-lan --port $Port --data-dir `"$ServerData`""
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Principal $Principal -Settings $Settings -Force | Out-Null

Write-Host ">> Starting the server now"
Start-ScheduledTask -TaskName $TaskName
$healthy = $false
for ($i = 0; $i -lt 30 -and -not $healthy; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $healthy = $true }
    } catch { }
}
if (-not $healthy) {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host ">> The server did not answer on port $Port within a minute." -ForegroundColor Red
    if ($info) { Write-Host ("   Task state: " + (Get-ScheduledTask -TaskName $TaskName).State + ", last result " + $info.LastTaskResult) }
    $log = Join-Path $ServerData "launcher.log"
    if (Test-Path $log) {
        Write-Host "   The last lines of $log :"
        Get-Content $log -Tail 15 | ForEach-Object { Write-Host ("   " + $_) }
    }
    Write-Host "   Fix what it says, then: Start-ScheduledTask -TaskName $TaskName"
    exit 1
}

# A Windows network marked Public gets no port from the rule above (Windows
# puts a new network there until someone says it is private).
$public = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq "Public" }
if ($public) {
    Write-Host ""
    Write-Host ">> This PC's network is marked Public in Windows, so the firewall rule does not apply to it" -ForegroundColor Yellow
    foreach ($p in $public) {
        Write-Host ("   If " + $p.Name + " is your office network, mark it private (as administrator):")
        Write-Host ("   Set-NetConnectionProfile -InterfaceIndex " + $p.InterfaceIndex + " -NetworkCategory Private")
    }
}

$ips = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -ExpandProperty IPAddress

Write-Host ""
Write-Host "SlowBooks Pro Server Edition is installed." -ForegroundColor Green
Write-Host "Books live in: $ServerData"
Write-Host "Keys (keep with your backups): $DataDir\.env"
Write-Host "Your team connects at:"
Write-Host "    http://$($env:COMPUTERNAME):$Port"
foreach ($ip in $ips) { Write-Host "    http://${ip}:$Port" }
Write-Host ""
Write-Host "It starts automatically with Windows (before anyone logs in)."
Write-Host "If this is a new company, open http://localhost:$Port on this PC now and set the"
Write-Host "administrator password: until it is set, anyone on the network could."
Write-Host "Plain HTTP - trusted networks only."
