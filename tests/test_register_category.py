"""The register's Category (#729): the account a posting's other line went
to, "3 accounts" with each account and amount for a split, and the account
a QuickBooks Desktop ledger import posted against. One query for the page,
not one per row."""

from sqlalchemy import event

from tests.test_qb_ledger_account_column import report


def _journal(client, seed_accounts, date, lines, description="synthetic entry"):
    r = client.post(
        "/api/journal",
        json={
            "date": date,
            "description": description,
            "lines": [
                {
                    "account_id": seed_accounts[number].id,
                    "debit": debit,
                    "credit": credit,
                }
                for number, debit, credit in lines
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _register(client, seed_accounts, number="1000"):
    r = client.get(f"/api/banking/check-register?account_id={seed_accounts[number].id}")
    assert r.status_code == 200, r.text
    return r.json()["entries"]


def test_a_one_line_posting_shows_its_account(client, seed_accounts):
    _journal(
        client,
        seed_accounts,
        "2026-09-03",
        [("1000", "500", "0"), ("3000", "0", "500")],
    )
    (row,) = _register(client, seed_accounts)
    assert row["category"] == "Owner's Equity"
    assert row["category_account_id"] == seed_accounts["3000"].id
    assert row["category_lines"] == [
        {
            "account_id": seed_accounts["3000"].id,
            "account_number": "3000",
            "account_name": "Owner's Equity",
            "amount": 500.0,
        }
    ]
    # the fields that were there stay as they were
    assert row["source_type"] == "manual" and row["deposit"] == 500.0


def test_a_three_account_posting_shows_the_count_and_each(client, seed_accounts):
    _journal(
        client,
        seed_accounts,
        "2026-09-04",
        [
            ("6400", "40", "0"),
            ("6500", "50", "0"),
            ("6900", "10", "0"),
            ("6900", "5", "0"),
            ("1000", "0", "105"),
        ],
    )
    (row,) = _register(client, seed_accounts)
    assert row["category"] == "3 accounts"
    assert row["category_account_id"] is None
    assert [(x["account_name"], x["amount"]) for x in row["category_lines"]] == [
        ("Office Supplies", 40.0),
        ("Rent or Lease", 50.0),
        # one account's two lines are one amount
        ("Utilities", 15.0),
    ]


def test_an_imported_ledger_entry_shows_the_account_it_was_posted_against(
    client, db_session, seed_accounts
):
    data = report(seed_accounts["1000"].name, seed_accounts["4000"].name, False)
    result = client.post(
        "/api/csv/import/qb-report",
        files={"file": ("ledger.csv", data.encode(), "text/csv")},
    ).json()
    assert result["journals"] == 1, result
    (row,) = _register(client, seed_accounts)
    assert row["source_type"] == "qb_ledger_import"
    assert row["category"] == "Service Income"
    assert row["category_account_id"] == seed_accounts["4000"].id


def test_the_categories_are_one_query_for_the_page(client, db_session, seed_accounts):
    for day in range(1, 9):
        _journal(
            client,
            seed_accounts,
            f"2026-09-{day:02d}",
            [("1000", "0", "10"), ("6400", "6", "0"), ("6500", "4", "0")],
        )
    engine = db_session.get_bind()
    seen = []

    def _count(conn, cursor, statement, *args):
        if "transaction_lines" in statement and "accounts" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        rows = _register(client, seed_accounts)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(rows) == 8 and {r["category"] for r in rows} == {"2 accounts"}
    lookups = [s for s in seen if "account_number" in s and "debit" in s]
    category_lookups = [s for s in lookups if "!=" in s or "<>" in s]
    assert len(category_lookups) == 1, category_lookups


def test_the_empty_and_populated_registers_have_the_same_columns():
    from pathlib import Path

    js = (Path(__file__).parent.parent / "app/static/js/banking.js").read_text(
        encoding="utf-8"
    )
    start = js.index("async renderRegister(")
    body = js[start : js.index("_clearedCell(e, accountId) {", start)]
    # one header, drawn for both
    assert body.count("<thead>") == 1
    assert body.count("${head}") == 2
    head = body[body.index("const head = `") : body.index("</thead>`;")]
    assert '<th scope="col">Type</th><th scope="col">Category</th>' in head
    assert head.count("<th ") == 11
