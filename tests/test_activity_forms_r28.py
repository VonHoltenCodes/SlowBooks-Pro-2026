"""Round 28, second review: an entry is read as its form holds it.

A register entry, an expense and a card charge each have one Payee and one
Memo on their form. The ledger keeps them in its own shapes: the payee
behind a prefix on the header ("Expense: Acme"), a placeholder for no payee
("Bank entry"), the memo on every line, and the payee on the lines when no
memo was typed. Activity reads the form (`_entry_form`), so:

- the Payee and the Memo are the entry's own fields, each said once, in
  the words typed, never the stored text or a placeholder;
- a line with no memo of its own has no description: it is named and
  paired by its account, like any line with no description (B1, B4);
- a deposit's credit to Undeposited Funds is nobody's typed line (B5): a
  payment taken or put back is said by its own entry and by the Total;
- a sales receipt is called a sales receipt and lists no stored flag (B6);
- marking an estimate lost is one entry, in the button's word;
- a line with no amount is named without "$0.00".
"""

import pytest

from app.services.activity import _entry_form, _form_changes
from app.services.entry_text import BANK_ENTRY_HEADER
from app.services.settings_service import set_setting
from tests.test_activity_line_rule_r28 import _deposit
from tests.test_co_effective_date_allowance import _setup
from tests.test_document_activity_894 import _activity, _age, _customer, _invoice
from tests.test_replace_transaction_689 import KINDS, books  # noqa: F401


def _copy_said(client, db_session, kind, first, second):
    """Make a `kind`, replace it by a corrected copy: what the copy says."""
    _, base, new_id, *_ = KINDS[kind]
    r = client.post(base, json=first)
    assert r.status_code == 201, r.text
    _age(db_session)
    r = client.post(f"{base}/{new_id(r.json())}/replace", json=second)
    assert r.status_code == 200, r.text
    events = _activity(client, kind, new_id(r.json()))["events"]
    copy = next(e for e in events if e["text"].startswith("Corrected copy of"))
    return copy["details"]


def _body(kind, books, **over):  # noqa: F811
    body = KINDS[kind][0](books) | over
    return {k: v for k, v in body.items() if v is not ...}


# who the money went to, as each kind's stock payload names it
PAYEE = {"expense": "Acme Supply", "cc_charge": "Office Depot"}


def _other_payee(client, kind, name):
    if kind == "expense":
        r = client.post("/api/vendors", json={"name": name})
        assert r.status_code == 201, r.text
        return {"vendor_id": r.json()["id"]}
    return {"payee": name}


def _accounts(kind, books):  # noqa: F811
    a = books["a"]
    return a["6400"].name, a["1000" if kind == "expense" else "2100"].name


# ---------------------------------------------------------------------------
# an expense's and a card charge's corrected copy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["expense", "cc_charge"])
def test_a_copys_memo_cleared_or_first_typed_is_said_once_and_truly(
    client, books, db_session, kind  # noqa: F811
):
    with_memo, without = _body(kind, books), _body(kind, books, memo=None)
    said = _copy_said(client, db_session, kind, with_memo, without)
    assert said == ["Memo cleared (was paper)"], said
    said = _copy_said(client, db_session, kind, without, with_memo)
    assert said == ["Memo set to paper"], said


@pytest.mark.parametrize("kind", ["expense", "cc_charge"])
@pytest.mark.parametrize("memo", [None, "paper"])
def test_a_copys_payee_reads_as_typed_and_is_no_memo(
    client, books, db_session, kind, memo  # noqa: F811
):
    was = PAYEE[kind]
    first = _body(kind, books, memo=memo)
    second = first | _other_payee(client, kind, "Zenith Tools")
    said = _copy_said(client, db_session, kind, first, second)
    assert said == [f"Payee {was} → Zenith Tools"], said


def test_a_card_charges_payee_cleared_names_no_stored_text(
    client, books, db_session  # noqa: F811
):
    first = _body("cc_charge", books)
    said = _copy_said(client, db_session, "cc_charge", first, first | {"payee": None})
    assert said == ["Payee cleared (was Office Depot)"], said


