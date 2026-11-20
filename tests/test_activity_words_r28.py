"""Round 28: what an Activity entry says, in the form's own words.

The same rule the line comparison follows (`_compare_lines`, rulings B6 and
B10) holds for a document's own fields: a name and never an id, a stored
choice and a column as the form shows them, a field said once, an action
in words and not as the stamp it wrote, a save that changed nothing saying
nothing, and the company's vocabulary swapped in our words only, never in
a customer's name or a file's name.
"""

import io

import pytest

from app.database import Base
from app.models.audit import AuditLog
from app.services import activity
from app.services.settings_service import set_setting
from tests.test_co_effective_date_allowance import _setup
from tests.test_document_activity_894 import _activity, _age, _customer


def _details(client, kind, id_):
    return _activity(client, kind, id_)["events"][0]["details"]


def _everything(client, kind, id_):
    events = _activity(client, kind, id_)["events"]
    return [e["text"] for e in events] + [d for e in events for d in e["details"]]


# ---------------------------------------------------------------------------
# a register entry reads as its form does
# ---------------------------------------------------------------------------


def _register_entry(client, seed_accounts, **more):
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": "2026-06-01",
            "amount": "-100",
            "payee": "Ridge",
            "category_account_id": seed_accounts["6900"].id,
            **more,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_register_entrys_payee_and_check_number_read_as_the_form_names_them(
    client, seed_accounts, db_session
):
    entry = _register_entry(client, seed_accounts)
    body = {"amount": "-100", "category_account_id": seed_accounts["6900"].id}
    _age(db_session)
    r = client.put(
        f"/api/banking/entries/{entry}",
        json=body | {"payee": "Ridge Supply", "check_number": "1042"},
    )
    assert r.status_code == 200, r.text
    said = _details(client, "bank_entry", entry)
    assert "Payee Ridge → Ridge Supply" in said, said
    assert "Check / ref # set to 1042" in said, said
    assert not any(d.startswith(("Description", "Reference")) for d in said), said
    assert not any(": Description " in d for d in said), said


def test_a_register_entrys_memo_is_said_once(client, seed_accounts, db_session):
    entry = _register_entry(client, seed_accounts, description="May fuel")
    body = {
        "amount": "-100",
        "payee": "Ridge",
        "category_account_id": seed_accounts["6900"].id,
    }
    _age(db_session)
    r = client.put(
        f"/api/banking/entries/{entry}", json=body | {"description": "June fuel"}
    )
    assert r.status_code == 200, r.text
    # the memo is kept on both of the entry's lines: one field, said once
    assert _details(client, "bank_entry", entry) == ["Memo May fuel → June fuel"]


def test_a_splits_own_memo_is_that_lines_change(client, seed_accounts, db_session):
    splits = [
        {
            "category_account_id": seed_accounts["6900"].id,
            "amount": "-60",
            "description": "gas",
        },
        {
            "category_account_id": seed_accounts["6000"].id,
            "amount": "-40",
            "description": "ads",
        },
    ]
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": seed_accounts["1000"].id,
            "date": "2026-06-01",
            "amount": "-100",
            "payee": "Ridge",
            "splits": splits,
        },
    )
    assert r.status_code == 201, r.text
    entry = r.json()["id"]
    _age(db_session)
    splits[0] = splits[0] | {"description": "fuel"}
    r = client.put(
        f"/api/banking/entries/{entry}", json={"amount": "-100", "splits": splits}
    )
    assert r.status_code == 200, r.text
    assert _details(client, "bank_entry", entry) == [
        "Line fuel, debit $60.00: Memo gas → fuel"
    ]


# ---------------------------------------------------------------------------
# a field said once; an action in words
# ---------------------------------------------------------------------------


