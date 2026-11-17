"""The hare mark (#979) is on the From QuickBooks sections and nowhere else:
the three Interoperability sections, Migrate Data's QuickBooks Online hint,
and the migration guide's head."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_assets_exist():
    for name in ("hare-mark.png", "hare-mark@2x.png"):
        assert (ROOT / "app/static/img" / name).stat().st_size > 1000
    assert (ROOT / "docs/img/hare-mark.png").stat().st_size > 1000


def test_the_three_interoperability_sections_carry_it():
    js = (ROOT / "app/static/js/iif.js").read_text(encoding="utf-8")
    assert js.count('class="hare-mark"') == 3
    for head in ("Export to IIF", "Import from IIF", "Import from Report CSV"):
        assert re.search(
            rf"<h3>[^<]*{re.escape(head)}<img class=\"hare-mark\"", js
        ), head


def test_migrate_data_shows_it_for_quickbooks_online_only():
    js = (ROOT / "app/static/js/migration.js").read_text(encoding="utf-8")
    assert js.count('class="hare-mark"') == 1
    hint = js.split("_hint(key) {")[1]
    qbo = hint.split("if (key === 'qbo_export') {")[1].split("}")[0]
    assert 'class="hare-mark"' in qbo


def test_the_mark_is_served_and_styled_and_in_the_guide(client):
    r = client.get("/static/img/hare-mark.png")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    css = (ROOT / "app/static/css/style.css").read_text(encoding="utf-8")
    assert ".hare-mark {" in css
    guide = (ROOT / "docs/migrate-from-quickbooks.md").read_text(encoding="utf-8")
    assert "](img/hare-mark.png)" in guide
    # nowhere in the chrome
    shell = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "hare-mark" not in shell