@pytest.mark.parametrize("kind", ["expense", "cc_charge"])
def test_a_copys_payee_and_amount_with_no_memo_edit_each_line(
    client, books, db_session, kind  # noqa: F811
):
    first = _body(kind, books, memo=None)
    second = first | _other_payee(client, kind, "Zenith Tools") | {"amount": "120.00"}
    said = _copy_said(client, db_session, kind, first, second)
    expense, paid_from = _accounts(kind, books)
    assert said == [
        f"Payee {PAYEE[kind]} → Zenith Tools",
        f"Line {expense}, debit $120.00: Debit $100.00 → $120.00",
        f"Line {paid_from}, credit $120.00: Credit $100.00 → $120.00",
    ], said


@pytest.mark.parametrize("memo", [None, "paper"])
def test_a_split_expense_lines_own_memo_cleared_is_cleared(
    client, books, db_session, memo  # noqa: F811
):
    a = books["a"]

    def body(first_memo):
        lines = [
            {"account_id": a["6000"].id, "amount": "45"}
            | ({"description": first_memo} if first_memo else {}),
            {"account_id": a["6400"].id, "amount": "60", "description": "Paper"},
        ]
        return _body(
            "expense", books, memo=memo, expense_account_id=..., amount=..., lines=lines
        )

    said = _copy_said(client, db_session, "expense", body("Posters"), body(None))
    assert said == [
        f"Line {a['6000'].name}, debit $45.00: Memo cleared (was Posters)"
    ], said


# ---------------------------------------------------------------------------
# a register entry
# ---------------------------------------------------------------------------


def _entry(client, a, **more):
    body = {
        "account_id": a["1000"].id,
        "date": "2026-06-01",
        "amount": "-100",
        "payee": "Ridge",
        "category_account_id": a["6900"].id,
    } | more
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _save(client, db_session, entry, **body):
    _age(db_session)
    r = client.put(f"/api/banking/entries/{entry}", json=body)
    assert r.status_code == 200, r.text
    return _activity(client, "bank_entry", entry)["events"][0]["details"]


def test_a_register_entrys_payee_and_amount_with_no_memo_edit_each_line(
    client, seed_accounts, db_session
):
    a = seed_accounts
    entry = _entry(client, a)
    made = _activity(client, "bank_entry", entry)["events"][-1]["details"]
    assert made == [
        f"Line: {a['6900'].name}, debit $100.00",
        f"Line: {a['1000'].name}, credit $100.00",
    ], made
    said = _save(
        client,
        db_session,
        entry,
        category_account_id=a["6900"].id,
        payee="Ridge Supply",
        amount="-120",
    )
    assert said == [
        "Payee Ridge → Ridge Supply",
        f"Line {a['6900'].name}, debit $120.00: Debit $100.00 → $120.00",
        f"Line {a['1000'].name}, credit $120.00: Credit $100.00 → $120.00",
    ], said


@pytest.mark.parametrize("memo", [None, "May fuel"])
def test_a_register_splits_lines_are_each_named_by_their_own_account(
    client, seed_accounts, db_session, memo
):
    a = seed_accounts
    more = {"description": memo} if memo else {}

    def splits(first, second):
        return [
            {"category_account_id": a["6900"].id, "amount": first},
            {"category_account_id": a["6000"].id, "amount": second},
        ]

    entry = _entry(
        client,
        a,
        amount="-60",
        category_account_id=None,
        splits=splits("-30", "-30"),
        **more,
    )
    said = _save(
        client,
        db_session,
        entry,
        payee="Ridge",
        amount="-70",
        splits=splits("-35", "-35"),
        **more,
    )
    assert sorted(said) == sorted(
        [
            f"Line {a['6900'].name}, debit $35.00: Debit $30.00 → $35.00",
            f"Line {a['6000'].name}, debit $35.00: Debit $30.00 → $35.00",
            f"Line {a['1000'].name}, credit $70.00: Credit $60.00 → $70.00",
        ]
    ), said


