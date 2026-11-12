"""Budgets name a sub-account by its path (#813).

A QuickBooks import names a sub-account by its leaf since 2.31.6
("Taxes", with Payroll Expenses as its parent), and the Budgets grid,
Budget vs Actual and their print pages listed "Taxes" alone where the
reports read "Payroll Expenses: Taxes"."""

import html


def _sub(client, parent_id):
    r = client.post(
        "/api/accounts",
        json={
            "name": "Taxes",
            "account_number": "6010",
            "account_type": "expense",
            "parent_id": parent_id,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_the_variance_and_both_print_pages_name_the_path(client, seed_accounts):
    parent = seed_accounts["6000"]
    sub = _sub(client, parent.id)
    r = client.post(
        "/api/budgets/bulk",
        json=[{"account_id": sub["id"], "year": 2026, "month": 1, "amount": 50}],
    )
    assert r.status_code == 200, r.text

    data = client.get("/api/budgets/variance?year=2026").json()
    (row,) = data["accounts"]
    assert row["account_name"] == "Taxes"
    assert row["account_path"] == f"{parent.name}: Taxes"

    page = html.unescape(client.get("/api/budgets/variance/print?year=2026").text)
    assert f"6010 — {parent.name}: Taxes" in page

    page = html.unescape(client.get("/api/budgets/print?year=2026").text)
    assert f"6010 {parent.name}: Taxes" in page
    # a top-level account is its own name
    assert f"{parent.account_number} {parent.name}<" in page
