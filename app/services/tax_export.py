# ============================================================================
# Tax Report Export — Schedule C (Profit or Loss from Business)
# Feature 19: Generate from P&L data, output PDF and CSV
# ============================================================================

import io
import re
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.models.tax import TaxCategoryMapping
from app.services.accounting import _q

# Schedule C (Form 1040) lines an account can land on, in form order:
# (line id, label, part). "income" lines add to gross income (line 7),
# "deduction" lines (returns, cost of goods sold) subtract from it, and
# "expense" lines are Part II, which total to line 28.
SCHEDULE_C_LINES = [
    ("1", "Gross receipts or sales", "income"),
    ("2", "Returns and allowances", "deduction"),
    ("4", "Cost of goods sold", "deduction"),
    ("6", "Other income", "income"),
    ("8", "Advertising", "expense"),
    ("9", "Car and truck expenses", "expense"),
    ("10", "Commissions and fees", "expense"),
    ("11", "Contract labor", "expense"),
    ("12", "Depletion", "expense"),
    ("13", "Depreciation and section 179 expense", "expense"),
    ("14", "Employee benefit programs", "expense"),
    ("15", "Insurance (other than health)", "expense"),
    ("16a", "Interest - mortgage", "expense"),
    ("16b", "Interest - other", "expense"),
    ("17", "Legal and professional services", "expense"),
    ("18", "Office expense", "expense"),
    ("19", "Pension and profit-sharing plans", "expense"),
    ("20a", "Rent or lease - vehicles, machinery, and equipment", "expense"),
    ("20b", "Rent or lease - other business property", "expense"),
    ("21", "Repairs and maintenance", "expense"),
    ("22", "Supplies", "expense"),
    ("23", "Taxes and licenses", "expense"),
    ("24a", "Travel", "expense"),
    ("24b", "Deductible meals", "expense"),
    ("25", "Utilities", "expense"),
    ("26", "Wages", "expense"),
    ("27a", "Other expenses", "expense"),
    ("27b", "Energy efficient commercial buildings deduction", "expense"),
]
_LINES = {lid: (label, part) for lid, label, part in SCHEDULE_C_LINES}
_ORDER = {lid: i for i, (lid, _label, _part) in enumerate(SCHEDULE_C_LINES)}

# The seeded chart (app/seed/chart_of_accounts.py), keyed on the EXACT
# account number. The old table matched number prefixes against a chart the
# app never seeds (6500 Rent landed on Supplies, 6900 Utilities on Rent)
# and compared display strings with `"Line 1" in line`, which also matched
# Lines 10-18 and moved those expenses into gross income. An account not
# listed here falls back by its type (_TYPE_DEFAULT).
DEFAULT_MAPPINGS = {
    "4000": "1",  # Service Income
    "4100": "1",  # Product Sales
    "4200": "1",  # Material Income
    "4300": "1",  # Labor Income
    "4400": "6",  # In-Kind Contributions
    "4900": "6",  # Other Income
    "5000": "4",  # Cost of Goods Sold
    "5100": "4",  # Materials Cost
    "5200": "4",  # Labor Cost
    "5300": "4",  # Subcontractor Costs
    "6000": "8",  # Advertising & Marketing
    "6100": "9",  # Auto & Truck Expense
    "6110": "26",  # Wages & Salaries
    "6120": "23",  # Payroll Tax Expense
    "6130": "15",  # Workers Compensation Insurance
    "6140": "27a",  # Employee Expense Reimbursements
    "6150": "14",  # Employee Benefits Expense
    "6160": "26",  # Paid Time Off Expense (wages)
    "6200": "27a",  # Bank Charges & Fees
    "6300": "15",  # Insurance
    "6400": "18",  # Office Supplies
    "6500": "20b",  # Rent or Lease
    "6600": "21",  # Repairs & Maintenance
    "6700": "25",  # Telephone & Internet
    "6800": "22",  # Tools & Equipment
    "6810": "13",  # Depreciation Expense
    "6900": "25",  # Utilities
    "6950": "27a",  # Miscellaneous Expense
    "6960": "27a",  # Bad Debt Expense
}
_TYPE_DEFAULT = {
    AccountType.INCOME: "1",
    AccountType.COGS: "4",
    AccountType.EXPENSE: "27a",
}
# A bare number people type for a line that the form splits in two.
_ALIASES = {"27": "27a"}
_LINE_ID_RE = re.compile(r"\bline\s*(\d{1,2}[ab]?)\b", re.IGNORECASE)


def schedule_c_line_id(tax_line: str | None) -> str | None:
    """The Schedule C line a user-configured mapping names ("Line 18",
    "Schedule C, Line 20b", "Line 27 - Other"), or None when it names no
    line this report knows."""
    if not tax_line:
        return None
    m = _LINE_ID_RE.search(tax_line)
    if not m:
        return None
    lid = m.group(1).lower()
    lid = _ALIASES.get(lid, lid)
    return lid if lid in _LINES else None


def _label(line_id: str) -> str:
    return f"Line {line_id} - {_LINES[line_id][0]}"


