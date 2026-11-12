"""Issue #119 — a document must never be accepted without its journal entry.

Reported by wilsons043 against the v2.10.0 Windows release: renumbering
Accounts Receivable away from 1100 made every later invoice post nothing,
while still returning 201 and still appearing in the A/R aging.

The reproduction is the first test here, written the way the reporter drove
it, and it asserts the thing that actually matters: the sub-ledger and the
general ledger agree. Note that the *old* behaviour kept the trial balance
balanced — debits equalled credits throughout — so a balance check can never
catch this class. Only the tie-out can.
"""

import pytest

from app.models.accounts import Account, AccountType
from app.services import control_accounts


def _customer(client, name="Control Co"):
    r = client.post("/api/customers", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _invoice(client, cid, amount, date="2026-01-05"):
    return client.post(
        "/api/invoices",
        json={
            "customer_id": cid,
            "date": date,
            "tax_rate": 0,
            "lines": [
                {"description": "work", "quantity": 1, "rate": amount, "line_order": 0}
            ],
        },
    )


def _tb(client):
    r = client.get(
        "/api/reports/trial-balance?start_date=2020-01-01&end_date=2030-12-31"
    )
    assert r.status_code == 200
    body = r.json()
    return body["total_debit"], body["total_credit"]


def _ar_total(client):
    """The aging's own TOTAL row — it lives under "totals", not in "items"."""
    body = client.get("/api/reports/ar-aging").json()
    return float(body["totals"]["total"])


# ---------------------------------------------------------------------------
# The reported path
# ---------------------------------------------------------------------------


def test_renumbering_accounts_receivable_is_refused(client, seed_accounts):
    """The reporter's step 2. It answered 200 and broke every later invoice."""
    ar = seed_accounts["1100"]
    r = client.put(f"/api/accounts/{ar.id}", json={"account_number": "1105"})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "1100" in detail and "control account" in detail
    assert "rename" in detail.lower()  # say what IS allowed

    # and it really did not change
    assert client.get(f"/api/accounts/{ar.id}").json()["account_number"] == "1100"


def test_renaming_a_control_account_is_still_allowed(client, seed_accounts):
    """Only the number is load-bearing. An accountant renaming 1100 to
    'Trade Debtors' must not be blocked — that was never the problem."""
    ar = seed_accounts["1100"]
    r = client.put(f"/api/accounts/{ar.id}", json={"name": "Trade Debtors"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Trade Debtors"
    assert r.json()["account_number"] == "1100"


def test_changing_a_control_account_type_is_refused(client, seed_accounts):
    """The same fail-open lands anywhere the type drives behaviour."""
    ap = seed_accounts["2000"]
    r = client.put(f"/api/accounts/{ap.id}", json={"account_type": "expense"})
    assert r.status_code == 400, r.text
    assert "2000" in r.json()["detail"]


def test_an_ordinary_seeded_account_can_still_be_renumbered(client, seed_accounts):
    """Every seeded account carries is_system, so guarding on that flag would
    have blocked ordinary renumbering — which is a real bookkeeping request.
    The guard is the control-account registry, not is_system."""
    office = next(
        a
        for n, a in seed_accounts.items()
        if not control_accounts.is_control_number(n) and n.startswith("6")
    )
    r = client.put(f"/api/accounts/{office.id}", json={"account_number": "6999"})
    assert r.status_code == 200, r.text
    assert r.json()["account_number"] == "6999"


@pytest.mark.parametrize("number", ["1100", "2000", "1200", "2200", "4000", "2100"])
def test_every_posting_control_account_is_protected(client, seed_accounts, number):
    acct = seed_accounts[number]
    r = client.put(f"/api/accounts/{acct.id}", json={"account_number": number + "9"})
    assert r.status_code == 400, f"{number} was renumberable: {r.text}"


# ---------------------------------------------------------------------------
# The other direction: a NEW account must not land on a reserved number
#
# 5900 Inventory Adjustments is created on demand, so its slot is usually
# empty. A user who numbered a new expense 5900 silently became where every
# inventory write-off posts — the resolver finds the control account by
# number and does not care that it is the wrong one.
# ---------------------------------------------------------------------------


def test_creating_an_account_on_a_reserved_number_is_refused(client, seed_accounts):
    """5900 is empty on a seeded chart (created on demand), so the unique
    constraint does not catch it; the reserved-number guard must."""
    r = client.post(
        "/api/accounts",
        json={"name": "Marketing", "account_number": "5900", "account_type": "expense"},
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "5900" in detail and "Inventory Adjustments" in detail
    assert "reserved" in detail.lower()


def test_creating_an_account_on_an_ordinary_number_still_works(client, seed_accounts):
    """The guard is the control-account registry, nothing else: an unused
    ordinary number is accepted as before."""
    r = client.post(
        "/api/accounts",
        json={"name": "Dues", "account_number": "6999", "account_type": "expense"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] == "6999"


def test_creating_an_account_with_no_number_still_works(client, seed_accounts):
    """#332: a number is optional. The reserved guard must not trip on None."""
    r = client.post(
        "/api/accounts",
        json={"name": "Miscellaneous", "account_type": "expense"},
    )
    assert r.status_code == 201, r.text


def test_renumbering_an_ordinary_account_onto_a_reserved_number_is_refused(
    client, seed_accounts
):
    """The PUT twin of the create guard: moving an ordinary account onto a
    control-account number is the same trap and refused the same way."""
    office = next(
        a
        for n, a in seed_accounts.items()
        if not control_accounts.is_control_number(n) and n.startswith("6")
    )
    r = client.put(f"/api/accounts/{office.id}", json={"account_number": "1100"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "1100" in detail and "Accounts Receivable" in detail
    assert "reserved" in detail.lower()
    # and it did not move
    assert (
        client.get(f"/api/accounts/{office.id}").json()["account_number"]
        == office.account_number
    )


def test_the_system_paths_still_create_a_control_account(db_session):
    """ensure_account and the importers create the control accounts directly
    on the model, not through POST /api/accounts, so the route guard must not
    reach them. 5900 is created on demand; this is that path."""
    from app.models.accounts import Account, AccountType

    db_session.add(
        Account(
            account_number="5900",
            name="Inventory Adjustments",
            account_type=AccountType.EXPENSE,
            is_system=True,
        )
    )
    db_session.commit()
    assert control_accounts.resolve(db_session, "5900") is not None


# ---------------------------------------------------------------------------
# The dangerous half: a posting path that cannot resolve its account
# ---------------------------------------------------------------------------


def test_invoice_is_refused_when_ar_is_absent_not_silently_unposted(
    client, db_session, seed_accounts
):
    """The heart of #119. With A/R gone the invoice must FAIL — the old
    behaviour created it, returned 201, and skipped the journal entry."""
    cid = _customer(client)
    assert _invoice(client, cid, 2000).status_code == 201
    before_tb, _ = _tb(client)
    before_ar = _ar_total(client)

    # Reach past the route guard the way a damaged or hand-built file would
    # arrive. Renumbering is what the reporter did; the effect on the
    # resolver is identical and it does not disturb the existing postings
    # (deleting the account would null their account_id).
    seed_accounts["1100"].account_number = "1105"
    db_session.commit()

    r = _invoice(client, cid, 5000, date="2026-01-06")
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "1100" in detail and "Accounts Receivable" in detail
    assert "Nothing was posted" in detail

    # and nothing moved on either side — no half-written document
    assert _tb(client)[0] == before_tb
    assert _ar_total(client) == before_ar


def test_bill_is_refused_when_accounts_payable_is_absent(
    client, db_session, seed_accounts
):
    """The payables half the reporter also confirmed by hand."""
    v = client.post("/api/vendors", json={"name": "Control Vendor"})
    assert v.status_code == 201, v.text
    vid = v.json()["id"]

    seed_accounts["2000"].account_number = "2005"
    db_session.commit()

    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vid,
            "date": "2026-01-06",
            "lines": [{"description": "parts", "quantity": 1, "rate": 900}],
        },
    )
    assert r.status_code == 409, r.text
    assert "2000" in r.json()["detail"]


def test_the_trial_balance_still_balanced_while_the_books_were_wrong(
    client, db_session, seed_accounts
):
    """Why no existing check caught this.

    With the journal entry skipped, debits still equalled credits exactly —
    the ledger stayed internally consistent and simply lost an entry. This
    test pins the reasoning: balance is not the same as complete, and the
    A/R tie-out is the assertion that has to be made.
    """
    cid = _customer(client)
    assert _invoice(client, cid, 2000).status_code == 201
    debit, credit = _tb(client)
    assert debit == credit  # consistent...
    assert _ar_total(client) == pytest.approx(debit)  # ...AND tied out


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------


def test_registry_matches_the_numbers_the_code_resolves_by_literal():
    """If someone adds a `account_number == "NNNN"` lookup and does not add
    it here, renumbering that account becomes a silent trap again. This test
    is the tripwire."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    found: set[str] = set()
    for path in root.rglob("*.py"):
        for m in re.finditer(
            r'account_number\s*==\s*"(\d{4})"', path.read_text(encoding="utf-8")
        ):
            found.add(m.group(1))
    unregistered = found - set(control_accounts.CONTROL_ACCOUNTS)
    assert not unregistered, (
        "these account numbers are resolved by literal value but are not in "
        f"CONTROL_ACCOUNTS, so they can be renumbered into silence: {sorted(unregistered)}"
    )


def test_registry_is_matched_on_number_and_type():
    """A number alone is not a control account; the posting code resolves
    Opening Balance Equity as an EQUITY 3900, so an expense 3900 is ordinary.
    The unit of the whole PR: is_control matches number AND type."""
    assert control_accounts.is_control("3900", "equity") is True
    assert control_accounts.is_control("3900", "expense") is False
    assert control_accounts.is_control("1100", "asset") is True
    assert control_accounts.is_control("1100", "expense") is False
    # the type the posting code expects for each registered number
    for number, (
        name,
        want_type,
        _purpose,
    ) in control_accounts.CONTROL_ACCOUNTS.items():
        assert control_accounts.expected_type(number) == want_type
        # an on-demand number (2010, #897) is matched on its name as well
        assert control_accounts.is_control(number, want_type, name) is True
        wrong = "income" if want_type != "income" else "expense"
        assert control_accounts.is_control(number, wrong, name) is False
    # number-only membership is unchanged for the paths that still use it
    assert control_accounts.is_control_number("3900") is True
    assert control_accounts.is_control_number("9999") is False


def test_resolve_wants_the_expected_type_not_just_the_number(db_session, seed_accounts):
    """resolve matches number AND type: a 1100 that is somehow an expense is
    not Accounts Receivable, so the posting path fails loudly rather than
    booking to the wrong account."""
    assert control_accounts.resolve(db_session, "1100") == seed_accounts["1100"].id
    seed_accounts["1100"].account_type = AccountType.EXPENSE
    db_session.commit()
    with pytest.raises(control_accounts.MissingControlAccount):
        control_accounts.resolve(db_session, "1100")


def test_a_non_equity_3900_is_not_a_control_account(client, seed_accounts, db_session):
    """The review's repro: an imported chart's expense '3900 Miscellaneous
    Expense' must not come back is_control, must be renumberable, and must be
    deletable — it is never where Opening Balance Equity resolves (that is an
    equity 3900). Made as an import makes it: through the API a new account
    on a reserved number is refused (#425)."""
    acct = Account(
        name="Miscellaneous Expense",
        account_number="3900",
        account_type=AccountType.EXPENSE,
    )
    db_session.add(acct)
    db_session.commit()
    r = client.get(f"/api/accounts/{acct.id}")
    assert r.status_code == 200, r.text
    acct_id = r.json()["id"]
    assert r.json()["is_control"] is False
    assert r.json()["control_purpose"] is None

    # renumbering it is allowed — it is an ordinary account
    rn = client.put(f"/api/accounts/{acct_id}", json={"account_number": "3905"})
    assert rn.status_code == 200, rn.text
    assert rn.json()["account_number"] == "3905"

    # and so is deleting it (no postings, nothing references it)
    rd = client.delete(f"/api/accounts/{acct_id}")
    assert rd.status_code == 200, rd.text


def test_an_equity_3900_is_a_control_account(client, db_session):
    """The matching half: an equity 3900 is Opening Balance Equity and is
    locked — renumbering is refused and the row is flagged."""
    from app.models.accounts import Account, AccountType as AT

    obe = Account(
        name="Opening Balance Equity",
        account_number="3900",
        account_type=AT.EQUITY,
        is_system=True,
    )
    db_session.add(obe)
    db_session.commit()

    row = client.get(f"/api/accounts/{obe.id}").json()
    assert row["is_control"] is True
    rn = client.put(f"/api/accounts/{obe.id}", json={"account_number": "3901"})
    assert rn.status_code == 400, rn.text
    assert "3900" in rn.json()["detail"] and "control account" in rn.json()["detail"]


def test_a_wrong_type_on_another_control_number_is_not_flagged(
    client, seed_accounts, db_session
):
    """Not just 3900: a '4800' booked as an expense (its slot is empty — Late
    Fee Income is created on demand as income) is an ordinary account too.
    Made as an import makes it; the API refuses a new one there (#425)."""
    acct = Account(
        name="Some Expense", account_number="4800", account_type=AccountType.EXPENSE
    )
    db_session.add(acct)
    db_session.commit()
    r = client.get(f"/api/accounts/{acct.id}")
    assert r.status_code == 200, r.text
    assert r.json()["is_control"] is False
    # renumbering the ordinary 4800 is allowed
    rn = client.put(f"/api/accounts/{r.json()['id']}", json={"account_number": "4850"})
    assert rn.status_code == 200, rn.text


def test_missing_lists_what_a_blank_chart_lacks(db_session):
    """The reporter's note (1): a company bootstrapped outside
    manifest_create_company() has no chart at all, and reached the same
    fail-open from a different direction."""
    gaps = control_accounts.missing(db_session)
    # every SEEDED control account, and only those — 4800 and 5900 are
    # created on demand, so their absence is not a fault to report; 2010
    # Accrued Job Costs is seeded but made on first use in an older chart
    # (#897), so it is not one either
    expected = control_accounts.seeded_numbers() - control_accounts.ON_DEMAND
    assert len(gaps) == len(expected)
    assert ("1100", "Accounts Receivable") in gaps
    assert "4800" not in {n for n, _ in gaps}
    assert "2010" not in {n for n, _ in gaps}


def test_missing_is_empty_on_a_seeded_chart(db_session, seed_accounts):
    assert control_accounts.missing(db_session) == []


def test_resolve_raises_and_find_does_not(db_session, seed_accounts):
    assert control_accounts.resolve(db_session, "1100") == seed_accounts["1100"].id
    assert control_accounts.find(db_session, "9999") is None
    with pytest.raises(control_accounts.MissingControlAccount) as exc:
        control_accounts.resolve(db_session, "9999")
    assert exc.value.number == "9999"


def test_exports_stay_tolerant_of_an_odd_chart(db_session):
    """An export only needs a display name; it must not raise on a chart
    that lacks the account (app/services/iif_export.py)."""
    assert control_accounts.find(db_session, "2000") is None


# ---------------------------------------------------------------------------
# Issue #122 — the Chart of Accounts must offer the rename we promise
# ---------------------------------------------------------------------------


def test_the_api_tells_the_ui_which_accounts_are_control_accounts(
    client, seed_accounts
):
    """The page must not keep its own copy of the registry — it would drift
    from the one the posting code and the route guard actually read."""
    rows = client.get("/api/accounts").json()
    by_number = {r["account_number"]: r for r in rows}

    ar = by_number["1100"]
    assert ar["is_control"] is True
    assert "customers owe" in (ar["control_purpose"] or "")

    ordinary = next(
        r
        for n, r in by_number.items()
        if not control_accounts.is_control_number(n) and n.startswith("6")
    )
    assert ordinary["is_control"] is False
    assert ordinary["control_purpose"] is None


def test_every_control_account_is_flagged_and_nothing_else_is(client, seed_accounts):
    rows = client.get("/api/accounts").json()
    flagged = {r["account_number"] for r in rows if r["is_control"]}
    expected = {
        n
        for n in control_accounts.CONTROL_ACCOUNTS
        if n in {r["account_number"] for r in rows}
    }
    assert flagged == expected


def test_the_page_offers_edit_on_every_account(client):
    """#122: the row rendered Edit only for `!is_system`, and every seeded
    account is a system account — so no account could be edited at all, while
    the release notes said renaming was allowed. Guard the markup itself."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/app.js").read_text(
        encoding="utf-8"
    )
    row = js.split("async showAccountForm")[0]
    assert (
        "App.showAccountForm(${a.id})" in row
    ), "the accounts row lost its Edit button"
    assert (
        "!a.is_system ? `<button" not in row
    ), "Edit is gated on is_system again — every seeded account has that flag (#122)"


def test_the_form_locks_a_control_account_and_says_renaming_is_allowed():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/app.js").read_text(
        encoding="utf-8"
    )
    form = js.split("async showAccountForm")[1].split("async saveAccount")[0]
    assert "acct.is_control" in form
    assert "You can rename it" in form
    # the number and the type are the two fields the API refuses
    assert form.count("locked ?") >= 2


def test_renaming_a_control_account_through_the_api_the_form_uses(
    client, seed_accounts
):
    """A disabled input is not submitted, so the form sends name only. That
    must succeed — it is the operation the refusal message points at."""
    ar = seed_accounts["1100"]
    r = client.put(f"/api/accounts/{ar.id}", json={"name": "Trade Debtors"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Trade Debtors"
    assert body["account_number"] == "1100"
    assert body["is_control"] is True


# ---------------------------------------------------------------------------
# The update notice sits at the top of the sidebar, not the footer
# ---------------------------------------------------------------------------


def test_update_notice_mounts_at_the_top_of_the_sidebar():
    """In the footer it was only seen by someone who scrolled the whole menu,
    so people stayed on old versions without knowing. Guard the markup: the
    mount must exist in the header, and the code must target it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shell = (root / "index.html").read_text(encoding="utf-8")
    header = shell.split('class="sidebar-header"')[1].split(
        "</div>\n                <ul"
    )[0]
    assert 'id="sidebar-update"' in header, "the update mount left the sidebar header"
    assert 'id="app-version"' in header, "the running version left the sidebar header"

    js = (root / "app/static/js/app.js").read_text(encoding="utf-8")
    assert "$('#sidebar-update')" in js, "the badge is not mounted at the top"
    # and it is still a link out, not a dialog
    assert "update-badge" in js and 'link.target = "_blank"' in js.replace("'", '"')


def test_update_notice_takes_no_space_when_up_to_date():
    """An empty mount must not leave a gap in the sidebar for the majority of
    users, who are on the current version."""
    from pathlib import Path

    css = (Path(__file__).resolve().parents[1] / "app/static/css/style.css").read_text(
        encoding="utf-8"
    )
    assert "#sidebar-update:empty" in css and "display: none" in css


def test_restoring_a_missing_control_account_by_its_number_and_name_works(
    client, seed_accounts
):
    """MissingControlAccount tells the operator to restore the account with
    that exact number and name from New Account; the guard lets that through
    while the slot is empty."""
    r = client.post(
        "/api/accounts",
        json={
            "name": "Inventory Adjustments",
            "account_number": "5900",
            "account_type": "expense",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["account_number"] == "5900"


def test_restoring_with_the_right_name_but_wrong_type_is_refused(client, seed_accounts):
    """The restore exception requires the registry's TYPE as well as the name:
    the posting code finds the control account by number AND type, so a right
    name with the wrong type is refused (400) and names the type to set.
    Otherwise 'inventory adjustments' as income would resolve as the 5900 every
    write-off posts to."""
    r = client.post(
        "/api/accounts",
        json={
            "name": "inventory adjustments",  # case-insensitive name match
            "account_number": "5900",
            "account_type": "income",
        },
    )
    assert r.status_code == 400, r.text
    assert "5900" in r.json()["detail"] and "expense" in r.json()["detail"]

    r = client.post(
        "/api/accounts",
        json={
            "name": "Late Fee Income",
            "account_number": "4800",
            "account_type": "expense",
        },
    )
    assert r.status_code == 400, r.text
    assert "4800" in r.json()["detail"] and "income" in r.json()["detail"]


def test_a_restored_control_account_matches_the_seed_is_system(
    client, db_session, seed_accounts
):
    """A restored control account comes back is_system to match the seed —
    but only where the seed actually creates it. 5900 is on-demand (not
    seeded), so it stays is_system=False; 1100 is seeded, so it restores
    is_system=True and is identical to a fresh company's."""
    from app.models.accounts import Account

    r = client.post(
        "/api/accounts",
        json={
            "name": "Inventory Adjustments",
            "account_number": "5900",
            "account_type": "expense",
        },
    )
    assert r.status_code == 201, r.text
    db_session.expire_all()
    on_demand = (
        db_session.query(Account).filter(Account.account_number == "5900").first()
    )
    assert on_demand.is_system is False

    # Empty a SEEDED control-account slot at the DB level — the delete route
    # refuses a control account — then restore it through New Account.
    db_session.delete(seed_accounts["1100"])
    db_session.commit()
    r = client.post(
        "/api/accounts",
        json={
            "name": "Accounts Receivable",
            "account_number": "1100",
            "account_type": "asset",
        },
    )
    assert r.status_code == 201, r.text
    db_session.expire_all()
    restored = (
        db_session.query(Account).filter(Account.account_number == "1100").first()
    )
    assert restored.is_system is True


# --- #425 fold-ins: the article, and renumbering the control account back ---


def test_the_reserved_number_message_uses_the_right_article(client, seed_accounts):
    for number, name, wrong, want in (
        ("5900", "Inventory Adjustments", "income", "must be an expense account"),
        ("4800", "Late Fee Income", "expense", "must be an income account"),
    ):
        r = client.post(
            "/api/accounts",
            json={"name": name, "account_number": number, "account_type": wrong},
        )
        assert r.status_code == 400, r.text
        assert want in r.json()["detail"]
        assert "must be a expense" not in r.json()["detail"]
        assert "must be a income" not in r.json()["detail"]


def test_renumbering_the_control_account_itself_into_its_empty_slot(
    client, seed_accounts
):
    # the real Inventory Adjustments (expense, that name) sitting on another
    # number goes back to the empty 5900: that restores the control account
    made = client.post(
        "/api/accounts",
        json={
            "name": "Inventory Adjustments",
            "account_number": "5990",
            "account_type": "expense",
        },
    )
    assert made.status_code == 201, made.text
    r = client.put(
        f"/api/accounts/{made.json()['id']}", json={"account_number": "5900"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["account_number"] == "5900"
    assert r.json()["is_control"] is True


def test_renumbering_a_foreign_account_into_a_control_slot_is_still_refused(
    client, seed_accounts
):
    other = client.post(
        "/api/accounts",
        json={"name": "Marketing", "account_number": "5991", "account_type": "expense"},
    ).json()
    r = client.put(f"/api/accounts/{other['id']}", json={"account_number": "5900"})
    assert r.status_code == 409, r.text
    assert "reserved for Inventory Adjustments" in r.json()["detail"]

    # the right name with the wrong type says which type it must be
    wrong = client.post(
        "/api/accounts",
        json={
            "name": "Inventory Adjustments",
            "account_number": "4990",
            "account_type": "income",
        },
    ).json()
    r = client.put(f"/api/accounts/{wrong['id']}", json={"account_number": "5900"})
    assert r.status_code == 400, r.text
    assert "must be an expense account" in r.json()["detail"]

    # renaming in the same PUT counts: the name it will have is the one checked
    r = client.put(
        f"/api/accounts/{other['id']}",
        json={"account_number": "5900", "name": "Inventory Adjustments"},
    )
    assert r.status_code == 200, r.text


def test_a_numberless_import_row_never_gets_a_control_number():
    """On an empty chart the import numbers a row that has none; it must not
    land on a control account's number, which the reserved-number guard
    (#425) would then refuse or, worse, a posting path would resolve."""
    from app.services.chart_import import NUMBER_RANGES, _Numbers

    numbers = _Numbers(set())
    for acct_type in NUMBER_RANGES:
        for _ in range(12):
            assert not control_accounts.is_control_number(numbers.next_free(acct_type))


def test_an_expense_3900_can_take_a_parent(client, seed_accounts, db_session):
    """An ordinary account on a control number of another type (an imported
    expense 3900) is not found by its number, so it may sit under a parent
    like any expense; an equity 3900 still stands alone (#413, #416)."""
    parent = Account(
        name="Overhead", account_number="6995", account_type=AccountType.EXPENSE
    )
    acct = Account(
        name="Miscellaneous Expense",
        account_number="3900",
        account_type=AccountType.EXPENSE,
    )
    db_session.add_all([parent, acct])
    db_session.commit()
    r = client.put(f"/api/accounts/{acct.id}", json={"parent_id": parent.id})
    assert r.status_code == 200, r.text
    assert r.json()["parent_id"] == parent.id
