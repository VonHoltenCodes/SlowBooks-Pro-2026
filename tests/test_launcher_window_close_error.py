"""A Mac quit showed "Could not open the native window" every time the
window had opened at a saved position (#763). An error out of webview.start()
after the window opened is a close-time error: it is logged, the position is
saved and the launcher exits normally. Only a failure before the window opens
gets the startup message. And pywebview's window-moved callback is replaced
on a Mac by one that cannot fail. No real window is opened, and pywebview is
never imported."""

import sys
import types

import pytest

import desktop_launcher as dl


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def set(self, *args):
        for fn in self.handlers:
            fn(*args)


class _Window:
    def __init__(self):
        names = ("moved", "resized", "maximized", "minimized", "restored")
        self.events = types.SimpleNamespace(
            **{n: _Event() for n in names + ("closing", "shown", "loaded")}
        )


class _FakeWebview:
    """pywebview with a window whose start() can open it and then fail."""

    def __init__(self, open_first: bool, error: Exception | None):
        self.settings = {}
        self.screens = []
        self.window = None
        self._open_first = open_first
        self._error = error

    def create_window(self, *args, **kwargs):
        self.window = _Window()
        return self.window

    def start(self, *args, **kwargs):
        if self._open_first:
            self.window.events.shown.set()
            self.window.events.loaded.set()
            self.window.events.moved.set(140, 90)
            self.window.events.resized.set(1300, 880)
        if self._error is not None:
            raise self._error


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(dl.sys, "platform", "linux")
    calls = {"shown": [], "stopped": 0}

    def stop(proc):
        calls["stopped"] += 1

    monkeypatch.setattr(dl, "stop_server", stop)
    monkeypatch.setattr(dl, "_show_error_box", lambda m: calls["shown"].append(m))

    def go(open_first, error):
        fake = _FakeWebview(open_first, error)
        monkeypatch.setitem(sys.modules, "webview", fake)
        return dl.run_window(3001), fake

    return go, calls, tmp_path / dl.WINDOW_GEOMETRY_FILE


def test_an_error_after_the_window_opened_is_a_close_not_a_failed_start(run, capsys):
    go, calls, geometry = run
    err = AttributeError("'NoneType' object has no attribute 'frame'")

    code, _ = go(open_first=True, error=err)

    assert code == 0
    assert calls["shown"] == []  # no error box
    assert calls["stopped"] == 1  # the server still stops
    out = capsys.readouterr().out
    assert "The window closed with an error from the native backend" in out
    assert "the window position was saved" in out
    assert "no attribute 'frame'" in out
    assert "Could not open the native window" not in out
    saved = dl.load_window_geometry(geometry)
    assert (saved["x"], saved["y"]) == (140, 90)
    assert (saved["width"], saved["height"]) == (1300, 880)


def test_an_error_before_the_window_opened_keeps_the_startup_message(run, capsys):
    go, calls, _ = run

    code, _ = go(open_first=False, error=RuntimeError("no display"))

    assert code == 1
    (shown,) = calls["shown"]
    assert shown.startswith("Could not open the native window: no display")
    assert "--no-window" in shown
    assert calls["stopped"] == 1
    assert "closed with an error" not in capsys.readouterr().out


def test_a_clean_close_still_returns_0_and_saves(run):
    go, calls, geometry = run

    code, _ = go(open_first=True, error=None)

    assert code == 0 and calls["shown"] == []
    assert dl.load_window_geometry(geometry)["x"] == 140


def test_the_mac_startup_message_does_not_blame_a_missing_dependency(run, monkeypatch):
    go, calls, _ = run
    monkeypatch.setattr(dl.sys, "platform", "darwin")
    monkeypatch.setattr(dl, "install_mac_window_move_guard", lambda: False)
    monkeypatch.setattr(dl, "install_mac_print_panel", lambda: False)

    code, _ = go(open_first=False, error=RuntimeError("no display"))

    assert code == 1
    (shown,) = calls["shown"]
    assert "dependency" not in shown
    assert "launcher log has the error" in shown


# --- macOS coordinates: the guard's position and the restore's screens ----


# pywebview's own move(): top-left (x, y) measured down from the top of the
# primary screen, placed as a Cocoa top-left point.
def _cocoa_top_left(x, y, primary_height):
    return x, primary_height - y


def test_a_moved_position_restores_to_the_same_place_on_a_secondary_screen():
    # Primary 2560x1440; window 1000x700 on a 1920x1080 display to its right,
    # top edges aligned. Its Cocoa frame origin is (2900, 540).
    x, y = dl._mac_moved_position(2900, 540, 700, 1440)
    assert (x, y) == (2900, 200)
    # move() puts the top-left corner back at the frame's top: 540 + 700
    assert _cocoa_top_left(x, y, 1440) == (2900, 1240)


