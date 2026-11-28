"""A portable copy extracted from a downloaded .zip did not start on Windows.

Explorer's Extract All copies the download's internet mark (the
Zone.Identifier stream) onto every file, and .NET Framework, which the
window runs on through pythonnet, will not load a marked DLL. The window
never opened, and the message blamed WebView2. Now the frozen Windows
portable copy removes the mark from its own DLLs before the window's
backend loads (what Properties > Unblock does), and when a DLL is still
marked the message names the real cause and the steps. The installed copy,
macOS, Linux and a source run are untouched.

The Windows calls (reading and removing the stream) are replaced here, so
this runs anywhere; no real window is opened and pywebview is never
imported."""

import sys
import types

import pytest

import desktop_launcher as dl


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    """A portable copy's folder: the exe beside _internal, with DLLs, a
    .pyd, a script and a data file; the marks live in a set."""
    app = tmp_path / "SlowBooks Pro"
    internal = app / "_internal"
    (internal / "pythonnet" / "runtime").mkdir(parents=True)
    (internal / "webview" / "lib").mkdir(parents=True)
    (internal / "scripts" / "windows").mkdir(parents=True)
    files = [
        app / "SlowBooksPro.exe",
        internal / "pythonnet" / "runtime" / "Python.Runtime.dll",
        internal / "webview" / "lib" / "Microsoft.Web.WebView2.Core.dll",
        internal / "python312.dll",
        internal / "_ssl.pyd",
        internal / "scripts" / "windows" / "serveredition-install.ps1",
        internal / "base_library.zip",
    ]
    for f in files:
        f.write_bytes(b"x")
    marks = {str(f) for f in files}
    removed = []

    def has_mark(path):
        return path in marks

    def remove_mark(path):
        if path not in marks:
            raise FileNotFoundError(path)
        marks.discard(path)
        removed.append(path)

    monkeypatch.setattr(dl, "_has_zone_mark", has_mark)
    monkeypatch.setattr(dl, "_remove_zone_mark", remove_mark)
    monkeypatch.setattr(dl, "FROZEN", True)
    monkeypatch.setattr(dl.sys, "platform", "win32")
    monkeypatch.setattr(dl.sys, "executable", str(app / "SlowBooksPro.exe"))
    return types.SimpleNamespace(
        app=app, internal=internal, files=files, marks=marks, removed=removed
    )


def _dlls(b):
    return {str(f) for f in b.files if f.suffix == ".dll"}


def test_the_marked_dlls_are_unblocked_and_nothing_else(bundle, capsys):
    left = dl.unblock_portable_copy(bundle.internal)
    assert left == []
    assert set(bundle.removed) == _dlls(bundle)
    # the exe, the .pyd, the script and the data file keep their mark
    assert bundle.marks == {str(f) for f in bundle.files if f.suffix != ".dll"}
    out = capsys.readouterr().out
    assert "the mark was removed from 3 of its own DLLs" in out
    assert str(bundle.internal) in out
    assert "No other file was changed." in out


def test_a_second_start_is_quiet(bundle, capsys):
    dl.unblock_portable_copy(bundle.internal)
    capsys.readouterr()
    bundle.removed.clear()
    assert dl.unblock_portable_copy(bundle.internal) == []
    assert bundle.removed == []
    assert capsys.readouterr().out == ""


def test_an_unmarked_copy_is_left_alone(bundle, capsys):
    bundle.marks.clear()
    assert dl.unblock_portable_copy(bundle.internal) == []
    assert bundle.removed == []
    assert capsys.readouterr().out == ""


def test_only_files_inside_the_program_folder_are_looked_at(bundle, tmp_path):
    outside = tmp_path / "Documents" / "books.dll"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    bundle.marks.add(str(outside))
    dl.unblock_portable_copy(bundle.internal)
    assert str(outside) in bundle.marks
    assert str(outside) not in bundle.removed


