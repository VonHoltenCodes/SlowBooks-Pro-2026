"""The dashboard's Monthly Revenue card leaves the WIP schedule's as-of
entry and its next-day reversal out (#805). The P&L by period keeps both:
that is the accounting. The card is a quick look at what was billed, where
the pair read as a rise one month and a fall the next that nobody billed.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from app.models.accounts import Account
from app.models.contacts import Customer
from app.models.job_costing import JobBudget
from app.models.jobs import Job
from app.services.accounting import create_journal_entry
from app.services.dashboard_widgets import MONTHLY_REVENUE_WIP_NOTE
from app.services.job_costing import ensure_default_cost_types


def _months_back(n: int) -> tuple[int, int]:
    """(year, month) n months before this one."""
    today = date.today()
    year, month = today.year, today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return year, month


def _month_end(n: int) -> date:
    year, month = _months_back(n)
    return date(year, month, monthrange(year, month)[1])


def _month_start(n: int) -> date:
    year, month = _months_back(n)
    return date(year, month, 1)


# The as-of date is a month end three months back, so the entry's month and
# the reversal's month are both on the card.
AS_OF = _month_end(3)
NEXT_DAY = AS_OF + timedelta(days=1)


def _acct(db, number):
    return db.query(Account).filter(Account.account_number == number).one()


def _over_billed_job(db):
    """Contract 100,000, budget 80,000, cost 40,000 and billed 60,000 in the
    as-of month: earned 50,000, over-billed 10,000."""
    ensure_default_cost_types(db)
    customer = Customer(name="Clinic Owner", is_active=True)
    db.add(customer)
    db.flush()
    job = Job(
        customer_id=customer.id,
        name="Clinic",
        status="in_progress",
        contract_type="lump_sum",
        contract_amount=Decimal("100000"),
        original_contract_amount=Decimal("100000"),
    )
    db.add(job)
    db.flush()
    db.add(
        JobBudget(
            job_id=job.id,
            cost_type="material",
            amount=Decimal("80000"),
            source="manual",
            locked=False,
        )
    )
    when = _month_start(3)
    create_journal_entry(
        db,
        when,
        "job cost",
        [
            {
                "account_id": _acct(db, "5100").id,
                "debit": Decimal("40000"),
                "job_id": job.id,
                "cost_type": "material",
            },
            {"account_id": _acct(db, "2000").id, "credit": Decimal("40000")},
        ],
    )
    create_journal_entry(
        db,
        when,
        "owner billing",
        [
            {"account_id": _acct(db, "1100").id, "debit": Decimal("60000")},
            {
                "account_id": _acct(db, "4000").id,
                "credit": Decimal("60000"),
                "job_id": job.id,
            },
        ],
    )
    db.commit()
    return job


def _construction_book(client, db):
    r = client.put("/api/settings", json={"company_type": "construction"})
    assert r.status_code == 200, r.text
    assert client.post("/api/construction/setup").status_code == 200
    return _over_billed_job(db)


def _card(client):
    r = client.get("/api/dashboard/data?ids=monthly_revenue")
    assert r.status_code == 200, r.text
    return r.json()["monthly_revenue"]


def _amounts(card):
    return {(m["year"], m["month"]): m["amount"] for m in card["months"]}


def _key(d: date):
    return (d.year, d.strftime("%b"))


def _pl_income(client, start, end):
    r = client.get(f"/api/reports/profit-loss?start_date={start}&end_date={end}")
    assert r.status_code == 200, r.text
    return r.json()["total_income"]


def test_a_wip_posting_across_a_month_end_moves_the_pl_by_period_but_not_the_card(
    client, db_session, seed_accounts
):
    _construction_book(client, db_session)
    before = _amounts(_card(client))
    pl_month = _pl_income(client, _month_start(3), AS_OF)
    pl_next = _pl_income(client, _month_start(2), _month_end(2))

    r = client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}")
    assert r.status_code == 200, r.text
    assert r.json()["reversal_date"] == NEXT_DAY.isoformat()

    # the P&L by period shows the entry in its month and the reversal in the next
    assert _pl_income(client, _month_start(3), AS_OF) == pl_month - 10000.0
    assert _pl_income(client, _month_start(2), _month_end(2)) == pl_next + 10000.0

    card = _card(client)
    assert _amounts(card) == before
    assert _amounts(card)[_key(AS_OF)] == 60000.0
    assert _amounts(card)[_key(NEXT_DAY)] == 0.0
    assert card["note"] == MONTHLY_REVENUE_WIP_NOTE


def test_a_voided_wip_posting_leaves_the_card_as_it_was(
    client, db_session, seed_accounts
):
    _construction_book(client, db_session)
    before = _amounts(_card(client))
    assert (
        client.post(f"/api/reports/wip-schedule/post?as_of={AS_OF}").status_code == 200
    )
    assert (
        client.post(f"/api/reports/wip-schedule/void?as_of={AS_OF}").status_code == 200
    )
    assert _amounts(_card(client)) == before


def test_a_book_with_no_wip_gives_the_card_its_ledger_income_by_month(
    client, db_session, seed_accounts
):
    income = _acct(db_session, "4000")
    bank = _acct(db_session, "1100")
    for n, amount in ((5, "1234.56"), (3, "700.00"), (0, "89.10")):
        create_journal_entry(
            db_session,
            _month_start(n),
            "income",
            [
                {"account_id": bank.id, "debit": Decimal(amount)},
                {"account_id": income.id, "credit": Decimal(amount)},
            ],
        )
    db_session.commit()

    card = _card(client)
    amounts = _amounts(card)
    assert len(card["months"]) == 12
    assert amounts[_key(_month_start(5))] == 1234.56
    assert amounts[_key(_month_start(3))] == 700.0
    assert amounts[_key(_month_start(0))] == 89.1
    assert sum(Decimal(str(v)) for v in amounts.values()) == Decimal("2023.66")
    # a business book's card carries no WIP caption
    assert "note" not in card
