"""Spreadsheet and printable exports of the core ledger reports (#179).

One layout per report, the same columns as the screen, with a short
preamble (company, report, period) so a file saved from one company can be
read without the program — and, later, fed to group reporting (#180), which
reads one trial balance per company. The preamble is always four lines
(three labelled rows and a blank) so a reader can skip it by count.

Amounts are written with two decimals and no currency sign or thousands
separator, so a spreadsheet reads them as numbers.
"""

from __future__ import annotations

import csv
import io
from decimal import ROUND_HALF_UP, Decimal

from app.services.csv_export import _SafeWriter


def _money(v) -> Decimal:
    # A Decimal, not a string: the safe writer only guards text cells, so a
    # negative amount stays a number in the spreadsheet instead of "'-300.00".
    return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _preamble(writer, company: str, report: str, period: str) -> None:
    writer.writerow(["Company", company])
    writer.writerow(["Report", report])
    writer.writerow(["Period", period])
    writer.writerow([])


def trial_balance_csv(data: dict, company: str) -> str:
    """Account number | Account name | Type | Debit | Credit | Net, one row
    per account with activity, then a totals row where Debit equals Credit.
    Net is debit minus credit, as the screen shows it."""
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(
        w, company, "Trial Balance", f"{data['start_date']} to {data['end_date']}"
    )
    w.writerow(["Account number", "Account name", "Type", "Debit", "Credit", "Net"])
    for i in data["items"]:
        w.writerow(
            [
                i["account_number"],
                i.get("account_path") or i["account_name"],  # W-12
                i["account_type"],
                _money(i["total_debit"]),
                _money(i["total_credit"]),
                _money(i["net_balance"]),
            ]
        )
    w.writerow(
        [
            "",
            "Total",
            "",
            _money(data["total_debit"]),
            _money(data["total_credit"]),
            _money(data["difference"]),
        ]
    )
    return out.getvalue()


def general_ledger_csv(data: dict, company: str, words=None) -> str:
    """One row per journal line, grouped by account as the screen is:
    Date | Reference | Description | Account number | Account name | Debit |
    Credit | Running balance | Source type | Class (#213, last so a sheet
    that reads the columns by position still lines up). Each account opens with its
    balance brought forward from before the period and closes with a
    period-total row; the period total's net equals that account's Net on
    the trial balance for the same dates. A ledger for one class says so
    in its preamble, as the PDF's title does, and carries the class on its
    brought-forward and period-total rows too, since those balances are
    the class's alone (NEW-34)."""
    out = io.StringIO()
    w = _SafeWriter(out)
    say = words or str
    cls = data.get("class_name") or ""
    period = f"{data['start_date']} to {data['end_date']}"
    if cls:
        period += (
            f" ({say('Class')}: {cls} only — each balance brought forward and"
            f" running balance counts this {say('class').lower()}'s lines alone,"
            " not the account's whole balance)"
        )
    _preamble(w, company, "General Ledger" + (f" — {cls}" if cls else ""), period)
    w.writerow(
        [
            "Date",
            "Reference",
            "Description",
            "Account number",
            "Account name",
            "Debit",
            "Credit",
            "Running balance",
            "Source type",
            "Class",
        ]
    )
    for a in data["accounts"]:
        num = a["account_number"] or ""
        name = a.get("account_path") or a["account_name"]  # W-12
        w.writerow(
            [
                data["start_date"],
                "",
                "Balance brought forward",
                num,
                name,
                "",
                "",
                _money(a["opening_balance"]),
                "opening",
                cls,
            ]
        )
        for e in a["entries"]:
            w.writerow(
                [
                    e["date"],
                    e["reference"],
                    e["description"],
                    num,
                    name,
                    _money(e["debit"]) if e["debit"] else "",
                    _money(e["credit"]) if e["credit"] else "",
                    _money(e["running_balance"]),
                    e["source_type"],
                    e.get("class_name", ""),
                ]
            )
        w.writerow(
            [
                data["end_date"],
                "",
                "Period total",
                num,
                name,
                _money(a["total_debit"]),
                _money(a["total_credit"]),
                _money(a["closing_balance"]),
                "total",
                cls,
            ]
        )
    return out.getvalue()


def profit_loss_csv(data: dict, company: str, words) -> str:
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(
        w,
        company,
        profit_loss_csv_class_line(data, words),
        f"{data['start_date']} to {data['end_date']}",
    )
    w.writerow(["Section", "Account number", "Account name", "Amount"])
    for label, key, total_key in (
        (words("Income"), "income", "total_income"),
        ("Cost of Goods Sold", "cogs", "total_cogs"),
        ("Expenses", "expenses", "total_expenses"),
    ):
        for i in data[key]:
            w.writerow(
                [
                    label,
                    i.get("account_number") or "",
                    i.get("account_path") or i["account_name"],  # W-8
                    _money(i["amount"]),
                ]
            )
        w.writerow([label, "", f"Total {label}", _money(data[total_key])])
    w.writerow(["", "", "Gross Profit", _money(data["gross_profit"])])
    w.writerow(["", "", words("Net Income"), _money(data["net_income"])])
    return out.getvalue()


def profit_loss_csv_class_line(data: dict, words) -> str:
    """The preamble's report line for one class's P&L: which class, and
    that the figures are that class's alone — the PDF's words (NEW-29)."""
    cls = data.get("class_name")
    return words("Profit & Loss") + (
        f" — {words('Class')}: {cls} only, not the company total" if cls else ""
    )


