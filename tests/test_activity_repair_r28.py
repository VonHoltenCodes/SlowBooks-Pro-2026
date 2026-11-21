"""Round 28, after review: the line rule where lines share a description,
and a field the save copies onto every line.

- Lines that all carry one description (a memo on every line of a journal
  entry; every register entry) pair each with the line on its own account,
  never across accounts (B4).
- A change order saved with every line removed says so in that save's
  entry, and the next save claims only what it did (B8).
- A journal-kept document's Class, Job, memo and payee are copied onto its
  ledger lines by the save: the change is the document's, said once, the
  document's own fields first and its lines after them in every save.
- A line that keeps its class by id and again by name has one Class.
- The entry that makes a document names a line with nothing to name it by
  its place, as an edit does.
- A nonprofit's words never rewrite the address an email went to (B10).
"""

import pytest

from app.services.activity import _compare_lines
from app.services.settings_service import set_setting
from tests.test_activity_line_rule_r28 import (
    _change_order,
    _col,
    _deposit,
    _jl,
    _journal,
    _save_change_order,
    _save_journal,
)
from tests.test_co_effective_date_allowance import _setup
from tests.test_document_activity_894 import _activity, _age, _customer
from tests.test_document_activity_894 import smtp  # noqa: F401
from tests.test_replace_transaction_689 import KINDS, _post, books  # noqa: F401


def _newest(client, kind, id_):
    return _activity(client, kind, id_)["events"][0]


def _no_account_change(details):
    assert details, details
    assert not any("Account " in d for d in details), details
    assert not any(d.startswith("Line added") for d in details), details
    assert not any(d.startswith("Line removed") for d in details), details


# ---------------------------------------------------------------------------
# B4: lines that share one description
# ---------------------------------------------------------------------------


def test_lines_of_one_description_pair_with_the_line_on_their_own_account():
    before = [
        {"description": "Rent", "account_id": 1, "debit": "100.00", "credit": "0"},
        {"description": "Rent", "account_id": 2, "debit": "0", "credit": "100.00"},
    ]
    after = [
        {"description": "Rent", "account_id": 2, "debit": "0", "credit": "120.00"},
        {"description": "Rent", "account_id": 1, "debit": "120.00", "credit": "0"},
    ]
    pairs, removed, added = _compare_lines(before, after, "transaction_id")
    assert not removed and not added
    assert sorted((b["account_id"], a["account_id"]) for b, a in pairs) == [
        (1, 1),
        (2, 2),
    ]


def test_item_lines_of_one_description_pair_by_their_item_then_their_amount():
    before = [
        {"description": "Labor", "item_id": 1, "amount": "100.00", "rate": "100"},
        {"description": "Labor", "item_id": 2, "amount": "60.00", "rate": "60"},
        {"description": "Labor", "amount": "30.00", "rate": "30"},
        {"description": "Labor", "amount": "40.00", "rate": "40"},
    ]
    after = [
        {"description": "Labor", "amount": "40.00", "rate": "40", "unit": "hr"},
        {"description": "Labor", "amount": "30.00", "rate": "30", "unit": "hr"},
        {"description": "Labor", "item_id": 2, "amount": "61.00", "rate": "61"},
        {"description": "Labor", "item_id": 1, "amount": "101.00", "rate": "101"},
    ]
    pairs, removed, added = _compare_lines(before, after, "invoice_id")
    assert not removed and not added
    assert sorted((b["amount"], a["amount"]) for b, a in pairs) == [
        ("100.00", "101.00"),
        ("30.00", "30.00"),
        ("40.00", "40.00"),
        ("60.00", "61.00"),
    ]


