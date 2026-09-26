"""The desktop app's PDF window has Open in <the PDF app> and Show in folder
(explore 2.17.3 integration, I23; macbase1 F24).

Save PDF writes the file to Documents/SlowBooks Pro/{Documents|Reports}
and shows it in a window of its own, which had no Save or Print on macOS
(Cmd+S did nothing); only a toast in the main window said where the file
was. The window now shows the PDF under a toolbar: Open in <the program the
system opens a PDF with> — Preview, Edge, Acrobat: it has Print and Save —
and Show in folder. The window's bridge is bound to the one file it shows,
and only a PDF inside the app's saved-documents folders is ever handed to
another program.
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

import desktop_launcher as dl


@pytest.fixture
def home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(dl.Path, "home", lambda: home)
    # On Windows the Documents folder is asked of the shell, not built from
    # home, so the saved-documents root must be pinned here as well.
    monkeypatch.setattr(dl, "_documents_dir", lambda: home / "Documents")
    monkeypatch.setattr(dl, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(dl.sys, "platform", "linux")
    return home


@pytest.fixture
def launched(monkeypatch):
    """What reaches another program: Popen argument lists and startfile."""
    seen = []
    monkeypatch.setattr(dl.subprocess, "Popen", lambda args, *a, **k: seen.append(args))
    monkeypatch.setattr(
        dl.os, "startfile", lambda p: seen.append(["startfile", p]), raising=False
    )
    return seen


def _saved(home, rel="Documents/Invoice_1002.pdf", data=b"%PDF-1.7"):
    path = home / "Documents" / "SlowBooks Pro" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ── the path guard ───────────────────────────────────────────────────────


def test_a_pdf_the_app_saved_may_be_opened(home, tmp_path):
    for rel in ("Documents/Invoice_1002.pdf", "Reports/profit-and-loss.PDF"):
        path = _saved(home, rel)
        assert dl._openable_pdf(str(path)) == path.resolve()
    # the app-data folders a refused Documents write falls back to
    for folder in ("Reports", "Documents"):
        path = tmp_path / "data" / folder / "Statement_Acme.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")
        assert dl._openable_pdf(path) == path.resolve()


def test_nothing_else_may_be_opened(home, tmp_path):
    root = home / "Documents" / "SlowBooks Pro"
    elsewhere = home / "Documents" / "taxes.pdf"
    elsewhere.write_bytes(b"%PDF")
    runnable = _saved(home, "Reports/setup.exe", b"MZ")
    export = _saved(home, "Reports/customers.csv", b"Name")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF")
    link = root / "Reports" / "link.pdf"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        link = None
    for path in [
        elsewhere,  # a PDF the app did not save
        runnable,  # under the root, but not a PDF
        export,
        root / "Documents" / "missing.pdf",  # not there
        root / ".." / "taxes.pdf",  # out of the root by '..'
        root,  # a folder
        "",
        None,
    ] + (
        [link] if link else []
    ):  # out of the root by a link
        assert dl._openable_pdf(path) is None, path


# ── the command per platform ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "platform, command",
    [("darwin", ["open"]), ("linux", ["xdg-open"]), ("win32", ["startfile"])],
)
def test_open_in_the_pdf_app_uses_the_systems_opener(
    home, launched, monkeypatch, platform, command
):
    path = _saved(home)
    monkeypatch.setattr(dl.sys, "platform", platform)
    assert dl.DocumentViewerApi(path).open_in_default_app() == {"success": True}
    assert launched == [command + [str(path.resolve())]]


def test_a_file_that_is_gone_or_elsewhere_is_not_opened(home, launched):
    path = _saved(home)
    api = dl.DocumentViewerApi(path)
    path.unlink()
    r = api.open_in_default_app()
    assert r["success"] is False and "moved or deleted" in r["error"]
    r = dl.DocumentViewerApi(home / "Documents" / "other.pdf").open_in_default_app()
    assert r["success"] is False
    assert launched == []


def test_show_in_folder_shows_the_windows_file(home, launched, monkeypatch):
    path = _saved(home)
    monkeypatch.setattr(dl.sys, "platform", "darwin")
    assert dl.DocumentViewerApi(path).show_in_folder() == {"success": True}
    assert launched == [["open", "-R", str(path.resolve())]]


def test_the_bridge_takes_no_path_from_the_page():
    public = [n for n in vars(dl.DocumentViewerApi) if not n.startswith("_")]
    assert sorted(public) == ["open_in_default_app", "show_in_folder"]
    for name in public:
        code = getattr(dl.DocumentViewerApi, name).__code__
        assert code.co_argcount == 1, name  # self only


# ── the window ───────────────────────────────────────────────────────────


@pytest.fixture
def windows(monkeypatch):
    made = []
    monkeypatch.setitem(
        sys.modules,
        "webview",
        types.SimpleNamespace(create_window=lambda *a, **k: made.append((a, k))),
    )
    return made


def _open_pdf(title):
    pdf = base64.b64encode(b"%PDF-1.7 test").decode()
    return dl.PickerApi(3001).open_document_pdf(title, pdf)


def _page(window):
    args, kwargs = window
    return Path(url2pathname(urlparse(args[1]).path)).read_text(encoding="utf-8")


def test_the_pdf_window_carries_the_toolbar(home, windows, monkeypatch):
    monkeypatch.setattr(dl, "_default_pdf_app", lambda pdf: "Preview")
    result = _open_pdf("Invoice_1002.pdf")
    saved = Path(result["path"])
    [window] = windows
    args, kwargs = window
    assert args[0] == "Invoice_1002.pdf"
    # the page lives in the app's data folder, not beside the user's files
    assert Path(url2pathname(urlparse(args[1]).path)).parent.name == "viewer"
    page = _page(window)
    assert f'<iframe src="{saved.as_uri()}"' in page
    assert ">Open in Preview</button>" in page
    assert ">Show in folder</button>" in page
    assert "call('open_in_default_app')" in page and "call('show_in_folder')" in page
    assert f"Saved in {saved.parent}" in page
    # the window's bridge is bound to the file it shows
    api = kwargs["js_api"]
    assert isinstance(api, dl.DocumentViewerApi)
    assert Path(api._path) == saved


def test_the_button_names_no_app_when_the_system_cant_say(home, windows, monkeypatch):
    monkeypatch.setattr(dl, "_default_pdf_app", lambda pdf: None)
    _open_pdf("Invoice_1002.pdf")
    assert ">Open in your PDF app</button>" in _page(windows[0])


def test_the_page_escapes_what_it_shows(home, windows, monkeypatch):
    monkeypatch.setattr(dl, "_default_pdf_app", lambda pdf: '<img src=x onerror="1">')
    _open_pdf('Statement_<script>alert("x")</script>.pdf')
    page = _page(windows[0])
    assert "<script>alert" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;" in page


def test_without_a_viewer_page_the_pdf_still_opens(home, windows, monkeypatch):
    def refused(pdf, title):
        raise PermissionError("read-only data folder")

    monkeypatch.setattr(dl, "_write_viewer", refused)
    result = _open_pdf("Invoice_1002.pdf")
    [(args, kwargs)] = windows
    assert args[1] == Path(result["path"]).as_uri()
    assert kwargs["js_api"] is None


def test_old_viewer_pages_are_cleared_out(home, windows, monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "_default_pdf_app", lambda pdf: None)
    folder = tmp_path / "data" / "viewer"
    folder.mkdir(parents=True)
    stale, fresh = folder / "viewer-old.html", folder / "viewer-new.html"
    stale.write_text("x")
    fresh.write_text("x")
    os.utime(stale, (0, 0))
    _open_pdf("Invoice_1002.pdf")
    assert not stale.exists() and fresh.exists()
    assert len(list(folder.glob("viewer-*.html"))) == 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_toolbar_works(tmp_path):
    page = dl._VIEWER_PAGE.substitute(
        title="t", name="n", path="p", where="w", src="file:///x.pdf", open_label="o"
    )
    script = tmp_path / "viewer.js"
    script.write_text(re.search(r"<script>(.*?)</script>", page, re.S | re.I).group(1))
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run(
        ["node", str(root / "tests" / "js" / "pdf_viewer_page_probe.js"), str(script)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["before_ready"] == [True, True]  # until the bridge is there
    assert got["after_ready"] == [False, False]
    assert got["note_after_show"] == ""
    assert got["note_after_open"] == "No app opens PDFs on this computer."
    assert got["prevented"] == 2  # Cmd+P and Cmd+S, not Cmd+X
    assert got["calls"] == [
        "show_in_folder",
        "open_in_default_app",
        "open_in_default_app",
        "open_in_default_app",
    ]
