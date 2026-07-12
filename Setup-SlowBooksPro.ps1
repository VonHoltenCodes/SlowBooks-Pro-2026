# ============================================================================
# SlowBooks Pro 2026 -- native Windows desktop setup.
#
# Fetched and run by "Setup SlowBooks Pro.bat" (which self-elevates first).
# Installs, with NO Docker and NO WSL2:
#   - the SlowBooks Pro app source (plain HTTPS zip download, no git)
#   - Python 3.13 (winget, falling back to the python.org installer)
#   - the Microsoft WebView2 runtime, if missing (renders the native
#     window; preinstalled on current Windows, absent on older images)
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
    Write-Host 'Fix the issue above, then run this setup again -- completed steps are skipped automatically.'
    exit 1
}

# Pick up PATH changes made by installers without needing a new console
# (or a restart).
function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Run a candidate interpreter and return its real sys.executable path, or
# $null. Deliberately NO stderr redirection: under Windows PowerShell 5.1
# with $ErrorActionPreference='Stop', redirecting a native command's stderr
# (2>$null) turns ANY stderr text into a terminating error -- which made an
# already-installed Python look missing here. Unredirected stderr just
# prints, and a failing candidate is caught by the exit code instead.
function Resolve-PythonPath([string]$exe, [string[]]$prefixArgs) {
    try {
        $ErrorActionPreference = 'Continue'  # scoped to this function
        $out = & $exe @prefixArgs '-c' 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -eq 0 -and $out) {
            return ([string]($out | Select-Object -First 1)).Trim()
        }
    } catch { }
    return $null
}