def test_a_register_entrys_payee_cleared_never_shows_the_placeholder(
    client, seed_accounts, db_session
):
    a = seed_accounts
    entry = _entry(client, a)
    keep = {"category_account_id": a["6900"].id, "amount": "-100"}
    said = _save(client, db_session, entry, payee="", **keep)
    assert said == ["Payee cleared (was Ridge)"], said
    said = _save(client, db_session, entry, payee="Ridge", **keep)
    assert said == ["Payee set to Ridge"], said
    events = _activity(client, "bank_entry", entry)["events"]
    assert BANK_ENTRY_HEADER not in str(events), events


def test_the_form_of_an_entry_from_what_the_ledger_keeps():
    lines = [
        {"account_id": 1, "debit": "45", "description": "Posters"},
        {"account_id": 2, "debit": "60", "description": "Acme"},
        {"account_id": 3, "credit": "105", "description": "Acme"},
    ]
    form, own = _entry_form("expense", {"description": "Expense: Acme"}, lines)
    assert form == {"payee": "Acme", "memo": ""}
    assert [v["description"] for v in own] == ["Posters", "", ""]
    # an expense keeps its memo in its own column: "" is no memo
    head = {"description": "Expense: Acme", "memo": "Acme"}
    assert _entry_form("expense", head, lines)[0] == {"payee": "Acme", "memo": "Acme"}
    for source, text in (
        ("expense", "Expense"),
        ("cc_charge", "Credit Card Charge"),
        ("bank_entry", BANK_ENTRY_HEADER),
    ):
        memo = [{"account_id": 1, "debit": "5", "description": "Fuel"}]
        form, own = _entry_form(source, {"description": text}, memo)
        assert form == {"payee": "", "memo": "Fuel"}, source
        assert own[0]["description"] == ""
    assert _form_changes({"payee": "A", "memo": ""}, {"payee": "A", "memo": ""}) == []


# ---------------------------------------------------------------------------
# a deposit that holds payments
# ---------------------------------------------------------------------------


def test_a_payment_taken_or_put_back_names_no_ledger_line(
    client, seed_accounts, db_session
):
    a = seed_accounts
    cust = _customer(client)
    for amount in ("100.00", "40.00"):
        inv = _invoice(client, cust["id"])
        r = client.post(
            "/api/payments",
            json={
                "customer_id": cust["id"],
                "date": "2026-06-05",
                "amount": amount,
                "method": "Check",
                "allocations": [{"invoice_id": inv["id"], "amount": amount}],
            },
        )
        assert r.status_code == 201, r.text
    held = [
        p["transaction_line_id"] for p in client.get("/api/deposits/pending").json()
    ]
    rebate = {"account_id": a["4900"].id, "amount": "10.00", "memo": "Rebate"}
    dep = _deposit(client, a, [rebate], line_ids=held[:1])
    bank = a["1000"].name
    made = next(
        e for e in _activity(client, "deposit", dep)["events"] if e["kind"] == "created"
    )
    assert len(made["details"]) == 2, made
    assert made["details"][1] == "Line: Rebate, $10.00", made
    assert made["details"][0].startswith(f"Line: Deposit to {bank}, debit $"), made

    def save(line_ids, amount):
        before = len(_activity(client, "deposit", dep)["events"])
        _age(db_session)
        r = client.put(
            f"/api/deposits/{dep}",
            json={"line_ids": line_ids, "other_lines": [rebate | {"amount": amount}]},
        )
        assert r.status_code == 200, r.text
        events = _activity(client, "deposit", dep)["events"]
        return events[: len(events) - before]

    new = save(held, "10.00")
    assert [e["kind"] for e in new] == ["deposit", "edited"] or [
        e["kind"] for e in new
    ] == ["edited", "deposit"], new
    edit = next(e for e in new if e["kind"] == "edited")
    assert len(edit["details"]) == 1 and edit["details"][0].startswith("Total $"), edit
    new = save(held[1:], "12.00")
    edit = next(e for e in new if e["kind"] == "edited")
    assert edit["details"][0] == "Line Rebate, $12.00: Amount $10.00 → $12.00", edit
    assert len(edit["details"]) == 2 and edit["details"][1].startswith("Total $"), edit
    assert not any(
        "Deposit to" in d or "Credit" in d
        for e in _activity(client, "deposit", dep)["events"]
        if e["kind"] == "edited"
        for d in e["details"]
    )


