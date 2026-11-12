"""The Mac app's Window menu (#813): pywebview's menu bar had no Window
menu, so no Minimize and no ⌘M. The launcher adds one each time pywebview
builds the bar. Driven here with a stand-in for AppKit; the real menu is a
Mac check (the macOS gate)."""

import desktop_launcher as dl


class _Item:
    def __init__(self, title="", action=None, key=""):
        self._title, self.action, self.key, self.submenu = title, action, key, None

    @classmethod
    def alloc(cls):
        return cls

    @classmethod
    def init(cls):
        return cls()

    @classmethod
    def separatorItem(cls):
        return cls("-")

    def title(self):
        return self._title

    def setTitle_(self, t):
        self._title = t

    def setSubmenu_(self, m):
        self.submenu = m


class _Menu:
    def __init__(self, title=""):
        self.title, self.items = title, []

    @classmethod
    def alloc(cls):
        return cls

    @classmethod
    def initWithTitle_(cls, title):
        return cls(title)

    def addItem_(self, item):
        self.items.append(item)

    def addItemWithTitle_action_keyEquivalent_(self, title, action, key):
        self.items.append(_Item(title, action, key))

    def numberOfItems(self):
        return len(self.items)

    def itemAtIndex_(self, i):
        return self.items[i]


class _AppKit:
    NSMenu = _Menu
    NSMenuItem = _Item


class _App:
    windows_menu = None

    def setWindowsMenu_(self, m):
        self.windows_menu = m


def test_the_window_menu_has_minimize_on_command_m():
    bar, app = _Menu(), _App()
    bar.addItem_(_Item("SlowBooks Pro"))
    dl._add_mac_window_menu(_AppKit, bar, app)
    window = bar.items[-1]
    assert window.title() == "Window"
    got = [(i.title(), i.action, i.key) for i in window.submenu.items]
    assert got == [
        ("Minimize", "performMiniaturize:", "m"),
        ("Zoom", "performZoom:", ""),
        ("-", None, ""),
        ("Bring All to Front", "arrangeInFront:", ""),
    ]
    assert app.windows_menu is window.submenu
    # a bar built again with its Window menu gets no second one
    dl._add_mac_window_menu(_AppKit, bar, app)
    assert [i.title() for i in bar.items] == ["SlowBooks Pro", "Window"]


def test_off_a_mac_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(dl.sys, "platform", "win32")
    assert dl.install_mac_window_menu() is False


def test_it_is_installed_before_the_window_starts():
    src = (dl.Path(dl.__file__)).read_text(encoding="utf-8")
    at = src.index("        install_mac_window_menu()\n        webview.start(")
    assert src.rindex("install_mac_print_panel()", 0, at) > 0
