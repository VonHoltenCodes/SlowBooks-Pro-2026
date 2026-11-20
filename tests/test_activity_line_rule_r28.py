"""Round 28: one rule for how Activity compares a document's lines.

The lines before a save and the lines after it are paired in passes (by
id where the form keeps ids, then identical, the same description, the
same item or account, the same amount), and what is left over was removed
or added. The rule is `app.services.activity._compare_lines`; these are
the cases it names.

W-235: a line that kept its amount but changed its description and its
quantity read as removed and added, because the amount pass also asked
for the same quantity.

W-236: a journal entry's lines with no descriptions all "had the same
description", so the first leftover line paired with the first new one,
across accounts.

W-237: a vendor credit's Edit voids it and posts a corrected copy. The old
credit read "Replaced by a corrected copy" twice, and the new one showed
the raw detail "Replaces set to 1" and never said what changed.
"""

import pytest

from app.services.activity import _compare_lines
from tests.test_activity_line_changes_w215 import _line_details, _newest
from tests.test_co_effective_date_allowance import _setup
from tests.test_document_activity_894 import _activity, _age, _customer
from tests.test_replace_transaction_689 import KINDS, _post, books  # noqa: F401


def _line(description, rate, quantity=1, **more):
    return {"description": description, "quantity": quantity, "rate": rate, **more}


def _invoice(client, lines):
    cust = _customer(client)
    r = client.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "date": "2026-06-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _save_invoice(client, db_session, inv, lines):
    _age(db_session)
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": lines})
    assert r.status_code == 200, r.text
    return _newest(client, "invoice", inv["id"])


# ---------------------------------------------------------------------------
# the rule itself, on plain lists
# ---------------------------------------------------------------------------


def _l(description=None, amount=None, **more):
    return {"description": description, "amount": amount, **more}


def _names(result):
    pairs, removed, added = result
    return (
        [(b.get("description"), a.get("description")) for b, a in pairs],
        [b.get("description") for b in removed],
        [a.get("description") for a in added],
    )


def test_the_passes_run_in_order_and_a_paired_line_is_out_of_the_later_ones():
    before = [
        _l("Demo", 100, quantity=1),
        _l("Haul away", 60, quantity=1),
        _l("Patch", 50, quantity=1),
        _l("Paint", 75, quantity=1),
    ]
    after = [
        _l("Paint", 75, quantity=1),  # identical, moved to the top
        _l("Demo", 120, quantity=1),  # the same description
        _l("Haul off", 60, quantity=2),  # the same amount
        _l("Sweep up", 40, quantity=1),  # nothing in common with Patch
    ]
    # the pairs come out in the order of the passes that made them
    assert _names(_compare_lines(before, after, "doc_id")) == (
        [("Paint", "Paint"), ("Demo", "Demo"), ("Haul away", "Haul off")],
        ["Patch"],
        ["Sweep up"],
    )


def test_an_identical_line_is_taken_before_one_that_only_shares_its_description():
    before = [_l("Patch", 50), _l("Patch", 60)]
    after = [_l("Patch", 60), _l("Patch", 55)]
    pairs, removed, added = _compare_lines(before, after, "doc_id")
    assert [(b["amount"], a["amount"]) for b, a in pairs] == [(60, 60), (50, 55)]
    assert not removed and not added


def test_lines_with_no_description_never_pair_as_the_same_description():
    before = [_l(None, 60, item_id=1), _l("", 160, item_id=2)]
    after = [_l("", 140, item_id=2), _l(None, 40, item_id=1)]
    pairs, removed, added = _compare_lines(before, after, "doc_id")
    assert [(b["item_id"], a["item_id"]) for b, a in pairs] == [(1, 1), (2, 2)]
    assert not removed and not added


def test_lines_with_nothing_to_name_them_pair_with_each_other_in_order():
    before = [_l("Demo", 100), _l(None, 200), _l(None, 300)]
    after = [_l(None, 250), _l(None, 350), _l("Demo", 100)]
    pairs, removed, added = _compare_lines(before, after, "doc_id")
    assert [(b["amount"], a["amount"]) for b, a in pairs] == [
        (100, 100),
        (200, 250),
        (300, 350),
    ]
    assert not removed and not added
    # a described line is never taken for one with no description
    pairs, removed, added = _compare_lines([_l("Demo", 100)], [_l(None, 40)], "doc_id")
    assert not pairs and len(removed) == 1 and len(added) == 1