# ---------------------------------------------------------------------------
# a sales receipt; an estimate marked lost; a line with no amount
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("asked", ["sales_receipt", "invoice"])
@pytest.mark.parametrize("nonprofit", [False, True])
def test_a_sales_receipt_is_called_one_and_lists_no_stored_flag(
    client, seed_accounts, seed_customer, db_session, asked, nonprofit
):
    if nonprofit:
        set_setting(db_session, "company_type", "nonprofit")
        db_session.commit()
    r = client.post(
        "/api/sales-receipts",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-02",
            "method": "Cash",
            "lines": [{"description": "Demo", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    inv = r.json()["invoice"]
    events = _activity(client, asked, inv["id"])["events"]
    made = next(e for e in events if e["kind"] == "created")
    word = "Donation" if nonprofit else "Sales Receipt"
    assert made["text"].startswith(f"{word} {inv['invoice_number']} created by"), made
    assert made["details"] == ["Line: Demo, $100.00"], made
    assert "sales receipt No" not in str(events), events


@pytest.mark.parametrize("reason", ["Price", None])
def test_marking_an_estimate_lost_is_one_entry_in_the_buttons_word(
    client, seed_customer, db_session, reason
):
    job, _ = _setup(client, seed_customer.id, [], 100000)
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": seed_customer.id,
            "job_id": job["id"],
            "date": "2026-06-01",
            "notes": "Call back in May",
            "lines": [{"description": "Deck", "quantity": 1, "rate": 100}],
        },
    )
    assert r.status_code == 201, r.text
    est = r.json()["id"]
    _age(db_session)
    r = client.post(
        f"/api/estimates/{est}/lost", **({"json": {"reason": reason}} if reason else {})
    )
    assert r.status_code == 200, r.text
    events = _activity(client, "estimate", est)["events"]
    assert [e["kind"] for e in events] == ["status", "created"], events
    text = events[0]["text"]
    assert text.startswith("Marked lost by "), text
    assert text.endswith(": Price." if reason else "."), text
    assert events[0]["details"] == [], events[0]
    assert "rejected" not in str(events) and "Notes" not in str(events), events
    # a note typed later is still an edit of the notes
    _age(db_session)
    r = client.put(f"/api/estimates/{est}", json={"notes": "Ask again"})
    assert r.status_code == 200, r.text
    newest = _activity(client, "estimate", est)["events"][0]
    assert newest["kind"] == "edited", newest
    assert any(d.startswith("Notes ") for d in newest["details"]), newest


def test_a_line_with_no_amount_is_named_without_one(client, seed_accounts, db_session):
    cust = _customer(client)
    priced = {"description": "Demo", "quantity": 1, "rate": 100}
    note = {"description": "Note only", "quantity": 1, "rate": 0}
    inv = _invoice(client, cust["id"], lines=[priced, note])["id"]
    made = _activity(client, "invoice", inv)["events"][-1]
    assert made["details"] == ["Line: Demo, $100.00", "Line: Note only"], made

    def save(lines):
        _age(db_session)
        r = client.put(f"/api/invoices/{inv}", json={"lines": lines})
        assert r.status_code == 200, r.text
        return _activity(client, "invoice", inv)["events"][0]["details"]

    assert save([priced]) == ["Line removed: Note only"]
    assert save([priced, note]) == ["Line added: Note only"]
    # a price put on it is a real change of real amounts, and is said
    said = save([priced, note | {"rate": 25}])
    assert (
        said[0] == "Line Note only, $25.00: Rate $0.00 → $25.00; Amount $0.00 → $25.00"
    )
