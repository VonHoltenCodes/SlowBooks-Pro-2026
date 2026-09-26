"""Item and account lists (2.17.3 exploratory test).

* W-M16 — a second active item of the same name is refused, and an item
  can be made inactive from its form; every item picker lists active items.
* W-L13 — the item form's Income account lists income accounts only, and a
  business does not see the nonprofit-only 4400 In-Kind Contributions; the
  nonprofit pages answer a business company with a sentence.
"""

from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


# ── W-M16: one active item per name ──────────────────────────────────────


def _item(client, name, **kw):
    return client.post("/api/items", json={"name": name, "item_type": "service", **kw})


def test_a_second_active_item_of_the_same_name_is_refused(client):
    assert _item(client, "Design Hour", rate=85).status_code == 201
    r = _item(client, "  design hour ", rate=90)
    assert r.status_code == 409
    assert 'already an item named "Design Hour"' in r.json()["detail"]
    assert [i["name"] for i in client.get("/api/items").json()] == ["Design Hour"]


def test_renaming_or_reactivating_into_a_clash_is_refused(client):
    first = _item(client, "Design Hour").json()
    other = _item(client, "Install Hour").json()
    r = client.put(f"/api/items/{other['id']}", json={"name": "DESIGN HOUR"})
    assert r.status_code == 409
    # make the first inactive: the name is free for a new item...
    r = client.put(f"/api/items/{first['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert _item(client, "Design Hour").status_code == 201
    # ...and the old one cannot come back while the new one is active
    r = client.put(f"/api/items/{first['id']}", json={"is_active": True})
    assert r.status_code == 409


def test_an_edit_that_keeps_the_name_is_not_refused(client, db_session):
    from app.models.items import Item, ItemType

    # two made before the check existed
    for _ in range(2):
        db_session.add(Item(name="Legacy Hour", item_type=ItemType.SERVICE))
    db_session.commit()
    dup = client.get("/api/items").json()[0]
    r = client.put(f"/api/items/{dup['id']}", json={"name": "Legacy Hour", "rate": 70})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/items/{dup['id']}", json={"is_active": False})
    assert r.status_code == 200


def test_the_item_form_has_an_active_toggle():
    js = (JS / "items.js").read_text(encoding="utf-8")
    assert 'name="is_active"' in js
    assert "data.is_active = form.is_active.checked" in js
    assert "inactive</span>" in js  # the list marks an inactive item


def test_every_item_picker_lists_active_items_only(client):
    for path in JS.glob("*.js"):
        if path.name == "items.js":  # the item list itself shows every item
            continue
        text = path.read_text(encoding="utf-8")
        for call in ("API.get('/items')", 'API.get("/items")', "API.get(`/items`)"):
            assert call not in text, f"{path.name} lists inactive items"
    first = _item(client, "Old Service").json()
    client.put(f"/api/items/{first['id']}", json={"is_active": False})
    assert client.get("/api/items?active_only=true").json() == []


# ── W-L13: nonprofit-only accounts ───────────────────────────────────────


def test_the_business_chart_marks_the_nonprofit_accounts(client, seed_accounts):
    by_number = {a["account_number"]: a for a in client.get("/api/accounts").json()}
    assert by_number["4400"]["nonprofit_only"] is True
    for number in ("4000", "4900", "6960", "5000"):
        assert by_number[number]["nonprofit_only"] is False, number


def test_a_business_that_reuses_4400_keeps_it(client, seed_accounts, db_session):
    seed_accounts["4400"].name = "Consulting Income"
    db_session.commit()
    a = client.get(f"/api/accounts/{seed_accounts['4400'].id}").json()
    assert a["nonprofit_only"] is False


def test_the_item_income_picker_offers_income_accounts_only():
    js = (JS / "items.js").read_text(encoding="utf-8")
    assert "pickerAccounts(accounts, ['income'], item.income_account_id)" in js
    assert "['income','cogs']" not in js
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "function pickerAccounts(" in utils
    assert "a.nonprofit_only && !nonprofit" in utils


def test_the_nonprofit_pages_answer_a_business_with_a_sentence():
    js = (JS / "app.js").read_text(encoding="utf-8")
    for route in ("'/releases'", "'/functional-allocations'", "'/in-kind-gifts'"):
        line = next(ln for ln in js.splitlines() if ln.strip().startswith(route))
        assert "nonprofit: true" in line, route
    assert "route.nonprofit && !Terms.isNonprofit()" in js
    assert "is for nonprofit companies" in js
