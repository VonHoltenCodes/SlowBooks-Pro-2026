"""The desktop window opens where it was closed (#681): the saved geometry
file, the restore decision against the connected screens, and the tracker
that follows the window's events. No real window is opened."""

import json

import desktop_launcher as dl

ONE_SCREEN = [(0, 0, 1920, 1080)]
TWO_SCREENS = [(0, 0, 1920, 1080), (1920, 0, 2560, 1440)]
DEFAULT = {"x": None, "y": None, "width": 1280, "height": 860, "maximized": False}


def _saved(x=100, y=80, width=1400, height=900, maximized=False):
    return {"x": x, "y": y, "width": width, "height": height, "maximized": maximized}


# --- the file -------------------------------------------------------------


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "window-geometry.json"
    assert dl.save_window_geometry(_saved(maximized=True), path)
    assert dl.load_window_geometry(path) == _saved(maximized=True)
    assert not (tmp_path / "window-geometry.json.tmp").exists()


def test_file_lives_in_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    assert dl.window_geometry_path() == tmp_path / "window-geometry.json"


def test_missing_file_loads_as_nothing(tmp_path):
    assert dl.load_window_geometry(tmp_path / "nope.json") is None


def test_corrupt_files_load_as_nothing(tmp_path):
    path = tmp_path / "window-geometry.json"
    for text in (
        "{not json",
        "[]",
        '"text"',
        json.dumps({"x": 1, "y": 2}),
        json.dumps({"width": "wide", "height": 600}),
        json.dumps({"width": True, "height": 600}),
        json.dumps({"width": 0, "height": 600}),
        json.dumps({"width": -5, "height": 600}),
    ):
        path.write_text(text, encoding="utf-8")
        assert dl.load_window_geometry(path) is None, text


def test_partial_position_is_dropped(tmp_path):
    path = tmp_path / "window-geometry.json"
    path.write_text(json.dumps({"x": 5, "width": 1000, "height": 700}))
    assert dl.load_window_geometry(path) == _saved(None, None, 1000, 700)


def test_save_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert dl.save_window_geometry(_saved(), blocker / "sub" / "g.json") is False
    assert (
        dl.save_window_geometry({"width": None, "height": 5}, tmp_path / "g") is False
    )


def test_corrupt_file_restores_default(tmp_path):
    path = tmp_path / "window-geometry.json"
    path.write_text("garbage")
    geometry = dl.restore_window_geometry(dl.load_window_geometry(path), ONE_SCREEN)
    assert geometry == DEFAULT


# --- the restore decision ---------------------------------------------------


def test_nothing_saved_gives_todays_default():
    assert dl.restore_window_geometry(None, ONE_SCREEN) == DEFAULT


def test_on_screen_restores_as_saved():
    assert dl.restore_window_geometry(_saved(), ONE_SCREEN) == _saved()


def test_on_second_screen_restores_there():
    # platform named: on a Mac the default is darwin, where maximized is
    # never restored (NEW-199), and this is the Windows/Linux rule (NEW-202)
    saved = _saved(x=2200, y=200, width=1600, height=1000, maximized=True)
    assert dl.restore_window_geometry(saved, TWO_SCREENS, platform="win32") == saved


def test_disconnected_monitor_falls_back_to_default():
    saved = _saved(x=2200, y=200)  # was on the second screen, now unplugged
    assert dl.restore_window_geometry(saved, ONE_SCREEN) == DEFAULT
    assert dl.restore_window_geometry(_saved(x=-3000, y=-3000), ONE_SCREEN) == DEFAULT


def test_partly_on_screen_is_pulled_fully_on():
    # Hanging off the right and bottom edges, title bar still grabbable.
    geometry = dl.restore_window_geometry(_saved(x=1500, y=700), ONE_SCREEN)
    assert geometry == _saved(x=1920 - 1400, y=1080 - 900)


def test_only_a_sliver_on_screen_counts_as_off_screen():
    # 50 px of the title bar on the screen is not enough to grab it.
    assert dl.restore_window_geometry(_saved(x=1870, y=100), ONE_SCREEN) == DEFAULT
    # Title bar above the top edge: nothing to grab either.
    assert dl.restore_window_geometry(_saved(x=100, y=-500), ONE_SCREEN) == DEFAULT


def test_larger_than_the_screen_is_shrunk_to_it():
    saved = _saved(x=10, y=10, width=3000, height=2000)
    assert dl.restore_window_geometry(saved, ONE_SCREEN) == _saved(0, 0, 1920, 1080)


