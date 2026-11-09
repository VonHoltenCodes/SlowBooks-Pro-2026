"""Reports on a cash basis, step 2 (#483, docs/design/cash-basis.md): the
Trial Balance, General Ledger, P&L Detail, account drill-down, the grids
(by class, by job, by month), Job Profitability and the nonprofit
statements take the basis the P&L and Balance Sheet took in step 1.

One scenario: an invoice nobody has paid, and an invoice paid in part a
month later. Accrual shows both invoices on their dates; cash shows only
the paid share, on the payment's date.
"""

from decimal import Decimal

import pytest

from tests.test_cash_basis import _invoice, _payment

D = Decimal
MARCH = "start_date=2026-03-01&end_date=2026-03-31"
APRIL = "start_date=2026-04-01&end_date=2026-04-30"
YEAR = "start_date=2026-01-01&end_date=2026-12-31"


@pytest.fixture(name="scene")
def scene_fixture(client, db_session, seed_accounts, seed_customer):
    """Invoice A, $1,000, unpaid (March 1). Invoice B, $600 (March 2), $200
    paid April 10."""
    cust = seed_customer.id
    a = _invoice(client, cust, "2026-03-01", [{"rate": 1000}])
    b = _invoice(client, cust, "2026-03-02", [{"rate": 600}])
    _payment(client, cust, "2026-04-10", 200, [(b["id"], 200)])
    return {"a": a, "b": b, "accounts": seed_accounts}