def test_a_debit_and_a_credit_of_one_figure_are_not_the_same_amount():
    before = [{"account_id": 1, "debit": 60, "credit": 0}]
    after = [{"account_id": 2, "debit": 0, "credit": 60}]
    pairs, removed, added = _compare_lines(before, after, "transaction_id")
    assert not pairs and len(removed) == 1 and len(added) == 1


def test_two_described_lines_on_one_item_that_share_nothing_else_stay_apart():
    # the stated consequence: no ids, a new description and a new amount
    before = [_l("Haul away", 60, item_id=7)]
    after = [_l("Sweep up", 40, item_id=7)]
    assert _names(_compare_lines(before, after, "doc_id")) == (
        [],
        ["Haul away"],
        ["Sweep up"],
    )


def test_where_ids_are_kept_only_the_id_and_the_identical_passes_run():
    kept = {"description": "Hinges", "amount": 30, "_row": 1}
    before = [kept, {"description": "Pulls", "amount": 45, "_row": 2}]
    after = [
        {"description": "Hinges", "amount": 90, "_row": 1},
        {"description": "Pulls", "amount": 50, "_row": 3},
    ]
    pairs, removed, added = _compare_lines(before, after, "doc_id", keeps_ids=True)
    assert [(b["amount"], a["amount"]) for b, a in pairs] == [(30, 90)]
    assert [b["amount"] for b in removed] == [45]
    assert [a["amount"] for a in added] == [50]


# ---------------------------------------------------------------------------
# W-235: the same amount is an edit, whatever else changed
# ---------------------------------------------------------------------------


def test_a_line_that_keeps_its_amount_is_edited_whatever_else_changed(
    client, seed_accounts, db_session
):
    inv = _invoice(client, [_line("Demo", 100), _line("Haul away", 60)])
    event = _save_invoice(
        client, db_session, inv, [_line("Demo", 100), _line("Haul off", 30, 2)]
    )
    assert _line_details(event) == [
        "Line Haul off, $60.00: Description Haul away → Haul off; "
        "Quantity 1 → 2; Rate $60.00 → $30.00"
    ], event
    # the amount did not move, so neither did the totals
    assert event["details"] == _line_details(event)


def test_the_same_amount_edit_reads_the_same_on_an_estimate_and_a_bill(
    client, seed_accounts, db_session
):
    want = [
        "Line Haul off, $60.00: Description Haul away → Haul off; "
        "Quantity 1 → 2; Rate $60.00 → $30.00"
    ]
    cust = _customer(client)
    lines = [_line("Demo", 100), _line("Haul away", 60)]
    new = [_line("Demo", 100), _line("Haul off", 30, 2)]
    r = client.post(
        "/api/estimates",
        json={"customer_id": cust["id"], "date": "2026-06-01", "lines": lines},
    )
    assert r.status_code == 201, r.text
    est = r.json()
    _age(db_session)
    r = client.put(f"/api/estimates/{est['id']}", json={"lines": new})
    assert r.status_code == 200, r.text
    assert _line_details(_newest(client, "estimate", est["id"])) == want

    vendor = client.post("/api/vendors", json={"name": "Hardware Supply"}).json()
    acct = seed_accounts["6000"].id
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-28",
            "date": "2026-06-01",
            "lines": [ln | {"account_id": acct} for ln in lines],
        },
    )
    assert r.status_code == 201, r.text
    bill = r.json()
    _age(db_session)
    r = client.put(
        f"/api/bills/{bill['id']}",
        json={"lines": [ln | {"account_id": acct} for ln in new]},
    )
    assert r.status_code == 200, r.text
    assert _line_details(_newest(client, "bill", bill["id"])) == want


def test_a_change_order_line_that_keeps_its_cost_is_edited(
    client, seed_customer, db_session
):
    job, _sov = _setup(client, seed_customer.id, [], 100000)
    lines = [
        {"description": "Demo", "cost_type": "labor", "quantity": 1, "unit_cost": 100},
        {
            "description": "Haul away",
            "cost_type": "labor",
            "quantity": 1,
            "unit_cost": 60,
        },
    ]
    r = client.post(
        "/api/change-orders",
        json={"job_id": job["id"], "kind": "owner", "title": "Porch", "lines": lines},
    )
    assert r.status_code == 201, r.text
    co = r.json()
    _age(db_session)
    new = [lines[0], lines[1] | {"description": "Haul off", "markup_pct": 10}]
    r = client.put(f"/api/change-orders/{co['id']}", json={"lines": new})
    assert r.status_code == 200, r.text
    assert _line_details(_newest(client, "change_order", co["id"])) == [
        "Line Haul off, $60.00: Description Haul away → Haul off; "
        "Markup % set to 10%; Sell amount $60.00 → $66.00"
    ]


