"""The CSV importers (2.17.3 exploratory test).

* W-M15 — the export guards a formula-shaped name with an apostrophe
  ("'=HYPERLINK(...)"); every importer takes it off again, so re-importing
  our own file finds the customer instead of creating a second one.
* W-L4 / macbase1 — a row is checked the way the form checks it (email,
  terms), a row without terms gets the company's default, and an item row
  whose rate is not a number says so.
"""

import io

EVIL = "=HYPERLINK(1)"


def _item(client, name, **kw):
    return client.post("/api/items", json={"name": name, "item_type": "service", **kw})


def _upload(client, entity, text, query=""):
    r = client.post(
        f"/api/csv/import/{entity}{query}",
        files={"file": (f"{entity}.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _export(client, entity):
    return client.get(f"/api/csv/export/{entity}").content


# ── W-M15: the round trip gives the names back ───────────────────────────


def test_reimporting_our_customer_export_finds_the_customer(client):
    assert client.post("/api/customers", json={"name": EVIL}).status_code == 201
    exported = _export(client, "customers")
    assert b"'" + EVIL.encode() in exported  # the export guards it
    r = client.post(
        "/api/csv/import/customers",
        files={"file": ("customers.csv", exported, "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 0 and r.json()["skipped"] == 1
    names = [c["name"] for c in client.get("/api/customers").json()]
    assert names == [EVIL]


def test_reimporting_vendors_and_items_finds_them_too(client):
    assert client.post("/api/vendors", json={"name": "+Plus Supply"}).status_code == 201
    r = client.post(
        "/api/items", json={"name": "-Discount", "item_type": "service", "rate": 5}
    )
    assert r.status_code == 201, r.text
    for entity in ("vendors", "items"):
        r = client.post(
            f"/api/csv/import/{entity}",
            files={"file": ("x.csv", _export(client, entity), "text/csv")},
        )
        assert r.json()["created"] == 0 and r.json()["skipped"] == 1, (entity, r.json())
    assert [v["name"] for v in client.get("/api/vendors").json()] == ["+Plus Supply"]
    assert [i["name"] for i in client.get("/api/items").json()] == ["-Discount"]


def test_a_guarded_name_imports_as_the_name(client):
    out = _upload(client, "customers", "Name,Email\n'@Acme Diner,\n'-Dash Co,\n")
    assert out["created"] == 2, out
    names = sorted(c["name"] for c in client.get("/api/customers").json())
    assert names == ["-Dash Co", "@Acme Diner"]


def test_the_chart_import_reads_our_own_export_without_renaming(
    client, seed_accounts, db_session
):
    seed_accounts["6000"].name = "=Promo"
    db_session.commit()
    exported = _export(client, "accounts")
    r = client.post(
        "/api/csv/import/accounts?dry_run=1",
        files={"file": ("chart.csv", exported, "text/csv")},
    )
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["rows"] if x["number"] == "6000")
    assert row["name"] == "=Promo" and row["action"] != "update", row


def test_the_migration_and_report_readers_take_the_guard_off():
    from app.services.migration_common import sniff_reader
    from app.services.qb_report_import import _csv_rows

    assert next(iter(sniff_reader("Name,Memo\n'=X,'@note\n"))) == {
        "Name": "=X",
        "Memo": "@note",
    }
    assert _csv_rows("a,b\n'+1,'-2\n") == [["a", "b"], ["+1", "-2"]]


# ── W-L4: a CSV row is checked the way the form checks it ────────────────


def test_an_email_the_form_refuses_is_refused_by_the_import(client):
    out = _upload(
        client,
        "customers",
        "Name,Email\nGood Co,ap@good.test\nBad Co,not-an-email\n",
    )
    assert out["created"] == 1
    assert len(out["errors"]) == 1 and "Row 3" in out["errors"][0]
    assert "not-an-email" in out["errors"][0] and "email" in out["errors"][0]
    names = [c["name"] for c in client.get("/api/customers").json()]
    assert names == ["Good Co"]


def test_terms_the_form_does_not_offer_are_refused(client):
    out = _upload(
        client, "customers", "Name,Terms\nOdd Terms Co,Net 99\nOk Co,net 45\n"
    )
    assert out["created"] == 1
    assert "Net 99" in out["errors"][0] and "Net 30" in out["errors"][0]
    ok = next(c for c in client.get("/api/customers").json() if c["name"] == "Ok Co")
    assert ok["terms"] == "Net 45"  # the form's spelling


def test_a_row_without_terms_gets_the_company_default(client):
    assert (
        client.put("/api/settings", json={"default_terms": "Net 15"}).status_code == 200
    )
    out = _upload(client, "customers", "Name,Terms\nNo Terms Co,\n")
    assert out["created"] == 1, out
    cust = client.get("/api/customers").json()[0]
    assert cust["terms"] == "Net 15"
    out = _upload(client, "vendors", "Name,Email\nNo Terms Supply,\n")
    assert client.get("/api/vendors").json()[0]["terms"] == "Net 30"


def test_a_vendor_email_is_checked_too(client):
    out = _upload(client, "vendors", "Name,Email\nBad Supply,nope\n")
    assert out["created"] == 0 and "nope" in out["errors"][0]


def test_the_csv_import_skips_an_item_it_would_duplicate(client):
    assert _item(client, "Design Hour").status_code == 201
    out = _upload(client, "items", "Name,Type,Rate\nDESIGN HOUR,service,90\n")
    assert out["created"] == 0 and out["skipped"] == 1


def test_an_item_named_twice_in_one_file_imports_once(client):
    out = _upload(client, "items", "Name,Rate\nDesign Hour,85\ndesign  hour,90\n")
    assert out["created"] == 1 and out["skipped"] == 1, out
    assert [i["name"] for i in client.get("/api/items").json()] == ["Design Hour"]


def test_an_item_row_with_a_rate_that_is_not_a_number_says_so(client):
    out = _upload(
        client, "items", 'Name,Rate\nWidget,abc\nGadget,"$1,250.50"\nOdd,nan\n'
    )
    assert out["errors"] == [
        'Row 2: Rate "abc" is not a number.',
        'Row 4: Rate "nan" is not a number.',
    ]
    gadget = client.get("/api/items").json()[0]
    assert gadget["name"] == "Gadget" and gadget["rate"] == "1250.50"
