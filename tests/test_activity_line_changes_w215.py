"""Round 25 (W-215): Activity says only what a save changed on the lines.

Saving a change order listed every line as "Line added: …, $0.00" and
"Amount $0.00 → $X", even when only the title changed, and never named a
line's real change: `_set_lines` bulk-deletes the lines (no audit row) and
inserts them at 0 before the recompute. Invoices, estimates, bills,
purchase orders and journal entries delete and insert their lines on every
save too, which read as "Line removed: X" and "Line added: X" for each
line. Activity now reads a save's lines as one change of the set: an
unchanged line says nothing, a changed one names the field old → new, and
only a line truly added or removed says so, once, with its real amount.
"""

from tests.test_co_effective_date_allowance import _setup
from tests.test_document_activity_894 import _activity, _age, _customer

CO_LINES = [
    {"description": "Deck", "cost_type": "material", "quantity": 1, "unit_cost": 100},
    {"cost_type": "material", "quantity": 1, "unit_cost": 200},
]


def _newest(client, kind, id_):
    return _activity(client, kind, id_)["events"][0]


def _line_details(event):
    return [d for d in event["details"] if d.startswith("Line")]


def test_a_change_orders_lines_say_only_what_changed(client, seed_customer, db_session):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    r = client.post(
        "/api/change-orders",
        json={
            "job_id": job["id"],
            "kind": "owner",
            "title": "Porch",
            "lines": CO_LINES,
        },
    )
    assert r.status_code == 201, r.text
    co = r.json()
    url = f"/api/change-orders/{co['id']}"
    created = _newest(client, "change_order", co["id"])
    # the lines as made, with their real amounts, once
    assert created["details"] == ["Line: Deck, $100.00", "Line: $200.00"]

    # the form's save: every line sent again with no ids, only the title new
    _age(db_session)
    r = client.put(url, json={"title": "Porch and steps", "lines": CO_LINES})
    assert r.status_code == 200, r.text
    edited = _newest(client, "change_order", co["id"])
    assert edited["details"] == ["Title Porch → Porch and steps"], edited

    # a line's quantity and another's own markup
    _age(db_session)
    lines = [CO_LINES[0] | {"markup_pct": 20}, CO_LINES[1] | {"quantity": 3}]
    assert client.put(url, json={"lines": lines}).status_code == 200
    edited = _newest(client, "change_order", co["id"])
    assert edited["details"] == [
        "Line Deck, $100.00: Markup % set to 20%; Sell amount $100.00 → $120.00",
        "Line 2 ($600.00): Quantity 1 → 3; Amount $200.00 → $600.00; "
        "Sell amount $200.00 → $600.00",
    ], edited

    # a line added, then one removed: each said once, with its amount
    _age(db_session)
    extra = {"description": "Rail", "cost_type": "labor", "quantity": 2}
    extra |= {"unit_cost": 40}
    assert client.put(url, json={"lines": [*lines, extra]}).status_code == 200
    assert _line_details(_newest(client, "change_order", co["id"])) == [
        "Line added: Rail, $80.00"
    ]
    _age(db_session)
    assert client.put(url, json={"lines": [lines[0], extra]}).status_code == 200
    said = _line_details(_newest(client, "change_order", co["id"]))
    # Deck and Rail are found unchanged; the $600.00 line is the one gone
    assert said == ["Line removed: $600.00"], said


def _same_then_quantity(client, db_session, kind, url, doc, lines, header):
    _age(db_session)
    r = client.put(f"{url}/{doc['id']}", json=header | {"lines": lines})
    assert r.status_code == 200, r.text
    edited = _newest(client, kind, doc["id"])
    assert edited["kind"] == "edited", edited
    assert _line_details(edited) == [], (kind, edited)
    _age(db_session)
    changed = [lines[0], lines[1] | {"quantity": 3}]
    r = client.put(f"{url}/{doc['id']}", json={"lines": changed})
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, kind, doc["id"]))
    assert len(said) == 1 and ": Quantity 2 → 3; Amount " in said[0], (kind, said)
    assert said[0].startswith("Line more"), said


def test_the_line_documents_say_only_what_changed(client, seed_accounts, db_session):
    cust = _customer(client)
    lines = [
        {"description": "work", "quantity": 1, "rate": 100, "line_order": 0},
        {"description": "more", "quantity": 2, "rate": 50, "line_order": 1},
    ]
    for kind, url, body in (
        ("invoice", "/api/invoices", {"customer_id": cust["id"]} | {"due_date": None}),
        ("estimate", "/api/estimates", {"customer_id": cust["id"]}),
    ):
        body = {k: v for k, v in body.items() if v is not None}
        r = client.post(url, json=body | {"date": "2026-06-01", "lines": lines})
        assert r.status_code == 201, r.text
        _same_then_quantity(
            client, db_session, kind, url, r.json(), lines, {"notes": "hello"}
        )

    vendor = client.post("/api/vendors", json={"name": "Parts Supply"}).json()
    po_lines = [
        {"description": "parts", "quantity": 1, "rate": "50.00"},
        {"description": "more", "quantity": 2, "rate": "10.00"},
    ]
    r = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor["id"], "date": "2026-04-01", "lines": po_lines},
    )
    assert r.status_code == 201, r.text
    po = client.get(f"/api/purchase-orders/{r.json()['id']}").json()
    # the form sends each kept line's id (a line with none is a new one, W-230)
    po_lines = [
        {k: ln[k] for k in ("id", "description", "quantity", "rate")}
        for ln in po["lines"]
    ]
    _same_then_quantity(
        client,
        db_session,
        "purchase_order",
        "/api/purchase-orders",
        po,
        po_lines,
        {"notes": "hi"},
    )

    exp = seed_accounts["6900"].id if "6900" in seed_accounts else None
    bill_lines = [
        {"account_id": exp, "description": "parts", "quantity": 1, "rate": "50.00"},
        {"account_id": exp, "description": "more", "quantity": 2, "rate": "10.00"},
    ]
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-215",
            "date": "2026-04-01",
            "due_date": "2026-05-01",
            "lines": [
                ln | {"amount": str(ln["quantity"] * float(ln["rate"]))}
                for ln in bill_lines
            ],
        },
    )
    assert r.status_code == 201, r.text
    bill_lines = [
        ln | {"amount": str(ln["quantity"] * float(ln["rate"]))} for ln in bill_lines
    ]
    bill = r.json()
    _age(db_session)
    r = client.put(
        f"/api/bills/{bill['id']}", json={"notes": "hi", "lines": bill_lines}
    )
    assert r.status_code == 200, r.text
    assert _line_details(_newest(client, "bill", bill["id"])) == []
    _age(db_session)
    changed = [bill_lines[0], bill_lines[1] | {"quantity": 3, "amount": "30.0"}]
    r = client.put(f"/api/bills/{bill['id']}", json={"lines": changed})
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, "bill", bill["id"]))
    assert len(said) == 1 and "Quantity 2 → 3" in said[0], said