def get_schedule_c_data(db: Session, start_date: date, end_date: date) -> dict:
    """Generate Schedule C data from P&L accounts.

    Each account lands on one line: the user's mapping when it names a line,
    else the seeded chart's line, else the default for its type. A line's
    amounts are in the line's own sense (receipts and other income as
    credits, returns, COGS and expenses as debits), so net profit always
    equals the Profit & Loss net income, whatever the mapping.
    """

    custom_mappings = {
        m.account_id: m.tax_line for m in db.query(TaxCategoryMapping).all()
    }

    # Get all income and expense transactions in period
    results = (
        db.query(
            Account,
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(TransactionLine, TransactionLine.account_id == Account.id)
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            Account.account_type.in_(
                [AccountType.INCOME, AccountType.EXPENSE, AccountType.COGS]
            )
        )
        .filter(Transaction.date >= start_date, Transaction.date <= end_date)
        .group_by(Account.id)
        .all()
    )

    lines: dict[str, dict] = {}
    for acct, total_debit, total_credit in results:
        custom = custom_mappings.get(acct.id)
        line_id = schedule_c_line_id(custom)
        if line_id is None and not custom:
            line_id = DEFAULT_MAPPINGS.get(acct.account_number or "")
            # The seeded chart's line, only for an account of the seeded
            # kind: an imported chart's 5000 may be Advertising, not COGS.
            type_part = _LINES[_TYPE_DEFAULT[acct.account_type]][1]
            if line_id is not None and _LINES[line_id][1] != type_part:
                line_id = None
        if line_id is None and not custom:
            line_id = _TYPE_DEFAULT[acct.account_type]

        if line_id is not None:
            key, label, part = line_id, _label(line_id), _LINES[line_id][1]
            order = (_ORDER[line_id], "")
        else:
            # A user label that names no line: keep it, and count it where
            # the account's type belongs so the totals still add up.
            fallback = _TYPE_DEFAULT[acct.account_type]
            part = _LINES[fallback][1]
            key, label = f"custom:{custom}:{part}", custom
            order = (_ORDER[fallback], custom)

        debit = Decimal(str(total_debit or 0))
        credit = Decimal(str(total_credit or 0))
        amount = _q(credit - debit) if part == "income" else _q(debit - credit)

        entry = lines.setdefault(
            key,
            {
                "line": label,
                "line_id": line_id,
                "part": part,
                "accounts": [],
                "total": Decimal("0"),
                "_order": order,
            },
        )
        entry["accounts"].append(
            {
                "account_number": acct.account_number,
                "account_name": acct.name,
                "amount": float(amount),
            }
        )
        entry["total"] += amount

    sorted_lines = sorted(lines.values(), key=lambda ln: ln.pop("_order"))

    def _sum(pred) -> Decimal:
        return sum((ln["total"] for ln in sorted_lines if pred(ln)), Decimal("0"))

    gross_receipts = _sum(lambda ln: ln["line_id"] == "1")
    returns = _sum(lambda ln: ln["line_id"] == "2")
    cogs = _sum(lambda ln: ln["part"] == "deduction" and ln["line_id"] != "2")
    other_income = _sum(lambda ln: ln["part"] == "income" and ln["line_id"] != "1")
    gross_profit = gross_receipts - returns - cogs  # line 5
    gross_income = gross_profit + other_income  # line 7
    total_expenses = _sum(lambda ln: ln["part"] == "expense")  # line 28
    net_profit = gross_income - total_expenses  # line 31

    for ln in sorted_lines:
        ln["total"] = float(ln["total"])

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "lines": sorted_lines,
        "gross_receipts": float(gross_receipts),
        "returns_and_allowances": float(returns),
        "cost_of_goods_sold": float(cogs),
        "gross_profit": float(gross_profit),
        "other_income": float(other_income),
        "gross_income": float(gross_income),
        "total_expenses": float(total_expenses),
        "net_profit": float(net_profit),
    }


def export_schedule_c_csv(data: dict) -> str:
    """Export Schedule C data as CSV.

    Uses the shared formula-injection-safe writer because line["accounts"]
    contains user-supplied account names; a chart-of-accounts entry named
    `=HYPERLINK("https://evil.example/")` would otherwise execute on open
    in Excel / Sheets / Numbers.
    """
    from app.services.csv_export import _SafeWriter

    output = io.StringIO()
    writer = _SafeWriter(output)
    writer.writerow(["Schedule C - Profit or Loss from Business"])
    writer.writerow([f"Period: {data['start_date']} to {data['end_date']}"])
    writer.writerow([])
    writer.writerow(["Tax Line", "Account #", "Account Name", "Amount"])

    for line in data["lines"]:
        for acct in line["accounts"]:
            writer.writerow(
                [
                    line["line"],
                    acct["account_number"],
                    acct["account_name"],
                    f"{acct['amount']:.2f}",
                ]
            )
        writer.writerow([f"  Total: {line['line']}", "", "", f"{line['total']:.2f}"])

    writer.writerow([])
    for label, key in (
        ("GROSS RECEIPTS (LINE 1)", "gross_receipts"),
        ("RETURNS AND ALLOWANCES (LINE 2)", "returns_and_allowances"),
        ("COST OF GOODS SOLD (LINE 4)", "cost_of_goods_sold"),
        ("OTHER INCOME (LINE 6)", "other_income"),
        ("GROSS INCOME (LINE 7)", "gross_income"),
        ("TOTAL EXPENSES (LINE 28)", "total_expenses"),
        ("NET PROFIT (LOSS) (LINE 31)", "net_profit"),
    ):
        writer.writerow([label, "", "", f"{data[key]:.2f}"])
    writer.writerow([])
    writer.writerow(
        ["DISCLAIMER: This report is for reference only. Consult a tax professional."]
    )

    return output.getvalue()