def test_a_journal_entry_with_one_memo_on_every_line_names_no_account(
    client, seed_accounts, db_session
):
    acc = seed_accounts
    util, adv, bank = acc["6900"].id, acc["6000"].id, acc["1000"].id
    # both amounts changed, saved in the other order
    je = _journal(client, [_jl(util, 100, 0, "Rent"), _jl(bank, 0, 100, "Rent")])
    events = _save_journal(
        client, db_session, je, [_jl(bank, 0, 120, "Rent"), _jl(util, 120, 0, "Rent")]
    )
    assert events[0]["details"] == [
        "Line Rent, credit $120.00: Credit $100.00 → $120.00",
        "Line Rent, debit $120.00: Debit $100.00 → $120.00",
    ]
    # entered backwards, corrected in the other order: each line flips side
    je = _journal(client, [_jl(util, 0, 100, "Rent"), _jl(bank, 100, 0, "Rent")])
    events = _save_journal(
        client, db_session, je, [_jl(bank, 0, 100, "Rent"), _jl(util, 100, 0, "Rent")]
    )
    assert events[0]["details"] == [
        "Line Rent, credit $100.00: Debit $100.00 → Credit $100.00",
        "Line Rent, debit $100.00: Credit $100.00 → Debit $100.00",
    ]
    # one debit line removed and the rest re-figured: the line that left is
    # the one named removed, and the bank line is edited
    je = _journal(
        client,
        [_jl(util, 60, 0, "Rent"), _jl(adv, 40, 0, "Rent"), _jl(bank, 0, 100, "Rent")],
    )
    events = _save_journal(
        client, db_session, je, [_jl(util, 70, 0, "Rent"), _jl(bank, 0, 70, "Rent")]
    )
    assert events[0]["details"] == [
        "Line Rent, debit $70.00: Debit $60.00 → $70.00",
        "Line Rent, credit $70.00: Credit $100.00 → $70.00",
        "Line removed: Rent, debit $40.00",
    ]
    # and a debit line added
    events = _save_journal(
        client,
        db_session,
        je,
        [_jl(util, 60, 0, "Rent"), _jl(adv, 40, 0, "Rent"), _jl(bank, 0, 100, "Rent")],
    )
    assert events[0]["details"] == [
        "Line Rent, debit $60.00: Debit $70.00 → $60.00",
        "Line Rent, credit $100.00: Credit $70.00 → $100.00",
        "Line added: Rent, debit $40.00",
    ]
    # three lines, one unchanged, saved in another order with two re-figured
    je = _journal(
        client,
        [
            _jl(util, 100, 0, "Payroll"),
            _jl(adv, 40, 0, "Payroll"),
            _jl(bank, 0, 140, "Payroll"),
        ],
    )
    events = _save_journal(
        client,
        db_session,
        je,
        [
            _jl(bank, 0, 160, "Payroll"),
            _jl(util, 100, 0, "Payroll"),
            _jl(adv, 60, 0, "Payroll"),
        ],
    )
    assert events[0]["details"] == [
        "Line Payroll, credit $160.00: Credit $140.00 → $160.00",
        "Line Payroll, debit $60.00: Debit $40.00 → $60.00",
    ]


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


def _save_entry(client, db_session, entry, seed_accounts, **body):
    _age(db_session)
    r = client.put(
        f"/api/banking/entries/{entry}",
        json={"category_account_id": seed_accounts["6900"].id, "payee": "Ridge"} | body,
    )
    assert r.status_code == 200, r.text
    return _newest(client, "bank_entry", entry)["details"]


@pytest.mark.parametrize("memo", [None, "May fuel"])
def test_a_register_payment_changed_to_a_deposit_names_no_account(
    client, seed_accounts, db_session, memo
):
    more = {"description": memo} if memo else {}
    entry = _register_entry(client, seed_accounts, **more)
    said = _save_entry(client, db_session, entry, seed_accounts, amount="100", **more)
    name = memo or "Ridge"
    assert said == [
        f"Line {name}, debit $100.00: Credit $100.00 → Debit $100.00",
        f"Line {name}, credit $100.00: Debit $100.00 → Credit $100.00",
    ]


def test_a_register_entry_split_in_two_edits_its_bank_line(
    client, seed_accounts, db_session
):
    acc = seed_accounts
    entry = _register_entry(client, acc, description="May fuel")
    said = _save_entry(
        client,
        db_session,
        entry,
        acc,
        amount="-110",
        description="May fuel",
        splits=[
            {"category_account_id": acc["6900"].id, "amount": "-60"},
            {"category_account_id": acc["6000"].id, "amount": "-50"},
        ],
    )
    assert "Line May fuel, credit $110.00: Credit $100.00 → $110.00" in said, said
    assert "Line May fuel, debit $60.00: Debit $100.00 → $60.00" in said, said
    assert "Line added: May fuel, debit $50.00" in said, said
    assert len(said) == 3, said


# ---------------------------------------------------------------------------
# a change order saved with every line removed
# ---------------------------------------------------------------------------


