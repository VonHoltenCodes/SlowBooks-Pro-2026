"""A category picked in the bank review list is kept on the statement line.

Exploratory 2.17.3 (skytech, W-M12): a category chosen in a line's dropdown
wasn't saved until that line's own Add was pressed. "Add all categorised"
then posted only the lines a rule had categorised ("Added 1"), and a reload
lost the on-screen picks. The pick is saved as it is made now (PATCH of the
statement line), so Add all posts it and the next visit shows it."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.bank_rules import BankRule
from app.models.banking import BankTransaction
from app.services.ofx_import import import_transactions

JS = (Path(__file__).resolve().parents[1] / "app/static/js/banking.js").read_text(
    encoding="utf-8"
)


@pytest.fixture
def feed(client, seed_accounts):
    r = client.post(
        "/api/banking/accounts",
        json={"name": "Checking feed", "account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def two_lines(client, db_session, seed_accounts, feed):
    """COMED is categorised by a rule; OFFICE DEPOT waits for a person."""
    db_session.add(
        BankRule(
            name="power",
            pattern="comed",
            account_id=seed_accounts["6900"].id,
            rule_type="contains",
            priority=10,
            is_active=True,
        )
    )
    db_session.commit()
    import_transactions(
        db_session,
        feed["id"],
        [
            {
                "fitid": "a",
                "date": date(2026, 9, 3),
                "amount": Decimal("-82.10"),
                "payee": "COMED",
                "memo": "",
            },
            {
                "fitid": "b",
                "date": date(2026, 9, 4),
                "amount": Decimal("-45.00"),
                "payee": "OFFICE DEPOT",
                "memo": "",
            },
        ],
    )
    rows = client.get(
        f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
    ).json()
    return {r["payee"]: r for r in rows}


def test_a_picked_category_is_saved_and_add_all_posts_it(
    client, seed_accounts, feed, two_lines
):
    office = two_lines["OFFICE DEPOT"]
    assert office["category_account_id"] is None
    r = client.patch(
        f"/api/banking/transactions/{office['id']}",
        json={"category_account_id": seed_accounts["6000"].id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["category_account_id"] == seed_accounts["6000"].id
    assert r.json()["category_name"] == "Advertising & Marketing"

    # a reload shows the pick
    again = client.get(
        f"/api/banking/transactions?bank_account_id={feed['id']}&status=unmatched"
    ).json()
    assert {x["payee"]: x["category_account_id"] for x in again} == {
        "COMED": seed_accounts["6900"].id,
        "OFFICE DEPOT": seed_accounts["6000"].id,
    }

    out = client.post(f"/api/banking/accounts/{feed['id']}/feed/add-all").json()
    assert out == {"added": 2, "skipped": []}
    reg = client.get(
        f"/api/banking/check-register?account_id={seed_accounts['1000'].id}"
    ).json()
    assert sorted(e["payment"] for e in reg["entries"]) == [45.0, 82.1]


def test_a_pick_can_be_cleared(client, seed_accounts, two_lines):
    comed = two_lines["COMED"]
    r = client.patch(
        f"/api/banking/transactions/{comed['id']}", json={"category_account_id": None}
    )
    assert r.status_code == 200 and r.json()["category_account_id"] is None


def test_the_guards(client, db_session, seed_accounts, feed, two_lines):
    office = two_lines["OFFICE DEPOT"]
    r = client.patch(
        f"/api/banking/transactions/{office['id']}",
        json={"category_account_id": 999999},
    )
    assert r.status_code == 404
    r = client.patch(
        f"/api/banking/transactions/{office['id']}",
        json={"category_account_id": seed_accounts["1000"].id},
    )
    assert r.status_code == 400 and "other than" in r.json()["detail"]
    # a line already in the books keeps the category it posted with
    comed = two_lines["COMED"]
    assert (
        client.post(f"/api/banking/transactions/{comed['id']}/add").status_code == 200
    )
    r = client.patch(
        f"/api/banking/transactions/{comed['id']}",
        json={"category_account_id": seed_accounts["6000"].id},
    )
    assert r.status_code == 400 and "already in the books" in r.json()["detail"]
    db_session.expire_all()
    assert (
        db_session.get(BankTransaction, comed["id"]).category_account_id
        == seed_accounts["6900"].id
    )


def test_the_review_dropdown_saves_on_change_and_add_all_waits_for_it():
    panel = re.search(
        r"\n    _reviewPanel\(review, feedId.*?\n    },\n", JS, re.S
    ).group(0)
    assert 'onchange="BankingPage.setCategory(${t.id}, this)"' in panel
    setter = re.search(r"\n    setCategory\(lineId, sel\).*?\n    },\n", JS, re.S)
    assert setter, "setCategory not found"
    assert "API.request('PATCH', `/banking/transactions/${lineId}`" in setter.group(0)
    add_all = re.search(r"async addAll\(feedId, accountId\).*?\n    },\n", JS, re.S)
    body = add_all.group(0)
    assert body.index("_pendingCategories") < body.index("feed/add-all")
