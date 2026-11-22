"""Where a pay run posts is a setting (#620).

Net pay always credited 1000 Checking and every other piece went to the
seeded number, so a company that pays the wages later could not hold them
in 2300 Payroll Liabilities, and nothing could be pointed anywhere else.
Each piece now has a setting under Settings → Payroll Accounts that
defaults to the account it always used: a run with nothing chosen posts
exactly as before; net pay set to 2300 credits 2300 instead of the bank
and the Balance Sheet carries it; a wrong-typed or inactive account is
refused when saved; the void reverses whatever the run posted to.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from app.models.accounts import Account
from app.models.transactions import Transaction, TransactionLine

ROOT = Path(__file__).resolve().parents[1]
PAY_DATE = "2026-04-20"

# The seeded accounts process_pay_run always posted to (runs.py before #620).
DEFAULT_NUMBERS = {
    "1000",
    "6110",
    "6120",
    "2310",
    "2320",
    "2330",
    "2340",
    "2350",
    "2360",
    "2370",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _employee(client, **overrides):
    body = {
        "first_name": "Pat",
        "last_name": "Worker",
        "pay_type": "salary",
        "pay_rate": 78000,
        "pay_frequency": "biweekly",
        "filing_status": "single",
        "work_state": "WA",
    }
    body.update(overrides)
    r = client.post("/api/employees", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _run(client, *emp_ids):
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": PAY_DATE,
            "stubs": [{"employee_id": e} for e in emp_ids],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _process(client, run_id):
    r = client.post(f"/api/payroll/{run_id}/process")
    assert r.status_code == 200, r.text
    return r.json()


def _by_number(db, txn_id):
    """{account number: (debits, credits)} of an entry, summed per account."""
    out = {}
    rows = (
        db.query(TransactionLine, Account)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(TransactionLine.transaction_id == txn_id)
        .all()
    )
    for ln, acct in rows:
        dr, cr = out.get(acct.account_number, (Decimal("0"), Decimal("0")))
        out[acct.account_number] = (
            dr + Decimal(str(ln.debit or 0)),
            cr + Decimal(str(ln.credit or 0)),
        )
    return out


def _void_txn(db, run_id):
    return (
        db.query(Transaction)
        .filter(
            Transaction.source_type == "payroll_void",
            Transaction.source_id == run_id,
        )
        .first()
    )


def _account_id(client, number):
    accounts = client.get("/api/accounts").json()
    return next(a for a in accounts if a["account_number"] == number)["id"]


def _make_account(client, name, account_type, number):
    r = client.post(
        "/api/accounts",
        json={"name": name, "account_type": account_type, "account_number": number},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _money(x):
    return Decimal(str(x)).quantize(Decimal("0.01"))


def _balances(db, *numbers):
    rows = db.query(Account).filter(Account.account_number.in_(numbers)).all()
    return {a.account_number: Decimal(str(a.balance or 0)) for a in rows}


def _balance_sheet_row(client, side, number):
    r = client.get(f"/api/reports/balance-sheet?as_of_date={PAY_DATE}")
    assert r.status_code == 200, r.text
    return next(
        (row for row in r.json()[side] if row.get("account_number") == number), None
    )


# ---------------------------------------------------------------------------
# Defaults: nothing changes for a company that chooses nothing
# ---------------------------------------------------------------------------
def test_a_run_with_the_defaults_posts_exactly_as_before(
    client, db_session, seed_accounts
):
    settings = client.get("/api/settings").json()
    for key in (
        "payroll_net_pay_account_id",
        "payroll_wages_account_id",
        "payroll_employer_tax_account_id",
        "payroll_federal_withholding_account_id",
        "payroll_state_withholding_account_id",
        "payroll_social_security_account_id",
        "payroll_medicare_account_id",
        "payroll_futa_account_id",
        "payroll_suta_account_id",
        "payroll_other_deductions_account_id",
    ):
        assert settings[key] == "", key

    emp = _employee(client)
    run = _run(client, emp["id"])
    stub = run["stubs"][0]
    # test_pay_run_process_posts_balanced_journal's expectations, kept:
    # gross less every employee deduction is the net, and the employer's
    # taxes are figured apart from it.
    employee_tax = (
        stub["federal_tax"]
        + stub["state_tax"]
        + stub["state_other_employee"]
        + stub["ss_tax"]
        + stub["medicare_tax"]
    )
    assert round(stub["gross_pay"] - employee_tax, 2) == stub["net_pay"]
    assert stub["employer_ss_tax"] > 0 and stub["futa_tax"] > 0

    txn_id = _process(client, run["id"])["transaction_id"]
    posted = _by_number(db_session, txn_id)

    # balanced, and every line on an account payroll always used
    assert sum(d for d, _ in posted.values()) == sum(c for _, c in posted.values())
    assert set(posted) <= DEFAULT_NUMBERS
    assert "2300" not in posted
    # the net pay left the bank, the wages and employer taxes hit their
    # expenses, and each withholding went to its own payable
    assert posted["1000"] == (Decimal("0"), _money(stub["net_pay"]))
    assert posted["6110"] == (_money(stub["gross_pay"]), Decimal("0"))
    employer = (
        _money(stub["employer_ss_tax"])
        + _money(stub["employer_medicare_tax"])
        + _money(stub["futa_tax"])
        + _money(stub["suta_tax"])
        + _money(stub["state_other_employer"])
    )
    assert posted["6120"] == (employer, Decimal("0"))
    assert posted["2310"][1] == _money(stub["federal_tax"])
    assert posted["2330"][1] == _money(stub["ss_tax"]) + _money(stub["employer_ss_tax"])
    assert posted["2340"][1] == _money(stub["medicare_tax"]) + _money(
        stub["employer_medicare_tax"]
    )
    assert posted["2350"][1] == _money(stub["futa_tax"])
    assert posted["2360"][1] == _money(stub["suta_tax"])


def test_the_pickers_say_the_defaults_and_what_each_piece_takes(client, seed_accounts):
    r = client.get("/api/settings/payroll-accounts")
    assert r.status_code == 200, r.text
    data = r.json()
    pieces = {p["key"]: p for p in data["pieces"]}
    assert [p["key"] for p in data["pieces"]][:3] == [
        "payroll_net_pay_account_id",
        "payroll_wages_account_id",
        "payroll_employer_tax_account_id",
    ]
    net = pieces["payroll_net_pay_account_id"]
    assert net["label"] == "Net pay"
    assert net["chosen"] == "" and net["problem"] == ""
    assert net["default"]["label"] == "1000 Checking"
    assert net["kinds"] == ["bank", "liability"]
    assert net["wanted"] == "a bank account or a liability account"
    assert pieces["payroll_wages_account_id"]["default"]["label"] == (
        "6110 Wages & Salaries"
    )
    assert pieces["payroll_federal_withholding_account_id"]["default"]["label"] == (
        "2310 Federal Income Tax Payable"
    )
    # the choices are the rule the save enforces: a bank is a bank, a
    # payable is a liability that is neither a card nor a control account,
    # an expense piece may also go to cost of goods sold
    labels = {
        kind: [c["label"] for c in choices] for kind, choices in data["choices"].items()
    }
    assert "1000 Checking" in labels["bank"]
    assert "1200 Undeposited Funds" not in labels["bank"]
    assert "2300 Payroll Liabilities" in labels["liability"]
    assert "2000 Accounts Payable" not in labels["liability"]
    assert "2100 Credit Card" not in labels["liability"]
    assert "6110 Wages & Salaries" in labels["expense"]
    assert "5200 Labor Cost" in labels["expense"]
    assert "1000 Checking" not in labels["expense"]


# ---------------------------------------------------------------------------
# Net pay held in 2300 Payroll Liabilities, paid later
# ---------------------------------------------------------------------------
def test_net_pay_to_2300_credits_it_and_the_balance_sheet_carries_it(
    client, db_session, seed_accounts
):
    liab = _account_id(client, "2300")
    r = client.put("/api/settings", json={"payroll_net_pay_account_id": str(liab)})
    assert r.status_code == 200, r.text
    assert r.json()["payroll_net_pay_account_id"] == str(liab)
    picker = client.get("/api/settings/payroll-accounts").json()["pieces"][0]
    assert picker["chosen"] == str(liab) and picker["problem"] == ""

    emp = _employee(client)
    run = _run(client, emp["id"])
    net = _money(run["stubs"][0]["net_pay"])
    txn_id = _process(client, run["id"])["transaction_id"]
    posted = _by_number(db_session, txn_id)

    # the net pay is a credit to 2300, and the bank is untouched
    assert posted["2300"] == (Decimal("0"), net)
    assert "1000" not in posted
    assert sum(d for d, _ in posted.values()) == sum(c for _, c in posted.values())
    # the rest posts as it always did
    assert posted["6110"][0] == _money(run["stubs"][0]["gross_pay"])
    assert posted["2310"][1] == _money(run["stubs"][0]["federal_tax"])

    # the Balance Sheet carries the wages as a liability until they are paid
    row = _balance_sheet_row(client, "liabilities", "2300")
    assert row is not None
    assert _money(row["amount"]) == net
    checking = _balance_sheet_row(client, "assets", "1000")
    assert checking is None or _money(checking["amount"]) == Decimal("0")


# ---------------------------------------------------------------------------
# The save refuses an account that will not do
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key, number, said",
    [
        # 4000 Service Income is neither a bank nor a liability
        ("payroll_net_pay_account_id", "4000", "a bank account or a liability"),
        # a credit card is a liability, but not one wages wait in
        ("payroll_net_pay_account_id", "2100", "cannot be a credit card"),
        # 2300 is a liability, not an expense
        ("payroll_wages_account_id", "2300", "an expense or cost of goods sold"),
        # the bank is not a payable
        ("payroll_federal_withholding_account_id", "1000", "a liability account"),
        # 2000 Accounts Payable's balance is its bills'
        ("payroll_suta_account_id", "2000", "kept by its own documents"),
    ],
)
def test_a_wrong_typed_account_is_refused_on_save(
    client, seed_accounts, key, number, said
):
    acct = _account_id(client, number)
    r = client.put("/api/settings", json={key: str(acct)})
    assert r.status_code == 400, r.text
    assert said in r.json()["detail"]
    assert client.get("/api/settings").json()[key] == ""


def test_an_inactive_account_is_refused_on_save(client, seed_accounts):
    wages_payable = _make_account(client, "Wages Payable", "liability", "2305")
    r = client.put(f"/api/accounts/{wages_payable}", json={"is_active": False})
    assert r.status_code == 200, r.text
    r = client.put(
        "/api/settings", json={"payroll_net_pay_account_id": str(wages_payable)}
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "The chosen Net pay account is inactive."
    assert client.get("/api/settings").json()["payroll_net_pay_account_id"] == ""


def test_a_refused_account_saves_nothing_else(client, seed_accounts):
    income = _account_id(client, "4000")
    r = client.put(
        "/api/settings",
        json={"company_name": "Changed Co", "payroll_wages_account_id": str(income)},
    )
    assert r.status_code == 400, r.text
    assert client.get("/api/settings").json()["company_name"] == "My Company"


def test_an_id_that_is_not_a_number_or_an_account_is_refused(client, seed_accounts):
    r = client.put("/api/settings", json={"payroll_net_pay_account_id": "abc"})
    assert r.status_code == 400
    assert r.json()["detail"] == "The Net pay account id must be a number."
    r = client.put("/api/settings", json={"payroll_net_pay_account_id": "999999"})
    assert r.status_code == 400
    assert r.json()["detail"] == "The chosen Net pay account does not exist."


def test_blank_clears_a_choice_back_to_the_default(client, db_session, seed_accounts):
    liab = _account_id(client, "2300")
    assert (
        client.put(
            "/api/settings", json={"payroll_net_pay_account_id": str(liab)}
        ).status_code
        == 200
    )
    assert (
        client.put("/api/settings", json={"payroll_net_pay_account_id": ""}).status_code
        == 200
    )
    emp = _employee(client)
    run = _run(client, emp["id"])
    txn_id = _process(client, run["id"])["transaction_id"]
    posted = _by_number(db_session, txn_id)
    assert posted["1000"][1] == _money(run["stubs"][0]["net_pay"])
    assert "2300" not in posted


# ---------------------------------------------------------------------------
# A chosen account that stops working refuses the run, in a sentence
# ---------------------------------------------------------------------------
def test_a_chosen_account_made_inactive_since_refuses_the_process(
    client, seed_accounts
):
    wages_payable = _make_account(client, "Wages Payable", "liability", "2305")
    r = client.put(
        "/api/settings", json={"payroll_net_pay_account_id": str(wages_payable)}
    )
    assert r.status_code == 200, r.text
    r = client.put(f"/api/accounts/{wages_payable}", json={"is_active": False})
    assert r.status_code == 200, r.text

    emp = _employee(client)
    run = _run(client, emp["id"])
    r = client.post(f"/api/payroll/{run['id']}/process")
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == (
        "The chosen Net pay account is inactive. Choose another under "
        "Settings → Payroll Accounts, then process the pay run again."
    )
    assert client.get(f"/api/payroll/{run['id']}").json()["status"] == "draft"
    # the pickers say the same under the choice
    picker = client.get("/api/settings/payroll-accounts").json()["pieces"][0]
    assert picker["chosen"] == str(wages_payable)
    assert picker["problem"] == "The chosen Net pay account is inactive."


# ---------------------------------------------------------------------------
# The void reverses the accounts the run posted to
# ---------------------------------------------------------------------------
def test_the_void_reverses_the_configured_accounts(client, db_session, seed_accounts):
    liab = _account_id(client, "2300")
    field_wages = _make_account(client, "Field Wages", "expense", "6115")
    r = client.put(
        "/api/settings",
        json={
            "payroll_net_pay_account_id": str(liab),
            "payroll_wages_account_id": str(field_wages),
        },
    )
    assert r.status_code == 200, r.text

    emp = _employee(client)
    run = _run(client, emp["id"])
    stub = run["stubs"][0]
    before = _balances(db_session, "1000", "2300", "6110", "6115")
    txn_id = _process(client, run["id"])["transaction_id"]
    posted = _by_number(db_session, txn_id)
    assert posted["2300"][1] == _money(stub["net_pay"])
    assert posted["6115"][0] == _money(stub["gross_pay"])
    assert "1000" not in posted and "6110" not in posted

    # the settings go back to the defaults before the void: the void mirrors
    # the entry the run posted, not what the settings say now
    r = client.put(
        "/api/settings",
        json={"payroll_net_pay_account_id": "", "payroll_wages_account_id": ""},
    )
    assert r.status_code == 200, r.text

    r = client.post(f"/api/payroll/{run['id']}/void")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "void"
    void = _void_txn(db_session, run["id"])
    assert void is not None
    reversed_ = _by_number(db_session, void.id)
    assert reversed_["2300"] == (_money(stub["net_pay"]), Decimal("0"))
    assert reversed_["6115"] == (Decimal("0"), _money(stub["gross_pay"]))
    assert "1000" not in reversed_ and "6110" not in reversed_
    # the void's lines are the entry's, debit and credit swapped
    assert {k: (c, d) for k, (d, c) in posted.items()} == reversed_
    # and the balances are back where they were before the run
    db_session.expire_all()
    assert _balances(db_session, "1000", "2300", "6110", "6115") == before


# ---------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------
def test_the_settings_page_draws_the_pickers_and_payroll_asks_about_the_right_bank():
    settings_js = (ROOT / "app" / "static" / "js" / "settings.js").read_text(
        encoding="utf-8"
    )
    assert 'id="settings-h-payroll-accounts">Payroll Accounts</h3>' in settings_js
    assert "API.get('/settings/payroll-accounts')" in settings_js
    assert "SettingsPage._payrollAccountsHtml(payrollAccounts)" in settings_js
    helper = settings_js[settings_js.index("_payrollAccountsHtml(data) {") :]
    helper = helper[: helper.index("\n    },")]
    # one picker per piece, the default named first, a saved id kept even
    # when it is no longer in the list, and the problem said under it
    assert "<select name=\"' + p.key + '\"'" in helper
    # #977: a piece's hint is its entry in the Payroll Accounts note
    assert (
        "(p.hint ? ' aria-describedby=\"' + SettingsPage._payrollHintId(p.key)"
        in helper
    )
    assert "' data-admin>';" in helper
    assert "'Default (' + p.default.label + ')'" in helper
    assert "(unavailable) #" in helper
    assert "escapeHtml(p.problem)" in helper
    # the pickers sit inside the administrator's form, so they are saved
    # with Save Settings and locked for every other role
    form = settings_js[
        settings_js.index(
            '<form id="settings-form" data-admin-fields'
        ) : settings_js.index("</form>`;")
    ]
    assert 'id="settings-payroll-accounts"' in form

    payroll_js = (ROOT / "app" / "static" / "js" / "payroll.js").read_text(
        encoding="utf-8"
    )
    process = payroll_js[payroll_js.index("async process(id) {") :]
    assert "App.settings.payroll_net_pay_account_id" in process
    assert "Overdraft.confirm(netPayAcct," in process
