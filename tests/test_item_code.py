"""An item code, searchable with the name (#445).

items.code is optional and unique when set, compared without regard to
capitals; GET /api/items?q= finds an item by name or code; the items CSV
carries a Code column both ways; and the migration that adds it is head.
"""

from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]
REVISION = "c4e5d6f7a8b9"


def _item(client, name, code=None, **extra):
    body = {"name": name, "item_type": "material", "rate": 9.5, **extra}
    if code is not None:
        body["code"] = code
    return client.post("/api/items", json=body)


def test_an_item_is_created_with_a_code_and_carries_it(client):
    r = _item(client, "2x6x16 SPF", "  2x6-16 ")
    assert r.status_code == 201, r.text
    assert r.json()["code"] == "2x6-16"  # trimmed
    got = client.get(f"/api/items/{r.json()['id']}").json()
    assert got["code"] == "2x6-16"
    listed = client.get("/api/items").json()
    assert [i["code"] for i in listed] == ["2x6-16"]


def test_no_code_and_a_blank_code_are_both_no_code(client):
    a = _item(client, "Labor hour").json()
    b = _item(client, "Delivery", "   ").json()
    assert a["code"] is None and b["code"] is None


def test_a_duplicate_code_is_refused_without_regard_to_capitals(client):
    assert _item(client, "2x6x16 SPF", "2x6-16").status_code == 201
    r = _item(client, "2x6x16 Treated", "2X6-16")
    assert r.status_code == 409
    assert r.json()["detail"] == "Item code 2X6-16 is already used by 2x6x16 SPF."
    assert len(client.get("/api/items").json()) == 1


def test_an_inactive_items_code_is_still_taken(client):
    old = _item(client, "Old stud", "STUD").json()
    client.delete(f"/api/items/{old['id']}")
    assert _item(client, "New stud", "stud").status_code == 409


def test_updating_to_a_used_code_is_refused_and_clearing_a_code_works(client):
    a = _item(client, "2x6x16 SPF", "2x6-16").json()
    b = _item(client, "2x4x8 SPF", "2x4-8").json()
    r = client.put(f"/api/items/{b['id']}", json={"code": "2x6-16"})
    assert r.status_code == 409
    assert r.json()["detail"] == "Item code 2x6-16 is already used by 2x6x16 SPF."
    # keeping its own code, in other capitals, is not a clash
    r = client.put(f"/api/items/{a['id']}", json={"code": "2X6-16"})
    assert r.status_code == 200 and r.json()["code"] == "2X6-16"
    # blank and null both clear it
    r = client.put(f"/api/items/{b['id']}", json={"code": ""})
    assert r.status_code == 200 and r.json()["code"] is None
    r = client.put(f"/api/items/{a['id']}", json={"code": None})
    assert r.status_code == 200 and r.json()["code"] is None
    # and a cleared code is free again
    assert (
        client.put(f"/api/items/{b['id']}", json={"code": "2x6-16"}).status_code == 200
    )


def test_a_code_longer_than_fifty_is_refused(client):
    assert _item(client, "Long", "x" * 51).status_code == 422


def test_q_finds_an_item_by_code_or_by_name(client):
    _item(client, "2x6x16 SPF", "LUM-266")
    _item(client, "Drywall screw", "FAST-01")
    _item(client, "Site cleanup")

    def names(q):
        return [i["name"] for i in client.get("/api/items", params={"q": q}).json()]

    assert names("lum-2") == ["2x6x16 SPF"]  # by code, any capitals
    assert names("screw") == ["Drywall screw"]  # by name
    assert names("fast") == ["Drywall screw"]
    assert names("nothing") == []
    assert len(names("")) == 3
    # the older ?search= finds codes too
    found = client.get("/api/items", params={"search": "FAST"}).json()
    assert [i["name"] for i in found] == ["Drywall screw"]


def test_the_csv_export_has_the_code_after_the_name(client):
    _item(client, "2x6x16 SPF", "2x6-16")
    _item(client, "Labor hour")
    text = client.get("/api/csv/export/items").text.lstrip("﻿")
    rows = [line.split(",") for line in text.strip().splitlines()]
    assert rows[0][:3] == ["ID", "Name", "Code"]
    by_name = {r[1]: r for r in rows[1:]}
    assert by_name["2x6x16 SPF"][2] == "2x6-16"
    assert by_name["Labor hour"][2] == ""