def test_a_new_description_and_a_new_amount_read_as_removed_and_added(
    client, seed_accounts, db_session
):
    inv = _invoice(client, [_line("Demo", 100), _line("Haul away", 60)])
    event = _save_invoice(
        client, db_session, inv, [_line("Demo", 100), _line("Sweep up", 40)]
    )
    assert _line_details(event) == [
        "Line added: Sweep up, $40.00",
        "Line removed: Haul away, $60.00",
    ]


def test_the_lines_come_before_the_subtotal_and_total(
    client, seed_accounts, db_session
):
    inv = _invoice(client, [_line("Demo", 100), _line("Patch", 50)])
    event = _save_invoice(
        client, db_session, inv, [_line("Patch", 50, 3), _line("Demo", 100)]
    )
    assert event["details"][0] == (
        "Line Patch, $150.00: Quantity 1 → 3; Amount $50.00 → $150.00"
    ), event
    rest = event["details"][1:]
    assert rest and all(d.startswith(("Subtotal", "Total")) for d in rest), event
    assert not any("$0.00" in d or "None" in d for d in event["details"])


def test_a_line_with_no_description_is_named_by_its_item_then_by_its_place(
    client, seed_accounts, db_session
):
    r = client.post(
        "/api/items",
        json={
            "name": "Hinge <b>set</b>",
            "item_type": "service",
            "rate": 20,
            "income_account_id": seed_accounts["4000"].id,
        },
    )
    assert r.status_code in (200, 201), r.text
    item = r.json()
    bare = {"quantity": 1, "rate": 200}
    of_item = {"item_id": item["id"], "quantity": 1, "rate": 20}
    inv = _invoice(client, [_line("Demo", 100), bare, of_item])
    event = _save_invoice(
        client,
        db_session,
        inv,
        [_line("Demo", 100), bare | {"quantity": 2}, of_item | {"quantity": 3}],
    )
    said = _line_details(event)
    assert said == [
        "Line 2 ($400.00): Quantity 1 → 2; Amount $200.00 → $400.00",
        # the item's name as typed: the panel escapes it when it draws it
        "Line Hinge <b>set</b>, $60.00: Quantity 1 → 3; Amount $20.00 → $60.00",
    ], said
    # removed and added lines with no name of their own say their place too
    event = _save_invoice(
        client,
        db_session,
        inv,
        [_line("Demo", 100), bare | {"quantity": 2}, bare, of_item | {"quantity": 3}],
    )
    assert _line_details(event) == ["Line added: Line 3 ($200.00)"], event
    event = _save_invoice(
        client, db_session, inv, [_line("Demo", 100), bare | {"quantity": 2}]
    )
    assert _line_details(event) == [
        "Line removed: Line 3 ($200.00)",
        "Line removed: Hinge <b>set</b>, $60.00",
    ], event
    assert not any("#" in d or "None" in d for d in event["details"]), event


# ---------------------------------------------------------------------------
# W-236: journal entry lines with no descriptions
# ---------------------------------------------------------------------------


def _jl(account, debit=0, credit=0, description=None):
    line = {"account_id": account, "debit": debit, "credit": credit}
    if description is not None:
        line["description"] = description
    return line


def _journal(client, lines):
    body = {"date": "2026-09-02", "description": "Accrue", "lines": lines}
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _save_journal(client, db_session, je, lines):
    _age(db_session)
    body = {"date": "2026-09-02", "description": "Accrue", "lines": lines}
    r = client.put(f"/api/journal/{je['id']}", json=body)
    assert r.status_code == 200, r.text
    return _activity(client, "journal_entry", je["id"])["events"]


def test_a_journal_entry_with_no_descriptions_pairs_within_each_account(
    client, seed_accounts, db_session
):
    exp, bank = seed_accounts["6900"], seed_accounts["1000"]
    je = _journal(
        client,
        [_jl(exp.id, debit=100), _jl(exp.id, debit=60), _jl(bank.id, credit=160)],
    )
    events = _save_journal(
        client,
        db_session,
        je,
        [_jl(bank.id, credit=140), _jl(exp.id, debit=100), _jl(exp.id, debit=40)],
    )
    said = _line_details(events[0])
    assert said == [
        f"Line {bank.name}, credit $140.00: Credit $160.00 → $140.00",
        f"Line {exp.name}, debit $40.00: Debit $60.00 → $40.00",
    ], said
    assert not any("Account" in d for d in said), said


