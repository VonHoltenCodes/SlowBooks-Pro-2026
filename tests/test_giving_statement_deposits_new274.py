"""Round 25, NEW-274: a donor's Make Deposits gift is on the giving statement.

On a nonprofit, a Make Deposits line on an income account naming a donor
(75.00) and a 50.00 sales receipt: Income by Customer showed the donor at
125.00, the giving statement $50.00, because `gifts_from_bank_entries` read
register and bank-feed entries only. The statement now reads the same
donor-named income entries Income by Customer counts (NEW-272), each a gift
only by its own rule: an income credit naming the donor, more than zero,
not void. Both reports are read on the same data here.
"""

from app.services.donor_documents import collect_gifts, load_gift
from tests.test_donor_documents import _nonprofit, _receipt
from tests.test_income_by_customer_deposits_new272 import _ids, _make_deposit

YEAR = {"start_date": "2026-01-01", "end_date": "2026-12-31", "basis": "cash"}


def _income_row(client, name):
    r = client.get("/api/reports/income-by-customer", params=YEAR)
    assert r.status_code == 200, r.text
    rows = {i["customer_name"]: i for i in r.json()["items"]}
    return rows[name]["total_sales"]


def test_a_deposit_gift_is_on_the_giving_statement(
    client, db_session, seed_accounts, seed_customer
):
    _nonprofit(client)
    bank, income = _ids(db_session)
    donor = seed_customer.id
    deposit = _make_deposit(
        client,
        bank,
        [
            {"account_id": income, "customer_id": donor, "amount": "75.00"},
            # money from nobody is no one's gift
            {"account_id": income, "amount": "20.00"},
        ],
    )
    r = _receipt(
        client,
        donor,
        date="2026-12-11",
        lines=[{"description": "Gift", "quantity": 1, "rate": "50.00"}],
    )
    assert r.status_code == 201, r.text

    gifts = collect_gifts(db_session, donor, 2026)
    assert float(gifts["totals"]["amount"]) == 125.00
    assert sorted(float(g["amount"]) for g in gifts["cash"]) == [50.00, 75.00]
    deposit_gift = next(g for g in gifts["cash"] if g["kind"] == "bank-entry")
    # and an acknowledgment reads the same gift by its line
    assert load_gift(db_session, "bank-entry", deposit_gift["id"])["amount"] == (
        deposit_gift["amount"]
    )
    # the two reports agree for the donor
    assert _income_row(client, seed_customer.full_name) == 125.00

    # a void deposit drops out of both
    assert client.post(f"/api/deposits/{deposit}/void").status_code == 200
    gifts = collect_gifts(db_session, donor, 2026)
    assert float(gifts["totals"]["amount"]) == 50.00
    assert _income_row(client, seed_customer.full_name) == 50.00
