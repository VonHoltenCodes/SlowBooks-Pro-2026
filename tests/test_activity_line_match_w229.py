"""Round 27: Activity matches a document's lines by what they are.

W-229: on a kind whose form keeps no line ids (the lines are rewritten on
every save), the leftover lines were still paired by place, so a kept line
that moved up a place read as removed and added. Demo, Haul away $60,
Patch $50 saved as Demo, Patch $55, Sweep up $40 read four entries. The
leftover lines are now paired by the same description first, then by the
same amount and quantity; only what is left unmatched was removed or
added.

W-230: a purchase order's form sends each kept line's id, but when no old
id was left the lines were paired by place, and Pulls $45 → Knobs $45 read
as an edit of the description. On a kind that sends ids, a line whose id is
gone was removed and a line with none was added, always.
"""

from tests.test_activity_line_changes_w215 import _line_details, _newest
from tests.test_document_activity_894 import _age, _customer


def _line(description, rate, quantity=1):
    return {"description": description, "quantity": quantity, "rate": rate}


def _invoice(client, lines):
    cust = _customer(client)
    r = client.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "date": "2026-06-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _save(client, db_session, inv, lines):
    _age(db_session)
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    return _line_details(_newest(client, "invoice", inv["id"]))


def test_a_kept_line_that_moves_up_a_place_is_that_line_edited(
    client, seed_accounts, db_session
):
    inv = _invoice(
        client, [_line("Demo", 95), _line("Haul away", 60), _line("Patch", 50)]
    )
    said = _save(
        client,
        db_session,
        inv,
        [_line("Demo", 95), _line("Patch", 55), _line("Sweep up", 40)],
    )
    assert said == [
        "Line Patch, $55.00: Rate $50.00 → $55.00; Amount $50.00 → $55.00",
        "Line added: Sweep up, $40.00",
        "Line removed: Haul away, $60.00",
    ], said


def test_a_line_that_keeps_its_amount_is_an_edit_of_its_description(
    client, seed_accounts, db_session
):
    inv = _invoice(client, [_line("Demo", 95), _line("Haul away", 60)])
    # Haul away renamed and moved to the top; Demo kept
    said = _save(client, db_session, inv, [_line("Haul off", 60), _line("Demo", 95)])
    assert said == ["Line Haul off, $60.00: Description Haul away → Haul off"], said


def test_a_reorder_alone_says_nothing_about_the_lines(
    client, seed_accounts, db_session
):
    lines = [_line("Demo", 95), _line("Haul away", 60), _line("Patch", 50)]
    inv = _invoice(client, lines)
    _save(client, db_session, inv, list(reversed(lines)))
    newest = _newest(client, "invoice", inv["id"])
    # no entry for the save (the newest is still the invoice's creation),
    # or an entry that names no line
    if newest["kind"] != "created":
        assert _line_details(newest) == [], newest


def test_one_of_two_identical_lines_removed_reads_once(
    client, seed_accounts, db_session
):
    inv = _invoice(client, [_line("Patch", 50), _line("Patch", 50), _line("Demo", 95)])
    said = _save(client, db_session, inv, [_line("Demo", 95), _line("Patch", 50)])
    assert said == ["Line removed: Patch, $50.00"], said


def test_a_journal_entry_line_that_moves_up_is_that_line_edited(
    client, seed_accounts, db_session
):
    exp, liab = seed_accounts["6900"].id, seed_accounts["2500"].id

    def jl(description, account, debit=0, credit=0):
        return {
            "account_id": account,
            "description": description,
            "debit": debit,
            "credit": credit,
        }

    body = {
        "date": "2026-09-02",
        "description": "Accrue",
        "lines": [
            jl("Fuel", exp, debit=100),
            jl("Haul away", exp, debit=60),
            jl("Paid", liab, credit=160),
        ],
    }
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201, r.text
    je = r.json()
    _age(db_session)
    body["lines"] = [
        jl("Fuel", exp, debit=100),
        jl("Paid", liab, credit=140),
        jl("Sweep up", exp, debit=40),
    ]
    r = client.put(f"/api/journal/{je['id']}", json=body)
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, "journal_entry", je["id"]))
    assert len(said) == 3, said
    assert said[0].startswith("Line Paid") and "Credit $160.00 → $140.00" in said[0]
    assert said[1].startswith("Line added: Sweep up"), said
    assert said[2].startswith("Line removed: Haul away"), said


def _po(client, lines):
    vendor = client.post("/api/vendors", json={"name": "Hardware Supply"}).json()
    r = client.post(
        "/api/purchase-orders",
        json={"vendor_id": vendor["id"], "date": "2026-04-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_a_purchase_order_whose_every_line_is_replaced_reads_removed_and_added(
    client, seed_accounts, db_session
):
    po = _po(client, [_line("Pulls", "45.00")])
    _age(db_session)
    r = client.put(
        f"/api/purchase-orders/{po['id']}", json={"lines": [_line("Knobs", "45.00")]}
    )
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, "purchase_order", po["id"]))
    assert said == ["Line added: Knobs, $45.00", "Line removed: Pulls, $45.00"], said

    # the same description with no id is a new line too
    _age(db_session)
    r = client.put(
        f"/api/purchase-orders/{po['id']}", json={"lines": [_line("Knobs", "50.00")]}
    )
    assert r.status_code == 200, r.text
    said = _line_details(_newest(client, "purchase_order", po["id"]))
    assert said == ["Line added: Knobs, $50.00", "Line removed: Knobs, $45.00"], said