def test_a_bills_terms_are_said_once(client, seed_accounts, db_session):
    from app.services import payment_terms

    # the company's list of terms, so a bill's terms are tied to an entry
    payment_terms.ensure_seeded(db_session)
    db_session.commit()
    vendor = client.post("/api/vendors", json={"name": "Hardware Supply"}).json()
    line = {
        "description": "Boards",
        "quantity": 1,
        "rate": 100,
        "account_id": seed_accounts["6000"].id,
    }
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-1",
            "date": "2026-11-01",
            "lines": [line],
        },
    )
    assert r.status_code in (200, 201), r.text
    bill = r.json()
    _age(db_session)
    r = client.put(f"/api/bills/{bill['id']}", json={"terms": "Net 45"})
    assert r.status_code == 200, r.text
    said = _details(client, "bill", bill["id"])
    # the terms' text and the list entry they are tied to are one field
    assert [d for d in said if d.startswith("Terms")] == ["Terms Net 30 → Net 45"]


def test_closing_a_job_is_one_entry_in_words(client, seed_customer, db_session):
    r = client.post(
        "/api/jobs", json={"name": "Kitchen", "customer_id": seed_customer.id}
    )
    assert r.status_code == 201, r.text
    job = r.json()
    _age(db_session)
    r = client.put(f"/api/jobs/{job['id']}", json={"status": "closed", "name": "Bath"})
    assert r.status_code == 200, r.text
    from app.models.jobs import Job

    db_session.expire_all()
    closed_on = db_session.get(Job, job["id"]).closed_on.isoformat()
    events = _activity(client, "job", job["id"])["events"]
    texts = [e["text"] for e in events]
    assert sum(t.startswith("Closed by ") for t in texts) == 1, texts
    closing = next(e for e in events if e["text"].startswith("Closed by "))
    assert closing["text"].endswith(f" on {closed_on}."), closing
    # the rename made in the same save is the only edit
    edited = [e for e in events if e["kind"] == "edited"]
    assert [e["details"] for e in edited] == [["Name Kitchen → Bath"]], events
    _age(db_session)
    r = client.put(f"/api/jobs/{job['id']}", json={"status": "in_progress"})
    assert r.status_code == 200, r.text
    said = _everything(client, "job", job["id"])
    assert not any("Closed on" in s for s in said), said
    assert any(s.startswith("Marked in progress by ") for s in said), said


