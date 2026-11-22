"""Round 26: Activity pairs a save's lines by id, and tidies a kept line.

W-222: removing one line and adding another in the same save read as one
line edited ("Line Knobs, $20.00: Description Pulls → Knobs; …"). A
purchase order's form sends each kept line's id, so there a line whose id
is gone was removed and a line with no id was added; round 25 read that
right, and #1174's pairing by place broke it. Where no ids are sent (the
other kinds rewrite their lines), two leftover lines that share nothing,
neither description nor amount, are read as one removed and one added.

W-223: a purchase order's kept line, edited in place, read "Quantity
1.0000 → 3.0" after the Subtotal and Total; it reads "Quantity 1 → 3",
before the document's own totals, as on every other kind.
"""

from tests.test_activity_line_changes_w215 import _line_details, _newest
from tests.test_document_activity_894 import _age, _customer


def _po(client):
    vendor = client.post("/api/vendors", json={"name": "Hardware Supply"}).json()
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-01",
            "lines": [
                {"description": "Hinges", "quantity": 1, "rate": "30.00"},
                {"description": "Pulls", "quantity": 1, "rate": "45.00"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return client.get(f"/api/purchase-orders/{r.json()['id']}").json()


def _kept(line, **change):
    keep = {k: line[k] for k in ("id", "description", "quantity", "rate")}
    return keep | change


def test_a_purchase_order_reads_a_removed_and_an_added_line(
    client, seed_accounts, db_session
):
    po = _po(client)
    hinges, pulls = po["lines"]
    _age(db_session)
    r = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={
            "lines": [
                _kept(hinges),
                {"description": "Knobs", "quantity": 1, "rate": "20.00"},
            ]
        },
    )
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, "purchase_order", po["id"]))
    assert said == ["Line added: Knobs, $20.00", "Line removed: Pulls, $45.00"], said


def test_a_purchase_order_quantity_reads_tidy_and_before_the_totals(
    client, seed_accounts, db_session
):
    po = _po(client)
    hinges, pulls = po["lines"]
    _age(db_session)
    r = client.put(
        f"/api/purchase-orders/{po['id']}",
        json={"lines": [_kept(hinges, quantity=3), _kept(pulls)]},
    )
    assert r.status_code == 200, r.text
    details = _newest(client, "purchase_order", po["id"])["details"]
    assert details[0] == (
        "Line Hinges, $90.00: Quantity 1 → 3; Amount $30.00 → $90.00"
    ), details
    assert all("1.0000" not in d and "3.0" not in d for d in details), details
    # the document's totals come after the line that moved them
    assert any(d.startswith("Subtotal") or d.startswith("Total") for d in details)


def test_a_replaced_line_with_nothing_in_common_reads_as_removed_and_added(
    client, seed_accounts, db_session
):
    cust = _customer(client)
    lines = [
        {"description": "Demo", "quantity": 1, "rate": 95, "line_order": 0},
        {"description": "Haul away", "quantity": 1, "rate": 60, "line_order": 1},
    ]
    r = client.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "date": "2026-06-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    _age(db_session)
    new = [lines[0], {"description": "Sweep up", "quantity": 1, "rate": 40}]
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": new})
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, "invoice", inv["id"]))
    assert said == [
        "Line added: Sweep up, $40.00",
        "Line removed: Haul away, $60.00",
    ], said

    # a line that keeps its description or its amount is still that line
    _age(db_session)
    new = [lines[0], {"description": "Sweep up", "quantity": 2, "rate": 40}]
    assert (
        client.put(f"/api/invoices/{inv['id']}", json={"lines": new}).status_code == 200
    )
    said = _line_details(_newest(client, "invoice", inv["id"]))
    assert said == ["Line Sweep up, $80.00: Quantity 1 → 2; Amount $40.00 → $80.00"]
    _age(db_session)
    new = [lines[0], {"description": "Sweep and mop", "quantity": 2, "rate": 40}]
    assert (
        client.put(f"/api/invoices/{inv['id']}", json={"lines": new}).status_code == 200
    )
    said = _line_details(_newest(client, "invoice", inv["id"]))
    assert said == ["Line Sweep and mop, $80.00: Description Sweep up → Sweep and mop"]
