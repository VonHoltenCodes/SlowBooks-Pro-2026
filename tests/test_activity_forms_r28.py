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

Third review: the reading is the record's own, not the asked type's.

- the Journal view asks for every posting as a journal entry: a register
  entry, an expense, a card charge and a deposit read there exactly as
  they do in their own view;
- the vendor a register entry is linked to follows its one Payee: said
  once, as the Payee, and never as a field of the entry that made it;
- the reason an estimate was marked lost for is what that save added to
  the notes, whatever the notes already held;
- a memo of spaces is no memo, on the entry and on its lines;
- an entry whose making is not in the audit log starts as the table has
  it with the log's edits undone: its payee is never read as a memo.
"""

import pytest

from app.models.audit import AuditLog
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


# ---------------------------------------------------------------------------
# third review: the reading is the record's own, whichever type is asked
# ---------------------------------------------------------------------------


def _read(client, type_, id_):
    return [
        (e["kind"], e["text"], e["details"])
        for e in _activity(client, type_, id_)["events"]
    ]


def _same_in_the_journal_view(client, kind, id_):
    own = _read(client, kind, id_)
    assert _read(client, "journal_entry", id_) == own, (kind, own)
    stored = ("Expense: ", "CC Charge: ", "Credit Card Charge", BANK_ENTRY_HEADER)
    assert not any(word in str(own) for word in stored), own
    assert "Description" not in str(own) and "ournal entry" not in str(own), own
    return own


def test_a_register_entry_reads_the_same_in_the_journal_view(
    client, seed_accounts, db_session
):
    a = seed_accounts
    entry = _entry(client, a, check_number="501")
    keep = {"category_account_id": a["6900"].id}
    _save(client, db_session, entry, payee="Ridge Supply", amount="-120", **keep)
    _save(client, db_session, entry, payee="", amount="-120", **keep)
    _save(
        client, db_session, entry, payee="", amount="-120", description="Fuel", **keep
    )
    own = _same_in_the_journal_view(client, "bank_entry", entry)
    assert [details for _, _, details in own] == [
        ["Memo set to Fuel"],
        ["Payee cleared (was Ridge Supply)"],
        [
            "Payee Ridge → Ridge Supply",
            f"Line {a['6900'].name}, debit $120.00: Debit $100.00 → $120.00",
            f"Line {a['1000'].name}, credit $120.00: Credit $100.00 → $120.00",
        ],
        [
            f"Line: {a['6900'].name}, debit $100.00",
            f"Line: {a['1000'].name}, credit $100.00",
        ],
    ], own
    assert own[-1][1].startswith("Register Entry 501 created by "), own


@pytest.mark.parametrize("kind", ["expense", "cc_charge"])
def test_a_corrected_copy_reads_the_same_in_the_journal_view(
    client, books, db_session, kind  # noqa: F811
):
    _, base, new_id, *_ = KINDS[kind]
    first = _body(kind, books)
    second = (
        _body(kind, books, memo=None)
        | _other_payee(client, kind, "Zenith Tools")
        | {"amount": "120.00"}
    )
    r = client.post(base, json=first)
    assert r.status_code == 201, r.text
    old = new_id(r.json())
    _age(db_session)
    r = client.post(f"{base}/{old}/replace", json=second)
    assert r.status_code == 200, r.text
    new = new_id(r.json())
    _same_in_the_journal_view(client, kind, old)
    own = _same_in_the_journal_view(client, kind, new)
    expense, paid_from = _accounts(kind, books)
    copy = next(e for e in own if e[0] == "replaces")
    word = "expense" if kind == "expense" else "card charge"
    assert copy[1].startswith("Corrected copy of "), copy
    assert "journal" not in copy[1] and (word in copy[1] or "earlier" not in copy[1])
    assert copy[2] == [
        f"Payee {PAYEE[kind]} → Zenith Tools",
        "Memo cleared (was paper)",
        f"Line {expense}, debit $120.00: Debit $100.00 → $120.00",
        f"Line {paid_from}, credit $120.00: Credit $100.00 → $120.00",
    ], copy


def test_a_deposit_reads_the_same_in_the_journal_view(
    client, seed_accounts, db_session
):
    a = seed_accounts
    cust = _customer(client)
    inv = _invoice(client, cust["id"])
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
    held = [
        p["transaction_line_id"] for p in client.get("/api/deposits/pending").json()
    ]
    rebate = {"account_id": a["4900"].id, "amount": "10.00", "memo": "Rebate"}
    dep = _deposit(client, a, [rebate])
    for body in (
        {"other_lines": [rebate | {"amount": "15.00"}]},
        {"line_ids": held},
    ):
        _age(db_session)
        r = client.put(f"/api/deposits/{dep}", json=body)
        assert r.status_code == 200, r.text
    own = _read(client, "deposit", dep)
    assert _read(client, "journal_entry", dep) == own, own
    assert any(text.startswith("Took payment of $100.00") for _, text, _ in own), own
    edits = [details for kind, _, details in own if kind == "edited"]
    assert ["Line Rebate, $15.00: Amount $10.00 → $15.00", "Total $10.00 → $15.00"] in (
        edits
    ), own
    assert not any("Deposit to" in d or "Credit" in d for e in edits for d in e), own


def test_a_true_journal_entry_is_still_a_journal_entry(
    client, seed_accounts, db_session
):
    a = seed_accounts
    r = client.post(
        "/api/journal",
        json={
            "date": "2026-06-01",
            "description": "Accrue rent",
            "lines": [
                {"account_id": a["6900"].id, "debit": "50", "credit": "0"},
                {"account_id": a["1000"].id, "debit": "0", "credit": "50"},
            ],
        },
    )
    assert r.status_code in (200, 201), r.text
    made = _activity(client, "journal_entry", r.json()["id"])["events"][-1]
    assert made["text"].startswith("Journal Entry"), made


def _vendor(client, name):
    r = client.post("/api/vendors", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_a_register_entrys_vendor_is_its_payee_said_once(
    client, seed_accounts, db_session
):
    a = seed_accounts
    _vendor(client, "Acme Supply")
    _vendor(client, "Zenith Tools")
    entry = _entry(client, a, payee="Acme Supply")
    made = _activity(client, "bank_entry", entry)["events"]
    assert len(made) == 1 and made[0]["details"] == [
        f"Line: {a['6900'].name}, debit $100.00",
        f"Line: {a['1000'].name}, credit $100.00",
    ], made
    keep = {"category_account_id": a["6900"].id, "amount": "-100"}
    for payee, want in (
        ("Zenith Tools", "Payee Acme Supply → Zenith Tools"),
        ("Corner Store", "Payee Zenith Tools → Corner Store"),
        ("Acme Supply", "Payee Corner Store → Acme Supply"),
        ("", "Payee cleared (was Acme Supply)"),
    ):
        said = _save(client, db_session, entry, payee=payee, **keep)
        assert said == [want], (payee, said)
    assert "Vendor" not in str(_read(client, "bank_entry", entry))
    assert "Vendor" not in str(_read(client, "journal_entry", entry))


def test_a_vendor_linked_with_the_payee_untouched_is_still_said(
    client, seed_accounts, db_session
):
    # only the API can do this: the form's vendor is its payee
    a = seed_accounts
    acme = _vendor(client, "Acme Supply")
    entry = _entry(client, a)
    said = _save(client, db_session, entry, vendor_id=acme)
    assert said == ["Vendor set to Acme Supply"], said


def _lost_sentences(client, est):
    return [
        e["text"]
        for e in _activity(client, "estimate", est)["events"]
        if e["text"].startswith("Marked lost")
    ]


def _estimate(client, customer_id, **more):
    r = client.post(
        "/api/estimates",
        json={
            "customer_id": customer_id,
            "date": "2026-06-01",
            "lines": [{"description": "Deck", "quantity": 1, "rate": 100}],
        }
        | more,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _lose(client, db_session, est, reason):
    _age(db_session)
    r = client.post(f"/api/estimates/{est}/lost", json={"reason": reason})
    assert r.status_code == 200, r.text


def test_an_estimate_lost_twice_gives_each_time_its_own_reason(
    client, seed_customer, db_session
):
    est = _estimate(client, seed_customer.id)
    _lose(client, db_session, est, "Price")
    _age(db_session)
    r = client.put(f"/api/estimates/{est}", json={"status": "pending"})
    assert r.status_code == 200, r.text
    _lose(client, db_session, est, "Timing")
    said = _lost_sentences(client, est)
    assert [s.split(": ", 1)[1] for s in said] == ["Timing.", "Price."], said
    assert "Lost 20" not in str(_activity(client, "estimate", est)["events"])


def test_a_lost_reason_is_not_the_note_that_was_already_there(
    client, seed_customer, db_session
):
    est = _estimate(client, seed_customer.id, notes="Lost 2025-01-01: first time")
    _lose(client, db_session, est, "Price")
    (said,) = _lost_sentences(client, est)
    assert said.endswith(": Price.") and "first time" not in said, said


@pytest.mark.parametrize("memo", [None, "Fuel"])
def test_a_register_memo_of_spaces_is_no_memo_and_names_no_line(
    client, seed_accounts, db_session, memo
):
    a = seed_accounts
    entry = _entry(client, a, **({"description": memo} if memo else {}))
    keep = {"category_account_id": a["6900"].id, "amount": "-100", "payee": "Ridge"}
    _save(client, db_session, entry, description="   ", **keep)
    # the entry that made it, and before it only a memo that was there
    edits = [details for _, _, details in _read(client, "bank_entry", entry)[:-1]]
    assert edits == ([["Memo cleared (was Fuel)"]] if memo else []), edits


def test_the_form_of_an_entry_with_a_blank_memo_or_a_linked_vendor():
    lines = [
        {"account_id": 1, "debit": "5", "description": "   "},
        {"account_id": 2, "credit": "5", "description": "   "},
    ]
    form, own = _entry_form("bank_entry", {"description": "Ridge"}, lines)
    assert form == {"payee": "Ridge", "memo": ""}
    assert [v["description"] for v in own] == ["", ""]
    head = {"description": BANK_ENTRY_HEADER}
    assert _entry_form("bank_entry", head, [], "Acme Supply")[0]["payee"] == (
        "Acme Supply"
    )
    assert _entry_form("bank_entry", {"description": "Ridge"}, [], "Acme Supply")[0][
        "payee"
    ] == ("Ridge")


def test_an_entry_made_before_the_log_never_reads_its_payee_as_a_memo(
    client, seed_accounts, db_session
):
    a = seed_accounts
    entry = _entry(client, a)
    db_session.query(AuditLog).filter(
        AuditLog.action == "INSERT",
        AuditLog.table_name.in_(["transactions", "transaction_lines"]),
    ).delete(synchronize_session=False)
    db_session.commit()
    keep = {"category_account_id": a["6900"].id, "payee": "Ridge"}
    said = _save(client, db_session, entry, description="Fuel", amount="-100", **keep)
    assert said == ["Memo set to Fuel"], said
    first = _read(client, "bank_entry", entry)
    _save(client, db_session, entry, description="Fuel", amount="-120", **keep)
    said = _save(
        client,
        db_session,
        entry,
        description="Fuel",
        amount="-120",
        category_account_id=a["6900"].id,
        payee="Ridge Supply",
    )
    assert said == ["Payee Ridge → Ridge Supply"], said
    # an entry, once shown, reads the same after later saves (B8)
    assert _read(client, "bank_entry", entry)[-len(first) :] == first