def test_the_installed_copy_is_never_touched(bundle, capsys):
    (bundle.app / "unins000.exe").write_bytes(b"x")
    assert dl.unblock_portable_copy(bundle.internal) == []
    assert bundle.removed == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_other_systems_are_never_touched(bundle, monkeypatch, platform):
    monkeypatch.setattr(dl.sys, "platform", platform)
    assert dl.unblock_portable_copy(bundle.internal) == []
    assert bundle.removed == []


def test_a_source_run_is_never_touched(bundle, monkeypatch):
    monkeypatch.setattr(dl, "FROZEN", False)
    assert dl.unblock_portable_copy(bundle.internal) == []
    assert bundle.removed == []


def test_a_read_only_folder_reports_what_is_still_marked(bundle, monkeypatch, capsys):
    def refuse(path):
        raise PermissionError(path)

    monkeypatch.setattr(dl, "_remove_zone_mark", refuse)
    left = dl.unblock_portable_copy(bundle.internal)
    assert {str(p) for p in left} == _dlls(bundle)
    out = capsys.readouterr().out
    assert "could not be removed from 3 of its DLLs" in out
    assert "was removed" not in out


def test_the_real_mark_check_says_no_off_windows(tmp_path):
    f = tmp_path / "Python.Runtime.dll"
    f.write_bytes(b"x")
    # the real function (no fixture): no ":Zone.Identifier" stream here
    assert dl._has_zone_mark(str(f)) is False


# ---------------------------------------------------------------------------
# The message when the window still cannot open
# ---------------------------------------------------------------------------


class _FailingWebview:
    def __init__(self, error):
        self.settings = {}
        self.screens = []
        self._error = error

    def create_window(self, *args, **kwargs):
        return types.SimpleNamespace(events=None)

    def start(self, *args, **kwargs):
        raise self._error


@pytest.fixture
def failing_window(bundle, monkeypatch, tmp_path):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(dl, "ROOT", bundle.internal)
    monkeypatch.setattr(dl, "_webview2_status", lambda: ("ok", "142.0.0.0"))
    monkeypatch.setattr(dl, "_windows_work_areas", lambda screens: screens)
    monkeypatch.setattr(dl, "stop_server", lambda proc: None)
    shown = []
    monkeypatch.setattr(dl, "_show_error_box", lambda m: shown.append(m))
    error = RuntimeError(
        "Failed to resolve Python.Runtime.Loader.Initialize from "
        r"C:\x\_internal\pythonnet\runtime\Python.Runtime.dll"
    )
    monkeypatch.setitem(sys.modules, "webview", _FailingWebview(error))

    def go():
        return dl.run_window(3001), shown

    return go


def test_the_window_unblocks_before_it_starts(bundle, failing_window):
    # the copy is unblocked, so a failure now is not the mark: WebView2 text
    rc, shown = failing_window()
    assert rc == 1
    assert set(bundle.removed) == _dlls(bundle)
    (msg,) = shown
    assert "WebView2 runtime is missing" in msg
    assert "Unblock" not in msg


def test_still_marked_names_the_real_cause_not_webview2(
    bundle, failing_window, monkeypatch
):
    def refuse(path):
        raise PermissionError(path)

    monkeypatch.setattr(dl, "_remove_zone_mark", refuse)
    rc, shown = failing_window()
    assert rc == 1
    (msg,) = shown
    assert "Could not open the native window: Failed to resolve" in msg
    assert "marked this copy's own files as downloaded" in msg
    assert "Right-click the downloaded .zip and choose Properties" in msg
    assert "tick Unblock, then press OK" in msg
    assert "Extract the .zip again, into a new folder" in msg
    assert dl.WINDOWS_INSTALLER in msg
    assert "WebView2 runtime is missing" not in msg
    assert "desktop_launcher.py --no-window" not in msg


def test_an_unmarked_failure_still_names_webview2(bundle, failing_window):
    bundle.marks.clear()
    rc, shown = failing_window()
    (msg,) = shown
    assert "WebView2 runtime is missing" in msg
    assert "Unblock" not in msg
    assert bundle.removed == []
