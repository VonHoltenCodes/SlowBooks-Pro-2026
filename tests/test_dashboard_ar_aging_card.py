"""The dashboard's A/R Aging card adds up to Total Receivables (2.18.0 gate,
macbase1 NEW-5).

The card summed open invoice balances by itself, so it read "Current $323.56"
beside a Total Receivables card of $303.56: the $20.00 a customer paid without
applying it to an invoice was in one figure and not the other. It also counted
a foreign-currency invoice at its face amount. Both cards now read the A/R
Aging report's TOTAL row, and the aging card shows the credits on their own
line and the total, as the report page does.
"""

import json
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func

from app.models.contacts import Customer
from app.models.transactions import TransactionLine

ROOT = Path(__file__).resolve().parents[1]


def _today():
    # today as the code under test reads it, at the moment the test runs: a
    # module constant, taken at import, failed seven tests when a CI run
    # crossed midnight UTC (#773)
    return date.today()


def _d(value) -> Decimal:
    return Decimal(str(value))


@pytest.fixture
def salt_and_pine(db_session):
    c = Customer(name="Salt & Pine", is_active=True)
    db_session.add(c)
    db_session.commit()
    return c.id


def _invoice(client, cid, amount, dated, due, **extra):
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": dated.isoformat(),
            "due_date": due.isoformat(),
            "tax_rate": 0,
            "lines": [{"description": "Loaves", "quantity": 1, "rate": amount}],
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _unapplied_payment(client, cid, amount):
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cid,
            "date": _today().isoformat(),
            "amount": amount,
            "allocations": [],
        },
    )
    assert r.status_code == 201, r.text


def _cards(client):
    return client.get("/api/dashboard/data?ids=receivables,ar_aging").json()


def _gl_ar(db_session, seed_accounts) -> Decimal:
    dr, cr = (
        db_session.query(
            func.coalesce(func.sum(TransactionLine.debit), 0),
            func.coalesce(func.sum(TransactionLine.credit), 0),
        )
        .filter(TransactionLine.account_id == seed_accounts["1100"].id)
        .one()
    )
    return _d(dr) - _d(cr)


def test_the_card_names_the_credit_and_totals_what_total_receivables_says(
    client, db_session, seed_accounts, salt_and_pine
):
    # The gate's numbers: $323.56 not yet due, and $20.00 paid on account.
    _invoice(
        client,
        salt_and_pine,
        323.56,
        _today() - timedelta(days=5),
        _today() + timedelta(days=25),
    )
    _unapplied_payment(client, salt_and_pine, 20)

    cards = _cards(client)
    aging = cards["ar_aging"]
    assert aging["current"] == 323.56
    assert aging["d30"] == aging["d60"] == aging["d90"] == 0
    assert aging["credits"] == 20.0
    assert aging["total"] == 303.56
    assert cards["receivables"]["total"] == 303.56
    report = client.get(f"/api/reports/ar-aging?as_of_date={_today()}").json()
    assert report["totals"]["total"] == 303.56
    assert _gl_ar(db_session, seed_accounts) == Decimal("303.56")


def test_the_buckets_less_the_credits_are_the_total_in_home_currency(
    client, db_session, seed_accounts, salt_and_pine
):
    muller = Customer(name="Bäckerei Müller", is_active=True)
    db_session.add(muller)
    db_session.commit()
    _invoice(
        client,
        salt_and_pine,
        400,
        _today() - timedelta(days=75),
        _today() - timedelta(days=45),  # 45 days overdue: 31-60
    )
    _invoice(
        client,
        salt_and_pine,
        120,
        _today() - timedelta(days=40),
        _today() - timedelta(days=10),  # 1-30
    )
    # EUR 850 booked at 1.10 is USD 935 on the ledger, not 850
    _invoice(
        client,
        muller.id,
        850,
        _today() - timedelta(days=3),
        _today() + timedelta(days=27),
        currency="EUR",
        exchange_rate="1.10",
    )
    _unapplied_payment(client, salt_and_pine, 50)

    cards = _cards(client)
    a = cards["ar_aging"]
    assert (a["current"], a["d30"], a["d60"], a["d90"]) == (935.0, 120.0, 400.0, 0)
    assert a["credits"] == 50.0
    added = _d(a["current"]) + _d(a["d30"]) + _d(a["d60"]) + _d(a["d90"])
    assert added - _d(a["credits"]) == _d(a["total"]) == Decimal("1405.00")
    assert cards["receivables"]["total"] == a["total"]
    assert _d(a["total"]) == _gl_ar(db_session, seed_accounts)


def test_the_two_cards_agree_with_a_post_dated_invoice_on_the_books(
    client, seed_accounts, salt_and_pine
):
    _invoice(client, salt_and_pine, 75, _today(), _today() + timedelta(days=30))
    _invoice(
        client,
        salt_and_pine,
        60,
        _today() + timedelta(days=5),  # dated next week
        _today() + timedelta(days=35),
    )
    _unapplied_payment(client, salt_and_pine, 10)
    cards = _cards(client)
    assert cards["receivables"]["total"] == cards["ar_aging"]["total"]


def test_no_receivables_is_said_plainly(client, seed_accounts):
    cards = _cards(client)
    assert cards["receivables"]["total"] == 0
    assert cards["ar_aging"] == {
        "current": 0,
        "d30": 0,
        "d60": 0,
        "d90": 0,
        "credits": 0,
        "total": 0,
    }


def _drawn(data) -> list[str]:
    out = subprocess.run(
        [
            "node",
            str(ROOT / "tests" / "js" / "dashboard_ar_aging_probe.js"),
            json.dumps(data),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.splitlines()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_card_on_screen_shows_the_credits_and_the_total(
    client, seed_accounts, salt_and_pine
):
    _invoice(
        client,
        salt_and_pine,
        323.56,
        _today() - timedelta(days=5),
        _today() + timedelta(days=25),
    )
    _unapplied_payment(client, salt_and_pine, 20)
    lines = _drawn(_cards(client)["ar_aging"])
    assert lines == [
        "Current $323.56",
        "1-30 $0.00",
        "31-60 $0.00",
        "61+ $0.00",
        "Credits not yet applied | -$20.00",
        "Total | $303.56",
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_card_says_so_when_nothing_is_owed():
    empty = {"current": 0, "d30": 0, "d60": 0, "d90": 0, "credits": 0, "total": 0}
    assert _drawn(empty) == ["No open receivables."]
    # a customer holding a credit and owing nothing still shows it
    lines = _drawn({**empty, "credits": 15.0, "total": -15.0})
    assert lines[-2:] == ["Credits not yet applied | -$15.00", "Total | -$15.00"]