def test_a_journal_entry_reorder_alone_writes_no_entry(
    client, seed_accounts, db_session
):
    exp, bank = seed_accounts["6900"], seed_accounts["1000"]
    lines = [_jl(exp.id, debit=100), _jl(exp.id, debit=60), _jl(bank.id, credit=160)]
    je = _journal(client, lines)
    before = _activity(client, "journal_entry", je["id"])["count"]
    events = _save_journal(client, db_session, je, list(reversed(lines)))
    assert len(events) == before, events
    assert events[0]["kind"] == "created", events


def test_described_journal_lines_on_one_account_are_not_one_line_edited(
    client, seed_accounts, db_session
):
    exp, liab = seed_accounts["6900"].id, seed_accounts["2500"].id
    je = _journal(
        client,
        [
            _jl(exp, debit=100, description="Fuel"),
            _jl(exp, debit=60, description="Haul away"),
            _jl(liab, credit=160, description="Paid"),
        ],
    )
    events = _save_journal(
        client,
        db_session,
        je,
        [
            _jl(exp, debit=100, description="Fuel"),
            _jl(liab, credit=140, description="Paid"),
            _jl(exp, debit=40, description="Sweep up"),
        ],
    )
    assert _line_details(events[0]) == [
        "Line Paid, credit $140.00: Credit $160.00 → $140.00",
        "Line added: Sweep up, debit $40.00",
        "Line removed: Haul away, debit $60.00",
    ]


# ---------------------------------------------------------------------------
# a reorder alone, and a title-only save
# ---------------------------------------------------------------------------


def test_an_invoice_reorder_alone_writes_no_entry(client, seed_accounts, db_session):
    lines = [_line("Demo", 100), _line("Haul away", 60), _line("Patch", 50)]
    inv = _invoice(client, lines)
    event = _save_invoice(client, db_session, inv, list(reversed(lines)))
    assert event["kind"] == "created", event


def test_a_purchase_order_reorder_alone_names_no_line(
    client, seed_accounts, db_session
):
    vendor = client.post("/api/vendors", json={"name": "Hardware Supply"}).json()
    r = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": vendor["id"],
            "date": "2026-04-01",
            "lines": [_line("Hinges", "30.00"), _line("Pulls", "45.00")],
        },
    )
    assert r.status_code == 201, r.text
    po = client.get(f"/api/purchase-orders/{r.json()['id']}").json()
    keep = ("id", "description", "quantity", "rate")
    hinges, pulls = ({k: ln[k] for k in keep} for ln in po["lines"])
    # by the form (each line's id kept), then by a client that sends none
    for lines in ([pulls, hinges], [_line("Hinges", "30.00"), _line("Pulls", "45.00")]):
        _age(db_session)
        r = client.put(f"/api/purchase-orders/{po['id']}", json={"lines": lines})
        assert r.status_code == 200, r.text
        newest = _newest(client, "purchase_order", po["id"])
        assert newest["kind"] == "created" or not _line_details(newest), newest


# ---------------------------------------------------------------------------
# W-237: a document corrected by replacement
# ---------------------------------------------------------------------------


def _texts(client, kind, id_):
    return [e["text"] for e in _activity(client, kind, id_)["events"]]


def _raw(client, kind, id_):
    events = _activity(client, kind, id_)["events"]
    return [e["text"] for e in events] + [d for e in events for d in e["details"]]


