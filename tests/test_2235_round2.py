"""2.23.5 round 2: what the Windows and macOS laps found on ba93bb67.

W-14 / NEW-140: the register's entry row was drawn only while the register
was empty. W-16 / NEW-139 (high): Remove unused seeded accounts took the
accounts a pay run finds by number, and the next pay run was a 500 (or
posted wages to Advertising). W-13: a split line with an amount and no
account was dropped and the entry posted short. W-15 / NEW-143: the
Through day box did not re-filter, so Mark through marked hidden lines.
NEW-141: the split table showed before "Split this entry…" (hidden lost to
.form-group's display:flex). NEW-142: the balance brought forward closed
an oldest-first register. NEW-144: an account with no number listed as
"- Name" at the top of every picker. NEW-145: a business company's
Settings → Sidebar listed the nonprofit pages."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _js(name):
    return (JS / name).read_text(encoding="utf-8")


def test_the_entry_row_follows_the_entries_on_a_populated_register():
    js = _js("banking.js")
    populated = js[js.index("} else {", js.index("if (!reg.entries.length) {")) :][
        :2600
    ]
    assert "</tbody>${filtered ? '' : quickRow}</table>" in populated
    # the brought-forward balance opens the oldest-first table (NEW-142)
    assert "<tbody>${opening}${rows}</tbody>" in populated
    # and the row's memo has a column of its own
    assert '<th scope="col">Memo</th>' in populated


def test_the_register_shows_the_memo_typed_into_the_row(client, seed_accounts):
    bank, adv = seed_accounts["1000"].id, seed_accounts["6000"].id
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank,
            "date": "2026-03-05",
            "amount": "-25.00",
            "category_account_id": adv,
            "payee": "Print shop",
            "description": "Flyers for the fair",
        },
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/banking/transactions",
        json={
            "account_id": bank,
            "date": "2026-03-06",
            "amount": "-10.00",
            "category_account_id": adv,
            "payee": "Stamps",
        },
    )
    assert r.status_code == 201, r.text
    entries = client.get(f"/api/banking/check-register?account_id={bank}").json()[
        "entries"
    ]
    by_payee = {e["payee"]: e for e in entries}
    assert by_payee["Print shop"]["description"] == "Flyers for the fair"
    assert by_payee["Stamps"]["description"] == "Stamps"  # no memo: the payee


def test_the_seeded_cleanup_keeps_what_payroll_finds_by_number(client, seed_accounts):
    dry = client.get("/api/accounts/unused-seeded").json()
    kept = {a["account_number"]: a["reason"] for a in dry["kept"]}
    removable = {a["account_number"] for a in dry["removable"]}
    for number in ("6110", "2300", "2310", "2330", "2340", "2370", "2380", "2390"):
        assert number in kept, number
        assert number not in removable
    assert kept["6110"] == "payroll posts wages to it"
    assert kept["2310"] == "payroll posts federal withholding to it"
    assert kept["2390"] == "PTO accruals post to it"
    done = client.post("/api/accounts/unused-seeded/remove", json={}).json()
    gone = {a["account_number"] for a in done["removed"]}
    assert not gone & {
        "6110",
        "2300",
        "2310",
        "2320",
        "2330",
        "2340",
        "2350",
        "2360",
        "2370",
        "2380",
        "2390",
    }
    numbers = {a["account_number"] for a in client.get("/api/accounts").json()}
    assert {"6110", "2300", "2310", "2390"} <= numbers


def test_an_account_with_no_number_lists_last_and_by_its_name(client, seed_accounts):
    r = client.post(
        "/api/accounts",
        json={"name": "Software Subscriptions", "account_type": "expense"},
    )
    assert r.status_code == 201, r.text
    listed = client.get("/api/accounts").json()
    assert listed[-1]["name"] == "Software Subscriptions"
    assert listed[0]["account_number"]
    utils = _js("utils.js")
    assert "function accountLabel(a, name = null)" in utils
    assert (
        "return number ? `${escapeHtml(number)} - ${escapeHtml(shown)}` : escapeHtml(shown);"
        in utils
    )
    # every picker that printed "number - name" goes through it
    for name in (
        "bank_rules.js",
        "banking.js",
        "expenses.js",
        "cc_charges.js",
        "fixed_assets.js",
        "journal.js",
    ):
        src = _js(name)
        assert "${accountLabel(" in src, name
        assert not re.search(
            r"account_number(?: \|\| '')?\)\} - \$\{escapeHtml\(\w+\.name\)", src
        ), name


def test_a_split_line_half_filled_is_refused_not_dropped():
    js = _js("banking.js")
    body = js[js.index("_splitLines(form) {") :][:1400]
    assert (
        "has an amount but no account. Choose the account, or remove the line." in body
    )
    assert "has an account but no amount. Enter its share, or remove the line." in body
    assert "if (!account && amount === '') return;" in body
    # the save builds its body through _entryData (#388), which reads the
    # split lines; a refusal there stops the save with its sentence
    data = js[js.index("_entryData(form, accountId) {") :][:300]
    assert "const splits = BankingPage._splitLines(form);" in data
    save = js[js.index("async saveEntry(e, accountId) {") :][:400]
    assert (
        "try { data = BankingPage._entryData(form, accountId); } catch (err) { toast(err.message, 'error'); return; }"
        in save
    )


def test_the_split_table_is_hidden_by_style_until_asked_for():
    js = _js("banking.js")
    assert 'id="entry-splits" style="display:none;"' in js
    assert 'id="entry-splits" hidden' not in js
    assert "return !!box && box.style.display !== 'none';" in js
    assert "box.style.display = open ? '' : 'none';" in js
    assert "if (!BankingPage._splitsOpen()) return null;" in js


def test_the_reconcile_day_box_refilters_and_keeps_its_day():
    js = _js("banking.js")
    assert 'onchange="BankingPage.reconDayChanged(${reconId}, this.value)"' in js
    body = js[js.index("reconDayChanged(reconId, value) {") :][:400]
    assert "BankingPage._reconDay[reconId] = value || '';" in body
    assert (
        "if (only && only.checked) BankingPage.reconShowThrough(reconId, value);"
        in body
    )
    assert (
        "const day = through || BankingPage._reconDay[reconId] || data.statement_date;"
        in js
    )
    assert 'value="${escapeHtml(day)}"' in js


def test_a_business_companys_sidebar_choices_leave_out_the_nonprofit_pages():
    js = _js("settings.js")
    body = js[js.index("_hiddenMenuChoices(raw) {") :][:3200]
    # an item hidden for another reason is left out; one the menu setting
    # hid stays on the list so it can be shown again (#457)
    assert "if (d.hiddenKind || d.hiddenRole) return;" in body
    assert "parent.closest('[hidden]')" in body
    app = _js("app.js")
    assert "$$('[data-nonprofit]').forEach(el => kind(el, !np));" in app
    assert "el.dataset.hiddenKind = hide ? '1' : '';" in app


def test_a_pay_run_with_nowhere_to_post_says_so_instead_of_a_500(
    client, db_session, seed_accounts
):
    from app.models.accounts import Account
    from tests.test_payroll import _create_employee

    emp = _create_employee(
        client, pay_type="salary", pay_rate=78000, pay_frequency="biweekly"
    )
    r = client.post(
        "/api/payroll",
        json={
            "period_start": "2026-04-01",
            "period_end": "2026-04-15",
            "pay_date": "2026-04-20",
            "stubs": [{"employee_id": emp["id"]}],
        },
    )
    assert r.status_code == 201, r.text
    run = r.json()
    # the accounts gone, as the cleanup used to take them (W-16 / NEW-139)
    for number in ("2300", "2310", "2320", "2330", "2340", "2350", "2360", "2370"):
        acct = (
            db_session.query(Account).filter(Account.account_number == number).first()
        )
        if acct is not None:
            db_session.delete(acct)
    db_session.commit()
    pr = client.post(f"/api/payroll/{run['id']}/process")
    assert pr.status_code == 400, pr.text
    detail = pr.json()["detail"]
    assert detail.startswith(
        "This pay run has nowhere to post 2310 (Federal income tax withheld)"
    )
    assert "2300 Payroll Liabilities" in detail and "Chart of Accounts" in detail
    # the run is still unprocessed, nothing posted
    assert client.get(f"/api/payroll/{run['id']}").json().get("transaction_id") is None
