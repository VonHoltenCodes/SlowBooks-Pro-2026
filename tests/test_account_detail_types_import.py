"""Every importer keeps the finer type its file carries (#390), and the IIF
lists round trip QuickBooks' fifteen ACCNTTYPEs exactly."""

import io
from pathlib import Path

import pytest

from app.models.accounts import Account, AccountType, DetailType
from app.services.iif_common import (
    IIF_TO_ACCOUNT_TYPE,
    IIF_TO_DETAIL_TYPE,
    account_to_iif_type,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _iif(client, text):
    r = client.post(
        "/api/iif/import",
        files={"file": ("lists.iif", io.BytesIO(text.encode("utf-8")), "text/plain")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _accounts(db_session):
    db_session.expire_all()
    return {a.name: a for a in db_session.query(Account).all()}


# --- IIF ----------------------------------------------------------------------

FIFTEEN = [
    ("Bank of the Bay", "BANK", "1050"),
    ("Accounts Receivable", "AR", "1100"),
    ("Employee Advances", "OCASSET", "1250"),
    ("Compressor", "FIXASSET", "1550"),
    ("Security Deposit", "OASSET", "1850"),
    ("Accounts Payable", "AP", "2000"),
    ("Shop Visa", "CCARD", "2150"),
    ("Customer Deposits", "OCLIAB", "2550"),
    ("Truck Note", "LTLIAB", "2650"),
    ("Partner Capital", "EQUITY", "3050"),
    ("Sign Sales", "INC", "4050"),
    ("Freight In", "COGS", "5050"),
    ("Shop Rent", "EXP", "6050"),
    ("Interest Earned", "EXINC", "7050"),
    ("Penalties", "EXEXP", "8050"),
]


def _lists(rows) -> str:
    out = "!ACCNT\tNAME\tACCNTTYPE\tACCNUM\n"
    for name, iif_type, number in rows:
        out += f"ACCNT\t{name}\t{iif_type}\t{number}\n"
    return out


def test_the_fifteen_accnttypes_are_the_fifteen_detail_types():
    assert set(IIF_TO_DETAIL_TYPE.values()) == set(DetailType)
    assert len(IIF_TO_DETAIL_TYPE) == 15
    for iif, detail in IIF_TO_DETAIL_TYPE.items():
        assert IIF_TO_ACCOUNT_TYPE[iif].value in (
            "asset",
            "liability",
            "equity",
            "income",
            "cogs",
            "expense",
        )
    assert "NONPOSTING" not in IIF_TO_ACCOUNT_TYPE


def test_the_fifteen_accnttypes_round_trip_exactly(client, db_session):
    """Import the fifteen, export the chart, and every account goes out with
    the ACCNTTYPE it came in with: 2400-style loans as LTLIAB, cards as CCARD."""
    result = _iif(client, _lists(FIFTEEN))
    assert result["errors"] == [], result
    accts = _accounts(db_session)
    for name, iif_type, _number in FIFTEEN:
        a = accts[name]
        assert a.detail_type == IIF_TO_DETAIL_TYPE[iif_type].value, name
        assert account_to_iif_type(a) == iif_type, name
    assert accts["Bank of the Bay"].bank_kind == "bank"
    assert accts["Shop Visa"].bank_kind == "credit_card"

    text = client.get("/api/iif/export/accounts").text
    out = {
        r[1]: r[2]
        for r in (ln.split("\t") for ln in text.splitlines())
        if r[0] == "ACCNT"
    }
    for name, iif_type, _number in FIFTEEN:
        assert out[name] == iif_type, name


def test_the_seeded_chart_exports_by_detail_type(client, seed_accounts):
    text = client.get("/api/iif/export/accounts").text
    out = {
        r[4]: r[2]
        for r in (ln.split("\t") for ln in text.splitlines())
        if r[0] == "ACCNT"
    }
    # the number-range guess sent these the other way round, and no CCARD
    assert out["2400"] == "LTLIAB"
    assert out["2500"] == "OCLIAB"
    assert out["2100"] == "CCARD"
    assert out["1000"] == out["1010"] == "BANK"
    assert out["1100"] == "AR" and out["2000"] == "AP"
    assert out["1500"] == out["1510"] == out["1600"] == "FIXASSET"
    assert out["1700"] == "OASSET"
    assert out["4900"] == "EXINC"
    assert out["6000"] == "EXP"


def test_non_posting_accounts_are_skipped_not_made_assets(client, db_session):
    text = _lists(
        [("Purchase Orders", "NONPOSTING", ""), ("Estimates", "NONPOSTING", "")]
    )
    report = client.post(
        "/api/iif/validate",
        files={"file": ("l.iif", io.BytesIO(text.encode()), "text/plain")},
    ).json()
    assert any("non-posting" in w for w in report["warnings"]), report
    result = _iif(client, text)
    assert result["errors"] == []
    names = set(_accounts(db_session))
    assert "Purchase Orders" not in names and "Estimates" not in names
    assert any("Purchase Orders" in w and "skipped" in w for w in result["warnings"])


def test_a_second_receivable_comes_in_as_a_current_asset(client, db_session):
    result = _iif(client, _lists([("Retainage Receivable", "AR", "1150")]))
    acct = _accounts(db_session)["Retainage Receivable"]
    assert acct.account_type == AccountType.ASSET
    assert acct.detail_type == "other_current_asset"
    assert any("Retainage Receivable" in w for w in result["warnings"]), result


def test_an_import_never_changes_the_charts_detail_type(
    client, db_session, seed_accounts
):
    result = _iif(client, _lists([("Loan Payable", "OCLIAB", "2400")]))
    assert seed_accounts["2400"].detail_type == "long_term_liability"
    db_session.refresh(seed_accounts["2400"])
    assert seed_accounts["2400"].detail_type == "long_term_liability"
    assert any(
        "'Loan Payable' is Other Current Liability in the file and Long Term "
        "Liability in the chart" in w
        for w in result["warnings"]
    ), result["warnings"]


def test_the_qb_mac_sample_still_imports(client, db_session):
    text = (FIXTURES / "sample_qbmac_opening_balances.iif").read_text(encoding="utf-8")
    result = _iif(client, text)
    names = _accounts(db_session)
    assert "Sales Orders" not in names and "Purchase Orders" not in names
    assert not result["errors"], result["errors"]


# --- the chart CSV import -----------------------------------------------------


def _chart(client, text):
    r = client.post(
        "/api/csv/import/accounts?dry_run=0",
        files={"file": ("chart.csv", io.BytesIO(text.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_the_chart_import_reads_type_words_and_the_detail_type_column(
    client, db_session
):
    out = _chart(
        client,
        "Number,Name,Type,Detail type\n"
        "1550,Excavator,fixed asset,\n"
        "1850,Deposits held,asset,Other Asset\n"
        "2650,Equipment note,liability,long_term_liability\n"
        "4950,Interest,other income,\n"
        "8100,Penalties,expense,Other Expense\n"
        "6100,Rent,expense,\n",
    )
    assert out["errors"] == [], out
    got = {n: a.detail_type for n, a in _accounts(db_session).items()}
    assert got["Excavator"] == "fixed_asset"
    assert got["Deposits held"] == "other_asset"
    assert got["Equipment note"] == "long_term_liability"
    assert got["Interest"] == "other_income"
    assert got["Penalties"] == "other_expense"
    assert got["Rent"] == "expense"


def test_the_chart_import_refuses_a_detail_type_against_its_type(client):
    r = client.post(
        "/api/csv/import/accounts",
        files={
            "file": (
                "c.csv",
                io.BytesIO(
                    b"Number,Name,Type,Detail type\n1550,Odd,expense,Fixed Asset\n"
                ),
                "text/csv",
            )
        },
    ).json()
    assert any("Odd" in e for e in r["errors"]), r


def test_the_chart_export_reimports_its_detail_types(client, db_session, seed_accounts):
    text = client.get("/api/csv/export/accounts").text.lstrip("﻿")
    seed_accounts["1700"].name = "Other Assets"  # unchanged; the row matches
    out = _chart(client, text.replace("Other Assets", "Long-term deposits"))
    assert out["errors"] == []
    acct = db_session.query(Account).filter_by(account_number="1700").one()
    db_session.refresh(acct)
    assert (acct.name, acct.detail_type) == ("Long-term deposits", "other_asset")


# --- the migration dialects ---------------------------------------------------


@pytest.mark.parametrize(
    "module,coa,expected",
    [
        (
            "xero",
            "Code,Name,Type\n"
            "710,Office Equipment,Fixed Asset\n"
            "720,Bond,Non-current Asset\n"
            "900,Term Loan,Non-current Liability\n"
            "260,Interest Income,Other Income\n"
            "610,Prepaid,Current Asset\n",
            {
                "Office Equipment": DetailType.FIXED_ASSET,
                "Bond": DetailType.OTHER_ASSET,
                "Term Loan": DetailType.LONG_TERM_LIABILITY,
                "Interest Income": DetailType.OTHER_INCOME,
                "Prepaid": None,
            },
        ),
        (
            "wave",
            "Account Name,Account Type\n"
            'Truck,"Property, Plant, Equipment"\n'
            "Accum. Dep.,Depreciation and Amortization\n"
            "Deposits,Other Long-Term Asset\n"
            "LOC,Loan and Line of Credit\n"
            "Shareholder loan,Other Long-Term Liability\n"
            "Interest,Other Income\n"
            "Rent,Operating Expense\n",
            {
                "Truck": DetailType.FIXED_ASSET,
                "Accum. Dep.": DetailType.FIXED_ASSET,
                "Deposits": DetailType.OTHER_ASSET,
                "LOC": DetailType.LONG_TERM_LIABILITY,
                "Shareholder loan": DetailType.LONG_TERM_LIABILITY,
                "Interest": DetailType.OTHER_INCOME,
                "Rent": None,
            },
        ),
        (
            "sage",
            "Account ID,Account Description,Account Type\n"
            "11000,Accounts Receivable,1\n"
            "15000,Furniture,5\n"
            "17000,Accum Depr - Furniture,6\n"
            "19000,Deposits,8\n"
            "20000,Accounts Payable,10\n"
            "27000,Long Term Note,14\n"
            "40000,Sales,21\n",
            {
                "Accounts Receivable": DetailType.ACCOUNTS_RECEIVABLE,
                "Furniture": DetailType.FIXED_ASSET,
                "Accum Depr - Furniture": DetailType.FIXED_ASSET,
                "Deposits": DetailType.OTHER_ASSET,
                "Accounts Payable": DetailType.ACCOUNTS_PAYABLE,
                "Long Term Note": DetailType.LONG_TERM_LIABILITY,
                "Sales": None,
            },
        ),
        (
            "zoho",
            "Account Name,Account Type,Account Code\n"
            "Furniture,Fixed Asset,1500\n"
            "Deposits,Other Asset,1800\n"
            "Mortgage,Long Term Liability,2700\n"
            "Interest Income,Other Income,4900\n"
            "Penalties,Other Expense,8000\n",
            {
                "Furniture": DetailType.FIXED_ASSET,
                "Deposits": DetailType.OTHER_ASSET,
                "Mortgage": DetailType.LONG_TERM_LIABILITY,
                "Interest Income": DetailType.OTHER_INCOME,
                "Penalties": DetailType.OTHER_EXPENSE,
            },
        ),
        (
            "myob",
            "Account Number,Account Name,Account Type\n"
            "1-2100,Plant,Fixed Asset\n"
            "1-3100,Bond,Other Asset\n"
            "2-2100,Bank Loan,Long Term Liability\n"
            "4-9000,Interest,Other Income\n"
            "9-1000,Fines,Other Expense\n",
            {
                "Plant": DetailType.FIXED_ASSET,
                "Bond": DetailType.OTHER_ASSET,
                "Bank Loan": DetailType.LONG_TERM_LIABILITY,
                "Interest": DetailType.OTHER_INCOME,
                "Fines": DetailType.OTHER_EXPENSE,
            },
        ),
    ],
)
def test_each_dialect_keeps_its_finer_types(module, coa, expected):
    import importlib

    mod = importlib.import_module(f"app.services.{module}_import")
    accounts, errors = mod.parse_coa(coa)
    assert errors == [], errors
    got = {a["name"]: a.get("detail_type") for a in accounts}
    assert got == expected


def test_the_qbo_reports_keep_the_innermost_heading():
    from app.services import qbo_export_import as qbo

    text = (FIXTURES / "qbo_export" / "Balance_sheet.csv").read_text(encoding="utf-8")
    text = text.replace(
        "TOTAL ASSETS,=5260.00\n",
        "   Fixed Assets,\n      Truck,=9000.00\n   Total Fixed Assets,=9000.00\n"
        "TOTAL ASSETS,=14260.00\n",
    ).replace(
        "   Total Liabilities,=620.00\n",
        "      Long-Term Liabilities,\n         Truck Loan,=9000.00\n"
        "      Total Long-Term Liabilities,=9000.00\n"
        "   Total Liabilities,=9620.00\n",
    )
    accounts, errors = qbo.parse_coa(text)
    assert errors == []
    got = {a["name"]: a["detail_type"] for a in accounts}
    assert got["Operating Account"] == DetailType.BANK
    assert got["Undeposited Funds"] == DetailType.OTHER_CURRENT_ASSET
    assert got["Truck"] == DetailType.FIXED_ASSET
    assert got["Company Visa"] == DetailType.CREDIT_CARD
    assert got["Escrow Held"] == DetailType.OTHER_CURRENT_LIABILITY
    assert got["Truck Loan"] == DetailType.LONG_TERM_LIABILITY
    assert got["Accounts Receivable (A/R)"] is None


def test_the_qbo_report_imports_cost_of_goods_sold_as_cogs():
    """A QuickBooks Online P&L's Cost of Goods Sold section is the COGS
    type, above Gross Profit, not an expense (2.26 imported it as one)."""
    from app.models.accounts import AccountType
    from app.services import qbo_export_import as qbo

    text = (FIXTURES / "qbo_export" / "Profit_and_loss.csv").read_text(encoding="utf-8")
    text = text.replace(
        "Gross Profit,=4550.00\n",
        "Cost of Goods Sold,\n   Materials,=500.00\n"
        "Total Cost of Goods Sold,=500.00\nGross Profit,=4050.00\n",
    )
    accounts, errors = qbo.parse_coa(text)
    assert errors == []
    got = {a["name"]: a["type"] for a in accounts}
    assert got["Materials"] == AccountType.COGS
    assert got["Advertising"] == AccountType.EXPENSE


def _nonprofit_words(text):
    """The same statement as QuickBooks Online prints it for a company set up
    as a nonprofit: Revenue for Income, Expenditures for Expenses, and the
    arithmetic lines renamed with them. Only the report's own labels change;
    an account named "Commission Income" keeps its name."""
    words = {
        "Income": "Revenue",
        "Total Income": "Total Revenue",
        "Other Income": "Other Revenue",
        "Total Other Income": "Total Other Revenue",
        "Net Operating Income": "Net Operating Revenue",
        "Net Other Income": "Net Other Revenue",
        "Net Income": "Net Revenue",
        "Expenses": "Expenditures",
        "Total Expenses": "Total Expenditures",
        "Other Expenses": "Other Expenditures",
        "Total Other Expenses": "Total Other Expenditures",
        "Equity": "Net Assets",
        "Total Equity": "Total Net Assets",
        "LIABILITIES AND EQUITY": "LIABILITIES AND NET ASSETS",
        "TOTAL LIABILITIES AND EQUITY": "TOTAL LIABILITIES AND NET ASSETS",
    }
    out = []
    for line in text.splitlines(keepends=True):
        label, sep, rest = line.partition(",")
        indent = label[: len(label) - len(label.lstrip(" "))]
        out.append(indent + words.get(label.strip(), label.strip()) + sep + rest)
    return "".join(out)


def test_the_qbo_report_reads_a_nonprofits_revenue_and_expenditures():
    """A nonprofit company's QuickBooks Online statement groups its accounts
    under Revenue and Expenditures (Other Revenue, Other Expenditures; Net
    Revenue for the arithmetic). The importer took only the business words,
    so a Hacksburg export's dry run stopped with every income and expense
    account sitting "under no section that names its type" (2.31.11)."""
    from app.models.accounts import AccountType
    from app.services import qbo_export_import as qbo

    business = (FIXTURES / "qbo_export" / "Profit_and_loss.csv").read_text(
        encoding="utf-8"
    )
    business = business.replace(
        "Net Operating Income,=3640.00\n",
        "Net Operating Income,=3640.00\nOther Income,\n   Interest Earned,=12.00\n"
        "Total Other Income,=12.00\nOther Expenses,\n   Bank Charges,=5.00\n"
        "Total Other Expenses,=5.00\nNet Other Income,=7.00\n",
    )
    nonprofit = _nonprofit_words(business)
    assert (
        "Income" not in nonprofit.replace("Commission Income", "")
        and "Expenses" not in nonprofit
    )
    want, errors = qbo.parse_coa(business)
    assert errors == []
    got, errors = qbo.parse_coa(nonprofit)
    assert errors == []
    assert {a["name"]: a["type"] for a in got} == {a["name"]: a["type"] for a in want}
    types = {a["name"]: a["type"] for a in got}
    assert types["Commission Income"] == AccountType.INCOME
    assert types["Interest Earned"] == AccountType.INCOME
    assert types["Advertising"] == AccountType.EXPENSE
    assert types["Bank Charges"] == AccountType.EXPENSE
    # the arithmetic lines are not accounts
    assert not {
        "Net Revenue",
        "Net Operating Revenue",
        "Net Other Revenue",
        "Gross Profit",
    } & set(types)


def test_the_qbo_balance_sheet_reads_a_nonprofits_net_assets():
    """The same company's Balance Sheet says Net Assets where a business
    file says Equity, and Net Revenue under it for the year's result
    (macbase1's question, 2.31.11 round 4): the section is equity, the
    arithmetic line no account, and the chart is the business file's."""
    from app.models.accounts import AccountType
    from app.services import qbo_export_import as qbo

    business = (FIXTURES / "qbo_export" / "Balance_sheet.csv").read_text(
        encoding="utf-8"
    )
    nonprofit = _nonprofit_words(business)
    labels = {line.partition(",")[0].strip() for line in nonprofit.splitlines()}
    assert not labels & {
        "Equity",
        "Total Equity",
        "LIABILITIES AND EQUITY",
        "Net Income",
    }
    assert "Opening Balance Equity" in labels  # an account keeps its name
    want, errors = qbo.parse_coa(business)
    assert errors == []
    got, errors = qbo.parse_coa(nonprofit)
    assert errors == []
    assert {a["name"]: a["type"] for a in got} == {a["name"]: a["type"] for a in want}
    types = {a["name"]: a["type"] for a in got}
    assert AccountType.EQUITY in types.values()
    assert "Net Revenue" not in types and "Net Assets" not in types


def test_a_dialects_detail_type_lands_on_the_account(client, db_session, seed_accounts):
    """Through the shared engine: the detail type reaches the row, and an AR
    the posting code does not use takes the default."""
    coa = (
        "Account ID,Account Description,Account Type\n"
        "10200,Regular Checking Account,Cash\n"
        "11500,Retainage Receivable,1\n"
        "15000,Furniture,5\n"
        "27000,Long Term Note,14\n"
        "40000,Sales,Income\n"
    )
    gl = (
        "Date,Reference,Jrnl,Account ID,Trans Description,Debit Amt,Credit Amt\n"
        '03/01/2026,DEP001,CRJ,10200,Deposit,"1,000.00",\n'
        '03/01/2026,DEP001,CRJ,40000,Deposit,,"1,000.00"\n'
    )
    files = [
        ("files", ("chart.csv", io.BytesIO(coa.encode()), "text/plain")),
        ("files", ("generalledger.csv", io.BytesIO(gl.encode()), "text/plain")),
    ]
    data = client.post("/api/migration/sage/import", files=files).json()
    assert data["ok"] is True, data
    got = {n: a.detail_type for n, a in _accounts(db_session).items()}
    assert got["Furniture"] == "fixed_asset"
    assert got["Long Term Note"] == "long_term_liability"
    assert got["Retainage Receivable"] == "other_current_asset"
