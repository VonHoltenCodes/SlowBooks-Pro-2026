"""The desktop app runs the same from a per-user install as from Program
Files (issue #699).

The installer now installs for one user (under %LOCALAPPDATA%\\Programs)
as well as for all users (Program Files). Two things could have tied the
app to the old place: the data folder, and the update path. The data folder
is %LOCALAPPDATA%\\SlowBooksPro whatever folder the program runs from. The
update path never starts an installer: the app shows an "Update available"
link to the download page, and the person runs the new installer, which
keeps the mode the copy already there was installed in. So the launcher
never needs /CURRENTUSER or an elevation prompt; these keep it that way.
"""

import re
from pathlib import Path

import desktop_launcher as dl

ROOT = Path(__file__).resolve().parents[1]


def test_the_data_folder_does_not_depend_on_where_the_program_is(monkeypatch):
    monkeypatch.delenv("SLOWBOOKS_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\pat\AppData\Local")
    monkeypatch.setattr(dl.sys, "platform", "win32")
    found = set()
    for exe in (
        r"C:\Program Files\SlowBooks Pro 2026\SlowBooksPro.exe",
        r"C:\Users\pat\AppData\Local\Programs\SlowBooks Pro 2026\SlowBooksPro.exe",
    ):
        monkeypatch.setattr(dl.sys, "executable", exe)
        found.add(str(dl.get_data_dir()))
    assert len(found) == 1, found
    (data,) = found
    assert data.startswith(r"C:\Users\pat\AppData\Local")
    assert data.replace("\\", "/").endswith("/SlowBooksPro/data"), data


def test_the_launcher_names_no_install_folder():
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    assert not re.search(r"program ?files", src, re.IGNORECASE)


def test_the_launcher_never_starts_the_installer():
    """Nothing in the launcher runs SlowBooksPro-Setup-x64.exe, so nothing
    has to pass /CURRENTUSER or ask a standard user for elevation."""
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    for word in ("/CURRENTUSER", "/ALLUSERS", '"runas"', "'runas'", "ShellExecute"):
        assert word not in src, word
    # WINDOWS_INSTALLER is only ever named in the text shown to the person
    for line in src.splitlines():
        if "WINDOWS_INSTALLER" in line and "=" in line.split("WINDOWS_INSTALLER")[0]:
            continue
        if "WINDOWS_INSTALLER" in line:
            assert "subprocess" not in line and "startfile" not in line, line


def test_an_update_is_offered_as_a_link_to_the_download_page():
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "link.href = check.download_url;" in js
    assert "link.target = '_blank';" in js
    route = (ROOT / "app" / "routes" / "system.py").read_text(encoding="utf-8")
    assert '"download_url": download_url' in route


def test_an_old_runtime_message_says_an_administrator_may_be_needed(monkeypatch):
    monkeypatch.setattr(dl, "FROZEN", True)
    msg = dl._old_webview2_message("100.0.1185.36")
    assert "again, which updates it" in msg
    assert "only be updated by an administrator" in msg