def _import(client, text):
    r = client.post(
        "/api/csv/import/items",
        files={"file": ("items.csv", text.encode("utf-8"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_the_csv_import_reads_the_code_and_notes_a_duplicate(client):
    _item(client, "2x6x16 SPF", "2x6-16")
    result = _import(
        client,
        "Name,Code,Type,Rate,Cost\n"
        "2x4x8 SPF,2x4-8,material,4.25,3.10\n"
        "2x6x16 Treated,2X6-16,material,12,9\n"
        "Labor hour,,labor,65,0\n"
        "Second 2x4,2x4-8,material,4,3\n",
    )
    assert result["created"] == 4
    assert result["errors"] == [
        "Row 3: Item code 2X6-16 is already used by 2x6x16 SPF; "
        "2x6x16 Treated was imported without a code.",
        "Row 5: Item code 2x4-8 is already used by 2x4x8 SPF; "
        "Second 2x4 was imported without a code.",
    ]
    codes = {i["name"]: i["code"] for i in client.get("/api/items").json()}
    assert codes == {
        "2x6x16 SPF": "2x6-16",
        "2x4x8 SPF": "2x4-8",
        "2x6x16 Treated": None,
        "Labor hour": None,
        "Second 2x4": None,
    }


def test_the_csv_round_trips_the_code(client):
    _item(client, "2x6x16 SPF", "2x6-16")
    _item(client, "Labor hour")
    exported = client.get("/api/csv/export/items").text.lstrip("﻿")
    # a fresh name for each row, so the round trip creates rather than skips
    renamed = exported.replace("2x6x16 SPF", "Copy SPF").replace(
        "Labor hour", "Copy labor"
    )
    for item in client.get("/api/items").json():
        client.put(f"/api/items/{item['id']}", json={"code": None})
    result = _import(client, renamed)
    assert result["errors"] == [] and result["created"] == 2
    codes = {i["name"]: i["code"] for i in client.get("/api/items").json()}
    assert codes["Copy SPF"] == "2x6-16" and codes["Copy labor"] is None


def _cfg(db):
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + Path(db).as_posix()
    return cfg


def test_the_migration_is_head_and_adds_a_unique_code(tmp_path):
    from alembic import command
    from alembic.script import ScriptDirectory

    db = tmp_path / "code.db"
    cfg = _cfg(db)
    assert ScriptDirectory.from_config(cfg).get_heads() == [REVISION]
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db}")
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("items")}
    assert cols["code"]["nullable"] is True
    idx = {i["name"]: i for i in insp.get_indexes("items")}
    assert idx["ix_items_code"]["column_names"] == ["code"]
    assert idx["ix_items_code"]["unique"]
    engine.dispose()
    command.downgrade(cfg, "d3e4f5a6b7c8")
    engine = create_engine(f"sqlite:///{db}")
    assert "code" not in {c["name"] for c in inspect(engine).get_columns("items")}
    engine.dispose()


# --- the line pickers (source checks, as tests/test_keyboard_paths.py does) ---

JS = ROOT / "app/static/js"
PICKERS = (
    "invoices.js",
    "sales_receipts.js",
    "estimates.js",
    "credit_memos.js",
    "recurring.js",
    "purchase_orders.js",
    "vendor_credits.js",
    "bills.js",
    "app.js",
)


def _src(name):
    return (JS / name).read_text(encoding="utf-8")


def test_every_line_item_select_renders_through_the_shared_options():
    import re

    for name in PICKERS:
        src = _src(name)
        selects = re.findall(
            r'<select class="line-item"[^>]*>(.*?)</select>', src, re.S
        )
        assert selects, name
        for body in selects:
            assert body.strip().startswith("${ItemQuickAdd.options("), (name, body)
        # and no page builds item options of its own any more
        assert "escapeHtml(i.name)}</option>" not in src, name
        assert "escapeHtml(it.name)}</option>" not in src, name


def test_every_change_handler_and_save_goes_through_the_helper():
    for name in PICKERS:
        src = _src(name)
        assert "ItemQuickAdd.selected(" in src, name
        assert "ItemQuickAdd.itemId(row.querySelector('.line-item'))" in src, name
        assert "parseInt(row.querySelector('.line-item').value)" not in src, name


def test_the_helper_labels_with_the_code_and_is_loaded():
    helper = _src("item_quick_add.js")
    assert "item.code ? `${item.code} — ${item.name}` : item.name" in helper
    assert "escapeHtml(ItemQuickAdd.label(i))" in helper
    assert '<option value="${ItemQuickAdd.NEW}">+ Add item</option>' in helper
    assert "/^\\d+$/.test(value) ? Number(value) : null" in helper
    index = (ROOT / "index.html").read_text(encoding="utf-8")
    at = index.index('<script src="/static/js/item_quick_add.js"></script>')
    # before every page that draws a picker
    for name in PICKERS:
        assert at < index.index(f'<script src="/static/js/{name}"></script>'), name


def test_the_items_page_shows_and_searches_the_code():
    items = _src("items.js")
    assert '<th scope="col">Name</th><th scope="col">Code</th>' in items
    assert 'id="items-search"' in items
    assert '<input name="code" maxlength="50"' in items
    assert "code: form.code.value.trim() || null" in items


def test_item_ids_from_the_picker_in_node():
    """itemId and options, run: junk is no item, a number is one."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = """
const fs = require('fs'), vm = require('vm');
const ctx = { escapeHtml: s => String(s ?? '').replace(/</g, '&lt;') };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8') + '\\nthis.Q = ItemQuickAdd;', ctx);
const Q = ctx.Q;
const ids = ['', '--', '__new__', '+ New Item', '12abc', '-3', '7', '0042']
    .map(v => Q.itemId({ value: v }));
const html = Q.options([{ id: 7, name: '2x6x16 SPF', code: '2x6-16' }, { id: 8, name: 'Labor' }], 7);
console.log(JSON.stringify({ ids, none: Q.itemId(null), html }));
"""
    out = subprocess.run(
        [node, "-e", script, str(JS / "item_quick_add.js")],
        capture_output=True,
        text=True,
        # node writes UTF-8; without this Windows decodes the label's em dash
        # as cp1252 and the assert reads mojibake (#445)
        encoding="utf-8",
        check=True,
    )
    got = json.loads(out.stdout)
    assert got["ids"] == [None, None, None, None, None, None, 7, 42]
    assert got["none"] is None
    assert got["html"] == (
        '<option value="">--</option><option value="__new__">+ Add item</option>'
        '<option value="7" selected>2x6-16 — 2x6x16 SPF</option>'
        '<option value="8">Labor</option>'
    )
