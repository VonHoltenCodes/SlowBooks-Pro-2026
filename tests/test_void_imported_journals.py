"""An imported journal entry voids from the Journal Entries page like a
hand-typed one. The lists IIF's opening balances come in as one entry
(reference IIF-OPENING); the page offered Void on hand-typed and
QuickBooks Online entries only, so a wrong opening-balance import had no
way out but the API (a CPA on X, after 2.23.5). The server now says which
entries may be voided (voidable), and the page reads that."""

from decimal import Decimal

from app.models.transactions import Transaction

LISTS_IIF = (
    "!ACCNT\tNAME\tACCNTTYPE\tOBAMOUNT\r\n"
    "ACCNT\tChecking\tBANK\t2500.00\r\n"
    "ACCNT\tOpening Bal Equity\tEQUITY\t-2500.00\r\n"
)


def _opening(db):
    return db.query(Transaction).filter(Transaction.reference == "IIF-OPENING").one()


def _tb(client):
    return client.get(
        "/api/reports/trial-balance?start_date=2000-01-01&end_date=2099-12-31"
    ).json()


def test_the_lists_imports_opening_balances_can_be_voided_from_the_page(
    client, db_session, seed_accounts
):
    from app.services.iif_import import import_all

    r = import_all(db_session, LISTS_IIF)
    db_session.commit()
    assert not r["errors"], r["errors"]
    opening = _opening(db_session)
    assert opening.source_type == "iif_import"
    assert _tb(client)["total_debit"] == 2500.0

    listed = {
        e["id"]: e
        for e in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
    }
    assert opening.id in listed
    assert (
        listed[opening.id]["voidable"] is True and listed[opening.id]["voided"] is False
    )
    one = client.get(f"/api/journal/{opening.id}").json()
    assert one["voidable"] is True

    r = client.post(f"/api/journal/{opening.id}/void")
    assert r.status_code == 200, r.text
    reversal = r.json()
    assert reversal["source_type"] == "manual_void"
    assert reversal["voidable"] is False  # a reversal is never voided
    after = client.get(f"/api/journal/{opening.id}").json()
    assert after["voided"] is True and after["voidable"] is False
    # the entry and its reversal cancel in each balance-sheet account (#597:
    # the Trial Balance reads balances as of the end date, not activity)
    assert _tb(client)["total_debit"] == 0.0
    assert {i["net_balance"] for i in _tb(client)["items"]} == {0.0}


def test_hand_typed_and_opening_balances_page_entries_say_so_too(client, seed_accounts):
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-02-01",
            "description": "Accrual",
            "lines": [
                {
                    "account_id": seed_accounts["6300"].id,
                    "debit": "10.00",
                    "credit": "0",
                },
                {
                    "account_id": seed_accounts["2500"].id,
                    "debit": "0",
                    "credit": "10.00",
                },
            ],
        },
    )
    assert r.status_code == 201 and r.json()["voidable"] is True
    r = client.post(
        "/api/opening-balances",
        json={
            "date": "2026-01-01",
            "lines": [{"account_id": seed_accounts["1000"].id, "amount": "100.00"}],
            "auto_balance_account_id": next(
                a.id
                for n, a in seed_accounts.items()
                if a.account_type.value == "equity"
            ),
        },
    )
    assert r.status_code in (200, 201), r.text
    entries = client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
        "entries"
    ]
    ob = next(e for e in entries if e["source_type"] == "opening_balance")
    assert ob["voidable"] is True
    assert Decimal(str(ob["total_debit"])) == Decimal("100.00")


def test_the_page_reads_the_servers_word():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/journal.js").read_text(
        encoding="utf-8"
    )
    assert "return !!e.voidable && !e.voided;" in js
    assert "['manual', 'qbo_journal', 'qbo_ledger'].includes" not in js