def test_a_new_job_cost_entry_lists_no_total_filling_in(
    client, seed_accounts, seed_customer
):
    job, _ = _setup(client, seed_customer.id, [], 100000)
    r = client.post(
        "/api/job-costs",
        json={
            "date": "2026-11-01",
            "job_id": job["id"],
            "memo": "accr",
            "reverses_on": "2026-12-01",
            "lines": [
                {
                    "job_id": job["id"],
                    "cost_type": "labor",
                    "description": "Crew",
                    "amount": 100,
                    "accrue": True,
                    "debit_account_id": seed_accounts["6900"].id,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    said = _everything(client, "job_cost", r.json()["id"])
    assert "Line: Crew, $100.00" in said, said
    assert not any("$0.00" in s for s in said), said


# ---------------------------------------------------------------------------
# a name, never an id; a choice as the form shows it
# ---------------------------------------------------------------------------


def test_a_documents_own_fields_read_names_and_the_forms_words(
    client, seed_accounts, seed_customer, db_session
):
    parent = client.post("/api/customers", json={"name": "Parent Co"}).json()
    cust = _customer(client)
    _age(db_session)
    r = client.put(
        f"/api/customers/{cust['id']}",
        json={
            "parent_customer_id": parent["id"],
            "bill_address1": "1 Main",
            "bill_city": "Joliet",
            "bill_zip": "60404",
            "tax_id": "12-3456789",
        },
    )
    assert r.status_code == 200, r.text
    said = _details(client, "customer", cust["id"])
    assert "Parent customer set to Parent Co" in said, said
    assert "Billing address set to 1 Main" in said, said
    assert "Billing city set to Joliet" in said, said
    assert "Billing ZIP set to 60404" in said, said
    # a tax ID changed, without its digits and without reading as a tax
    assert "Tax ID changed" in said, said

    vendor = client.post("/api/vendors", json={"name": "Vend One"}).json()
    _age(db_session)
    r = client.put(
        f"/api/vendors/{vendor['id']}",
        json={
            "is_1099_vendor": True,
            "vendor_1099_type": "nec",
            "w9_on_file": True,
            "vendor_type": "sub",
            "coi_carrier": "Acuity",
        },
    )
    assert r.status_code == 200, r.text
    said = _details(client, "vendor", vendor["id"])
    for want in (
        "1099 vendor No → Yes",
        "1099 type set to 1099-NEC",
        "W-9 on file No → Yes",
        "Vendor type set to Subcontractor",
        "Insurance carrier set to Acuity",
    ):
        assert want in said, said

    job = client.post(
        "/api/jobs", json={"name": "Kitchen", "customer_id": seed_customer.id}
    ).json()
    _age(db_session)
    r = client.put(
        f"/api/jobs/{job['id']}/contract",
        json={
            "contract_type": "cost_plus",
            "contract_amount": 50000,
            "co_markup_pct": 15,
            "sales_tax_treatment": "contractor_pays",
        },
    )
    assert r.status_code == 200, r.text
    said = _details(client, "job", job["id"])
    assert "Contract type set to Cost-plus" in said, said
    assert "Change order markup % set to 15%" in said, said
    assert "Sales tax set to Contractor pays tax on materials" in said, said


def test_changing_an_invoices_customer_says_it_once(client, seed_accounts, db_session):
    first, second = _customer(client), _customer(client, "Second Cust")
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": first["id"],
            "date": "2026-06-01",
            "lines": [{"description": "Work", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    _age(db_session)
    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": second["id"]})
    assert r.status_code == 200, r.text
    said = _details(client, "invoice", inv["id"])
    assert [d for d in said if "Second Cust" in d] == [
        "Customer Probe Co → Second Cust"
    ], said


def _id_columns():
    seen = set()
    for dt in activity.DOC_TYPES.values():
        for table in (dt.table, *(t for t, _ in dt.lines)):
            for col in Base.metadata.tables[table].c:
                if col.name.endswith("_id") and (table, col.name) not in seen:
                    seen.add((table, col.name))
                    yield table, col.name


@pytest.mark.parametrize("table,column", sorted(_id_columns()))
def test_no_field_that_holds_an_id_ever_shows_it(db_session, table, column):
    """Every column of every document and line table that points at another
    record: its change reads a name, plain words for a record that is gone,
    or just that it changed. Never the number."""
    words = activity._Words(db_session)
    for old, new in ((None, 987654), (987654, 987655), (987654, None)):
        row = AuditLog(
            table_name=table,
            record_id=1,
            action="UPDATE",
            changed_fields=[column],
            old_values={column: old},
            new_values={column: new},
        )
        for said in activity._changes(words, row):
            assert "98765" not in said and "#" not in said, said
            assert "None" not in said, said


# ---------------------------------------------------------------------------
# the company's vocabulary: our words, never the person's
# ---------------------------------------------------------------------------


def test_a_nonprofits_words_leave_names_and_file_names_as_typed(
    client, seed_accounts, db_session
):
    # every word of this name and this file is one the vocabulary swaps
    name = "Class of 1988 Job Corps Invoice Club"
    cust = _customer(client, name)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "lines": [
                {"description": "Job Class Invoice work", "quantity": 1, "rate": 100}
            ],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-05",
            "amount": "100.00",
            "method": "Check",
            "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
        },
    )
    assert r.status_code == 201, r.text
    pay = r.json()
    r = client.post(
        f"/api/attachments/invoice/{inv['id']}",
        files={
            "file": (
                "Invoice Class Job.pdf",
                io.BytesIO(b"%PDF-1.4\n%%EOF\n"),
                "application/pdf",
            )
        },
    )
    assert r.status_code == 201, r.text
    [row] = client.get("/api/deposits/pending").json()
    r = client.post(
        "/api/deposits",
        json={
            "deposit_to_account_id": seed_accounts["1000"].id,
            "date": "2026-06-06",
            "line_ids": [row["transaction_line_id"]],
        },
    )
    assert r.status_code in (200, 201), r.text
    deposit = r.json()["transaction_id"]

    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()

    said = _everything(client, "invoice", inv["id"])
    # our own words are the company's
    assert any(s.startswith("Pledge ") and " created by " in s for s in said), said
    # the person's are as typed
    assert any(
        s.startswith(f"Payment of $100.00 from {name} ") and " applied by " in s
        for s in said
    ), said
    assert any(s.startswith("Invoice Class Job.pdf attached by ") for s in said), said
    assert "Line: Job Class Invoice work, $100.00" in said, said

    said = _everything(client, "customer", cust["id"])
    assert any(s.startswith(f"Donor {name} created by ") for s in said), said
    said = _everything(client, "deposit", deposit)
    assert any(s.startswith(f"Took payment of $100.00 from {name} by ") for s in said)
    said = _everything(client, "payment", pay["id"])
    assert any(s.startswith("$100.00 applied to pledge ") for s in said), said
    for kind, id_ in (
        ("invoice", inv["id"]),
        ("customer", cust["id"]),
        ("deposit", deposit),
        ("payment", pay["id"]),
    ):
        for s in _everything(client, kind, id_):
            assert "Fund of 1988" not in s and "Grant Corps" not in s, s
            assert "" not in s and "" not in s, s


def test_the_swap_marks_never_reach_the_page(db_session):
    words = activity._Words(db_session)
    marked = f"Paid by {words.data('Ann')} for {words.data('')}{words.data(None)}x"
    assert words.text(marked) == "Paid by Ann for x"
    event = activity._Event(None, 1, words.data("Ann"), "edited", marked, [marked])
    out = event.out(0)
    assert out["who"] == "Ann" and out["text"] == "Paid by Ann for x"
    assert out["details"] == ["Paid by Ann for x"]


def test_a_nonprofits_void_refusal_has_no_article_to_get_wrong(
    client, seed_accounts, db_session
):
    cust = _customer(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "lines": [{"description": "Work", "quantity": 1, "rate": 100}],
        },
    )
    inv = r.json()
    r = client.post(
        "/api/payments",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-05",
            "amount": "100.00",
            "method": "Check",
            "allocations": [{"invoice_id": inv["id"], "amount": "100.00"}],
        },
    )
    assert r.status_code == 201, r.text
    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    r = client.post(f"/api/invoices/{inv['id']}/void")
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "payments applied" in detail and "pledge" in detail, detail
    assert "an pledge" not in detail and "a invoice" not in detail, detail


# ---------------------------------------------------------------------------
# a save that changed nothing says nothing
# ---------------------------------------------------------------------------


def test_no_class_and_the_class_that_stands_for_none_are_the_same(
    client, seed_accounts, db_session
):
    """A document made with no class, opened and saved untouched: the form
    preselects Uncategorized and sends its id. Nothing was changed."""
    from app.services.classes_service import UNCATEGORIZED_NAME, uncategorized_class_id

    default = {"id": uncategorized_class_id(db_session), "name": UNCATEGORIZED_NAME}
    db_session.commit()
    real = client.post("/api/classes", json={"name": "North"}).json()
    cust = _customer(client)
    lines = [{"description": "Demo", "quantity": 1, "rate": 100}]
    r = client.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "date": "2026-06-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["class_id"] is None
    _age(db_session)
    r = client.put(
        f"/api/invoices/{inv['id']}", json={"class_id": default["id"], "lines": lines}
    )
    assert r.status_code == 200, r.text
    events = _activity(client, "invoice", inv["id"])["events"]
    assert [e["kind"] for e in events] == ["created"], events
    # a real class is a change, both ways
    _age(db_session)
    r = client.put(f"/api/invoices/{inv['id']}", json={"class_id": real["id"]})
    assert r.status_code == 200, r.text
    assert _details(client, "invoice", inv["id"]) == [
        f"Class {default['name']} → North"
    ]
    _age(db_session)
    r = client.put(f"/api/invoices/{inv['id']}", json={"class_id": default["id"]})
    assert r.status_code == 200, r.text
    assert _details(client, "invoice", inv["id"]) == [
        f"Class North → {default['name']}"
    ]
