# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the native Windows build (one-folder).
#
# Built by .github/workflows/windows.yml on standard CPython (NOT MSYS2
# python — pywebview needs pythonnet/.NET, which mingw python can't load).
# WeasyPrint's native dependencies (Pango/GObject/HarfBuzz/Fontconfig DLLs)
# are staged from MSYS2 into gtk-dlls/ by the workflow and bundled under
# _internal/gtk/; desktop_launcher.py points WEASYPRINT_DLL_DIRECTORIES at
# that folder before anything imports weasyprint.

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))

# Version resource for the exe (issue #106): FileVersion / ProductVersion /
# ProductName come from app/__init__.py, so Properties → Details and
# inventory tools can answer "what version is this" from the binary.
sys.path.insert(0, SPECPATH)
import version_info as _version_info  # noqa: E402

VERSION_FILE = os.path.join(SPECPATH, "version_info.txt")
APP_VERSION = _version_info.write(
    __import__("pathlib").Path(ROOT, "app", "__init__.py"),
    __import__("pathlib").Path(VERSION_FILE),
)

# Build identity (#816): the commit this bundle is built from, baked as
# app/BUILD_SHA so About, the smoke test and Contact support's diagnostics
# show <version>+<sha> the way the macOS bundle's CFBundleVersion does. CI
# passes APP_BUILD_SHA; a local build reads the checkout.
APP_BUILD_SHA = _version_info.build_sha(__import__("pathlib").Path(ROOT))
BUILD_SHA_FILE = _version_info.write_build_sha(
    APP_BUILD_SHA, __import__("pathlib").Path(SPECPATH, "build-identity")
)
print(f"SlowBooks Pro build {APP_VERSION}+{APP_BUILD_SHA or 'unknown'}")


def _tree(src_rel, dest):
    """Recursively collect a repo directory as data files, skipping caches."""
    out = []
    src_root = os.path.join(ROOT, src_rel)
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fname in filenames:
            if fname.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, fname)
            rel_dir = os.path.relpath(dirpath, src_root)
            target = dest if rel_dir == "." else os.path.join(dest, rel_dir)
            out.append((full, target))
    return out


datas = [
    (os.path.join(ROOT, "index.html"), "."),
    (os.path.join(ROOT, "alembic.ini"), "."),
    (os.path.join(ROOT, ".env.example"), "."),
]
datas += _tree("app/static", "app/static")
datas += _tree("app/templates", "app/templates")
# Alembic loads migration scripts as FILES at runtime (script_location) —
# they must exist on disk in the bundle, not just inside the PYZ.
datas += _tree("migrations", "migrations")
# Server Edition helper scripts ship in the bundle so an installed or
# portable copy can register the startup task without downloading anything:
#   _internal\scripts\windows\serveredition-install.ps1
datas += _tree("scripts/windows", "scripts/windows")
# repair-schema.py is named by app.main's startup refusal when it meets a
# half-upgraded database (#132). Server Edition is precisely the deployment
# shape that refusal exists for, and its install scripts ship in this bundle
# — so the operator most likely to read the message was the one least likely
# to have the file (#144). An error naming a path the reader cannot reach is
# the same defect as "deactivate it instead" with no deactivate control.
datas += [(os.path.join(ROOT, "scripts", "repair-schema.py"), "scripts")]
# The build identity file app/build_info.py reads (#816).
datas += [(str(BUILD_SHA_FILE), "app")]

# The WeasyPrint DLL set staged by CI (empty when building without it, so a
# local `pyinstaller SlowBooksPro.spec` still produces a testable bundle).
binaries = [
    (p, "gtk") for p in glob.glob(os.path.join(SPECPATH, "gtk-dlls", "*.dll"))
]

hiddenimports = (
    # uvicorn.run("app.main:app") passes the app as a STRING — invisible to
    # PyInstaller's import scanner, so pull in the whole package explicitly.
    collect_submodules("app")
    + collect_submodules("uvicorn")
    + collect_submodules("alembic")
    + [
        # pywebview's Windows backend (WinForms via pythonnet)
        "webview.platforms.winforms",
        "clr",
        "clr_loader",
        # alembic.ini logging config
        "logging.config",
        # The .xlsx reader behind the QuickBooks Online export import, imported
        # lazily in app/routes/migration.py.
        "openpyxl",
        "sqlalchemy.dialects.sqlite",
        # WeasyPrint is imported lazily since #121 (so the suite runs on a
        # machine without the native stack). PyInstaller's scanner does walk
        # function-level imports, but the PDF engine is not something to
        # leave to "usually" — name it, so its hook always fires.
        "weasyprint",
        # The encrypted company file's driver (#290), imported lazily in
        # app/services/company_file.py; the smoke test reads its version
        # through the bundle.
        "sqlcipher3",
    ]
)

# Built-in OCR: the WinRT projection (packaging/windows/requirements-ocr.txt)
# is imported lazily inside WinRTEngine._bridge, invisible to the scanner.
# Guarded so a local `pyinstaller SlowBooksPro.spec` without winrt installed
# still produces a testable (tesseract-fallback) bundle.
try:
    hiddenimports += collect_submodules("winrt")
except Exception:
    pass

a = Analysis(
    [os.path.join(ROOT, "desktop_launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Desktop mode is SQLite-only; psycopg2 stays out of the bundle.
    excludes=["psycopg2", "psycopg2_binary"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SlowBooksPro",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # GUI app: no console window (launcher logs to file)
    icon="slowbookspro.ico",
    version=VERSION_FILE,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SlowBooksPro",
)