def test_never_below_the_minimum():
    geometry = dl.restore_window_geometry(_saved(width=300, height=200), ONE_SCREEN)
    assert (geometry["width"], geometry["height"]) == (900, 600)
    # Even on a screen smaller than the minimum, the minimum wins.
    tiny = [(0, 0, 800, 500)]
    geometry = dl.restore_window_geometry(_saved(x=0, y=0), tiny)
    assert (geometry["width"], geometry["height"]) == (900, 600)


def test_unknown_screens_keep_size_but_let_the_os_place_it():
    geometry = dl.restore_window_geometry(_saved(width=1500, height=950), [])
    assert geometry == _saved(None, None, 1500, 950)


def test_no_saved_position_keeps_size_only():
    geometry = dl.restore_window_geometry(_saved(None, None, 1500, 950), ONE_SCREEN)
    assert geometry == _saved(None, None, 1500, 950)


# --- the tracker -------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_tracker_follows_moves_and_resizes():
    tracker = dl.WindowGeometryTracker(DEFAULT, clock=_Clock())
    tracker.on_moved(40, 30)
    tracker.on_resized(1500, 950)
    assert tracker.geometry() == _saved(40, 30, 1500, 950)


def test_maximize_keeps_the_normal_rectangle():
    clock = _Clock()
    tracker = dl.WindowGeometryTracker(_saved(), clock=clock)
    clock.now += 30
    tracker.on_moved(0, 0)  # events the maximize itself fires...
    tracker.on_resized(1920, 1050)
    clock.now += 0.1
    tracker.on_maximized()  # ...are rolled back
    tracker.on_resized(1920, 1050)  # and later ones ignored
    assert tracker.geometry() == _saved(maximized=True)
    tracker.on_restored()
    tracker.on_resized(1300, 880)
    assert tracker.geometry() == _saved(100, 80, 1300, 880)


def test_minimize_is_not_saved():
    clock = _Clock()
    tracker = dl.WindowGeometryTracker(_saved(), clock=clock)
    clock.now += 30
    tracker.on_resized(160, 28)
    tracker.on_minimized()
    tracker.on_moved(-32000, -32000)
    tracker.on_restored()
    assert tracker.geometry() == _saved()


def test_restore_from_minimized_keeps_maximized():
    tracker = dl.WindowGeometryTracker(_saved(maximized=True), clock=_Clock())
    tracker.on_minimized()
    tracker.on_restored()
    assert tracker.geometry()["maximized"] is True


def test_tracker_saves_through_the_closing_event(tmp_path):
    class _Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, fn):
            self.handlers.append(fn)
            return self

    class _Events:
        def __init__(self):
            for name in ("moved", "resized", "maximized", "minimized"):
                setattr(self, name, _Event())
            self.restored = _Event()
            self.closing = _Event()

    class _Window:
        events = _Events()

    window = _Window()
    path = tmp_path / "window-geometry.json"
    tracker = dl.WindowGeometryTracker(DEFAULT, clock=_Clock())
    dl._track_window_geometry(window, tracker, path)
    window.events.moved.handlers[0](12, 34)
    window.events.resized.handlers[0](1000, 700)
    # A closing handler that returns False would cancel the close.
    assert window.events.closing.handlers[0]() is None
    assert dl.load_window_geometry(path) == _saved(12, 34, 1000, 700)


# --- macOS (NEW-199): maximized there is full screen ------------------------

MAC_SCREEN = [(0, 0, 1440, 900)]


def test_mac_never_opens_maximized_but_gets_its_normal_size_back():
    saved = _saved(x=100, y=50, width=1150, height=800, maximized=True)
    geometry = dl.restore_window_geometry(saved, MAC_SCREEN, platform="darwin")
    assert geometry == _saved(x=100, y=50, width=1150, height=800)
    # Unknown screens and no saved position: still not maximized.
    for screens, x in (([], None), (MAC_SCREEN, None)):
        saved = _saved(x=x, y=x, width=1150, height=800, maximized=True)
        geometry = dl.restore_window_geometry(saved, screens, platform="darwin")
        assert geometry["maximized"] is False


def test_windows_and_linux_still_open_maximized():
    saved = _saved(maximized=True)
    for platform in ("win32", "linux"):
        geometry = dl.restore_window_geometry(saved, ONE_SCREEN, platform=platform)
        assert geometry == saved


