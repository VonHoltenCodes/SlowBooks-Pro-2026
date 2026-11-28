"""A portable copy extracted from a downloaded .zip did not start on Windows.

Explorer's Extract All copies the download's internet mark (the
Zone.Identifier stream) onto every file, and .NET Framework, which the
window runs on through pythonnet, will not load a marked DLL. The window
never opened, and the message blamed WebView2. Now the frozen Windows
portable copy removes the mark from its own .NET DLLs (pythonnet's runtime
folder and pywebview's lib folder, the ones .NET Framework refuses) before
the window's backend loads (what Properties > Unblock does), and when one is
still marked the message names the real cause and the steps. Every other
file keeps its mark; nothing happens unless the files are in the "_internal"
folder beside the exe; a link out of that folder is never followed; a
failure of the step never stops the start. The installed copy, macOS, Linux
and a source run are untouched.

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
    app = tmp_path.resolve() / "SlowBooks Pro"
    internal = app / "_internal"
    lib = internal / "webview" / "lib"
    files = [
        app / "SlowBooksPro.exe",
        internal / "pythonnet" / "runtime" / "Python.Runtime.dll",
        lib / "Microsoft.Web.WebView2.Core.dll",
        lib / "WebBrowserInterop.x64.dll",
        # native DLLs and everything else: they load with the mark
        lib / "runtimes" / "win-x64" / "native" / "WebView2Loader.dll",
        internal / "clr_loader" / "ffi" / "dlls" / "amd64" / "ClrLoader.dll",
        internal / "python312.dll",
        internal / "_ssl.pyd",
        internal / "scripts" / "windows" / "serveredition-install.ps1",
        internal / "base_library.zip",
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
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
    """The .NET DLLs the fix may unmark: directly in the two named folders."""
    named = {("pythonnet", "runtime"), ("webview", "lib")}
    return {
        str(f)
        for f in b.files
        if f.suffix == ".dll" and f.parent.relative_to(b.internal).parts in named
    }


def test_only_the_named_net_dlls_are_unblocked(bundle, capsys):
    left = dl.unblock_portable_copy(bundle.internal)
    assert left == []
    assert len(_dlls(bundle)) == 3
    assert set(bundle.removed) == _dlls(bundle)
    # the exe, the other DLLs (native loader, clr_loader, python312), the
    # .pyd, the script and the data file all keep their mark
    assert bundle.marks == {str(f) for f in bundle.files} - _dlls(bundle)
    out = capsys.readouterr().out
    assert (
        "the mark was removed from 3 .NET DLLs in pythonnet\\runtime and "
        f"webview\\lib under {bundle.internal}, as Unblock does" in out
    )
    assert " ms). No other file was changed." in out
    assert out.count("\n") == 1


def test_a_dll_elsewhere_in_the_bundle_keeps_its_mark(bundle):
    # e.g. another app's _internal merged by an "extract here"
    other = bundle.internal / "otherapp" / "Other.Managed.dll"
    other.parent.mkdir()
    other.write_bytes(b"x")
    bundle.marks.add(str(other))
    dl.unblock_portable_copy(bundle.internal)
    assert str(other) in bundle.marks
    assert str(other) not in bundle.removed


def test_nothing_happens_unless_the_folder_is_internal_beside_the_exe(
    bundle, monkeypatch, tmp_path
):
    # files beside the exe (contents_directory=".") : the exe's own folder
    monkeypatch.setattr(dl.sys, "executable", str(bundle.internal / "x.exe"))
    assert dl.unblock_portable_copy(bundle.internal.parent) == []
    # an "_internal" that is not beside the running exe
    monkeypatch.setattr(dl.sys, "executable", str(tmp_path / "elsewhere" / "x.exe"))
    assert dl.unblock_portable_copy(bundle.internal) == []
    # a folder beside the exe that is not named _internal
    renamed = bundle.app / "lib"
    bundle.internal.rename(renamed)
    monkeypatch.setattr(dl.sys, "executable", str(bundle.app / "SlowBooksPro.exe"))
    assert dl.unblock_portable_copy(renamed) == []
    assert bundle.removed == []


def test_a_link_out_of_the_bundle_is_never_followed(bundle, tmp_path):
    outside = tmp_path / "Elsewhere"
    (outside / "runtime").mkdir(parents=True)
    far_file = outside / "Far.dll"
    far_in_dir = outside / "runtime" / "Python.Runtime.dll"
    for f in (far_file, far_in_dir):
        f.write_bytes(b"x")
        bundle.marks.add(str(f))
    # a file link in a named folder, and a named folder that is a link
    (bundle.internal / "webview" / "lib" / "Far.dll").symlink_to(far_file)
    runtime = bundle.internal / "pythonnet" / "runtime"
    (runtime / "Python.Runtime.dll").unlink()
    runtime.rmdir()
    runtime.symlink_to(outside / "runtime", target_is_directory=True)
    dl.unblock_portable_copy(bundle.internal)
    assert str(far_file) in bundle.marks
    assert str(far_in_dir) in bundle.marks
    assert not any(p.startswith(str(outside)) for p in bundle.removed)
    # the real DLLs in the bundle were still unblocked
    assert str(bundle.internal / "webview" / "lib" / "WebBrowserInterop.x64.dll") in (
        bundle.removed
    )


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
    assert "could not be removed from 3 of its .NET DLLs" in out
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
    assert "could not remove the mark itself" in msg
    assert "WebView2 is probably not the problem" in msg
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


def test_a_failure_of_the_step_never_stops_the_start(
    bundle, failing_window, monkeypatch, capsys
):
    def broken(root=None):
        raise ValueError("odd path")

    monkeypatch.setattr(dl, "unblock_portable_copy", broken)
    rc, shown = failing_window()
    # the start went on to the window (which fails here), and the fallback
    # still names the real cause: the DLLs are still marked
    assert rc == 1
    out = capsys.readouterr().out
    assert "Could not check the download mark: ValueError('odd path')" in out
    (msg,) = shown
    assert "marked this copy's own files as downloaded" in msg
    assert "WebView2 runtime is missing" not in msg