def test_mac_screens_turn_into_the_coordinates_the_window_is_placed_in():
    cocoa = [(0, 0, 2560, 1440), (2560, 360, 1920, 1080), (0, 1440, 1920, 1080)]
    assert dl._mac_top_left_screens(cocoa) == [
        (0, 0, 2560, 1440),
        (2560, 0, 1920, 1080),  # to the right, tops aligned
        (0, -1080, 1920, 1080),  # above the primary
    ]
    assert dl._mac_top_left_screens([]) == []


def test_a_window_on_a_mac_display_above_the_primary_restores_there():
    cocoa = [(0, 0, 2560, 1440), (0, 1440, 1920, 1080)]
    screens = dl._mac_top_left_screens(cocoa)
    saved = {"x": 300, "y": -900, "width": 1280, "height": 800, "maximized": False}
    assert dl.restore_window_geometry(saved, screens, platform="darwin") == saved


# --- the guard itself, with a fake AppKit and a fake pywebview Cocoa backend --


class _Point:
    def __init__(self, x, y):
        self.x, self.y = x, y


class _Size:
    def __init__(self, width, height):
        self.width, self.height = width, height


class _Rect:
    def __init__(self, x, y, w, h):
        self.origin, self.size = _Point(x, y), _Size(w, h)


class _NSWindow:
    def __init__(self, rect, fail=False):
        self._rect, self._fail = rect, fail

    def frame(self):
        if self._fail:
            raise ValueError("window torn down")
        return self._rect

    def screen(self):
        return None  # what made pywebview's own callback raise


class _NSScreen:
    def __init__(self, rect):
        self._rect = rect

    def frame(self):
        return self._rect


@pytest.fixture
def cocoa(monkeypatch):
    monkeypatch.setattr(dl.sys, "platform", "darwin")
    state = {"instance": None, "screens": [_NSScreen(_Rect(0, 0, 2560, 1440))]}

    class BrowserView:
        class WindowDelegate:
            def windowDidMove_(self, notification):
                raise AssertionError("pywebview's own callback ran")

        @staticmethod
        def get_instance(attr, value):
            return state["instance"]

    appkit = types.ModuleType("AppKit")
    appkit.NSScreen = types.SimpleNamespace(screens=lambda: state["screens"])
    backend = types.ModuleType("webview.platforms.cocoa")
    backend.BrowserView = BrowserView
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setitem(
        sys.modules, "webview.platforms", types.ModuleType("webview.platforms")
    )
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", backend)
    return BrowserView, state


def _instance(window):
    moved = _Event()
    seen = []
    moved += lambda x, y: seen.append((x, y))
    events = types.SimpleNamespace(moved=moved)
    return (
        types.SimpleNamespace(
            window=window, pywebview_window=types.SimpleNamespace(events=events)
        ),
        seen,
    )


def test_the_guard_reports_moves_against_the_primary_screen(cocoa):
    BrowserView, state = cocoa
    assert dl.install_mac_window_move_guard() is True
    state["instance"], seen = _instance(_NSWindow(_Rect(2900, 540, 1000, 700)))

    note = types.SimpleNamespace(object=lambda: None)
    BrowserView.WindowDelegate().windowDidMove_(note)

    # window.screen() is None here: pywebview's own callback raised on it
    assert seen == [(2900, 200)]


def test_the_guard_never_raises_into_appkit(cocoa, capsys):
    BrowserView, state = cocoa
    dl.install_mac_window_move_guard()
    note = types.SimpleNamespace(object=lambda: None)
    delegate = BrowserView.WindowDelegate()

    state["instance"] = None  # the window already closed
    delegate.windowDidMove_(note)
    state["instance"], seen = _instance(_NSWindow(_Rect(0, 0, 900, 600)))
    state["screens"] = []  # no display at all
    delegate.windowDidMove_(note)
    state["screens"] = [_NSScreen(_Rect(0, 0, 1440, 900))]
    state["instance"], seen = _instance(_NSWindow(None, fail=True))
    delegate.windowDidMove_(note)

    assert seen == []
    assert "Window move not tracked: window torn down" in capsys.readouterr().out


def test_the_guard_installs_once_and_only_on_a_mac(cocoa, monkeypatch):
    BrowserView, _ = cocoa
    assert dl.install_mac_window_move_guard() is True
    first = BrowserView.WindowDelegate.windowDidMove_
    assert dl.install_mac_window_move_guard() is True
    assert BrowserView.WindowDelegate.windowDidMove_ is first
    monkeypatch.setattr(dl.sys, "platform", "win32")
    assert dl.install_mac_window_move_guard() is False