def test_a_change_order_saved_with_no_lines_says_so_and_the_next_save_does_not(
    client, seed_customer, db_session
):
    co = _change_order(client, seed_customer, [_col("Demo", 100), _col("Paint", 300)])
    events = _save_change_order(client, db_session, co, [])
    assert len(events) == 2, events
    removed = [d for d in events[0]["details"] if d.startswith("Line ")]
    assert removed == ["Line removed: Demo, $100.00", "Line removed: Paint, $300.00"]
    # a save of the title alone between them changes nothing about that
    _age(db_session)
    r = client.put(f"/api/change-orders/{co['id']}", json={"title": "Porch two"})
    assert r.status_code == 200, r.text
    events = _save_change_order(client, db_session, co, [_col("Stairs", 55)])
    lines = [d for d in events[0]["details"] if d.startswith("Line ")]
    assert lines == ["Line added: Stairs, $55.00"], events[0]
    # and the earlier entry still reads as it did (B8)
    assert [
        d for d in events[-2]["details"] if d.startswith("Line ")
    ] == removed, events


# ---------------------------------------------------------------------------
# what the save copies from the document onto its ledger lines
# ---------------------------------------------------------------------------


def _classes(client):
    return [
        client.post("/api/classes", json={"name": n}).json()["id"]
        for n in ("East", "West")
    ]


def test_a_journal_entrys_class_and_job_are_said_once_before_its_lines(
    client, seed_accounts, seed_customer, db_session
):
    acc = seed_accounts
    east, _west = _classes(client)
    job, _ = _setup(client, seed_customer.id, [], 100000, name="HdrJob")

    def body(lines=None, **head):
        return {
            "date": "2026-09-02",
            "description": "Accrue",
            "lines": lines
            or [
                _jl(acc["6900"].id, 100, 0, "Fuel"),
                _jl(acc["1000"].id, 0, 100, "Paid"),
            ],
            **head,
        }

    r = client.post("/api/journal", json=body())
    assert r.status_code == 201, r.text
    je = r.json()["id"]

    def save(**head):
        _age(db_session)
        r = client.put(f"/api/journal/{je}", json=body(**head))
        assert r.status_code == 200, r.text
        return _newest(client, "journal_entry", je)["details"]

    assert save(class_id=east) == ["Class set to East"]
    assert save(class_id=east, job_id=job["id"]) == ["Job set to HdrJob"]
    # the document's own change first, its lines after it
    said = save(
        class_id=east,
        job_id=job["id"],
        date="2026-09-05",
        lines=[
            _jl(acc["6900"].id, 120, 0, "Fuel"),
            _jl(acc["1000"].id, 0, 120, "Paid"),
        ],
    )
    assert said == [
        "Date 2026-09-02 → 2026-09-05",
        "Line Fuel, debit $120.00: Debit $100.00 → $120.00",
        "Line Paid, credit $120.00: Credit $100.00 → $120.00",
    ]


def test_a_register_entrys_class_is_said_once(client, seed_accounts, db_session):
    east, west = _classes(client)
    entry = _register_entry(client, seed_accounts)
    said = _save_entry(
        client, db_session, entry, seed_accounts, amount="-100", class_id=east
    )
    assert said == ["Class set to East"]
    said = _save_entry(
        client, db_session, entry, seed_accounts, amount="-100", class_id=west
    )
    assert said == ["Class East → West"]


def test_a_deposits_class_and_job_are_never_said_on_its_bank_line(
    client, seed_accounts, seed_customer, db_session
):
    east, west = _classes(client)
    job, _ = _setup(client, seed_customer.id, [], 100000, name="HdrJob")
    dep = _deposit(
        client,
        seed_accounts,
        [{"account_id": seed_accounts["4900"].id, "amount": "20.00", "memo": "Rebate"}],
    )

    def save(**head):
        _age(db_session)
        r = client.put(f"/api/deposits/{dep}", json=head)
        assert r.status_code == 200, r.text
        return _newest(client, "deposit", dep)["details"]

    assert save(class_id=east) == ["Class set to East"]
    assert save(class_id=west, job_id=job["id"]) == [
        "Class East → West",
        "Job set to HdrJob",
    ]


