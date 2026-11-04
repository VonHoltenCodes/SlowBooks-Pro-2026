"""Edit from any report drill-down (#689).

A kind that edits in place (invoice, bill, deposit, hand-entered journal
entry, register entry) opens its own edit form from a report line. A kind
with no edit of its own (expense, card charge, transfer, credit memo, vendor
credit, customer payment, bill payment) is changed by void-and-replace:
POST /api/<kind>/{id}/replace takes the kind's create payload, voids the
original through its own void and posts the new one through its own create,
in one database transaction, the two linked both ways and in the audit log.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.audit import AuditLog
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "app" / "static" / "js"
DAY = "2026-04-10"


@pytest.fixture
def books(client, seed_accounts, seed_customer):
    v = client.post("/api/vendors", json={"name": "Acme Supply"})
    assert v.status_code in (200, 201), v.text
    vendor = v.json()
    inv = client.post(
        "/api/invoices",
        json={
            "customer_id": seed_customer.id,
            "date": "2026-04-01",
            "lines": [{"description": "Service", "quantity": 1, "rate": 500}],
        },
    )
    assert inv.status_code == 201, inv.text
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor["id"],
            "bill_number": "B-001",
            "date": "2026-04-01",
            "due_date": "2026-04-30",
            "lines": [
                {
                    "description": "materials",
                    "quantity": 1,
                    "rate": "500.00",
                    "account_id": seed_accounts["6000"].id,
                    "line_order": 0,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    return {
        "a": seed_accounts,
        "customer_id": seed_customer.id,
        "vendor_id": vendor["id"],
        "invoice_id": inv.json()["id"],
        "bill_id": bill.json()["id"],
    }


# --- each replaceable kind: its create payload, its address, how its view
# --- says it is void ----------------------------------------------------------


def _expense(b, amount="100.00", memo="paper"):
    return {
        "date": DAY,
        "vendor_id": b["vendor_id"],
        "paid_from_account_id": b["a"]["1000"].id,
        "expense_account_id": b["a"]["6400"].id,
        "amount": amount,
        "reference": "1001",
        "memo": memo,
    }


def _cc(b, amount="100.00", memo="paper"):
    return {
        "date": DAY,
        "payee": "Office Depot",
        "account_id": b["a"]["6400"].id,
        "card_account_id": b["a"]["2100"].id,
        "amount": amount,
        "memo": memo,
    }


def _transfer(b, amount="100.00", memo="paper"):
    return {
        "date": DAY,
        "from_account_id": b["a"]["1000"].id,
        "to_account_id": b["a"]["2100"].id,
        "amount": amount,
        "memo": memo,
    }


def _credit_memo(b, amount="100.00", memo="paper"):
    return {
        "customer_id": b["customer_id"],
        "date": DAY,
        "notes": memo,
        "lines": [{"description": "Credit", "quantity": 1, "rate": float(amount)}],
    }


def _vendor_credit(b, amount="100.00", memo="paper"):
    return {
        "vendor_id": b["vendor_id"],
        "date": DAY,
        "notes": memo,
        "lines": [
            {
                "description": "returned",
                "quantity": 1,
                "rate": float(amount),
                "account_id": b["a"]["6000"].id,
            }
        ],
    }


def _payment(b, amount="100.00", memo="paper"):
    return {
        "customer_id": b["customer_id"],
        "date": DAY,
        "amount": amount,
        "method": "Check",
        "check_number": "77",
        "notes": memo,
        "allocations": [{"invoice_id": b["invoice_id"], "amount": float(amount)}],
    }


def _bill_payment(b, amount="100.00", memo="paper"):
    return {
        "vendor_id": b["vendor_id"],
        "date": DAY,
        "amount": float(amount),
        "method": "check",
        "notes": memo,
        "allocations": [{"bill_id": b["bill_id"], "amount": float(amount)}],
    }


def _void_from_journal(j):
    return j["voided"]


KINDS = {
    # kind: (payload, api base, new id from the create's answer, view path,
    #        "is void" from the view, the view's memo)
    "expense": (
        _expense,
        "/api/expenses",
        lambda r: r["id"],
        "/api/expenses/{id}",
        lambda v: v["status"] == "void",
        lambda v: v["memo"],
    ),
    "cc_charge": (
        _cc,
        "/api/cc-charges",
        lambda r: r["transaction_id"],
        "/api/journal/{id}",
        _void_from_journal,
        lambda v: next(ln for ln in v["lines"] if ln["debit"] > 0)["description"],
    ),
    "transfer": (
        _transfer,
        "/api/transfers",
        lambda r: r["id"],
        "/api/journal/{id}",
        _void_from_journal,
        lambda v: v["description"].split(" — ", 1)[-1],
    ),
    "credit_memo": (
        _credit_memo,
        "/api/credit-memos",
        lambda r: r["id"],
        "/api/credit-memos/{id}",
        lambda v: v["status"] == "void",
        lambda v: v["notes"],
    ),
    "vendor_credit": (
        _vendor_credit,
        "/api/vendor-credits",
        lambda r: r["id"],
        "/api/vendor-credits/{id}",
        lambda v: v["status"] == "void",
        lambda v: v["notes"],
    ),
    "payment": (
        _payment,
        "/api/payments",
        lambda r: r["id"],
        "/api/payments/{id}",
        lambda v: v["is_voided"],
        lambda v: v["notes"],
    ),
    "bill_payment": (
        _bill_payment,
        "/api/bill-payments",
        lambda r: r["id"],
        "/api/bill-payments/{id}",
        lambda v: v["is_voided"],
        lambda v: v["notes"],
    ),
}


def _balances(TestSession):
    with TestSession() as s:
        out = {}
        for ln in s.query(TransactionLine).all():
            out[ln.account_id] = out.get(ln.account_id, Decimal("0")) + (
                (ln.debit or 0) - (ln.credit or 0)
            )
        return {k: v for k, v in out.items() if v != 0}


def _post(client, kind, books, **kw):
    payload, base, new_id, *_ = KINDS[kind]
    r = client.post(base, json=payload(books, **kw))
    assert r.status_code == 201, r.text
    return new_id(r.json())


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_replace_voids_the_original_posts_the_change_and_links_both(
    client, books, TestSession, kind
):
    payload, base, new_id, view, is_void, memo_of = KINDS[kind]
    orig = _post(client, kind, books)
    r = client.post(
        f"{base}/{orig}/replace", json=payload(books, amount="120.00", memo="ink")
    )
    assert r.status_code == 200, r.text
    new = new_id(r.json())
    assert new != orig and r.json()["replaces_id"] == orig

    old_v = client.get(view.format(id=orig)).json()
    new_v = client.get(view.format(id=new)).json()
    assert is_void(old_v) and not is_void(new_v)
    assert old_v["replaced_by_id"] == new and old_v["replaces_id"] is None
    assert new_v["replaces_id"] == orig and new_v["replaced_by_id"] is None
    assert memo_of(new_v) == "ink"

    # the change is what the books now hold: 120 in place of the original
    with TestSession() as s:
        log = (
            s.query(AuditLog)
            .filter(AuditLog.action == "REPLACE", AuditLog.record_id == orig)
            .one()
        )
        assert log.new_values["replaces_id"] == orig
        assert log.new_values["replaced_by_id"] == new
        assert log.new_values["kind"] == kind


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_an_unchanged_replacement_leaves_the_ledger_net_as_it_was(
    client, books, TestSession, kind
):
    payload, base, *_ = KINDS[kind]
    orig = _post(client, kind, books)
    before = _balances(TestSession)
    r = client.post(f"{base}/{orig}/replace", json=payload(books))
    assert r.status_code == 200, r.text
    assert _balances(TestSession) == before


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_the_new_amount_is_what_the_ledger_holds(client, books, TestSession, kind):
    payload, base, *_ = KINDS[kind]
    start = _balances(TestSession)
    # what 120 posted on its own does to the books, measured and undone
    _post(client, kind, books, amount="120.00")
    with_120 = _balances(TestSession)
    effect = {
        k: with_120.get(k, Decimal("0")) - start.get(k, Decimal("0"))
        for k in set(with_120) | set(start)
    }
    orig = _post(client, kind, books)
    r = client.post(f"{base}/{orig}/replace", json=payload(books, amount="120.00"))
    assert r.status_code == 200, r.text
    after = _balances(TestSession)
    for k in set(after) | set(with_120):
        assert after.get(k, Decimal("0")) - with_120.get(k, Decimal("0")) == effect.get(
            k, Decimal("0")
        ), k


def test_a_replaced_payment_gives_its_invoice_back_then_pays_the_new_amount(
    client, books
):
    orig = _post(client, "payment", books)
    r = client.post(
        f"/api/payments/{orig}/replace",
        json=_payment(books, amount="150.00"),
    )
    assert r.status_code == 200, r.text
    inv = client.get(f"/api/invoices/{books['invoice_id']}").json()
    assert Decimal(str(inv["balance_due"])) == Decimal("350.00")


def test_a_replaced_bill_payment_gives_its_bill_back_then_pays_the_new_amount(
    client, books
):
    orig = _post(client, "bill_payment", books)
    r = client.post(
        f"/api/bill-payments/{orig}/replace",
        json=_bill_payment(books, amount="150.00"),
    )
    assert r.status_code == 200, r.text
    bill = client.get(f"/api/bills/{books['bill_id']}").json()
    assert Decimal(str(bill["balance_due"])) == Decimal("350.00")


# --- the refusals: a void's, in its words, and nothing changes -----------------


def _count(TestSession):
    with TestSession() as s:
        return s.query(Transaction).count()


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_a_closed_period_refuses_the_replace_and_nothing_changes(
    client, books, TestSession, kind
):
    payload, base, _, view, is_void, _ = KINDS[kind]
    orig = _post(client, kind, books)
    n = _count(TestSession)
    assert (
        client.put("/api/settings", json={"closing_date": "2026-04-30"}).status_code
        == 200
    )
    r = client.post(f"{base}/{orig}/replace", json=payload(books, amount="120.00"))
    assert r.status_code == 403, r.text
    assert _count(TestSession) == n
    v = client.get(view.format(id=orig)).json()
    assert not is_void(v) and v["replaced_by_id"] is None


@pytest.mark.parametrize("kind", ["expense", "cc_charge", "transfer", "bill_payment"])
def test_a_reconciled_line_refuses_the_replace_in_the_voids_words(
    client, books, TestSession, kind
):
    payload, base, _, view, is_void, _ = KINDS[kind]
    orig = _post(client, kind, books)
    with TestSession() as s:
        if kind == "bill_payment":
            from app.models.bills import BillPayment

            txn_id = s.get(BillPayment, orig).transaction_id
        else:
            txn_id = orig
        ln = s.query(TransactionLine).filter_by(transaction_id=txn_id).first()
        ln.reconciliation_id = 999  # as a completed reconciliation stamps it
        s.commit()
    n = _count(TestSession)
    r = client.post(f"{base}/{orig}/replace", json=payload(books, amount="120.00"))
    assert r.status_code == 400, r.text
    assert "completed reconciliation and cannot be voided" in r.json()["detail"]
    assert _count(TestSession) == n
    assert not is_void(client.get(view.format(id=orig)).json())


def test_a_create_refusal_leaves_the_original_standing(client, books, TestSession):
    """The void goes through first; a refused create takes it back."""
    orig = _post(client, "expense", books)
    n = _count(TestSession)
    bad = _expense(books)
    bad["paid_from_account_id"] = books["a"]["6400"].id  # same as the expense
    r = client.post(f"/api/expenses/{orig}/replace", json=bad)
    assert r.status_code == 400, r.text
    assert _count(TestSession) == n
    v = client.get(f"/api/expenses/{orig}").json()
    assert v["status"] == "recorded" and v["replaced_by_id"] is None


def test_a_discount_credit_memo_is_refused_as_its_void_is(client, books, TestSession):
    cm = _post(client, "credit_memo", books)
    with TestSession() as s:
        from app.models.credit_memos import CreditMemo

        pay = _post(client, "payment", books)
        s.get(CreditMemo, cm).discount_payment_id = pay
        s.commit()
    r = client.post(f"/api/credit-memos/{cm}/replace", json=_credit_memo(books))
    assert r.status_code == 409, r.text
    assert "void that payment to undo it" in r.json()["detail"]


def test_a_replaced_record_is_not_replaced_twice(client, books):
    orig = _post(client, "transfer", books)
    assert (
        client.post(f"/api/transfers/{orig}/replace", json=_transfer(books)).status_code
        == 200
    )
    again = client.post(f"/api/transfers/{orig}/replace", json=_transfer(books))
    assert again.status_code == 400
    assert "already replaced" in again.json()["detail"]


def test_the_read_only_and_accountant_sign_ins_cannot_replace():
    """The server refuses the write, as every other write for those
    sign-ins; the screen offers no Edit (below)."""
    from app.main import _role_allows

    for path in (
        "/api/expenses/1/replace",
        "/api/cc-charges/1/replace",
        "/api/transfers/1/replace",
        "/api/credit-memos/1/replace",
        "/api/vendor-credits/1/replace",
        "/api/payments/1/replace",
        "/api/bill-payments/1/replace",
    ):
        assert not _role_allows("readonly", "POST", path), path
        assert not _role_allows("accountant", "POST", path), path
        assert _role_allows("bookkeeper", "POST", path) or _role_allows(
            "admin", "POST", path
        ), path


# --- what a report line's Edit opens ------------------------------------------


def test_every_kind_says_what_its_edit_opens():
    from app.services.edit_targets import edit_target

    def t(st, sid=7, tid=3):
        return edit_target(Transaction(id=tid, source_type=st, source_id=sid), False)

    assert t("invoice") == {"kind": "invoice", "id": 7, "replace": False}
    assert t("bill") == {"kind": "bill", "id": 7, "replace": False}
    assert t("deposit") == {"kind": "deposit", "id": 3, "replace": False}
    assert t("manual") == {"kind": "journal", "id": 3, "replace": False}
    assert t("bank_entry") == {"kind": "bank_entry", "id": 3, "replace": False}
    for st in ("expense", "cc_charge", "transfer"):
        assert t(st) == {"kind": st, "id": 3, "replace": True}
    for st in ("credit_memo", "vendor_credit", "payment", "bill_payment"):
        assert t(st) == {"kind": st, "id": 7, "replace": True}
    # a void, a reversal and a kind with no edit: Open only
    assert edit_target(Transaction(id=3, source_type="expense"), True) is None
    assert t("expense_void") is None and t("payroll") is None and t(None) is None


def test_the_general_ledger_lines_link_and_carry_their_edit(client, books):
    ids = {k: _post(client, k, books) for k in KINDS}
    gl = client.get(
        "/api/reports/general-ledger?start_date=2026-04-01&end_date=2026-04-30"
    ).json()
    entries = [e for a in gl["accounts"] for e in a["entries"]]
    assert all(e["source_link"] and e["transaction_id"] for e in entries)
    edits = {(e["edit"]["kind"], e["edit"]["id"]) for e in entries if e["edit"]}
    for kind, rid in ids.items():
        assert (kind, rid) in edits, kind
    assert ("invoice", books["invoice_id"]) in edits
    assert ("bill", books["bill_id"]) in edits
    # a voided line offers Open only
    client.post(f"/api/transfers/{ids['transfer']}/void")
    gl = client.get(
        "/api/reports/general-ledger?start_date=2026-04-01&end_date=2026-04-30"
    ).json()
    t = [
        e
        for a in gl["accounts"]
        for e in a["entries"]
        if e["transaction_id"] == ids["transfer"]
    ]
    assert t and all(e["voided"] and e["edit"] is None for e in t)


def test_the_drill_down_and_pl_detail_lines_carry_their_edit(client, books):
    exp = _post(client, "expense", books)
    q = "start_date=2026-04-01&end_date=2026-04-30"
    d = client.get(
        f"/api/reports/account-transactions?account_id={books['a']['6400'].id}&{q}"
    ).json()
    assert {"kind": "expense", "id": exp, "replace": True} in [
        e["edit"] for e in d["entries"]
    ]
    s = client.get(f"/api/reports/account-transactions?section=expenses&{q}").json()
    assert {"kind": "expense", "id": exp, "replace": True} in [
        e["edit"] for e in s["entries"]
    ]
    pl = client.get(f"/api/reports/profit-loss-detail?{q}").json()
    found = []

    def walk(x):
        if isinstance(x, dict):
            if "edit" in x and "line_id" in x:
                found.append(x["edit"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(pl)
    assert {"kind": "expense", "id": exp, "replace": True} in found
    assert {"kind": "bill", "id": books["bill_id"], "replace": False} in found


# --- the screens ---------------------------------------------------------------


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_every_drill_down_list_and_the_general_ledger_show_edit():
    reports = _js("reports.js")
    # the account drill-down, a total's lines, the P&L Detail, the GL
    assert reports.count("${TxnEdit.button(e)}") == 4
    gl = reports[reports.index("async generalLedger(prefill)") :][:9000]
    assert "TxnEdit.button(e)" in gl and 'class="gl-line-link"' in gl


def test_edit_is_a_write_control_the_read_only_and_accountant_sign_ins_do_not_see():
    utils = _js("utils.js")
    body = utils[utils.index("const TxnEdit = {") :][:4000]
    button = body[body.index("button(e) {") :][:900]
    assert "data-write" in button and "if (!t" in button
    # App.hideWriteControls hides every data-write control for both
    app = _js("app.js")
    assert "isLocked() { return App.isReadOnly() || App.isAccountant(); }" in app
    hide = app[app.index("hideWriteControls(root) {") :][:600]
    assert "[data-write]" in hide and "App.isLocked()" in hide


def test_every_kind_opens_its_own_form():
    utils = _js("utils.js")
    body = utils[utils.index("    async open(kind, id) {") :][:2500]
    calls = dict(re.findall(r"case '(\w+)':.*?return await ([\w.]+)\(", body, re.S))
    assert set(calls) == {
        "invoice",
        "bill",
        "deposit",
        "journal",
        "bank_entry",
        "expense",
        "cc_charge",
        "transfer",
        "credit_memo",
        "vendor_credit",
        "payment",
        "bill_payment",
    }
    files = {
        "InvoicesPage": "invoices.js",
        "BillsPage": "bills.js",
        "DepositsPage": "deposits.js",
        "JournalPage": "journal.js",
        "BankingPage": "banking.js",
        "ExpensesPage": "expenses.js",
        "CCChargesPage": "cc_charges.js",
        "CreditMemosPage": "credit_memos.js",
        "VendorCreditsPage": "vendor_credits.js",
        "PaymentsPage": "payments.js",
    }
    for kind, call in calls.items():
        page, fn = call.split(".")
        assert re.search(rf"\n    async {fn}\(", _js(files[page])), (kind, call)
    # a replace form saves to /replace and its view names the other side
    for name, base in (
        ("expenses.js", "'/expenses'"),
        ("cc_charges.js", "'/cc-charges'"),
        ("banking.js", "'/transfers'"),
        ("credit_memos.js", "'/credit-memos'"),
        ("vendor_credits.js", "'/vendor-credits'"),
        ("payments.js", "'/payments'"),
        ("bills.js", "'/bill-payments'"),
    ):
        src = _js(name)
        assert (
            f"API.post({base} + (typeof Replace === 'undefined' ? '' : "
            "Replace.suffix(form))" in src
        ), name
        assert "Replace.mark(form, '" in src, name
    for name in (
        "expenses.js",
        "credit_memos.js",
        "vendor_credits.js",
        "payments.js",
        "bills.js",
        "journal.js",
    ):
        assert "Replace.links(" in _js(name), name
    links = utils[utils.index("const Replace = {") :][:4000]
    assert "Replaced by <a" in links and "Replaces <a" in links


# --- the migration ---------------------------------------------------------------


def test_the_migration_adds_the_links_and_walks_back(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect

    db = tmp_path / "replace.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = "sqlite:///" + db.as_posix()
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision("c4e8a1f7d2b6").down_revision == "e5c1a7d3f9b2"
    tables = (
        "transactions",
        "payments",
        "bill_payments",
        "credit_memos",
        "vendor_credits",
    )

    def cols():
        engine = create_engine(f"sqlite:///{db}")
        try:
            insp = inspect(engine)
            return {t: {c["name"] for c in insp.get_columns(t)} for t in tables}
        finally:
            engine.dispose()

    command.upgrade(cfg, "c4e8a1f7d2b6")
    assert all({"replaces_id", "replaced_by_id"} <= c for c in cols().values())
    command.downgrade(cfg, "e5c1a7d3f9b2")
    assert all(not ({"replaces_id", "replaced_by_id"} & c) for c in cols().values())
    command.upgrade(cfg, "c4e8a1f7d2b6")
    assert all({"replaces_id", "replaced_by_id"} <= c for c in cols().values())
