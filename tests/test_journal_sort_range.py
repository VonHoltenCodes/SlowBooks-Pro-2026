"""The Journal Entries list's Sort and From / To range (#687).

`GET /api/journal` takes `sort=date|entered` (entry date, newest first, as
before; or the time each entry was entered, newest first) and a
`start_date` / `end_date` range, both ends inclusive, either one alone, and
applied with the list's other filters.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from app.services.accounting import create_journal_entry


def _journal(db, on, memo, entered, adjusting=False):
    from app.models.accounts import Account

    a, b = db.query(Account).order_by(Account.account_number).limit(2).all()
    txn = create_journal_entry(
        db,
        on,
        memo,
        [
            {"account_id": a.id, "debit": Decimal("10"), "credit": Decimal("0")},
            {"account_id": b.id, "debit": Decimal("0"), "credit": Decimal("10")},
        ],
        source_type="manual",
        reference=memo,
    )
    txn.created_at = entered
    txn.is_adjusting = adjusting
    db.flush()
    return txn.id


def _entries(db):
    """Three entries dated in one order and entered in another."""
    at = lambda d, h: datetime(2026, 11, d, h, tzinfo=timezone.utc)  # noqa: E731
    ids = {
        "sept": _journal(db, date(2026, 9, 15), "September", at(2, 9)),
        "oct1": _journal(db, date(2026, 10, 1), "October 1st", at(3, 9), True),
        "oct31": _journal(db, date(2026, 10, 31), "October 31st", at(1, 9)),
    }
    db.commit()
    return ids


def _listed(client, **params):
    r = client.get("/api/journal", params=params)
    assert r.status_code == 200, r.text
    return [e["id"] for e in r.json()]


def test_by_entry_date_newest_first_as_before(client, db_session, seed_accounts):
    ids = _entries(db_session)
    expect = [ids["oct31"], ids["oct1"], ids["sept"]]
    assert _listed(client) == expect
    assert _listed(client, sort="date") == expect


def test_by_date_entered_newest_entered_first(client, db_session, seed_accounts):
    ids = _entries(db_session)
    assert _listed(client, sort="entered") == [ids["oct1"], ids["sept"], ids["oct31"]]


def test_entered_in_the_same_second_newest_id_first(client, db_session, seed_accounts):
    same = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
    first = _journal(db_session, date(2026, 10, 5), "first", same)
    second = _journal(db_session, date(2026, 10, 1), "second", same)
    db_session.commit()
    assert _listed(client, sort="entered") == [second, first]


def test_an_unknown_sort_is_refused(client, db_session, seed_accounts):
    assert client.get("/api/journal", params={"sort": "amount"}).status_code == 422


def test_the_range_is_inclusive_at_both_ends(client, db_session, seed_accounts):
    ids = _entries(db_session)
    assert _listed(client, start_date="2026-10-01", end_date="2026-10-31") == [
        ids["oct31"],
        ids["oct1"],
    ]
    assert _listed(client, start_date="2026-09-15", end_date="2026-09-15") == [
        ids["sept"]
    ]


def test_one_bound_alone(client, db_session, seed_accounts):
    ids = _entries(db_session)
    assert _listed(client, start_date="2026-10-01") == [ids["oct31"], ids["oct1"]]
    assert _listed(client, end_date="2026-10-01") == [ids["oct1"], ids["sept"]]


def test_an_empty_range(client, db_session, seed_accounts):
    _entries(db_session)
    assert _listed(client, start_date="2026-08-01", end_date="2026-08-31") == []
    # From after To: nothing, not an error
    assert _listed(client, start_date="2026-10-31", end_date="2026-10-01") == []


def test_the_range_and_sort_apply_with_the_other_filters(
    client, db_session, seed_accounts
):
    ids = _entries(db_session)
    assert _listed(
        client, adjusting="true", start_date="2026-10-01", sort="entered"
    ) == [ids["oct1"]]
    assert _listed(
        client, adjusting="false", start_date="2026-09-01", sort="entered"
    ) == [ids["sept"], ids["oct31"]]
    assert _listed(client, source_type="manual", end_date="2026-09-30") == [ids["sept"]]
