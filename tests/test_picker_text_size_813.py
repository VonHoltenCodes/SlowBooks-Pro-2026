"""The desktop company picker follows Text size (#813).

The picker is the launcher's own page (HTML with no origin), so it could
not read the size the company's pages keep in their storage and stayed at
100% whatever the setting, with Ctrl + doing nothing. The pages now hand
the size to the window, the launcher keeps it beside the window's
geometry, and the picker applies it and steps it with the same keys."""

import json

import desktop_launcher as dl


def test_the_size_is_kept_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    api = dl.PickerApi(port=0)
    assert api.text_size() == {"size": 100}
    assert api.set_text_size(125) == {"success": True}
    assert json.loads((tmp_path / dl.TEXT_SIZE_FILE).read_text()) == {"size": 125}
    assert api.text_size() == {"size": 125}
    # a string from the page is the same size
    assert api.set_text_size("150") == {"success": True}
    assert api.text_size() == {"size": 150}


def test_anything_but_the_four_sizes_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    api = dl.PickerApi(port=0)
    api.set_text_size(115)
    for bad in (90, 200, "big", None, True):
        assert api.set_text_size(bad) == {"success": False}, bad
    assert api.text_size() == {"size": 115}
    # a file that says something else reads as 100
    (tmp_path / dl.TEXT_SIZE_FILE).write_text('{"size": 300}')
    assert api.text_size() == {"size": 100}
    (tmp_path / dl.TEXT_SIZE_FILE).write_text("not json")
    assert api.text_size() == {"size": 100}


def test_the_picker_applies_and_steps_the_size():
    html = dl.PICKER_HTML
    assert "api.text_size()" in html
    assert "api.set_text_size(textSize)" in html
    assert "document.documentElement.style.zoom" in html
    assert "var SIZES = [100, 115, 125, 150]" in html
    assert list(dl.TEXT_SIZES) == [100, 115, 125, 150]