def test_a_register_entrys_payee_alone_says_nothing_of_a_memo(
    client, seed_accounts, db_session
):
    # with no memo typed each line holds the payee: that is no memo
    entry = _register_entry(client, seed_accounts)
    said = _save_entry(
        client, db_session, entry, seed_accounts, amount="-100", payee="Ridge Supply"
    )
    assert said == ["Payee Ridge → Ridge Supply"]
    # a memo first typed is a memo set, and one taken off a memo cleared
    said = _save_entry(
        client,
        db_session,
        entry,
        seed_accounts,
        amount="-100",
        payee="Ridge Supply",
        description="June fuel",
    )
    assert said == ["Memo set to June fuel"]
    said = _save_entry(
        client,
        db_session,
        entry,
        seed_accounts,
        amount="-100",
        payee="Ridge Supply",
        description=None,
    )
    assert said == ["Memo cleared (was June fuel)"]


@pytest.mark.parametrize("kind", ["expense", "cc_charge"])
def test_a_corrected_copys_memo_is_said_once(
    client, books, db_session, kind  # noqa: F811
):
    payload, base, new_id, *_ = KINDS[kind]
    first = _post(client, kind, books, memo="paper")
    _age(db_session)
    r = client.post(f"{base}/{first}/replace", json=payload(books, memo="ink"))
    assert r.status_code == 200, r.text
    events = _activity(client, kind, new_id(r.json()))["events"]
    copy = next(e for e in events if e["text"].startswith("Corrected copy of"))
    assert copy["details"] == ["Memo paper → ink"], copy


# ---------------------------------------------------------------------------
# one Class on a line that keeps it by id and by name
# ---------------------------------------------------------------------------


def test_an_invoice_lines_class_sent_by_name_is_one_class(
    client, seed_accounts, db_session
):
    east, west = _classes(client)
    cust = _customer(client)
    line = {"description": "Demo", "quantity": 1, "rate": 100}
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "lines": [line | {"class_id": west}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()["id"]

    def save(**more):
        _age(db_session)
        r = client.put(f"/api/invoices/{inv}", json={"lines": [line | more]})
        assert r.status_code == 200, r.text
        return _activity(client, "invoice", inv)["events"]

    # the same class, now kept by its name: nothing changed, nothing said
    events = save(class_name="West")
    assert len(events) == 1, events
    events = save(class_name="East", class_id=east)
    assert events[0]["details"] == ["Line Demo, $100.00: Class West → East"]
    events = save(class_name="West", class_id=west)
    assert events[0]["details"] == ["Line Demo, $100.00: Class East → West"]


# ---------------------------------------------------------------------------
# the entry that makes a document names a line as an edit does
# ---------------------------------------------------------------------------


def test_a_new_documents_unnamed_lines_are_named_by_their_place(
    client, seed_accounts, db_session
):
    cust = _customer(client)
    lines = [
        {"description": "Deck", "quantity": 1, "rate": 100},
        {"description": "", "quantity": 1, "rate": 60},
        {"description": "  ", "quantity": 1, "rate": 0},
    ]
    r = client.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "date": "2026-06-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    inv = r.json()["id"]
    assert _newest(client, "invoice", inv)["details"] == [
        "Line: Deck, $100.00",
        "Line 2 ($60.00)",
        "Line 3 ($0.00)",
    ]
    _age(db_session)
    r = client.put(f"/api/invoices/{inv}", json={"lines": lines[:2]})
    assert r.status_code == 200, r.text
    assert _newest(client, "invoice", inv)["details"] == [
        "Line removed: Line 3 ($0.00)"
    ]


# ---------------------------------------------------------------------------
# B10: the address an email went to
# ---------------------------------------------------------------------------


def test_a_nonprofits_words_leave_an_emails_address_as_typed(
    client, seed_accounts, db_session, smtp  # noqa: F811
):
    cust = _customer(client)
    r = client.post(
        "/api/invoices",
        json={
            "customer_id": cust["id"],
            "date": "2026-06-01",
            "lines": [{"description": "Demo", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()["id"]
    address = "invoice.class@customer-job.test"
    r = client.post(
        f"/api/invoices/{inv}/email",
        json={"recipient": address, "subject": "Your Invoice", "message": ""},
    )
    assert r.status_code == 200, r.text
    set_setting(db_session, "company_type", "nonprofit")
    db_session.commit()
    sent = [
        e for e in _activity(client, "invoice", inv)["events"] if address in e["text"]
    ]
    assert len(sent) == 1, _activity(client, "invoice", inv)["events"]
    assert sent[0]["text"].startswith(f"Emailed to {address} by "), sent
