"""Editing an estimate settles each line's tax before it stores the line.

update_estimate stored the lines' tax flags as they were sent — a missing
flag as taxable — and applied the customer's exemption and the item's flag
only to the totals. An edit could therefore store taxable lines on a
non-taxable customer's estimate, and a non-taxable item's line stored as
taxable was taxed on the invoice the estimate became. Create settles the
flags first (resolve_line_taxable); an edit now does the same. Found while
integrating the 2.17.3 exploratory fixes.
"""

from decimal import Decimal

from app.models.contacts import Customer
from app.models.items import Item


def _estimate(client, customer_id, lines, tax_rate="0.0825"):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-09-01",
            "tax_rate": tax_rate,
            "lines": lines,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_an_exempt_customers_estimate_keeps_no_taxable_line_after_an_edit(
    client, db_session, seed_accounts
):
    church = Customer(name="Grace Chapel", is_active=True, is_taxable=False)
    db_session.add(church)
    db_session.commit()
    est = _estimate(client, church.id, [{"description": "Sign", "rate": 100}])
    assert [ln["is_taxable"] for ln in est["lines"]] == [False]

    # the form sends each line's Tax box; an API client may send True
    r = client.put(
        f"/api/estimates/{est['id']}",
        json={"lines": [{"description": "Sign", "rate": 120, "is_taxable": True}]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [ln["is_taxable"] for ln in body["lines"]] == [False]
    assert Decimal(body["tax_amount"]) == Decimal("0")
    assert Decimal(body["total"]) == Decimal("120.00")


def test_a_non_taxable_items_line_is_stored_untaxed_and_converts_untaxed(
    client, db_session, seed_accounts, seed_customer
):
    labor = Item(name="Install labor", item_type="service", rate=Decimal("75"))
    labor.is_taxable = False
    db_session.add(labor)
    db_session.commit()
    est = _estimate(client, seed_customer.id, [{"description": "Sign", "rate": 100}])

    # the edit adds the labor line without a flag: the item's flag decides
    r = client.put(
        f"/api/estimates/{est['id']}",
        json={
            "lines": [
                {"description": "Sign", "rate": 100},
                {"item_id": labor.id, "description": "Install", "rate": 75},
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [ln["is_taxable"] for ln in body["lines"]] == [True, False]
    assert Decimal(body["tax_amount"]) == Decimal("8.25")

    inv = client.post(f"/api/estimates/{est['id']}/convert")
    assert inv.status_code == 200, inv.text
    inv = inv.json()
    assert [ln["is_taxable"] for ln in inv["lines"]] == [True, False]
    assert Decimal(inv["tax_amount"]) == Decimal("8.25")
    assert Decimal(inv["total"]) == Decimal("183.25")


def test_an_edit_that_changes_the_customer_uses_the_new_ones_exemption(
    client, db_session, seed_accounts, seed_customer
):
    reseller = Customer(name="Resale Signs", is_active=True, is_taxable=False)
    db_session.add(reseller)
    db_session.commit()
    est = _estimate(client, seed_customer.id, [{"description": "Sign", "rate": 100}])
    assert [ln["is_taxable"] for ln in est["lines"]] == [True]

    r = client.put(
        f"/api/estimates/{est['id']}",
        json={
            "customer_id": reseller.id,
            "lines": [{"description": "Sign", "rate": 100, "is_taxable": True}],
        },
    )
    assert r.status_code == 200, r.text
    assert [ln["is_taxable"] for ln in r.json()["lines"]] == [False]
    assert Decimal(r.json()["tax_amount"]) == Decimal("0")