# Resolve a REAL Python 3 interpreter path. "python" on a fresh Windows
# install is often the Microsoft Store alias stub, which is not Python --
# so every candidate is verified by actually running it. PATH can also be
# stale in this console (Python installed by an earlier setup run), so the
# standard install directories are probed directly as a last resort.
function Get-Python {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $found = Resolve-PythonPath $py.Source @('-3')
        if ($found) { return $found }
        Write-Host "    ('py' launcher at $($py.Source) could not run Python 3)"
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $found = Resolve-PythonPath $python.Source @()
        if ($found) { return $found }
        Write-Host "    ('python' at $($python.Source) did not run -- likely the Microsoft Store stub)"
    }
    foreach ($pattern in @(
        (Join-Path $env:ProgramFiles 'Python3*\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*\python.exe')
    )) {
        $exes = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending
        foreach ($exe in $exes) {
            $found = Resolve-PythonPath $exe.FullName @()
            if ($found) {
                Write-Host "    (not on this console's PATH, but found at $found)"
                return $found
            }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Step 0 -- fetch the app source (plain zip download, no git needed)
#
# Version-marker based: the repo ships a DESKTOP_INSTALL_VERSION file, and a
# matching marker in $AppDir means the snapshot is current. A mismatched or
# missing marker (including installs made by the retired WSL2-based setup,
# which had no marker) gets its app files replaced wholesale. That is safe:
# company data lives in the sibling 'data' folder and is never touched; the
# only per-install file inside $AppDir is .env, which is preserved because
# it holds the generated PAYROLL_ENCRYPTION_SECRET that encrypted data
# depends on.
# ---------------------------------------------------------------------------
Banner 'Step 0/6: SlowBooks Pro application files'
$RequiredAppVersion = '8'
$markerPath = Join-Path $AppDir 'DESKTOP_INSTALL_VERSION'
$installedVersion = ''
if (Test-Path $markerPath) {
    $installedVersion = (Get-Content $markerPath -First 1).Trim()
}
if ((Test-Path (Join-Path $AppDir 'desktop_launcher.py')) -and ($installedVersion -eq $RequiredAppVersion)) {
    Write-Host "Already present (version $installedVersion) at $AppDir -- skipping download."
} else {
    if (Test-Path $AppDir) {
        Write-Host 'Found application files from an older setup -- replacing them (your data and settings are kept).'
    }
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
    # The zip contains a single "<repo>-main" root folder -- that becomes $AppDir.
    $inner = Get-ChildItem -Directory $extract | Select-Object -First 1
    if (Test-Path $AppDir) {
        $savedEnv = Join-Path $env:TEMP 'SlowBooksPro-saved.env'
        $liveEnv = Join-Path $AppDir '.env'
        if (Test-Path $liveEnv) { Copy-Item $liveEnv $savedEnv -Force }
        Remove-Item -Recurse -Force $AppDir
        Move-Item $inner.FullName $AppDir
        if (Test-Path $savedEnv) {
            Move-Item $savedEnv (Join-Path $AppDir '.env') -Force
            Write-Host 'Restored existing .env (keeps your encryption secret).'
        }
    } else {
        Move-Item $inner.FullName $AppDir
    }
    Remove-Item -Force $zip
    Remove-Item -Recurse -Force $extract
    Write-Host "Installed application files to $AppDir"
}

# ---------------------------------------------------------------------------
# Step 1 -- Python
# ---------------------------------------------------------------------------
Banner 'Step 1/6: Python'
$python = Get-Python
if ($python) {
    Write-Host "Found Python: $python -- skipping install."
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
# Step 2 -- Microsoft WebView2 runtime (renders the app's native window)
#
# Ships preinstalled on Windows 11 and current Windows 10, but older
# Windows 10 images (and some VMs) lack it. Without it, pywebview falls
# back to the legacy IE/MSHTML control, where the app's JavaScript (and
# pywebview's own js_api bridge) does not work -- field-observed as
# endless "SyncRoot ... maximum recursion depth exceeded" console spam
# and a company picker whose buttons do nothing. desktop_launcher.py now
# requires WebView2 (gui='edgechromium') and refuses the MSHTML fallback.
# ---------------------------------------------------------------------------
Banner 'Step 2/6: Native window component (Microsoft WebView2)'
function Test-WebView2 {
    # Per Microsoft's docs: the Evergreen runtime registers a 'pv' version
    # value under one of these EdgeUpdate keys.
    $keys = @(
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    )
    foreach ($k in $keys) {
        $pv = (Get-ItemProperty -Path $k -Name pv -ErrorAction SilentlyContinue).pv
        if ($pv -and $pv -ne '0.0.0.0') { return $pv }
    }
    return $null
}
$wv2 = Test-WebView2
if ($wv2) {
    Write-Host "WebView2 runtime $wv2 already installed -- skipping."
} else {
    Write-Host 'Installing the Microsoft WebView2 runtime...'
    $wv2Installer = Join-Path $env:TEMP 'MicrosoftEdgeWebView2Setup.exe'
    try {
        # Microsoft's stable "Evergreen Bootstrapper" permalink.
        Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $wv2Installer
        Start-Process -FilePath $wv2Installer -Wait -ArgumentList '/silent', '/install'
        Remove-Item -Force $wv2Installer -ErrorAction SilentlyContinue
    } catch {
        Fail ("Could not install the WebView2 runtime ($($_.Exception.Message)). Install it manually from " +
              'https://developer.microsoft.com/microsoft-edge/webview2/ then run this file again.')
    }
    if (-not (Test-WebView2)) {
        Fail ('The WebView2 installer finished but the runtime is not registered. Install it manually from ' +
              'https://developer.microsoft.com/microsoft-edge/webview2/ then run this file again.')
    }
    Write-Host 'WebView2 runtime installed.'
}

# ---------------------------------------------------------------------------
# Step 3 -- GTK3 runtime (WeasyPrint's PDF-rendering libraries)
#
# WeasyPrint needs Cairo/Pango/gdk-pixbuf, which have no pip wheels on
# Windows. WeasyPrint's own docs point to this prebuilt GTK3 runtime
# installer. The bin dir is force-added to the machine PATH below because
# the NSIS silent mode may not tick the installer's own "set PATH" option.
# ---------------------------------------------------------------------------
Banner 'Step 3/6: PDF rendering component (GTK3 runtime)'
if (Test-Path (Join-Path $GtkBinDir 'libgobject-2.0-0.dll')) {
    Write-Host "GTK3 runtime already installed at $GtkBinDir -- skipping."
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
# Step 4 -- Python dependencies
# ---------------------------------------------------------------------------
Banner 'Step 4/6: Python packages'
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
# Step 5 -- first-run configuration (.env)
# ---------------------------------------------------------------------------
Banner 'Step 5/6: First-run configuration'
Push-Location $AppDir
try {
    & $python desktop_launcher.py --setup-only
    if ($LASTEXITCODE -ne 0) { Fail 'First-run configuration failed (see output above).' }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# Step 6 -- Desktop shortcut + first launch
# ---------------------------------------------------------------------------
Banner 'Step 6/6: Desktop shortcut'
$desktop = [Environment]::GetFolderPath('Desktop')
# Remove the shortcut left by the retired WSL2-based setup, if any.
$oldShortcut = Join-Path $desktop 'Slowbooks Pro 2026.lnk'
if (Test-Path $oldShortcut) { Remove-Item -Force $oldShortcut }
$shortcutPath = Join-Path $desktop 'SlowBooks Pro.lnk'
# The shortcut targets the hidden .vbs launcher (no console window) --
# "Launch SlowBooks Pro.bat" still exists in the app folder for anyone
# who wants to see live console output while troubleshooting.
$launchVbs = Join-Path $AppDir 'Launch SlowBooks Pro.vbs'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launchVbs
$shortcut.WorkingDirectory = $AppDir
$shortcut.Description = 'SlowBooks Pro 2026'
$shortcut.Save()
Write-Host "Created shortcut: $shortcutPath"

Write-Host ''
Write-Host 'Setup complete! Opening SlowBooks Pro now...' -ForegroundColor Green
Write-Host 'Next time, use the "SlowBooks Pro" shortcut on your Desktop.'
# Launch through explorer.exe so the app runs as the normal (non-elevated)
# user rather than inheriting this script's Administrator token.
Start-Process explorer.exe -ArgumentList "`"$launchVbs`""
