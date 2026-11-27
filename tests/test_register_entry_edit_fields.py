"""Edit entry's Job and Customer boxes save what they hold (2.32.0 beta
report): the form said "Entry saved" but a changed or cleared job came back
as it was, and a customer picked on money out was never sent. The browser
half is tests/js/entry_edit_job_customer.test.cjs; this is the server round
trip the form now relies on, and what a name on money out means downstream:
nothing for donor gifts or for Income by Customer, as an expense line is
neither."""

from app.models.transactions import TransactionLine
from tests.test_register_edit_entry import _entry, _form


def _ok(r, code=(200, 201)):
    assert r.status_code in code, r.text
    return r.json()


def _job(client, customer_id, name):
    return _ok(
        client.post("/api/jobs", json={"customer_id": customer_id, "name": name})
    )


def test_a_changed_or_cleared_job_and_a_customer_on_money_out_round_trip(
    client, db_session, seed_accounts, seed_customer
):
    kitchen = _job(client, seed_customer.id, "Kitchen")
    garage = _job(client, seed_customer.id, "Garage")
    posted = _entry(client, seed_accounts, job_id=kitchen["id"])
    e = _ok(client.get(f"/api/banking/entries/{posted['id']}"))
    assert e["job_id"] == kitchen["id"] and e["job_name"] == kitchen["full_name"]

    # the body the form now sends: the Job box's pick and the Customer box
    e = _ok(
        client.put(
            f"/api/banking/entries/{posted['id']}",
            json=_form(e, job_id=garage["id"], customer_id=seed_customer.id),
        )
    )
    again = _ok(client.get(f"/api/banking/entries/{posted['id']}"))
    assert again["job_id"] == garage["id"]
    assert again["customer_id"] == seed_customer.id
    db_session.expire_all()
    cat = (
        db_session.query(TransactionLine)
        .filter_by(transaction_id=posted["id"], account_id=seed_accounts["6900"].id)
        .one()
    )
    assert cat.customer_id == seed_customer.id and cat.job_id == garage["id"]

    # both boxes cleared: both clear
    _ok(
        client.put(
            f"/api/banking/entries/{posted['id']}",
            json=_form(again, job_id=None, customer_id=None),
        )
    )
    cleared = _ok(client.get(f"/api/banking/entries/{posted['id']}"))
    assert cleared["job_id"] is None and cleared["customer_id"] is None
    assert cleared["job_name"] is None


def test_an_inactive_job_still_comes_back_by_name(client, seed_accounts, seed_customer):
    """The form's Job box lists active jobs only; the entry's own name lets it
    keep an inactive one as a choice, so saving as is keeps the job."""
    job = _job(client, seed_customer.id, "Porch")
    posted = _entry(client, seed_accounts, job_id=job["id"])
    _ok(client.put(f"/api/jobs/{job['id']}", json={"is_active": False}))
    e = _ok(client.get(f"/api/banking/entries/{posted['id']}"))
    assert e["job_id"] == job["id"] and e["job_name"] == job["full_name"]
    _ok(client.put(f"/api/banking/entries/{posted['id']}", json=_form(e)))
    assert (
        _ok(client.get(f"/api/banking/entries/{posted['id']}"))["job_id"] == job["id"]
    )


def test_a_customer_on_an_expense_line_is_no_gift_and_no_income(
    client, seed_accounts, seed_customer
):
    """Why money out may name a customer: card charges and expenses already
    do (job costing). A donor's giving and Income by Customer count income
    lines only, so a check that names the customer changes neither."""
    posted = _entry(client, seed_accounts, customer_id=seed_customer.id)
    e = _ok(client.get(f"/api/banking/entries/{posted['id']}"))
    assert e["customer_id"] == seed_customer.id
    assert not e.get("gift_letters")
    report = _ok(
        client.get(
            "/api/reports/income-by-customer",
            params={"start_date": "2026-09-01", "end_date": "2026-09-30"},
        )
    )
    assert seed_customer.id not in {i["customer_id"] for i in report["items"]}