def test_mac_title_bar_zoom_keeps_the_normal_size():
    clock = _Clock()
    start = _saved(x=100, y=50, width=1150, height=800)
    tracker = dl.WindowGeometryTracker(
        start, clock=clock, screens=MAC_SCREEN, platform="darwin"
    )
    clock.now += 30
    tracker.on_moved(0, 25)  # the zoom's own move, just before its resize...
    clock.now += 0.1
    tracker.on_resized(1440, 875)  # ...to the screen less the menu bar
    tracker.on_moved(0, 25)  # and one just after it
    assert tracker.geometry() == start
    tracker.on_resized(1440, 900)  # exactly the screen (our old maximize())
    assert tracker.geometry() == start
    # Zoomed back out: sizes count again.
    clock.now += 30
    tracker.on_resized(1200, 820)
    tracker.on_moved(140, 60)
    assert tracker.geometry() == _saved(x=140, y=60, width=1200, height=820)


def test_mac_full_screen_close_saves_the_normal_size(tmp_path):
    clock = _Clock()
    start = _saved(x=100, y=50, width=1150, height=800)
    tracker = dl.WindowGeometryTracker(
        start, clock=clock, screens=MAC_SCREEN, platform="darwin"
    )
    clock.now += 30
    tracker.on_resized(1440, 900)
    tracker.on_maximized()  # windowDidEnterFullScreen_
    path = tmp_path / "window-geometry.json"
    dl.save_window_geometry(tracker.geometry(), path)
    reopened = dl.restore_window_geometry(
        dl.load_window_geometry(path), MAC_SCREEN, platform="darwin"
    )
    assert reopened == start


def test_screen_sized_resize_is_a_normal_size_off_mac():
    tracker = dl.WindowGeometryTracker(
        DEFAULT, clock=_Clock(), screens=ONE_SCREEN, platform="win32"
    )
    tracker.on_resized(1920, 1080)
    assert (tracker.width, tracker.height) == (1920, 1080)


# --- W-100: the screen holding the corner, clamped to its work area ---------

TASKBAR = 48


def _with_work_area(x, y, w, h):
    return (x, y, w, h, x, y, w, h - TASKBAR)


THREE_SCREENS = [
    _with_work_area(-1920, 0, 1920, 1080),
    _with_work_area(0, 0, 1920, 1080),
    _with_work_area(1920, 0, 1920, 1080),
]


def test_oversized_window_stays_on_the_screen_holding_its_corner():
    # 1920 px of title bar on the right-hand screen beats 1820 on the centre
    # one, but the top-left corner is on the centre screen.
    saved = _saved(x=100, y=50, width=5000, height=3000)
    geometry = dl.restore_window_geometry(saved, THREE_SCREENS, platform="win32")
    assert geometry == _saved(x=0, y=0, width=1920, height=1080 - TASKBAR)


def test_corner_off_every_screen_falls_back_to_the_most_title_bar():
    # Top-left just left of the screens; the title bar lands on both, most
    # of it on the left-hand one.
    saved = _saved(x=50, y=100, width=3000, height=900)
    two = [(100, 0, 1920, 1080), (2020, 0, 1920, 1080)]
    geometry = dl.restore_window_geometry(saved, two)
    assert geometry == _saved(x=100, y=100, width=1920, height=900)


def test_window_low_on_the_screen_is_shifted_clear_of_the_taskbar():
    screen = [_with_work_area(0, 0, 1920, 1080)]
    work_bottom = 1080 - TASKBAR
    # Title bar 20 px above the taskbar line: pulled up onto the work area.
    saved = _saved(x=200, y=work_bottom - 20, width=1400, height=900)
    geometry = dl.restore_window_geometry(saved, screen, platform="win32")
    assert geometry == _saved(x=200, y=work_bottom - 900, width=1400, height=900)
    # Taller than the work area: shrunk to it, not to the screen.
    saved = _saved(x=0, y=0, width=1400, height=1080)
    geometry = dl.restore_window_geometry(saved, screen, platform="win32")
    assert geometry["height"] == work_bottom
    # No saved position: the size is clamped to the work area too.
    saved = _saved(x=None, y=None, width=1400, height=1080)
    geometry = dl.restore_window_geometry(saved, screen, platform="win32")
    assert geometry["height"] == work_bottom


def test_work_areas_off_windows_keep_the_screen():
    if dl.sys.platform == "win32":
        return
    assert dl._windows_work_areas([(0, 0, 1920, 1080)]) == [(0, 0, 1920, 1080)]
