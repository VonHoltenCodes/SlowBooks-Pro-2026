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


def _other_customer(db_session, name):
    from app.models.contacts import Customer

    other = Customer(name=name, is_active=True)
    db_session.add(other)
    db_session.commit()
    return other


def test_an_edit_checks_the_job_as_a_new_entry_does(
    client, db_session, seed_accounts, seed_customer
):
    """Review of the fix: the form can now change an entry's job, so the
    edit refuses what a new entry refuses: another customer's job, an
    inactive job picked now, and a job that does not exist."""
    other = _other_customer(db_session, "Other Co")
    deck = _job(client, other.id, "Deck")
    porch = _job(client, seed_customer.id, "Porch")
    shed = _job(client, seed_customer.id, "Shed")
    posted = _entry(client, seed_accounts)
    e = _ok(client.get(f"/api/banking/entries/{posted['id']}"))
    url = f"/api/banking/entries/{posted['id']}"

    # a new entry refuses this pair; the edit does too
    r = client.post(
        "/api/banking/transactions?allow_duplicate_check_number=true",
        json={
            **_form(e),
            "customer_id": seed_customer.id,
            "job_id": deck["id"],
        },
    )
    assert r.status_code == 400, r.text
    r = client.put(url, json=_form(e, job_id=deck["id"], customer_id=seed_customer.id))
    assert r.status_code == 400, r.text
    assert (
        r.json()["detail"]
        == f"The job {deck['full_name']} belongs to another customer."
    )

    # an inactive job picked now, and one that does not exist
    _ok(client.put(f"/api/jobs/{shed['id']}", json={"is_active": False}))
    r = client.put(url, json=_form(e, job_id=shed["id"]))
    assert r.status_code == 400 and "inactive" in r.json()["detail"], r.text
    assert client.put(url, json=_form(e, job_id=999999)).status_code == 404

    # nothing above was saved
    assert _ok(client.get(url))["job_id"] is None

    # the entry keeps its job; a customer picked now must own it
    e = _ok(client.put(url, json=_form(e, job_id=porch["id"])))
    r = client.put(url, json=_form(e, customer_id=other.id))
    assert r.status_code == 400, r.text
    assert (
        r.json()["detail"]
        == f"The job {porch['full_name']} belongs to another customer."
    )
    # its own customer is fine
    e = _ok(client.put(url, json=_form(e, customer_id=seed_customer.id)))
    assert e["job_id"] == porch["id"] and e["customer_id"] == seed_customer.id


def test_an_entry_keeping_its_own_names_is_not_asked_again(
    client, db_session, seed_accounts, seed_customer
):
    """An entry saved as is keeps its job and name with no new check, though
    the job has gone inactive since."""
    porch = _job(client, seed_customer.id, "Porch")
    posted = _entry(
        client, seed_accounts, job_id=porch["id"], customer_id=seed_customer.id
    )
    _ok(client.put(f"/api/jobs/{porch['id']}", json={"is_active": False}))
    url = f"/api/banking/entries/{posted['id']}"
    e = _ok(client.get(url))
    e = _ok(
        client.put(url, json=_form(e, customer_id=seed_customer.id, description="Fuel"))
    )
    assert e["job_id"] == porch["id"] and e["customer_id"] == seed_customer.id
