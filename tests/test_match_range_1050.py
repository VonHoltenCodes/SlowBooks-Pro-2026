"""Match looks further back than 30 days when asked (#1050).

Reported by a bookkeeper testing 2.31.14: Match offered entries from 30
days either side of the line only, so a deposit recorded earlier (an
invoice paid late, a check that cleared the next month) could not be
found. The candidates now take a range of 30 days (the default), 90 days,
a year or all dates, and the dialog keeps the choice for the sitting."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.ofx_import import import_transactions

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "app/static/js/banking.js").read_text(encoding="utf-8")


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


@pytest.fixture
def line(client, db_session, seed_accounts):
    checking, income = seed_accounts["1000"].id, seed_accounts["4000"].id
    feed = _ok(
        client.post(
            "/api/banking/accounts",
            json={"name": "Checking feed", "account_id": checking},
        )
    )
    # the same 250.00 deposit entered 45, 200 and 500 days before the line
    for when in ("2026-07-31", "2026-02-27", "2025-05-03"):
        _ok(
            client.post(
                "/api/banking/transactions",
                json={
                    "account_id": checking,
                    "date": when,
                    "amount": "250.00",
                    "category_account_id": income,
                    "payee": f"Deposit {when}",
                },
            )
        )
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "LATE1",
                "date": date(2026, 9, 14),
                "amount": Decimal("250.00"),
                "payee": "DEPOSIT",
                "memo": "",
            }
        ],
    )
    db_session.commit()
    return _ok(
        client.get(
            f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
        )
    )[0]


def _found(client, line, days=None):
    q = f"?days={days}" if days else ""
    return [
        c["date"]
        for c in _ok(
            client.get(f"/api/banking/transactions/{line['id']}/candidates{q}")
        )
    ]


def test_thirty_days_is_still_the_default(client, line):
    assert _found(client, line) == []
    assert _found(client, line, "30") == []


def test_ninety_days_a_year_and_all_dates_look_further(client, line):
    assert _found(client, line, "90") == ["2026-07-31"]
    assert _found(client, line, "365") == ["2026-07-31", "2026-02-27"]
    assert _found(client, line, "all") == ["2026-07-31", "2026-02-27", "2025-05-03"]


def test_a_found_posting_far_back_matches(client, line):
    far = _ok(
        client.get(f"/api/banking/transactions/{line['id']}/candidates?days=all")
    )[-1]
    out = _ok(
        client.post(
            f"/api/banking/transactions/{line['id']}/match",
            json={"line_id": far["line_id"]},
        )
    )
    assert out["match_status"] == "manual"


def test_another_range_is_refused_in_a_sentence(client, line):
    r = client.get(f"/api/banking/transactions/{line['id']}/candidates?days=12")
    assert r.status_code == 422
    assert "30 days, 90 days, a year or all dates" in r.json()["detail"]


def test_the_dialog_offers_the_ranges_and_keeps_the_choice():
    show = JS[JS.index("    async showMatch(lineId, accountId") :]
    show = show[: show.index("\n    },\n")]
    assert "candidates?days=${days}" in show
    assert 'id="match-range"' in show and '<label for="match-range">' in show
    ranges = JS[JS.index("    MATCH_RANGES: [") :]
    assert ranges.startswith(
        "    MATCH_RANGES: [['30', '30 days'], ['90', '90 days'], ['365', 'A year'], ['all', 'All dates']],"
    )
    keep = JS[JS.index("    _keepMatchRange(value) {") :]
    assert "sessionStorage.setItem('sb.matchDays'" in keep[: keep.index("\n    },\n")]
