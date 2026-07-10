# ============================================================================
# SlowBooks Pro 2026 — native Windows desktop setup.
#
# Fetched and run by "Setup SlowBooks Pro.bat" (which self-elevates first).
# Installs, with NO Docker and NO WSL2:
#   - the SlowBooks Pro app source (plain HTTPS zip download, no git)
#   - Python 3.13 (winget, falling back to the python.org installer)
#   - the GTK3 runtime (Cairo/Pango/gdk-pixbuf DLLs WeasyPrint needs
#     to render PDFs on native Windows)
#   - the app's Python dependencies + pywebview (native window)
# then creates a Desktop shortcut and launches the app.
#
# Every step is idempotent: it checks "is this already done?" before doing
# anything, so the script is safe to re-run after an interruption or a
# manual fix. No step requires (or ever performs) a Windows restart.
# ============================================================================

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RepoZipUrl  = 'https://github.com/moshgrossman/SlowBooks-Pro-2026/archive/refs/heads/main.zip'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'SlowBooksPro'
$AppDir      = Join-Path $InstallRoot 'app'
$GtkBinDir   = Join-Path $env:ProgramFiles 'GTK3-Runtime Win64\bin'

# Fallback used only if winget is unavailable or fails. Kept pinned because
# python.org has no "latest installer" URL; bump occasionally.
$PythonFallbackUrl = 'https://www.python.org/ftp/python/3.13.5/python-3.13.5-amd64.exe'

function Banner([string]$msg) {
    Write-Host ''
    Write-Host "==== $msg ====" -ForegroundColor Cyan
}

function Fail([string]$msg) {
    Write-Host ''
    Write-Host "SETUP STOPPED: $msg" -ForegroundColor Red
    Write-Host 'Fix the issue above, then run this setup again — completed steps are skipped automatically.'
    exit 1
}

