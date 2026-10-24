"""Generic migration route + the Sage 50, Wave, Zoho Books, and GnuCash
dialects — each exercised through /api/migration/{source} with its own
format quirks."""

import io

import pytest

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine


def _files(**named):
    return [
        ("files", (name, io.BytesIO(text.encode()), "text/plain"))
        for name, text in named.items()
    ]


def _import(client, source, **named):
    return client.post(f"/api/migration/{source}/import", files=_files(**named))


def _journals(db_session, source_type):
    return (
        db_session.query(Transaction)
        .filter(Transaction.source_type == source_type)
        .all()
    )


def _assert_balanced(db_session, journals):
    for txn in journals:
        lines = db_session.query(TransactionLine).filter_by(transaction_id=txn.id).all()
        assert sum(ln.debit or 0 for ln in lines) == sum(ln.credit or 0 for ln in lines)


# ── Registry ─────────────────────────────────────────────────────────────


def test_sources_listing(client):
    keys = {s["key"] for s in client.get("/api/migration/sources").json()}
    assert keys == {"xero", "myob", "sage", "wave", "zoho", "gnucash", "qbo_export"}


def test_unknown_source_400(client):
    resp = client.post(
        "/api/migration/quicken/dry-run", files=_files(**{"chart.csv": "x"})
    )
    assert resp.status_code == 400


# ── Sage 50: account-ID GL rows, mm/dd dates, Equity-gets closed ─────────

SAGE_COA = (
    "Account ID,Account Description,Account Type\n"
    "10200,Regular Checking Account,Cash\n"
    "39007,Owner Contributions,Equity-gets closed\n"
    "40000,Sales,Income\n"
)

SAGE_GL = (
    "Date,Reference,Jrnl,Account ID,Trans Description,Debit Amt,Credit Amt\n"
    '03/01/2026,DEP001,CRJ,10200,Deposit,"1,000.00",\n'
    '03/01/2026,DEP001,CRJ,40000,Deposit,,"1,000.00"\n'
)


