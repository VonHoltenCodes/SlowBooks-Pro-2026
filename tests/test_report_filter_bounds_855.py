"""A report filter id past a 64-bit integer is a 422, not a 500 (#855).

`GET /api/reports/sales-by-item?customer_id=999999999999999999999999`
raised OverflowError ("Python int too large to convert to SQLite
INTEGER") when the id reached a SQL bind. Every id filter on the report
routes is bounded at the largest signed 64-bit integer now, so an id too
large to exist is refused with a 422 and an id in range answers as before.
"""

import pytest

from app.models.classes import TxnClass
from app.models.contacts import Customer, Vendor

HUGE = "9" * 24  # far past 2**63 - 1
INT64_MAX = "9223372036854775807"

# One per filter name, across the report modules
HUGE_CASES = [
    "/api/reports/sales-by-item?customer_id={}",
    "/api/reports/sales-by-item?class_id={}",
    "/api/reports/sales-by-item?job_id={}",
    "/api/reports/sales-by-item/csv?job_id={}",
    "/api/reports/sales-by-item/print?customer_id={}",
    "/api/reports/sales-by-item/lines?item_id={}",
    "/api/reports/purchases-by-item?vendor_id={}",
    "/api/reports/general-ledger?account_id={}",
    "/api/reports/general-ledger/pdf?class_id={}",
    "/api/reports/account-transactions?account_id={}",
    "/api/reports/account-transactions?family={}",
    "/api/reports/account-transactions?account_id=1&job_id={}",
    "/api/reports/profit-loss?class_id={}",
    "/api/reports/profit-loss-detail?job_id={}",
    "/api/reports/profit-loss-by-job?customer_id={}",
    "/api/reports/job-profitability?customer_id={}",
    "/api/reports/possible-duplicates?account_id={}",
    "/api/reports/pledges?customer_id={}",
    "/api/reports/estimate-closing-rate?customer_id={}",
    "/api/reports/customer-statement/{}/pdf",
    "/api/saved-reports/{}",
]


@pytest.mark.parametrize("url", HUGE_CASES)
def test_an_id_past_64_bits_is_a_422(client, url):
    r = client.get(url.format(HUGE))
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "url",
    [
        "/api/reports/wip-schedule?job_id={}",
        "/api/reports/retainage?customer_id={}",
        "/api/reports/job-cost-by-vendor?customer_id={}",
    ],
)
def test_construction_report_ids_past_64_bits_are_a_422(client, seed_accounts, url):
    assert client.post("/api/construction/setup").status_code in (200, 201)
    client.put("/api/settings", json={"company_type": "construction"})
    r = client.get(url.format(HUGE))
    assert r.status_code == 422, r.text


def test_the_largest_64_bit_id_is_not_refused(client):
    """The bound is inclusive: INT64_MAX passes validation and reads as
    an id that does not exist, as any unknown id did before."""
    r = client.get(f"/api/reports/sales-by-item?customer_id={INT64_MAX}")
    assert r.status_code == 404, r.text
    r = client.get(f"/api/reports/sales-by-item/lines?item_id={INT64_MAX}")
    assert r.status_code == 200, r.text


def test_income_by_customer_takes_no_customer_filter(client):
    """Income by Customer has no customer_id filter, so the value never
    reaches a bind; it answers as it always has."""
    r = client.get(f"/api/reports/income-by-customer?customer_id={HUGE}")
    assert r.status_code == 200, r.text


def test_in_range_ids_still_answer(client, db_session, seed_accounts):
    cust = Customer(name="Filter Co", is_active=True)
    vend = Vendor(name="Supply Co", is_active=True)
    cls = TxnClass(name="East")
    db_session.add_all([cust, vend, cls])
    db_session.commit()
    acct = seed_accounts["4000"].id
    bank = seed_accounts["1000"].id
    for url in [
        f"/api/reports/sales-by-item?customer_id={cust.id}",
        f"/api/reports/sales-by-item?class_id={cls.id}",
        f"/api/reports/purchases-by-item?vendor_id={vend.id}",
        f"/api/reports/general-ledger?account_id={acct}&class_id={cls.id}",
        f"/api/reports/account-transactions?account_id={acct}",
        f"/api/reports/account-transactions?family={acct}",
        f"/api/reports/profit-loss?class_id={cls.id}",
        f"/api/reports/profit-loss-by-job?customer_id={cust.id}",
        f"/api/reports/possible-duplicates?account_id={bank}",
        f"/api/reports/pledges?customer_id={cust.id}",
        f"/api/reports/estimate-closing-rate?customer_id={cust.id}",
        f"/api/reports/customer-statement/{cust.id}/pdf",
    ]:
        r = client.get(url)
        assert r.status_code == 200, (url, r.text)


@pytest.mark.parametrize(
    "url",
    [
        "/api/reports/possible-duplicates/dismissals/{}",
        "/api/saved-reports/{}",
    ],
)
def test_path_ids_past_64_bits_on_delete_are_a_422(client, url):
    r = client.delete(url.format(HUGE))
    assert r.status_code == 422, r.text


def test_saved_report_update_past_64_bits_is_a_422(client):
    r = client.put(f"/api/saved-reports/{HUGE}", json={"name": "x"})
    assert r.status_code == 422, r.text