# Pick up PATH changes made by installers without needing a new console
# (or a restart).
function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Resolve a REAL Python 3 interpreter path. "python" on a fresh Windows
# install is often the Microsoft Store alias stub, which is not Python —
# so verify by actually running it and resolving sys.executable.
function Get-Python {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            [string]$exe = (& $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch { }
    }
    $direct = Get-Command python -ErrorAction SilentlyContinue
    if ($direct) {
        try {
            [string]$exe = (& $direct.Source -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
        } catch { }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Step 0 — fetch the app source (plain zip download, no git needed)
# ---------------------------------------------------------------------------
Banner 'Step 0/5: SlowBooks Pro application files'
if (Test-Path (Join-Path $AppDir 'desktop_launcher.py')) {
    Write-Host "Already present at $AppDir — skipping download."
    # Never re-fetch over an existing install; company data lives in the
    # sibling 'data' folder and is never touched here either way.
} else {
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    $zip = Join-Path $env:TEMP 'SlowBooksPro-main.zip'
    $extract = Join-Path $env:TEMP 'SlowBooksPro-extract'
    Write-Host "Downloading $RepoZipUrl ..."
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $RepoZipUrl -OutFile $zip
    } catch {
        Fail "Could not download the application ($($_.Exception.Message)). Check your internet connection."
    }
    if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
    Expand-Archive -Path $zip -DestinationPath $extract
    # The zip contains a single "<repo>-main" root folder — that becomes $AppDir.
    $inner = Get-ChildItem -Directory $extract | Select-Object -First 1
    Move-Item $inner.FullName $AppDir
    Remove-Item -Force $zip
    Remove-Item -Recurse -Force $extract
    Write-Host "Installed application files to $AppDir"
}

# ---------------------------------------------------------------------------
# Step 1 — Python
# ---------------------------------------------------------------------------
Banner 'Step 1/5: Python'
$python = Get-Python
if ($python) {
    Write-Host "Found Python: $python — skipping install."
} else {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host 'Installing Python 3.13 via winget...'
        try {
            & winget install --id Python.Python.3.13 -e --silent `
                --accept-package-agreements --accept-source-agreements
        } catch {
            Write-Host "winget install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        Update-SessionPath
        $python = Get-Python
    }
    if (-not $python) {
        Write-Host 'Falling back to the official python.org installer...'
        $pyInstaller = Join-Path $env:TEMP 'python-installer.exe'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $PythonFallbackUrl -OutFile $pyInstaller
            # Silent, all users, on PATH. Does not require a restart.
            Start-Process -FilePath $pyInstaller -Wait -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1'
            Remove-Item -Force $pyInstaller -ErrorAction SilentlyContinue
        } catch {
            Write-Host "python.org installer failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        Update-SessionPath
        $python = Get-Python
    }
    if (-not $python) {
        Fail ("Python could not be installed automatically. Install it manually from " +
              "https://www.python.org/downloads/ (check 'Add python.exe to PATH'), then run this file again.")
    }
    Write-Host "Installed Python: $python"
}

# ---------------------------------------------------------------------------
# Step 2 — GTK3 runtime (WeasyPrint's PDF-rendering libraries)
#
# !! HIGHEST-UNCERTAINTY STEP — verify on a real Windows machine !!
# WeasyPrint needs Cairo/Pango/gdk-pixbuf, which have no pip wheels on
# Windows. WeasyPrint's own docs point to this prebuilt GTK3 runtime
# installer. Two things MUST be confirmed by an actual test (not assumed):
#   (a) the silent install (/S) completes and puts DLLs in $GtkBinDir,
#   (b) WeasyPrint can actually render a PDF afterward.
# The bin dir is force-added to the machine PATH below because the NSIS
# silent mode may not tick the installer's own "set PATH" option.
# ---------------------------------------------------------------------------
Banner 'Step 2/5: PDF rendering component (GTK3 runtime)'
if (Test-Path (Join-Path $GtkBinDir 'libgobject-2.0-0.dll')) {
    Write-Host "GTK3 runtime already installed at $GtkBinDir — skipping."
} else {
    Write-Host 'Looking up the latest GTK3 runtime release...'
    try {
        # Always resolve the latest release rather than hardcoding a version.
        $release = Invoke-RestMethod -UseBasicParsing `
            -Uri 'https://api.github.com/repos/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases/latest'
        $asset = $release.assets | Where-Object { $_.name -match '\.exe$' } | Select-Object -First 1
        if (-not $asset) { throw 'No installer asset found in the latest release.' }
        $gtkInstaller = Join-Path $env:TEMP $asset.name
        Write-Host "Downloading $($asset.name)..."
        Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $gtkInstaller
        # NSIS silent install. No restart needed or performed.
        Start-Process -FilePath $gtkInstaller -Wait -ArgumentList '/S'
        Remove-Item -Force $gtkInstaller -ErrorAction SilentlyContinue
    } catch {
        Fail ("Could not install the GTK3 runtime ($($_.Exception.Message)). Install it manually from " +
              'https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases ' +
              'then run this file again.')
    }
    if (-not (Test-Path (Join-Path $GtkBinDir 'libgobject-2.0-0.dll'))) {
        Fail "GTK3 runtime installer finished but $GtkBinDir is missing its DLLs."
    }
    Write-Host 'GTK3 runtime installed.'
}
# Ensure the DLLs are findable by WeasyPrint regardless of installer options.
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
if ($machinePath -notlike "*$GtkBinDir*") {
    [Environment]::SetEnvironmentVariable('Path', "$machinePath;$GtkBinDir", 'Machine')
    Write-Host "Added $GtkBinDir to the system PATH."
}
Update-SessionPath

# ---------------------------------------------------------------------------
# Step 3 — Python dependencies
# ---------------------------------------------------------------------------
Banner 'Step 3/5: Python packages'
Push-Location $AppDir
try {
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements.txt failed (see output above).' }
    & $python -m pip install -r requirements-desktop.txt
    if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements-desktop.txt failed (see output above).' }
} finally {
    Pop-Location
}
Write-Host 'Python packages installed.'

# ---------------------------------------------------------------------------
# Step 4 — first-run configuration (.env)
# ---------------------------------------------------------------------------
Banner 'Step 4/5: First-run configuration'
Push-Location $AppDir
try {
    & $python desktop_launcher.py --setup-only
    if ($LASTEXITCODE -ne 0) { Fail 'First-run configuration failed (see output above).' }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Step 5 — Desktop shortcut + first launch
# ---------------------------------------------------------------------------
Banner 'Step 5/5: Desktop shortcut'
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'SlowBooks Pro.lnk'
$launchBat = Join-Path $AppDir 'Launch SlowBooks Pro.bat'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launchBat
$shortcut.WorkingDirectory = $AppDir
$shortcut.Description = 'SlowBooks Pro 2026'
$shortcut.Save()
Write-Host "Created shortcut: $shortcutPath"

Write-Host ''
Write-Host 'Setup complete! Opening SlowBooks Pro now...' -ForegroundColor Green
Write-Host 'Next time, use the "SlowBooks Pro" shortcut on your Desktop.'
# Launch through explorer.exe so the app runs as the normal (non-elevated)
# user rather than inheriting this script's Administrator token.
Start-Process explorer.exe -ArgumentList "`"$launchBat`""