def test_sage_import(client, db_session, seed_accounts):
    resp = _import(
        client, "sage", **{"chart.csv": SAGE_COA, "generalledger.csv": SAGE_GL}
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    assert data["imported_accounts"] == 3
    checking = (
        db_session.query(Account)
        .filter(Account.name == "Regular Checking Account")
        .one()
    )
    assert checking.account_type == AccountType.ASSET
    equity = (
        db_session.query(Account).filter(Account.name == "Owner Contributions").one()
    )
    assert equity.account_type == AccountType.EQUITY
    journals = _journals(db_session, "sage_import")
    assert len(journals) == 1
    # mm/dd/yyyy: March 1st, not January 3rd
    assert journals[0].date.isoformat() == "2026-03-01"
    _assert_balanced(db_session, journals)


# ── Wave: keyword-mapped types + signed single-amount rows ───────────────

WAVE_COA = (
    "Account Name,Account Type\n"
    "Business Chequing,Cash and Bank\n"
    "Consulting Income,Income\n"
    "Software Subscriptions,Operating Expense\n"
)

WAVE_GL = (
    "Transaction ID,Transaction Date,Account Name,Transaction Line Description,Amount (One column)\n"
    "T-1,2026-02-10,Business Chequing,Client payment,500.00\n"
    "T-1,2026-02-10,Consulting Income,Client payment,-500.00\n"
    "T-2,2026-02-12,Software Subscriptions,IDE license,40.00\n"
    "T-2,2026-02-12,Business Chequing,IDE license,-40.00\n"
)


def test_wave_import_signed_amounts(client, db_session, seed_accounts):
    resp = _import(
        client, "wave", **{"accounts.csv": WAVE_COA, "transactions.csv": WAVE_GL}
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    journals = _journals(db_session, "wave_import")
    assert len(journals) == 2
    _assert_balanced(db_session, journals)
    chequing = (
        db_session.query(Account).filter(Account.name == "Business Chequing").one()
    )
    assert chequing.account_type == AccountType.ASSET


def test_wave_unmapped_type_flagged(client, seed_accounts):
    weird = WAVE_COA + "Mystery,Completely Unknown Bucket\n"
    resp = client.post(
        "/api/migration/wave/dry-run",
        files=_files(**{"accounts.csv": weird, "transactions.csv": WAVE_GL}),
    )
    data = resp.json()
    assert data["ok"] is False
    assert any("Completely Unknown Bucket" in e for e in data["errors"])


# ── Zoho Books ───────────────────────────────────────────────────────────

ZOHO_COA = (
    "Account Name,Account Type\n"
    "Petty Cash,Cash\n"
    "Sales,Income\n"
    "Office Rent,Expense\n"
)

ZOHO_GL = (
    "Journal Number,Journal Date,Account,Description,Debit,Credit\n"
    "J-1,2026-04-01,Office Rent,April rent,900.00,\n"
    "J-1,2026-04-01,Petty Cash,April rent,,900.00\n"
)


def test_zoho_import(client, db_session, seed_accounts):
    resp = _import(client, "zoho", **{"chart.csv": ZOHO_COA, "journal.csv": ZOHO_GL})
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    journals = _journals(db_session, "zoho_import")
    assert len(journals) == 1
    _assert_balanced(db_session, journals)


# ── GnuCash: full account names, placeholders, signed splits ─────────────

GNUCASH_COA = (
    "Type,Full Account Name,Name,Account Code,Description,Placeholder\n"
    "ROOT,Root Account,Root Account,,,F\n"
    "ASSET,Assets,Assets,,,T\n"
    "BANK,Assets:Checking,Checking,,,F\n"
    "INCOME,Income:Consulting,Consulting,,,F\n"
    "EXPENSE,Expenses:Rent,Rent,,,F\n"
)

GNUCASH_GL = (
    "Date,Transaction ID,Description,Full Account Name,Amount Num.\n"
    "2026-05-01,tx001,May invoice,Assets:Checking,1200.00\n"
    "2026-05-01,tx001,May invoice,Income:Consulting,-1200.00\n"
    "2026-05-02,tx002,May rent,Expenses:Rent,800.00\n"
    "2026-05-02,tx002,May rent,Assets:Checking,-800.00\n"
)


def test_gnucash_import(client, db_session, seed_accounts):
    resp = _import(
        client,
        "gnucash",
        **{"accounts.csv": GNUCASH_COA, "transactions.csv": GNUCASH_GL},
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]
    # ROOT + placeholder rows skipped: 3 postable accounts
    assert data["imported_accounts"] == 3
    assert (
        db_session.query(Account).filter(Account.name == "Assets:Checking").one()
    ).account_type == AccountType.ASSET
    assert db_session.query(Account).filter(Account.name == "Assets").count() == 0
    journals = _journals(db_session, "gnucash_import")
    assert len(journals) == 2
    _assert_balanced(db_session, journals)
    # no TB export in GnuCash → warning, not error
    assert any("trial balance" in w.lower() for w in data["warnings"])


def test_a_dialect_child_takes_its_parents_detail_type(db_session, seed_accounts):
    """A migration spec whose child declares a detail type its parent cannot
    share: the link stands, so the child inherits the parent's and a warning
    says so — the same rule the chart import follows (#416 / #390)."""
    from datetime import date
    from decimal import Decimal

    from app.models.accounts import Account, AccountType
    from app.services.migration_common import run_import_bundle

    def _coa(_text):
        return (
            [
                {
                    "name": "Penalties",
                    "code": "8900",
                    "type": AccountType.EXPENSE,
                    "description": "",
                    "detail_type": "other_expense",
                    "parent": None,
                },
                {
                    "name": "Late fees",
                    "code": "8910",
                    "type": AccountType.EXPENSE,
                    "description": "",
                    "detail_type": "expense",
                    "parent": "Penalties",
                },
            ],
            [],
        )

    def _gl(_text):
        return (
            [
                [
                    {
                        "account": "Penalties",
                        "date": date(2026, 1, 15),
                        "debit": Decimal("100"),
                        "credit": Decimal("0"),
                        "description": "fine",
                        "reference": "J1",
                    },
                    {
                        "account": "Late fees",
                        "date": date(2026, 1, 15),
                        "debit": Decimal("0"),
                        "credit": Decimal("100"),
                        "description": "fine",
                        "reference": "J1",
                    },
                ]
            ],
            [],
        )

    result = run_import_bundle(
        db_session,
        {"coa": "x", "gl": "x"},
        {"coa": _coa, "gl": _gl},
        "test_inherit",
        "Test",
    )
    assert result["imported_accounts"] == 2
    late = db_session.query(Account).filter(Account.name == "Late fees").one()
    penalties = db_session.query(Account).filter(Account.name == "Penalties").one()
    assert late.detail_type == "other_expense"
    assert late.parent_id == penalties.id
    assert any(
        "Late fees" in w
        and "takes Other Expense" in w
        and "parent 'Penalties'" in w
        and "the file said Expense" in w
        for w in result["warnings"]
    )


def test_the_dry_run_previews_the_parent_rules(db_session, seed_accounts):
    """The migration dry run previews the inheritance and the link refusals
    the import applies, in identical words (#416): factored into one
    _parent_decision the dry run and the apply both call."""
    from datetime import date
    from decimal import Decimal

    from app.services.migration_common import dry_run_bundle, run_import_bundle

    chart = [
        {
            "name": "Penalties",
            "code": "8900",
            "type": AccountType.EXPENSE,
            "description": "",
            "detail_type": "other_expense",
            "parent": None,
        },
        {
            "name": "Late fees",
            "code": "8910",
            "type": AccountType.EXPENSE,
            "description": "",
            "detail_type": "expense",
            "parent": "Penalties",
        },
        {
            "name": "Spare cash",
            "code": "1850",
            "type": AccountType.ASSET,
            "description": "",
            "bank_kind": "bank",
            "parent": "Penalties",
        },
    ]
    gl = [
        [
            {
                "account": "Penalties",
                "date": date(2026, 1, 15),
                "debit": Decimal("100"),
                "credit": Decimal("0"),
                "reference": "J1",
            },
            {
                "account": "Late fees",
                "date": date(2026, 1, 15),
                "debit": Decimal("0"),
                "credit": Decimal("100"),
                "reference": "J1",
            },
        ]
    ]
    parsers = {"coa": lambda _t: (chart, []), "gl": lambda _t: (gl, [])}
    bundle = {"coa": "x", "gl": "x"}

    inherit = (
        "'Late fees' takes Other Expense, the detail type of its parent "
        "'Penalties' (the file said Expense)"
    )
    refusal = (
        "'Spare cash' was not made a sub-account of 'Penalties': "
        "a sub-account of Penalties must be an expense too"
    )
    preview = dry_run_bundle(db_session, bundle, parsers, "Test", "test_preview")
    assert inherit in preview["warnings"]
    assert refusal in preview["warnings"]
    applied = run_import_bundle(db_session, bundle, parsers, "test_preview", "Test")
    assert inherit in applied["warnings"]
    assert refusal in applied["warnings"]


def test_a_long_reference_is_not_re_imported_as_a_duplicate(db_session, seed_accounts):
    """A source reference over the 100-char column width is cut the same way
    on the write side and the dedupe key, so a second import recognises it and
    posts the journal once (#498). The stored reference equals the key."""
    from datetime import date
    from decimal import Decimal

    from app.services.migration_common import _group_reference, run_import_bundle

    long_ref = "J" * 120
    chart = [
        {
            "name": "Spare cash",
            "code": "1850",
            "type": AccountType.ASSET,
            "description": "",
            "bank_kind": "bank",
            "parent": None,
        },
        {
            "name": "Sales",
            "code": "4100",
            "type": AccountType.INCOME,
            "description": "",
            "parent": None,
        },
    ]
    gl = [
        [
            {
                "account": "Spare cash",
                "date": date(2026, 1, 15),
                "debit": Decimal("100"),
                "credit": Decimal("0"),
                "reference": long_ref,
            },
            {
                "account": "Sales",
                "date": date(2026, 1, 15),
                "debit": Decimal("0"),
                "credit": Decimal("100"),
                "reference": long_ref,
            },
        ]
    ]
    parsers = {"coa": lambda _t: (chart, []), "gl": lambda _t: (gl, [])}
    bundle = {"coa": "x", "gl": "x"}

    first = run_import_bundle(db_session, bundle, parsers, "long_ref_src", "Test")
    assert first["imported_journals"] == 1

    key = _group_reference(gl[0])
    stored = [t.reference for t in _journals(db_session, "long_ref_src")]
    assert stored == [key]  # what is stored is exactly the dedupe key

    second = run_import_bundle(db_session, bundle, parsers, "long_ref_src", "Test")
    assert second["imported_journals"] == 0
    assert second["duplicate_journals"] == 1
    assert len(_journals(db_session, "long_ref_src")) == 1  # posted once


# ── Readiness metadata via the generic route ─────────────────────────────


def test_generic_import_records_chart_source(client, seed_accounts):
    _import(client, "zoho", **{"chart.csv": ZOHO_COA, "journal.csv": ZOHO_GL})
    status = client.get("/api/opening-balances/status").json()
    assert status["chart_setup_source"] == "zoho_import"


def test_wave_trial_balance_report_shape(client, seed_accounts):
    """Real Wave TB exports are reports: BOM + title preamble, UPPERCASE
    headers, section rows without amounts, and Total rows — captured from
    an actual Wave export."""
    wave_tb = (
        "﻿Trial Balance\n"
        "TRENTON Lee Von Holten\n"
        "As of 2026-08-09\n"
        "Report Type: Accrual (Paid & Unpaid)\n"
        '""\n'
        "ACCOUNT NUMBER,ACCOUNTS,DEBIT,CREDIT\n"
        ",Assets,,\n"
        ",Business Chequing,540.00,\n"
        ",Total Assets,540.00,0.00\n"
        ",Income,,\n"
        ",Consulting Income,,540.00\n"
        ",Total Income,0.00,540.00\n"
        ",Total for all accounts,540.00,540.00\n"
    )
    wave_gl = (
        "Transaction ID,Transaction Date,Account Name,Transaction Line Description,Amount (One column)\n"
        "T-9,2026-03-01,Business Chequing,Consulting payment,540.00\n"
        "T-9,2026-03-01,Consulting Income,Consulting payment,-540.00\n"
    )
    resp = client.post(
        "/api/migration/wave/dry-run",
        files=_files(
            **{
                "accounts.csv": WAVE_COA,
                "transactions.csv": wave_gl,
                "trial balance.csv": wave_tb,
            }
        ),
    )
    data = resp.json()
    assert data["ok"] is True, data["errors"]


# These are deliberately malformed synthetic inputs, not vendor export samples.
@pytest.mark.parametrize(
    "source,coa,gl",
    [("sage", SAGE_COA, SAGE_GL), ("zoho", ZOHO_COA, ZOHO_GL)],
    ids=["sage", "zoho"],
)
@pytest.mark.parametrize(
    "filename,label",
    [
        ("chart.csv", "chart of accounts"),
        ("journal.csv", "general ledger"),
        ("trial.csv", "trial balance"),
    ],
)
@pytest.mark.parametrize("content", ["", "Unexpected,Columns\nexample,123\n"])
def test_migration_refuses_files_with_no_recognized_records(
    client, db_session, seed_accounts, source, coa, gl, filename, label, content
):
    from app.models.settings import Settings

    before = (
        db_session.query(Account).count(),
        db_session.query(Transaction).count(),
        db_session.query(Settings).count(),
    )
    files = {"chart.csv": coa, "journal.csv": gl, filename: content}
    for action in ("dry-run", "import"):
        response = client.post(
            f"/api/migration/{source}/{action}", files=_files(**files)
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is False, data
        assert any(f"no {label} records" in e.lower() for e in data["errors"]), data
        if action == "import":
            assert data["imported_accounts"] == data["imported_journals"] == 0
    db_session.expire_all()
    assert (
        db_session.query(Account).count(),
        db_session.query(Transaction).count(),
        db_session.query(Settings).count(),
    ) == before


def test_sage_zero_trial_balance_is_data_not_an_empty_file(client, seed_accounts):
    tb = (
        "Account Description,Debit,Credit\n"
        "Regular Checking Account,0,0\n"
        "Sales,0,0\n"
    )
    response = client.post(
        "/api/migration/sage/dry-run",
        files=_files(
            **{"chart.csv": SAGE_COA, "journal.csv": SAGE_GL, "trial.csv": tb}
        ),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True, data["errors"]
    assert len(data["opening_balances"]) == 2


def test_sage_header_only_ledger_is_refused_before_opening_balances(
    client, db_session, seed_accounts
):
    before = db_session.query(Account).count()
    response = _import(
        client,
        "sage",
        **{
            "chart.csv": SAGE_COA,
            "journal.csv": SAGE_GL.splitlines()[0] + "\n",
            "trial.csv": (
                "Account Description,Debit,Credit\n"
                "Regular Checking Account,1000,\n"
                "Sales,,1000\n"
            ),
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is False
    assert data["imported_accounts"] == data["imported_journals"] == 0
    assert any("No general ledger records" in e for e in data["errors"])
    assert db_session.query(Account).count() == before


def test_a_header_only_chart_beside_a_good_ledger_blames_the_chart_alone(
    client, seed_accounts
):
    """The ledger's rows were read, then refused one by one because the
    empty chart lists none of their accounts; "No general ledger records
    were recognised" beside those errors blamed a good file (2.23.1 gate,
    W-1 / NEW-97). The sentence names a file only when its parser read
    nothing and had nothing to say."""
    response = client.post(
        "/api/migration/sage/dry-run",
        files=_files(
            **{"chart.csv": SAGE_COA.splitlines()[0] + "\n", "journal.csv": SAGE_GL}
        ),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is False
    assert any("No chart of accounts records" in e for e in data["errors"]), data
    assert not any("No general ledger records" in e for e in data["errors"]), data
