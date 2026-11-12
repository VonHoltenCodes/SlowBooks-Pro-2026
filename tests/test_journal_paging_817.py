"""The Journal Entries list pages (#817).

`GET /api/journal` answers a page of entries (100 unless `limit` says
otherwise, at most 500, from `skip`) with `total`, how many the filter holds
in all, and the date range it read: the current fiscal year when the request
names neither `start_date` nor `end_date`. A large book opens the page in a
moment instead of loading every entry and its lines. The sort and filters
hold on every page, an entry is still opened by its own address, and the
journal's export (IIF; the list has no CSV) still carries every entry.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.services.accounting import create_journal_entry


def _fy(db):
    from app.services.fiscal_year import fiscal_year_bounds

    return fiscal_year_bounds(db, date.today())


def _book(db, count=250):
    """`count` entries inside the current fiscal year, a few to a day, every
    third one adjusting, entered in the reverse order of their ids."""
    from app.models.accounts import Account

    a, b = db.query(Account).order_by(Account.account_number).limit(2).all()
    start, _ = _fy(db)
    ids = []
    for i in range(count):
        txn = create_journal_entry(
            db,
            start + timedelta(days=i // 3),
            f"Entry {i}",
            [
                {"account_id": a.id, "debit": Decimal("5"), "credit": Decimal("0")},
                {"account_id": b.id, "debit": Decimal("0"), "credit": Decimal("5")},
            ],
            source_type="manual",
            reference=f"E{i}",
        )
        txn.created_at = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(
            minutes=count - i
        )
        txn.is_adjusting = i % 3 == 0
        ids.append(txn.id)
    db.commit()
    return ids


def _page(client, **params):
    r = client.get("/api/journal", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _every(client, **params):
    """The whole filter, a page at a time, as the page's Next walks it."""
    out, skip = [], 0
    while True:
        page = _page(client, skip=skip, **params)
        out += [e["id"] for e in page["entries"]]
        skip += page["limit"]
        if skip >= page["total"]:
            return out, page["total"]


def test_two_hundred_fifty_entries_page_one_hundred_one_hundred_and_fifty(
    client, db_session, seed_accounts
):
    ids = _book(db_session)
    pages = [_page(client, skip=skip) for skip in (0, 100, 200)]
    assert [len(p["entries"]) for p in pages] == [100, 100, 50]
    assert {p["total"] for p in pages} == {250}
    assert [p["skip"] for p in pages] == [0, 100, 200]
    assert {p["limit"] for p in pages} == {100}
    listed = [e["id"] for p in pages for e in p["entries"]]
    # every entry once, newest date first, the newest id first within a day
    assert sorted(listed) == sorted(ids)
    assert listed == sorted(
        ids, key=lambda i: (-(ids.index(i) // 3), -i)
    ), "newest first, a day's entries by id"
    # each entry comes with its lines, as the list always gave
    assert len(pages[0]["entries"][0]["lines"]) == 2


def test_a_page_past_the_end_is_empty_and_still_counts(
    client, db_session, seed_accounts
):
    _book(db_session, 30)
    page = _page(client, skip=100)
    assert page["entries"] == [] and page["total"] == 30


def test_the_limit_is_at_most_five_hundred(client, db_session, seed_accounts):
    _book(db_session, 30)
    assert len(_page(client, limit=10)["entries"]) == 10
    assert len(_page(client, limit=500)["entries"]) == 30
    assert client.get("/api/journal", params={"limit": 501}).status_code == 422
    assert client.get("/api/journal", params={"limit": 0}).status_code == 422
    assert client.get("/api/journal", params={"skip": -1}).status_code == 422


def test_with_no_range_the_list_is_the_current_fiscal_year(
    client, db_session, seed_accounts
):
    ids = _book(db_session, 5)
    start, end = _fy(db_session)
    from app.models.accounts import Account

    a, b = db_session.query(Account).order_by(Account.account_number).limit(2).all()
    lines = [
        {"account_id": a.id, "debit": Decimal("1"), "credit": Decimal("0")},
        {"account_id": b.id, "debit": Decimal("0"), "credit": Decimal("1")},
    ]
    before = create_journal_entry(
        db_session, start - timedelta(days=1), "Last year", lines, source_type="manual"
    ).id
    after = create_journal_entry(
        db_session, end + timedelta(days=1), "Next year", lines, source_type="manual"
    ).id
    db_session.commit()

    page = _page(client)
    assert page["default_range"] is True
    assert (page["start_date"], page["end_date"]) == (
        start.isoformat(),
        end.isoformat(),
    )
    assert sorted(e["id"] for e in page["entries"]) == sorted(ids)
    assert page["total"] == 5

    # the user widens it: either end named reads that range instead
    wide = _page(client, start_date=(start - timedelta(days=400)).isoformat())
    assert wide["default_range"] is False and wide["end_date"] is None
    assert before in [e["id"] for e in wide["entries"]]
    assert after in [e["id"] for e in wide["entries"]]
    assert wide["total"] == 7
    assert [e["id"] for e in _page(client, end_date=start.isoformat())["entries"]][
        -1
    ] == before


def test_the_default_range_follows_the_fiscal_year_setting(
    client, db_session, seed_accounts
):
    from app.services.settings_service import set_setting

    set_setting(db_session, "fiscal_year_start_month", "7")
    db_session.commit()
    today = date.today()
    year = today.year if today.month >= 7 else today.year - 1
    page = _page(client)
    assert page["start_date"] == date(year, 7, 1).isoformat()
    assert page["end_date"] == date(year + 1, 6, 30).isoformat()


def test_the_sort_and_the_filters_hold_on_every_page(client, db_session, seed_accounts):
    ids = _book(db_session)
    # by date entered: the book was entered in the reverse order of its ids
    walked, total = _every(client, sort="entered")
    assert total == 250 and walked == sorted(ids)
    # adjusting only, by date entered, in pages of 30
    adjusting = [i for n, i in enumerate(ids) if n % 3 == 0]
    walked, total = _every(client, sort="entered", adjusting="true", limit=30)
    assert total == len(adjusting) == 84
    assert walked == sorted(adjusting)
    # a range, the rest of the filters and the sort together
    start, _ = _fy(db_session)
    day_ten = (start + timedelta(days=10)).isoformat()
    walked, total = _every(
        client, adjusting="false", end_date=day_ten, source_type="manual", limit=7
    )
    expect = [i for n, i in enumerate(ids) if n % 3 and n // 3 <= 10]
    assert total == len(expect) and sorted(walked) == sorted(expect)
    assert walked == sorted(expect, key=lambda i: (-(ids.index(i) // 3), -i))


def test_an_entry_on_page_three_opens_by_its_own_address(
    client, db_session, seed_accounts
):
    ids = _book(db_session)
    target = ids[10]  # dated early in the year: the third page, newest first
    first = _page(client)
    assert target not in [e["id"] for e in first["entries"]]
    assert target in [e["id"] for e in _page(client, skip=200)["entries"]]
    # #/journal/<id> (a bookmark, a register or report drill-down) opens the
    # entry from its own GET, whichever page of the list holds it
    r = client.get(f"/api/journal/{target}")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == target and r.json()["reference"] == "E10"


def test_the_journal_export_still_carries_every_entry(
    client, db_session, seed_accounts
):
    _book(db_session)
    start, end = _fy(db_session)
    r = client.get(
        "/api/iif/export/journal-entries",
        params={"date_from": start.isoformat(), "date_to": end.isoformat()},
    )
    assert r.status_code == 200, r.text
    rows = [ln for ln in r.text.splitlines() if ln.startswith("TRNS\t")]
    assert len(rows) == 250
