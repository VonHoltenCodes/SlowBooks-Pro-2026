"""Journal Entries lists the journals a migration posted (2.23.2, NEW-118).

The page's list took manual entries and the QuickBooks Online import's
journals only, so the 13 journals the Export data route posted were
nowhere on it: a drill-down's link opened one over an empty list. Every
Migrate Data source's journals, and the IIF opening-balance entry, list
with the others now; the filtered list (?source_type=) is as it was.
"""

from datetime import date
from decimal import Decimal

from app.routes.migration import SOURCES
from app.services.accounting import create_journal_entry


def _journal(db, source_type, memo):
    from app.models.accounts import Account

    accounts = db.query(Account).order_by(Account.account_number).limit(2).all()
    a, b = accounts[0], accounts[1]
    return create_journal_entry(
        db,
        date(2026, 10, 1),
        memo,
        [
            {"account_id": a.id, "debit": Decimal("10"), "credit": Decimal("0")},
            {"account_id": b.id, "debit": Decimal("0"), "credit": Decimal("10")},
        ],
        source_type=source_type,
        reference=memo,
    )


def test_every_migration_sources_journals_list(client, db_session, seed_accounts):
    posted = {}
    for key, (_label, _module, source_type) in SOURCES.items():
        posted[source_type] = _journal(db_session, source_type, f"{key} journal").id
    posted["iif_import"] = _journal(db_session, "iif_import", "IIF opening").id
    posted["qb_ledger_import"] = _journal(
        db_session, "qb_ledger_import", "Desktop ledger"
    ).id
    posted["manual"] = _journal(db_session, "manual", "by hand").id
    # a document's own posting stays off the list, as before
    invoice_posting = _journal(db_session, "invoice", "an invoice's posting").id
    db_session.commit()

    listed = {e["id"]: e["source_type"] for e in client.get("/api/journal").json()}
    for source_type, txn_id in posted.items():
        assert listed.get(txn_id) == source_type, source_type
    assert invoice_posting not in listed
    assert "qbo_export_import" in posted  # the route this came from

    # the filtered list is as it was
    only = client.get("/api/journal", params={"source_type": "wave_import"}).json()
    assert [e["id"] for e in only] == [posted["wave_import"]]