def _get(client, path, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    r = client.get(f"/api/reports/{path}" + (f"?{qs}" if qs else ""))
    assert r.status_code == 200, (path, r.text)
    return r


def _sales(report_accounts, number="4000"):
    for line in report_accounts:
        if line.get("account_number") == number:
            return D(str(line["amount"]))
    return D("0")


@pytest.fixture(name="pdf_sections")
def pdf_sections_fixture(monkeypatch):
    """The sections each PDF route hands the renderer, so a test reads the
    period line without a PDF engine."""
    seen = []

    def fake(sections, settings, landscape=False):
        seen.extend(sections)
        return b"%PDF-1.4 test"

    monkeypatch.setattr("app.services.pdf_service.generate_report_pdf", fake)
    return seen


def _pdf(client, path, sections, **params):
    sections.clear()
    r = _get(client, f"{path}/pdf", **params)
    assert r.headers["content-type"] == "application/pdf"
    return " ".join(str(s.get("period")) for s in sections)


# ---------------------------------------------------------------------------
# General Ledger
# ---------------------------------------------------------------------------


def test_general_ledger_reads_a_basis_and_still_foots(client, scene):
    ar = scene["accounts"]["1100"].id

    def ledger(basis=None):
        return _get(
            client,
            "general-ledger",
            start_date="2026-04-01",
            end_date="2026-04-30",
            basis=basis,
        ).json()

    accrual, cash = ledger("accrual"), ledger("cash")
    assert ledger() == accrual and accrual["basis"] == "accrual"
    assert cash["basis"] == "cash" and cash["basis_note"] is None

    def account(data, number):
        return next(a for a in data["accounts"] if a["account_number"] == number)

    # April: accrual has no sales line; cash has the paid share
    assert not any(a["account_number"] == "4000" for a in accrual["accounts"])
    sales = account(cash, "4000")
    assert D(str(sales["total_credit"])) == D("200.00")
    assert [e["date"] for e in sales["entries"]] == ["2026-04-10"]
    # the opening balance is on the basis too: March's sales are not in
    # the cash books, the bank deposit is
    assert D(str(sales["opening_balance"])) == 0
    assert D(str(account(accrual, "1100")["opening_balance"])) == D("1600.00")
    assert D(str(account(cash, "1100")["opening_balance"])) == 0
    assert ar  # the A/R account exists on the chart

    # opening + activity = closing, per account, on both bases
    for data in (accrual, cash):
        for a in data["accounts"]:
            sign = 1 if a["normal_balance"] == "debit" else -1
            activity = D(str(a["total_debit"])) - D(str(a["total_credit"]))
            assert D(str(a["opening_balance"])) + sign * activity == D(
                str(a["closing_balance"])
            ), a["account_name"]
    # a year of cash books leaves no receivable
    year = _get(
        client,
        "general-ledger",
        start_date="2026-01-01",
        end_date="2026-12-31",
        basis="cash",
    ).json()
    assert D(str(account(year, "1100")["closing_balance"])) == 0


def test_general_ledger_follows_the_company_default(client, scene):
    qs = dict(start_date="2026-01-01", end_date="2026-12-31")
    plain = _get(client, "general-ledger", **qs).json()
    assert plain["basis"] == "accrual"
    assert client.put("/api/settings", json={"report_basis": "cash"}).status_code == 200
    flipped = _get(client, "general-ledger", **qs).json()
    assert flipped["basis"] == "cash"
    assert flipped["accounts"] != plain["accounts"]
    assert _get(client, "general-ledger", basis="accrual", **qs).json() == plain


def test_general_ledger_pdf_and_csv_say_cash_basis(client, scene, pdf_sections):
    qs = dict(start_date="2026-01-01", end_date="2026-12-31")
    assert "(cash basis)" in _pdf(
        client, "general-ledger", pdf_sections, basis="cash", **qs
    )
    assert "(cash basis)" not in _pdf(client, "general-ledger", pdf_sections, **qs)
    csv = _get(client, "general-ledger/csv", basis="cash", **qs).text
    assert "(cash basis)" in csv
    assert "(cash basis)" not in _get(client, "general-ledger/csv", **qs).text
    client.put("/api/settings", json={"report_basis": "cash"})
    assert "(cash basis)" in _get(client, "general-ledger/csv", **qs).text


# ---------------------------------------------------------------------------
# Account drill-down
# ---------------------------------------------------------------------------


def test_drill_down_lines_follow_the_basis(client, scene):
    sales = scene["accounts"]["4000"].id

    def drill(basis=None, **extra):
        return _get(
            client,
            "account-transactions",
            account_id=sales,
            basis=basis,
            **{**dict(start_date="2026-01-01", end_date="2026-12-31"), **extra},
        ).json()

    accrual, cash = drill("accrual"), drill("cash")
    assert drill() == accrual and accrual["basis"] == "accrual"
    assert accrual["period_credit"] == 1600.0
    assert cash["period_credit"] == 200.0 and cash["basis"] == "cash"
    assert [e["date"] for e in cash["entries"]] == ["2026-04-10"]
    assert cash["entries"][0]["source_type"] == "payment"
    # the opening balance is on the same basis
    assert drill("accrual", start_date="2026-04-01")["opening_balance"] == 1600.0
    assert drill("cash", start_date="2026-04-01")["opening_balance"] == 0.0

    # a P&L total's drill-down ends on the statement's figure
    pl = _get(
        client,
        "profit-loss",
        basis="cash",
        **dict(start_date="2026-01-01", end_date="2026-12-31"),
    ).json()
    total = _get(
        client,
        "account-transactions",
        section="net_income",
        basis="cash",
        start_date="2026-01-01",
        end_date="2026-12-31",
    ).json()
    assert total["basis"] == "cash"
    assert total["period_net"] == pl["net_income"] == 200.0
    accrual_total = _get(
        client,
        "account-transactions",
        section="net_income",
        start_date="2026-01-01",
        end_date="2026-12-31",
    ).json()
    assert accrual_total["period_net"] == 1600.0


def test_drill_down_pdf_and_csv_say_cash_basis(client, scene, pdf_sections):
    sales = scene["accounts"]["4000"].id
    for extra in ({"account_id": sales}, {"section": "income"}):
        qs = dict(start_date="2026-01-01", end_date="2026-12-31", **extra)
        assert "(cash basis)" in _pdf(
            client, "account-transactions", pdf_sections, basis="cash", **qs
        )
        assert "(cash basis)" not in _pdf(
            client, "account-transactions", pdf_sections, **qs
        )
        assert (
            "(cash basis)"
            in _get(client, "account-transactions/csv", basis="cash", **qs).text
        )
        assert "(cash basis)" not in _get(client, "account-transactions/csv", **qs).text


# ---------------------------------------------------------------------------
# P&L Detail
# ---------------------------------------------------------------------------


def test_pl_detail_lines_are_the_payments_on_the_cash_basis(
    client, scene, pdf_sections
):
    def detail(period, basis=None):
        return _get(client, "profit-loss-detail", basis=basis, **period).json()

    march = dict(start_date="2026-03-01", end_date="2026-03-31")
    april = dict(start_date="2026-04-01", end_date="2026-04-30")
    assert detail(march)["net_income"] == 1600.0
    assert detail(march, "cash")["net_income"] == 0.0
    assert detail(march) == detail(march, "accrual")
    cash = detail(april, "cash")
    assert cash["basis"] == "cash" and cash["net_income"] == 200.0
    # it ties to the cash P&L
    pl = _get(client, "profit-loss", basis="cash", **april).json()
    assert cash["net_income"] == pl["net_income"]
    (entry,) = [e for b in cash["sections"]["income"] for e in b["entries"]]
    assert entry["source_type"] == "payment" and entry["date"] == "2026-04-10"
    assert entry["amount"] == 200.0
    assert detail(april)["net_income"] == 0.0

    assert "(cash basis)" in _pdf(
        client, "profit-loss-detail", pdf_sections, basis="cash", **april
    )
    assert "(cash basis)" not in _pdf(
        client, "profit-loss-detail", pdf_sections, **april
    )
    assert (
        "(cash basis)"
        in _get(client, "profit-loss-detail/csv", basis="cash", **april).text
    )
    assert "(cash basis)" not in _get(client, "profit-loss-detail/csv", **april).text
    client.put("/api/settings", json={"report_basis": "cash"})
    assert detail(april)["basis"] == "cash"


# ---------------------------------------------------------------------------
# The grids and Job Profitability
# ---------------------------------------------------------------------------


def test_the_class_and_job_grids_follow_the_basis(client, scene, pdf_sections):
    for path, key in (
        ("profit-loss-by-class", "classes"),
        ("profit-loss-by-job", "jobs"),
    ):
        accrual = _get(
            client,
            path,
            basis="accrual",
            **dict(start_date="2026-03-01", end_date="2026-04-30"),
        ).json()
        plain = _get(
            client, path, start_date="2026-03-01", end_date="2026-04-30"
        ).json()
        cash = _get(
            client, path, basis="cash", start_date="2026-03-01", end_date="2026-04-30"
        ).json()
        assert plain == accrual and accrual["basis"] == "accrual"
        assert accrual["total_net_income"] == 1600.0
        assert cash["basis"] == "cash" and cash["total_net_income"] == 200.0
        assert sum(c["net_income"] for c in cash[key]) == 200.0
        # the Total column is the cash P&L's
        pl = _get(
            client,
            "profit-loss",
            basis="cash",
            start_date="2026-03-01",
            end_date="2026-04-30",
        ).json()
        assert cash["total_net_income"] == pl["net_income"]

        qs = dict(start_date="2026-03-01", end_date="2026-04-30")
        assert "(cash basis)" in _pdf(client, path, pdf_sections, basis="cash", **qs)
        assert "(cash basis)" not in _pdf(client, path, pdf_sections, **qs)
        assert "(cash basis)" in _get(client, f"{path}/csv", basis="cash", **qs).text
        assert "(cash basis)" not in _get(client, f"{path}/csv", **qs).text
        client.put("/api/settings", json={"report_basis": "cash"})
        assert _get(client, path, **qs).json()["basis"] == "cash"
        client.put("/api/settings", json={"report_basis": "accrual"})


def test_the_month_columns_csv_says_cash_basis(client, scene):
    qs = dict(start_date="2026-03-01", end_date="2026-04-30", columns="month")
    assert "(cash basis)" in _get(client, "profit-loss/csv", basis="cash", **qs).text
    assert "(cash basis)" not in _get(client, "profit-loss/csv", **qs).text


def test_job_profitability_follows_the_basis(client, scene):
    qs = dict(start_date="2026-03-01", end_date="2026-04-30")
    plain = _get(client, "job-profitability", **qs).json()
    assert plain == _get(client, "job-profitability", basis="accrual", **qs).json()
    assert plain["total_income"] == 1600.0 and plain["basis"] == "accrual"
    cash = _get(client, "job-profitability", basis="cash", **qs).json()
    assert cash["total_income"] == 200.0 and cash["basis"] == "cash"
    client.put("/api/settings", json={"report_basis": "cash"})
    assert _get(client, "job-profitability", **qs).json()["total_income"] == 200.0


@pytest.mark.parametrize("basis,income", [("accrual", "1600.00"), ("cash", "200.00")])
def test_job_profitability_print_pdf_and_csv_follow_the_basis_in_construction(
    client, scene, pdf_sections, basis, income
):
    """In a construction company the Job Profitability PDF and CSV accept a
    basis and print that basis's figures: with an invoice unpaid, accrual
    income is 1,600.00 and cash income 200.00."""
    client.put("/api/settings", json={"company_type": "construction"})
    qs = dict(start_date="2026-03-01", end_date="2026-04-30", basis=basis)

    _pdf(client, "job-profitability", pdf_sections, **qs)
    total_row = pdf_sections[0]["rows"][-1]["cells"]
    assert D(total_row[3].replace(",", "").replace("$", "")) == D(income)

    csv = _get(client, "job-profitability/csv", **qs).text
    total_line = [ln for ln in csv.splitlines() if ln.startswith("Total")][0]
    assert income in total_line.replace(",", "").replace('"', "")


# ---------------------------------------------------------------------------
# Nonprofit statements
# ---------------------------------------------------------------------------


def test_the_nonprofit_statements_follow_the_basis(client, scene, pdf_sections):
    qs = dict(start_date="2026-03-01", end_date="2026-04-30")
    for path in ("statement-of-activities", "functional-expenses"):
        plain = _get(client, path, **qs).json()
        assert plain == _get(client, path, basis="accrual", **qs).json()
        assert plain["basis"] == "accrual"
        assert _get(client, path, basis="cash", **qs).json()["basis"] == "cash"
        assert "(cash basis)" in _pdf(client, path, pdf_sections, basis="cash", **qs)
        assert "(cash basis)" not in _pdf(client, path, pdf_sections, **qs)
        assert "(cash basis)" in _get(client, f"{path}/csv", basis="cash", **qs).text
        assert "(cash basis)" not in _get(client, f"{path}/csv", **qs).text

    def soa(basis):
        return _get(client, "statement-of-activities", basis=basis, **qs).json()

    assert soa("accrual")["totals"]["revenue"] == 1600.0
    assert soa("cash")["totals"]["revenue"] == 200.0
    assert soa("cash")["totals"]["change_total"] == 200.0
    prior = _get(
        client, "statement-of-activities", basis="cash", compare="prior_year", **qs
    ).json()
    assert prior["basis"] == "cash" and prior["prior"]["totals"]["revenue"] == 0.0

    as_of = dict(as_of_date="2026-04-30")
    plain = _get(client, "statement-of-financial-position", **as_of).json()
    assert (
        plain
        == _get(
            client, "statement-of-financial-position", basis="accrual", **as_of
        ).json()
    )
    cash = _get(client, "statement-of-financial-position", basis="cash", **as_of).json()

    def ar(d):
        return sum(
            i["amount"] for i in d["assets"] if i.get("account_number") == "1100"
        )

    assert ar(plain) == 1400.0 and ar(cash) == 0.0
    assert cash["total_assets"] == cash["total_liabilities_and_net_assets"] == 200.0
    assert "(cash basis)" in _pdf(
        client, "statement-of-financial-position", pdf_sections, basis="cash", **as_of
    )
    assert (
        "(cash basis)"
        in _get(
            client, "statement-of-financial-position/csv", basis="cash", **as_of
        ).text
    )
    assert (
        "(cash basis)"
        not in _get(client, "statement-of-financial-position/csv", **as_of).text
    )
    client.put("/api/settings", json={"report_basis": "cash"})
    assert _get(client, "statement-of-activities", **qs).json()["basis"] == "cash"
    assert (
        _get(client, "statement-of-financial-position", **as_of).json()["basis"]
        == "cash"
    )


def test_the_cash_note_comes_with_a_journal_receivable(client, scene):
    """A/R left from a journal entry is not zero on a cash report: the
    report says so, wherever it has an end date."""
    from tests.test_cash_basis import _ar_journal

    _ar_journal(client, scene["accounts"], "2026-02-01", 75)
    qs = dict(start_date="2026-01-01", end_date="2026-12-31", basis="cash")
    for path in (
        "general-ledger",
        "profit-loss-detail",
        "profit-loss-by-class",
        "profit-loss-by-job",
        "job-profitability",
        "statement-of-activities",
        "functional-expenses",
    ):
        assert "not zero" in (_get(client, path, **qs).json()["basis_note"] or ""), path
        accrual = _get(client, path, **{**qs, "basis": "accrual"}).json()
        assert accrual["basis_note"] is None, path
    sofp = _get(
        client, "statement-of-financial-position", as_of_date="2026-12-31", basis="cash"
    ).json()
    assert "not zero" in sofp["basis_note"]
    drill = _get(
        client,
        "account-transactions",
        account_id=scene["accounts"]["4000"].id,
        start_date="2026-01-01",
        end_date="2026-12-31",
        basis="cash",
    ).json()
    assert "not zero" in drill["basis_note"]


def test_an_unknown_basis_is_refused_everywhere(client, scene):
    for path in (
        "general-ledger",
        "profit-loss-detail",
        "profit-loss-by-class",
        "profit-loss-by-job",
        "job-profitability",
        "statement-of-activities",
        "functional-expenses",
        "statement-of-financial-position",
    ):
        r = client.get(f"/api/reports/{path}?basis=modified")
        assert r.status_code == 422, path
    r = client.get("/api/reports/account-transactions?account_id=1&basis=modified")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Trial Balance
# ---------------------------------------------------------------------------


def test_trial_balance_cash_basis_has_no_receivable(client, scene, pdf_sections):
    qs = dict(start_date="2026-01-01", end_date="2026-12-31")

    def tb(basis=None):
        return _get(client, "trial-balance", basis=basis, **qs).json()

    def row(data, number):
        return next((i for i in data["items"] if i["account_number"] == number), None)

    accrual, cash = tb("accrual"), tb("cash")
    assert tb() == accrual and accrual["basis"] == "accrual"
    assert cash["basis"] == "cash" and cash["basis_note"] is None
    # accrual: both invoices are income and $1,400 is still owed; cash: the
    # paid $200 is income and nothing is owed
    assert row(accrual, "4000")["total_credit"] == 1600.0
    assert row(accrual, "1100")["net_balance"] == 1400.0
    assert row(cash, "4000")["total_credit"] == 200.0
    assert row(cash, "1100")["net_balance"] == 0.0
    # each basis balances on its own
    for data in (accrual, cash):
        assert abs(data["difference"]) < 0.005
        assert data["total_debit"] == data["total_credit"]
    # the money itself reads the same on both
    assert row(cash, "1200") == row(accrual, "1200")
    assert row(cash, "1200")["net_balance"] == 200.0

    assert "(cash basis)" in _pdf(
        client, "trial-balance", pdf_sections, basis="cash", **qs
    )
    assert "(cash basis)" not in _pdf(client, "trial-balance", pdf_sections, **qs)
    assert "(cash basis)" in _get(client, "trial-balance/csv", basis="cash", **qs).text
    assert "(cash basis)" not in _get(client, "trial-balance/csv", **qs).text
    client.put("/api/settings", json={"report_basis": "cash"})
    assert tb()["basis"] == "cash" and row(tb(), "1100")["net_balance"] == 0.0
    assert tb("accrual") == accrual
    r = client.get("/api/reports/trial-balance?basis=modified")
    assert r.status_code == 422


def test_the_package_ledger_and_trial_balance_follow_the_company_basis(client, scene):
    import io
    import zipfile

    def package():
        r = client.get(f"/api/reports/accountants-package?{YEAR}")
        assert r.status_code == 200, r.text
        return zipfile.ZipFile(io.BytesIO(r.content))

    names = package().namelist()
    tb_name = next(n for n in names if n.startswith("trial-balance_"))
    gl_name = next(n for n in names if n.startswith("general-ledger_"))
    accrual = package()
    assert "(cash basis)" not in accrual.read(tb_name).decode("utf-8-sig")
    assert "as posted" not in accrual.read("README.txt").decode()

    client.put("/api/settings", json={"report_basis": "cash"})
    cash = package()
    for name in (tb_name, gl_name):
        assert "(cash basis)" in cash.read(name).decode("utf-8-sig"), name
    readme = cash.read("README.txt").decode()
    assert "Trial Balance and General Ledger are on a cash basis" in readme
    assert "as posted (accrual)" in readme  # the agings only
