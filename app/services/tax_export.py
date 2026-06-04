# ============================================================================
# Tax Report Export — Schedule C (Profit or Loss from Business)
# Feature 19: Generate from P&L data, output PDF and CSV
# ============================================================================

import io
from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.models.tax import TaxCategoryMapping

# Default Schedule C line mappings by account number prefix
DEFAULT_MAPPINGS = {
    "4000": "Line 1 - Gross receipts or sales",
    "4100": "Line 1 - Gross receipts or sales",
    "4200": "Line 1 - Gross receipts or sales",
    "4900": "Line 6 - Other income",
    "5000": "Line 4 - Cost of goods sold",
    "6000": "Line 27 - Other expenses",
    "6100": "Line 10 - Commissions and fees",
    "6200": "Line 17 - Legal and professional",
    "6300": "Line 15 - Insurance",
    "6400": "Line 18 - Office expense",
    "6500": "Line 22 - Supplies",
    "6600": "Line 24a - Travel",
    "6700": "Line 25 - Utilities",
    "6800": "Line 20a - Rent - vehicles/machinery",
    "6900": "Line 20b - Rent - other business property",
    "7000": "Line 9 - Car and truck expenses",
    "7100": "Line 23 - Taxes and licenses",
    "7200": "Line 13 - Depreciation",
}


def get_schedule_c_data(db: Session, start_date: date, end_date: date) -> dict:
    """Generate Schedule C data from P&L accounts."""

    # Get custom mappings
    custom_mappings = {}
    for m in db.query(TaxCategoryMapping).all():
        custom_mappings[m.account_id] = m.tax_line

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

    lines = {}
    for acct, total_debit, total_credit in results:
        # Determine the tax line
        tax_line = custom_mappings.get(acct.id)
        if not tax_line:
            # Fall back to default by account number prefix
            for prefix, default_line in DEFAULT_MAPPINGS.items():
                if acct.account_number and acct.account_number.startswith(prefix):
                    tax_line = default_line
                    break
        if not tax_line:
            tax_line = (
                "Line 27 - Other expenses"
                if acct.account_type == AccountType.EXPENSE
                else "Line 1 - Gross receipts or sales"
            )

        # Calculate amount (income = credit - debit, expense = debit - credit)
        if acct.account_type == AccountType.INCOME:
            amount = float(total_credit - total_debit)
        else:
            amount = float(total_debit - total_credit)

        if tax_line not in lines:
            lines[tax_line] = {"line": tax_line, "accounts": [], "total": 0}
        lines[tax_line]["accounts"].append(
            {
                "account_number": acct.account_number,
                "account_name": acct.name,
                "amount": amount,
            }
        )
        lines[tax_line]["total"] += amount

    # Sort by line number
    sorted_lines = sorted(lines.values(), key=lambda x: x["line"])

    gross_income = sum(
        ln["total"]
        for ln in sorted_lines
        if "Line 1" in ln["line"] or "Line 6" in ln["line"]
    )
    total_expenses = sum(
        ln["total"]
        for ln in sorted_lines
        if "Line 1" not in ln["line"] and "Line 6" not in ln["line"]
    )
    net_profit = gross_income - total_expenses

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "lines": sorted_lines,
        "gross_income": gross_income,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
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
    writer.writerow(["GROSS INCOME", "", "", f"{data['gross_income']:.2f}"])
    writer.writerow(["TOTAL EXPENSES", "", "", f"{data['total_expenses']:.2f}"])
    writer.writerow(["NET PROFIT (LOSS)", "", "", f"{data['net_profit']:.2f}"])
    writer.writerow([])
    writer.writerow(
        ["DISCLAIMER: This report is for reference only. Consult a tax professional."]
    )

    return output.getvalue()
