"""A journal line keeps its own job (#672), and the Journal Entries page
says which entries it may offer Edit on (#673).

#672: the API took a job per line all along; the journal form now sends
one, so a line's job must come back on the entry's GET and in the list, by
name for the view, and a line that names none takes the header's.

#673: the list answers `editable` the way the entry's GET and the register
do (#388): a hand-entered entry, standing, never a reversal, an import or
a document's posting, and never for the accountant's sign-in."""

from app.models.contacts import Customer
from app.models.jobs import Job
from app.models.transactions import Transaction
from app.models.users import ROLE_ACCOUNTANT
from app.services import auth as auth_service
from app.models.users import User


def _jobs(db):
    c = Customer(name="Acme Diner", is_active=True)
    db.add(c)
    db.flush()
    kitchen = Job(customer_id=c.id, name="Kitchen")
    bath = Job(customer_id=c.id, name="Bath")
    db.add_all([kitchen, bath])
    db.commit()
    return kitchen.id, bath.id


def _post(client, accounts, header_job=None, line_jobs=(None, None), **over):
    lines = []
    for (acct, debit, credit), job in zip(
        ((accounts["6900"].id, 100, 0), (accounts["2000"].id, 0, 100)), line_jobs
    ):
        line = {"account_id": acct, "debit": debit, "credit": credit}
        if job is not None:
            line["job_id"] = job
        lines.append(line)
    body = {
        "date": "2026-09-02",
        "description": "Accrue materials",
        "job_id": header_job,
        "lines": lines,
        **over,
    }
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_line_job_survives_create_and_read(client, db_session, seed_accounts):
    kitchen, bath = _jobs(db_session)
    posted = _post(client, seed_accounts, header_job=kitchen, line_jobs=(bath, None))
    e = client.get(f"/api/journal/{posted['id']}").json()
    by_acct = {ln["account_id"]: ln for ln in e["lines"]}
    line, rest = by_acct[seed_accounts["6900"].id], by_acct[seed_accounts["2000"].id]
    assert line["job_id"] == bath and line["job_name"] == "Acme Diner: Bath"
    # a line naming no job takes the header's
    assert rest["job_id"] == kitchen and rest["job_name"] == "Acme Diner: Kitchen"
    assert e["job_id"] == kitchen
    listed = next(
        x
        for x in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
        if x["id"] == e["id"]
    )
    assert {ln["job_id"] for ln in listed["lines"]} == {bath, kitchen}
    assert "Acme Diner: Bath" in {ln["job_name"] for ln in listed["lines"]}


def test_a_line_with_no_job_anywhere_has_none(client, db_session, seed_accounts):
    posted = _post(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    assert all(ln["job_id"] is None and ln["job_name"] == "" for ln in e["lines"])


def test_an_edit_moves_a_line_to_another_job_and_back_to_the_header(
    client, db_session, seed_accounts
):
    kitchen, bath = _jobs(db_session)
    posted = _post(client, seed_accounts, header_job=kitchen)
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = {
        "date": e["date"],
        "description": e["description"],
        "job_id": kitchen,
        "lines": [
            {
                "account_id": ln["account_id"],
                "debit": ln["debit"],
                "credit": ln["credit"],
                # the form's blank ("Same as header") sends null
                "job_id": bath if ln["debit"] else None,
            }
            for ln in e["lines"]
        ],
    }
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 200, r.text
    jobs = {ln["debit"] > 0: ln["job_id"] for ln in r.json()["lines"]}
    assert jobs == {True: bath, False: kitchen}


def test_the_list_marks_hand_entered_standing_entries_editable(
    client, db_session, seed_accounts
):
    standing = _post(client, seed_accounts)
    voided = _post(client, seed_accounts, description="Wrong one")
    assert client.post(f"/api/journal/{voided['id']}/void").status_code == 200
    imported = _post(client, seed_accounts, description="Opening balance")
    db_session.query(Transaction).filter_by(id=imported["id"]).update(
        {"source_type": "iif_import"}
    )
    db_session.commit()
    listed = {
        x["id"]: x
        for x in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
    }
    assert listed[standing["id"]]["editable"] is True
    assert listed[voided["id"]]["editable"] is False
    assert listed[imported["id"]]["editable"] is False
    # the reversal the void posted is not editable either
    reversals = [x for x in listed.values() if x["source_type"].endswith("_void")]
    assert all(x["editable"] is False for x in reversals)
    # the list agrees with each entry's own GET
    for entry_id, x in listed.items():
        assert (
            client.get(f"/api/journal/{entry_id}").json()["editable"] == x["editable"]
        )


def test_the_list_offers_the_accountant_no_edit(client, db_session, seed_accounts):
    standing = _post(client, seed_accounts)
    db_session.add(
        User(
            username="cpa",
            display_name="Cpa",
            password_hash=auth_service.hash_password("cpa-password-123"),
            role=ROLE_ACCOUNTANT,
            is_active=True,
        )
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login", json={"username": "cpa", "password": "cpa-password-123"}
    )
    assert r.status_code == 200, r.text
    listed = {
        x["id"]: x
        for x in client.get("/api/journal", params={"start_date": "1900-01-01"}).json()[
            "entries"
        ]
    }
    assert listed[standing["id"]]["editable"] is False


def test_an_edit_keeps_the_header_class_on_a_line_sent_blank(
    client, db_session, seed_accounts
):
    # the in-place edit path stored a blank line class as null while a new
    # entry's line takes the header's; same shape as the job fix above
    r = client.post("/api/classes", json={"name": "Shop"})
    assert r.status_code in (200, 201), r.text
    shop = r.json()["id"]
    posted = _post(client, seed_accounts)
    e = client.get(f"/api/journal/{posted['id']}").json()
    body = {
        "date": e["date"],
        "description": e["description"],
        "class_id": shop,
        "lines": [
            {
                "account_id": ln["account_id"],
                "debit": ln["debit"],
                "credit": ln["credit"],
                "class_id": None,
            }
            for ln in e["lines"]
        ],
    }
    r = client.put(f"/api/journal/{posted['id']}", json=body)
    assert r.status_code == 200, r.text
    assert {ln["class_id"] for ln in r.json()["lines"]} == {shop}