def profit_loss_by_class_csv(
    data: dict, columns: list, company: str, words, layout: str = "wide"
) -> str:
    """The P&L by Class (or by Job) grid as a spreadsheet (#232).

    wide (the default): Section | Account number | Account name | one
    column per class | Total — the grid as the screen shows it, pasted
    into the owner's sheet; the subtotal rows carry the section's name in
    the first column and the subtotal's in the third, so a sheet that
    filters on Section keeps them. long: Section | Account number |
    Account name | Class | Amount, one row per account and class, for a
    pivot table. `columns` are the grid's {name, income, cogs,
    gross_profit, expenses, net_income}, aligned with each account's
    amounts[]. A filtered grid says so in the preamble and labels its
    total "Total (shown)".
    """
    out = io.StringIO()
    w = _SafeWriter(out)
    period = f"{data['start_date']} to {data['end_date']}"
    if data.get("filtered"):
        period += f" ({filtered_phrase(data, columns)})"
    _preamble(w, company, data.get("report_name") or words("P&L by Class"), period)
    dimension = data.get("dimension") or words("Class")
    sections = (
        (words("Income"), "income", words("Total Income"), "total_income"),
        ("Cost of Goods Sold", "cogs", "Gross Profit", "total_gross_profit"),
        ("Expenses", "expenses", "Total Expenses", "total_expenses"),
    )
    if layout == "long":
        w.writerow(["Section", "Account number", "Account name", dimension, "Amount"])
        for label, key, _sub, _sub_key in sections:
            for a in data["accounts"][key]:
                for c, amount in zip(columns, a["amounts"]):
                    if not amount:
                        continue
                    w.writerow(
                        [
                            label,
                            a.get("account_number") or "",
                            a["account_name"],
                            c["name"],
                            _money(amount),
                        ]
                    )
        for c in columns:
            w.writerow(
                ["", "", words("Net Income"), c["name"], _money(c["net_income"])]
            )
        w.writerow(
            ["", "", words("Net Income"), "Total", _money(data["total_net_income"])]
        )
        return out.getvalue()

    total_label = "Total (shown)" if data.get("filtered") else "Total"
    w.writerow(
        ["Section", "Account number", "Account name"]
        + [c["name"] for c in columns]
        + [total_label]
    )
    for label, key, sub, sub_key in sections:
        for a in data["accounts"][key]:
            w.writerow(
                [label, a.get("account_number") or "", a["account_name"]]
                + [_money(x) if x else "" for x in a["amounts"]]
                + [_money(a["total"])]
            )
        sub_col = "gross_profit" if key == "cogs" else key
        w.writerow(
            [label, "", sub]
            + [_money(c[sub_col]) for c in columns]
            + [_money(data[sub_key])]
        )
    w.writerow(
        ["", "", words("Net Income")]
        + [_money(c["net_income"]) for c in columns]
        + [_money(data["total_net_income"])]
    )
    return out.getvalue()


def filtered_phrase(data: dict, columns: list) -> str:
    """What a filtered grid says of itself — the CSV's preamble, the PDF's
    period line and the screen's note, in the same words: how many of the
    columns with activity in the period are shown; a chosen column with
    no activity, left out, or drawn as zeros when the empty ones were
    asked for (NEW-26); and that the totals are the columns shown, not the
    company's. `columns` are the grid's, each saying whether it is empty."""
    shown = sum(1 for c in columns if not c.get("empty"))
    drawn_empty = len(columns) - shown
    chosen_empty = data.get("chosen_empty") or []
    text = f"filtered: {shown} of {data.get('columns_total', shown)} shown"
    if drawn_empty:
        text += f", plus {drawn_empty} with no activity in this period"
        if chosen_empty:
            text += f" ({', '.join(chosen_empty)})"
    elif chosen_empty:
        n = len(chosen_empty)
        text += (
            f"; {n} chosen {'has' if n == 1 else 'have'} no activity in this period"
            f" and {'is' if n == 1 else 'are'} left out ({', '.join(chosen_empty)})"
        )
    return text + "; totals are for the columns shown, not the company"


def balance_sheet_csv(data: dict, company: str, words) -> str:
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, words("Balance Sheet"), f"As of {data['as_of_date']}")
    w.writerow(["Section", "Account number", "Account name", "Amount"])
    for label, key, total_key in (
        ("Assets", "assets", "total_assets"),
        ("Liabilities", "liabilities", "total_liabilities"),
        (words("Equity"), "equity", "total_equity"),
    ):
        for i in data[key]:
            w.writerow(
                [
                    label,
                    i.get("account_number") or "",
                    i.get("account_path") or i["account_name"],  # W-8
                    _money(i["amount"]),
                ]
            )
        w.writerow([label, "", f"Total {label}", _money(data[total_key])])
    w.writerow(
        [
            "",
            "",
            words("Liabilities + Equity"),
            _money(data["total_liabilities"] + data["total_equity"]),
        ]
    )
    return out.getvalue()


def rows_of(text: str) -> list[list[str]]:
    """The table rows of an export, preamble skipped — for tests and for any
    reader that wants the data (group reporting, #180)."""
    return list(csv.reader(io.StringIO(text)))[4:]
