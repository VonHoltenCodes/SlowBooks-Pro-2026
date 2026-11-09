"""Markup percents stay inside their Numeric(7, 4) columns, and credit memo
and vendor credit lines stay inside their money columns — each a 422 with a
sentence, never a 500 or a figure Postgres cannot store."""

import pytest

from tests.test_bill_edit import _vendor
from tests.test_construction_tax import _construction

MAX = 9999999999999.99
PAST = 10000000000000.00
PCTS = ("gc_pct", "insurance_pct", "fee_pct", "bond_pct")
LABELS = dict(
    gc_pct="General conditions",
    insurance_pct="Insurance",
    fee_pct="Fee",
    bond_pct="Bond",
)
COST_LINE = {"description": "x", "quantity": 1, "rate": 1000, "unit_cost": 1000}


def _message(r) -> str:
    detail = r.json().get("detail")
    if isinstance(detail, list):
        return " ".join(e.get("message", "") for e in detail)
    return detail if isinstance(detail, str) else str(detail)


def _estimate(client, customer_id, **pcts):
    return client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-03-01",
            "lines": [COST_LINE],
            **pcts,
        },
    )


# --- estimate markups -------------------------------------------------------
@pytest.mark.parametrize("field", PCTS)
@pytest.mark.parametrize("value", ["1e30", 5000, -50, "1000"])
def test_an_estimate_markup_outside_zero_to_999_9999_is_refused_naming_the_markup(
    client, seed_accounts, seed_customer, field, value
):
    _construction(client)
    r = _estimate(client, seed_customer.id, **{field: value})
    assert r.status_code == 422, r.text
    assert f"{LABELS[field]} must be between 0 and 999.9999%." in _message(r)
    assert any(field in e["loc"] for e in r.json()["detail"])


@pytest.mark.parametrize("value", [0, "999.9999"])
def test_an_estimate_markup_of_0_or_999_9999_is_saved(
    client, seed_accounts, seed_customer, value
):
    _construction(client)
    r = _estimate(client, seed_customer.id, **{f: value for f in PCTS})
    assert r.status_code == 201, r.text
    assert float(r.json()["gc_pct"]) == float(value)


@pytest.mark.parametrize("value", ["1e30", 5000, -50])
def test_an_estimate_edit_holds_its_markups_to_the_same_range(
    client, seed_accounts, seed_customer, value
):
    _construction(client)
    est = _estimate(client, seed_customer.id, gc_pct=10).json()
    r = client.put(f"/api/estimates/{est['id']}", json={"bond_pct": value})
    assert r.status_code == 422, r.text
    assert "Bond must be between 0 and 999.9999%." in _message(r)
    assert (
        client.put(
            f"/api/estimates/{est['id']}", json={"bond_pct": "999.9999"}
        ).status_code
        == 200
    )


# --- change-order markups: the column holds at most 999.9999 -----------------
def test_a_change_order_markup_of_1000_is_refused_as_it_does_not_fit_its_column(
    client, seed_customer
):
    _construction(client)
    job = client.post(
        "/api/jobs", json={"customer_id": seed_customer.id, "name": "Clinic"}
    ).json()
    r = client.put(f"/api/jobs/{job['id']}/contract", json={"co_markup_pct": 1000})
    assert r.status_code == 422, r.text
    assert "at most 999.9999%" in _message(r)
    r = client.put(
        f"/api/jobs/{job['id']}/contract", json={"co_markup_pct": "999.9999"}
    )
    assert r.status_code == 200, r.text


# --- credit memos -----------------------------------------------------------
def _memo(client, customer_id, lines, **extra):
    return client.post(
        "/api/credit-memos",
        json={
            "customer_id": customer_id,
            "date": "2026-03-01",
            "lines": lines,
            **extra,
        },
    )


def test_a_credit_memo_line_of_1e15_by_1e15_is_refused_with_a_sentence(
    client, seed_accounts, seed_customer
):
    r = _memo(
        client, seed_customer.id, [{"description": "x", "quantity": 1e15, "rate": 1e15}]
    )
    assert r.status_code == 422, r.text
    assert "more than a line can be (9,999,999,999,999.99)" in _message(r)