def test_a_replaced_vendor_credit_says_so_once_and_the_copy_says_what_changed(
    client, seed_accounts, db_session
):
    vendor = client.post("/api/vendors", json={"name": "Hardware Supply"}).json()
    acct = seed_accounts["6000"].id

    def body(*lines):
        return {
            "vendor_id": vendor["id"],
            "date": "2026-03-01",
            "lines": [ln | {"account_id": acct} for ln in lines],
        }

    r = client.post(
        "/api/vendor-credits",
        json=body(_line("Return A", 11), _line("Return B", 22), _line("Return C", 33)),
    )
    assert r.status_code in (200, 201), r.text
    old = r.json()
    _age(db_session)
    r = client.post(
        f"/api/vendor-credits/{old['id']}/replace",
        json=body(_line("Return A", 11), _line("Return C", 33, 2)),
    )
    assert r.status_code == 200, r.text
    new = r.json()
    old_no, new_no = old["credit_number"], new["credit_number"]

    texts = _texts(client, "vendor_credit", old["id"])
    replaced = [t for t in texts if t.startswith("Replaced by")]
    assert len(replaced) == 1, texts
    assert replaced[0].startswith(f"Replaced by a corrected copy ({new_no}) by "), texts
    assert sum(t.startswith("Voided by") for t in texts) == 1, texts

    events = _activity(client, "vendor_credit", new["id"])["events"]
    copies = [e for e in events if e["text"].startswith("Corrected copy of")]
    assert len(copies) == 1, events
    assert copies[0]["text"].startswith(f"Corrected copy of {old_no} by "), events
    assert copies[0]["details"] == [
        "Line Return C, $66.00: Quantity 1 → 2; Amount $33.00 → $66.00",
        "Line removed: Return B, $22.00",
        "Subtotal $66.00 → $77.00",
        "Total $66.00 → $77.00",
    ], copies
    created = [e for e in events if e["kind"] == "created"]
    assert len(created) == 1, events
    assert created[0]["details"] == ["Line: Return A, $11.00", "Line: Return C, $66.00"]
    for said in (_raw(client, "vendor_credit", i) for i in (old["id"], new["id"])):
        assert not any("Replaces" in s or "set to 1" in s for s in said), said


def test_a_replaced_credit_memo_names_the_line_its_copy_dropped(
    client, seed_accounts, seed_customer, db_session
):
    r = client.post(
        "/api/credit-memos",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "lines": [_line("Return A", 25), _line("Return B", 10)],
        },
    )
    assert r.status_code in (200, 201), r.text
    old = r.json()
    _age(db_session)
    r = client.post(
        f"/api/credit-memos/{old['id']}/replace",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-03-01",
            "lines": [_line("Return A", 25)],
        },
    )
    assert r.status_code == 200, r.text
    new = r.json()
    texts = _texts(client, "credit_memo", old["id"])
    assert sum(t.startswith("Replaced by a corrected copy") for t in texts) == 1, texts
    assert f"({new['memo_number']})" in "".join(texts), texts
    events = _activity(client, "credit_memo", new["id"])["events"]
    copy = next(e for e in events if e["text"].startswith("Corrected copy of"))
    assert old["memo_number"] in copy["text"], copy
    assert copy["details"][0] == "Line removed: Return B, $10.00", copy
    for id_ in (old["id"], new["id"]):
        said = _raw(client, "credit_memo", id_)
        assert not any("Replaces" in s or "Replaced by set" in s for s in said), said


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_kind_corrected_by_replacement_reads_in_words_once(
    client, books, db_session, kind  # noqa: F811
):
    payload, base, new_id, *_ = KINDS[kind]
    first = _post(client, kind, books)
    _age(db_session)
    r = client.post(
        f"{base}/{first}/replace", json=payload(books, amount="120.00", memo="ink")
    )
    assert r.status_code == 200, r.text
    second = new_id(r.json())
    if client.get(f"/api/activity?type={kind}&id={first}").status_code == 404:
        # a transfer has no Activity panel
        assert kind == "transfer"
        return
    old_events = _activity(client, kind, first)["events"]
    new_events = _activity(client, kind, second)["events"]
    replaced = [e for e in old_events if e["text"].startswith("Replaced by")]
    copies = [e for e in new_events if e["text"].startswith("Corrected copy of")]
    assert len(replaced) == 1 and len(copies) == 1, (old_events, new_events)
    assert replaced[0]["text"].startswith("Replaced by a corrected copy"), replaced
    # the copy says what it changed from the one it replaced
    assert any("120.00" in d for d in copies[0]["details"]), copies
    assert sum(e["kind"] == "created" for e in new_events) == 1, new_events
    numbered = kind in ("credit_memo", "vendor_credit")
    assert ("(" in replaced[0]["text"]) == numbered, replaced
    assert ("an earlier" in copies[0]["text"]) != numbered, copies
    for e in old_events + new_events:
        for said in [e["text"], *e["details"]]:
            assert "Replaces" not in said and "Replaced by set" not in said, said
            assert "#" not in said and "None" not in said, said
