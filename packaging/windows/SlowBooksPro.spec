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

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))


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
        "sqlalchemy.dialects.sqlite",
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SlowBooksPro",
)
