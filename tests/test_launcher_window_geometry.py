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
    saved = _saved(x=2200, y=200, width=1600, height=1000, maximized=True)
    assert dl.restore_window_geometry(saved, TWO_SCREENS) == saved


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
