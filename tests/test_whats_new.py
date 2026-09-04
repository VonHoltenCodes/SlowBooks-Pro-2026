"""The splash's what's-new feed ships with the build and stays well-formed."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_whats_new_feed_is_valid_and_wired():
    notes = json.loads((ROOT / "app/static/whats-new.json").read_text())
    assert notes, "at least one release"
    for version, entry in notes.items():
        assert version.count(".") == 2, version
        assert entry["items"] and all(isinstance(i, str) and i for i in entry["items"])
    html = (ROOT / "index.html").read_text()
    assert 'id="splash-whatsnew"' in html and 'id="topbar-company"' in html
    js = (ROOT / "app/static/js/bootstrap.js").read_text()
    assert "/static/whats-new.json" in js


def test_whats_new_is_public(client):
    r = client.get("/static/whats-new.json")
    assert r.status_code == 200 and "2.8.0" in r.json()