def test_a_credit_memo_at_the_columns_edge_posts_and_one_cent_past_is_refused(
    client, seed_accounts, seed_customer
):
    r = _memo(
        client, seed_customer.id, [{"description": "x", "quantity": 1, "rate": 25}]
    )
    assert r.status_code == 201, r.text
    r = _memo(
        client, seed_customer.id, [{"description": "x", "quantity": 1, "rate": MAX}]
    )
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "9999999999999.99"
    r = _memo(
        client, seed_customer.id, [{"description": "x", "quantity": 1, "rate": PAST}]
    )
    assert r.status_code == 422, r.text
    halves = [{"description": "x", "quantity": 1, "rate": MAX / 2 + 0.01}] * 2
    r = _memo(client, seed_customer.id, halves)
    assert r.status_code == 422, r.text
    assert "more than a document can be" in _message(r)


def test_a_credit_memo_whose_tax_takes_it_past_the_column_is_refused(
    client, seed_accounts, seed_customer
):
    r = _memo(
        client,
        seed_customer.id,
        [{"description": "x", "quantity": 1, "rate": MAX, "is_taxable": True}],
        tax_rate=0.08,
    )
    assert r.status_code == 422, r.text
    assert "more than a document can be (9,999,999,999,999.99)" in _message(r)


def test_replacing_a_credit_memo_holds_the_new_lines_to_the_column(
    client, seed_accounts, seed_customer
):
    cm = _memo(
        client, seed_customer.id, [{"description": "x", "quantity": 1, "rate": 25}]
    ).json()
    r = client.post(
        f"/api/credit-memos/{cm['id']}/replace",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "lines": [{"description": "x", "quantity": 1e15, "rate": 1e15}],
        },
    )
    assert r.status_code == 422, r.text
    assert "more than a line can be" in _message(r)


# --- vendor credits ---------------------------------------------------------
def _credit(client, vendor_id, account_id, lines, **extra):
    lines = [{"account_id": account_id, **ln} for ln in lines]
    return client.post(
        "/api/vendor-credits",
        json={"vendor_id": vendor_id, "date": "2026-03-01", "lines": lines, **extra},
    )


def test_a_vendor_credit_line_of_1e15_by_1e15_is_refused_with_a_sentence(
    client, seed_accounts
):
    v = _vendor(client)["id"]
    acct = seed_accounts["6000"].id
    r = _credit(client, v, acct, [{"description": "x", "quantity": 1e15, "rate": 1e15}])
    assert r.status_code == 422, r.text
    assert "more than a line can be (9,999,999,999,999.99)" in _message(r)


def test_a_vendor_credit_at_the_columns_edge_posts_and_one_cent_past_is_refused(
    client, seed_accounts
):
    v = _vendor(client)["id"]
    acct = seed_accounts["6000"].id
    r = _credit(client, v, acct, [{"description": "x", "quantity": 2, "rate": 12.5}])
    assert r.status_code == 201, r.text
    r = _credit(client, v, acct, [{"description": "x", "quantity": 1, "rate": MAX}])
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "9999999999999.99"
    r = _credit(client, v, acct, [{"description": "x", "quantity": 1, "rate": PAST}])
    assert r.status_code == 422, r.text
    halves = [{"description": "x", "quantity": 1, "rate": MAX / 2 + 0.01}] * 2
    r = _credit(client, v, acct, halves)
    assert r.status_code == 422, r.text
    assert "more than a document can be" in _message(r)


def test_a_vendor_credit_whose_tax_takes_it_past_the_column_is_refused(
    client, seed_accounts
):
    v = _vendor(client)["id"]
    acct = seed_accounts["6000"].id
    r = _credit(
        client,
        v,
        acct,
        [{"description": "x", "quantity": 1, "rate": MAX}],
        tax_rate=0.08,
    )
    assert r.status_code == 422, r.text
    assert "more than a document can be (9,999,999,999,999.99)" in _message(r)


def test_replacing_a_vendor_credit_holds_the_new_lines_to_the_column(
    client, seed_accounts
):
    v = _vendor(client)["id"]
    acct = seed_accounts["6000"].id
    vc = _credit(
        client, v, acct, [{"description": "x", "quantity": 1, "rate": 25}]
    ).json()
    r = client.post(
        f"/api/vendor-credits/{vc['id']}/replace",
        json={
            "vendor_id": v,
            "date": "2026-03-01",
            "lines": [
                {"account_id": acct, "description": "x", "quantity": 1e15, "rate": 1e15}
            ],
        },
    )
    assert r.status_code == 422, r.text
    assert "more than a line can be" in _message(r)
