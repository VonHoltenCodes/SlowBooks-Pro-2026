"""W-206: the desktop window's title names the open company.

The page's document.title was "<company> — SlowBooks Pro 2026", but WebView2
(and macOS's WebKit window) never take a page's title, so the title bar read
"SlowBooks Pro 2026" on every company. The page now hands the company's name
to the launcher, which titles the window the same way; the picker puts the
plain name back. Nothing finds the window by its title (a second launch is
caught by the port), so no reader breaks.
"""

from pathlib import Path

import desktop_launcher as dl

ROOT = Path(__file__).resolve().parents[1]


class _Window:
    def __init__(self):
        self.titles = []

    def set_title(self, title):
        self.titles.append(title)

    def load_html(self, html):
        self.loaded = html


def test_the_title_reads_as_the_pages_does():
    assert dl.window_title_for("Harbor Light Bakery") == (
        "Harbor Light Bakery — SlowBooks Pro 2026"
    )
    assert dl.window_title_for("") == "SlowBooks Pro 2026"
    assert dl.window_title_for("  ") == "SlowBooks Pro 2026"
    # no control characters reach the title bar, and a long name is cut
    assert dl.window_title_for("A\nB\tC") == "A B C — SlowBooks Pro 2026"
    assert len(dl.window_title_for("x" * 500)) == 100 + len(" — SlowBooks Pro 2026")


def test_the_page_sets_it_and_the_picker_puts_the_plain_one_back(monkeypatch):
    api = dl.PickerApi(0)
    api._window = _Window()
    assert api.set_window_title("Harbor Light Bakery")["success"] is True
    assert api._window.titles == ["Harbor Light Bakery — SlowBooks Pro 2026"]
    monkeypatch.setattr(dl, "stop_server", lambda server: None)
    monkeypatch.setattr(dl.time, "sleep", lambda s: None)
    api.show_picker()
    import time

    deadline = time.monotonic() + 5
    while not hasattr(api._window, "loaded") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert api._window.titles[-1] == "SlowBooks Pro 2026"


def test_a_window_that_cannot_take_it_is_no_error():
    api = dl.PickerApi(0)
    assert api.set_window_title("X")["success"] is True  # no window yet

    class Broken:
        def set_title(self, title):
            raise RuntimeError("gone")

    api._window = Broken()
    assert api.set_window_title("X") == {"success": False}


def test_the_window_starts_with_the_plain_name_and_the_page_hands_it_over():
    src = (ROOT / "desktop_launcher.py").read_text(encoding="utf-8")
    made = src[src.index("api._window = webview.create_window(") :]
    assert made.split("\n", 2)[1].strip() == "WINDOW_TITLE,"
    js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
    show = js[js.index("    showCompany(s) {") :]
    show = show[: show.index("\n    },")]
    assert "document.title = `${name} — SlowBooks Pro 2026`;" in show
    assert "App.setWindowTitle(name);" in show
    assert "api.set_window_title(name)" in js
