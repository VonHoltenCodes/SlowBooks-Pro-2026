"""A class or job named on a feed add, a register entry or a transfer must
exist, and the class must not be archived (2.31.4; the routes used to post
any integer). The transfer list says its class."""


def _entry(client, seed_accounts, **over):
    body = {
        "account_id": seed_accounts["1000"].id,
        "date": "2026-09-06",
        "amount": "-40.00",
        "payee": "Pens & Paper",
        "category_account_id": (
            seed_accounts["6500"].id
            if "6500" in seed_accounts
            else next(a.id for k, a in seed_accounts.items() if k.startswith("6"))
        ),
    }
    body.update(over)
    return client.post("/api/banking/transactions", json=body)


def _transfer(client, seed_accounts, **over):
    body = {
        "date": "2026-09-05",
        "from_account_id": seed_accounts["1000"].id,
        "to_account_id": seed_accounts["2100"].id,
        "amount": "250.00",
        "memo": "card payment",
        "reference": "web",
    }
    body.update(over)
    return client.post("/api/transfers", json=body)


def test_an_unknown_class_or_job_is_refused_on_entries_and_transfers(
    client, seed_accounts
):
    r = _entry(client, seed_accounts, class_id=99999)
    assert r.status_code == 404 and "Class 99999" in r.json()["detail"]
    r = _entry(client, seed_accounts, job_id=99999)
    assert r.status_code == 404 and "Job 99999" in r.json()["detail"]
    r = _transfer(client, seed_accounts, class_id=99999)
    assert r.status_code == 404 and "Class 99999" in r.json()["detail"]


def test_an_archived_class_is_refused_with_its_name(client, seed_accounts):
    cls = client.post("/api/classes", json={"name": "Old Crew"}).json()
    r = client.put(f"/api/classes/{cls['id']}", json={"is_archived": True})
    assert r.status_code == 200, r.text
    r = _transfer(client, seed_accounts, class_id=cls["id"])
    assert r.status_code == 400 and "Old Crew" in r.json()["detail"]
    r = _entry(client, seed_accounts, class_id=cls["id"])
    assert r.status_code == 400 and "archived" in r.json()["detail"]


def test_the_transfer_list_names_the_class(client, seed_accounts):
    cls = client.post("/api/classes", json={"name": "North Crew"}).json()
    r = _transfer(client, seed_accounts, class_id=cls["id"])
    assert r.status_code == 201, r.text
    assert r.json()["class_id"] == cls["id"] and r.json()["class_name"] == "North Crew"
    plain = _transfer(client, seed_accounts).json()
    listed = {t["id"]: t for t in client.get("/api/transfers").json()}
    assert listed[r.json()["id"]]["class_name"] == "North Crew"
    assert (
        listed[plain["id"]]["class_id"] is None
        and listed[plain["id"]]["class_name"] == ""
    )


def test_a_register_entry_takes_a_job(client, seed_accounts, db_session):
    from app.models.jobs import Job
    from app.models.contacts import Customer

    cust = Customer(name="Harbor LLC", is_active=True)
    db_session.add(cust)
    db_session.flush()
    job = Job(customer_id=cust.id, name="Dock repair")
    db_session.add(job)
    db_session.commit()
    r = _entry(client, seed_accounts, job_id=job.id)
    assert r.status_code == 201, r.text
    from app.models.transactions import Transaction

    txn = db_session.get(Transaction, r.json()["transaction_id"])
    assert txn.job_id == job.id
