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


class _Percent:
    """A % Change cell. Not a str, so the safe writer leaves it alone: a fall
    is written -12.3% like a rise is 62.8%, and a spreadsheet reads both as
    numbers. As text it got the formula guard's apostrophe ('-12.3%), which
    Excel shows as text (2.26.0 gate, W-29 / NEW-171). A number with a
    percent sign is never a formula."""

    __slots__ = ("value",)

    def __init__(self, value: float):
        self.value = value

    def __str__(self) -> str:
        return f"{self.value:.1f}%"


def _preamble(writer, company: str, report: str, period: str) -> None:
    writer.writerow(["Company", company])
    writer.writerow(["Report", report])
    writer.writerow(["Period", period])
    writer.writerow([])


def _range(data: dict) -> str:
    """ "2026-01-01 to 2026-12-31", and when the statement was run without
    the adjusting entries (#341) it says so, so the file is not mistaken for
    the books as they stand."""
    period = f"{data['start_date']} to {data['end_date']}"
    if data.get("exclude_adjusting"):
        period += " (adjusting entries left out)"
    return period


def trial_balance_csv(data: dict, company: str) -> str:
    """Account number | Account name | Type | Debit | Credit | Net, one row
    per account with activity, then a totals row where Debit equals Credit.
    Net is debit minus credit, as the screen shows it."""
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Trial Balance", _range(data))
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


def account_transactions_csv(data: dict, company: str, words) -> str:
    """The drill-down (one account's lines for a date range, the balance
    brought forward and the running balance) as the screen shows it
    (#333): a bookkeeper hands a client what was spent on one account."""
    out = io.StringIO()
    w = _SafeWriter(out)
    acct = data.get("account") or {}
    head = f"{acct.get('number') or ''} {acct.get('name') or ''}".strip()
    period = f"{data['start_date']} to {data['end_date']}"
    if data.get("class_name"):
        period += f" ({words('Class')}: {data['class_name']} only)"
    if data.get("job_name"):
        period += f" ({words('Job')}: {data['job_name']})"
    _preamble(w, company, f"Account detail — {head}", period)
    w.writerow(
        [
            "Date",
            "Payee",
            "Reference",
            "Description",
            "Source",
            "Debit",
            "Credit",
            "Running balance",
            "Cleared",
        ]
    )
    w.writerow(
        [
            data["start_date"],
            "",
            "",
            "Balance brought forward",
            "",
            "",
            "",
            _money(data.get("opening_balance", 0)),
            "",
        ]
    )
    for e in data.get("entries", []):
        w.writerow(
            [
                e.get("date", ""),
                e.get("payee") or "",
                e.get("reference") or "",
                (e.get("description") or "") + (" (void)" if e.get("voided") else ""),
                e.get("source_type") or "",
                _money(e["debit"]) if e.get("debit") else "",
                _money(e["credit"]) if e.get("credit") else "",
                _money(e.get("running_balance", 0)),
                (
                    "R"
                    if e.get("reconciliation_id")
                    else ("cleared" if e.get("cleared") else "")
                ),
            ]
        )
    entries = data.get("entries", [])
    closing = (
        entries[-1]["running_balance"] if entries else data.get("opening_balance", 0)
    )
    w.writerow(
        [
            "",
            "",
            "",
            "Period total",
            "",
            _money(data.get("period_debit", 0)),
            _money(data.get("period_credit", 0)),
            _money(closing),
            "",
        ]
    )
    return out.getvalue()


def _cmp_header(prior, show_changes: bool) -> list:
    if not prior:
        return ["Amount"]
    return ["Current", "Prior year"] + (
        ["$ Change", "% Change"] if show_changes else []
    )


def _cmp_cells(cur, before, prior, show_changes: bool) -> list:
    """A row's money cells: Amount alone, or Current | Prior year, plus
    $ Change | % Change unless they are turned off."""
    from app.routes.reports.financial import pct_change

    if not prior:
        return [_money(cur)]
    cells = [_money(cur), _money(before)]
    if show_changes:
        pct = pct_change(cur, before)
        cells += [
            _money(float(cur or 0) - float(before or 0)),
            "" if pct is None else _Percent(pct),
        ]
    return cells


def profit_loss_csv(data: dict, company: str, words, show_changes: bool = True) -> str:
    """The P&L as a spreadsheet. With a prior-year comparison (`prior` on
    the data) each row also carries Prior year, and $ Change and % Change
    unless show_changes is off."""
    prior = data.get("prior")
    p_tot = prior or {}
    out = io.StringIO()
    w = _SafeWriter(out)
    period = _range(data)
    if prior:
        period += f" compared with {prior['start_date']} to {prior['end_date']}"
    _preamble(w, company, profit_loss_csv_class_line(data, words), period)

    def figures(cur, before):
        return _cmp_cells(cur, before, prior, show_changes)

    w.writerow(
        ["Section", "Account number", "Account name"] + _cmp_header(prior, show_changes)
    )
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
                ]
                + figures(i["amount"], i.get("prior_amount", 0))
            )
        w.writerow(
            [label, "", f"Total {label}"]
            + figures(data[total_key], p_tot.get(total_key, 0))
        )
    w.writerow(
        ["", "", "Gross Profit"]
        + figures(data["gross_profit"], p_tot.get("gross_profit", 0))
    )
    w.writerow(
        ["", "", words("Net Income")]
        + figures(data["net_income"], p_tot.get("net_income", 0))
    )
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


def balance_sheet_csv(
    data: dict, company: str, words, show_changes: bool = True
) -> str:
    """The Balance Sheet as a spreadsheet. With a prior-year comparison
    (`prior` on the data) each row also carries Prior year, and $ Change
    and % Change unless show_changes is off."""
    prior = data.get("prior")
    p_tot = prior or {}
    out = io.StringIO()
    w = _SafeWriter(out)
    period = f"As of {data['as_of_date']}"
    if prior:
        period += f" compared with {prior['as_of_date']}"
    _preamble(w, company, words("Balance Sheet"), period)

    def figures(cur, before):
        return _cmp_cells(cur, before, prior, show_changes)

    w.writerow(
        ["Section", "Account number", "Account name"] + _cmp_header(prior, show_changes)
    )
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
                ]
                + figures(i["amount"], i.get("prior_amount", 0))
            )
        w.writerow(
            [label, "", f"Total {label}"]
            + figures(data[total_key], p_tot.get(total_key, 0))
        )
    w.writerow(
        ["", "", words("Liabilities + Equity")]
        + figures(
            data["total_liabilities"] + data["total_equity"],
            p_tot.get("total_liabilities", 0) + p_tot.get("total_equity", 0),
        )
    )
    return out.getvalue()


def rows_of(text: str) -> list[list[str]]:
    """The table rows of an export, preamble skipped — for tests and for any
    reader that wants the data (group reporting, #180)."""
    return list(csv.reader(io.StringIO(text)))[4:]


def adjusting_entries_csv(data: dict, company: str) -> str:
    """The Adjusting Journal Entries report (#341): one row per line of each
    adjusting entry, the entry's own words on every row (so a spreadsheet
    sorts and filters by entry, date or poster), then the period's totals,
    which tie to the General Ledger's adjusting lines."""
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(
        w,
        company,
        "Adjusting Journal Entries",
        f"{data['start_date']} to {data['end_date']}",
    )
    w.writerow(
        [
            "Entry",
            "Date",
            "Reference",
            "Description",
            "Posted by",
            "Voided",
            "Account number",
            "Account name",
            "Line memo",
            "Debit",
            "Credit",
        ]
    )
    for e in data["entries"]:
        for ln in e["lines"]:
            w.writerow(
                [
                    e["id"],
                    e["date"],
                    e["reference"],
                    e["description"],
                    e["posted_by"],
                    "yes" if e["voided"] else "",
                    ln["account_number"],
                    ln["account_name"],
                    ln["description"],
                    _money(ln["debit"]),
                    _money(ln["credit"]),
                ]
            )
    w.writerow(
        [
            "",
            "",
            "",
            f"Total ({data['count']} entries)",
            "",
            "",
            "",
            "",
            "",
            _money(data["total_debit"]),
            _money(data["total_credit"]),
        ]
    )
    return out.getvalue()


# ── Sales by Item / Purchases by Item (#435) ────────────────────────────────
# Flat one-row-per-item reports. The CSV follows the shared pattern (safe
# writer for the formula guard, four-line preamble); the PDF reuses the
# report_pdf.html layout via *_sections, so the PDF rows can be tested
# without WeasyPrint, as the P&L tests do.


def _pdf_money(value) -> str:
    """A money cell for the PDF: "$1,234.56", a negative with a leading
    minus (the screen's _money in financial.py)."""
    amount = Decimal(str(value or 0))
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def _qty_text(value) -> str:
    """A quantity without trailing zeros: 3, 1.5, 0.25."""
    d = Decimal(str(value or 0))
    text = format(d.normalize(), "f")
    return text if text != "-0" else "0"


def sales_by_item_csv(data: dict, company: str) -> str:
    """Item | Type | Quantity | Amount | Avg price | COGS | Gross margin |
    Margin %, one row per item with a "No item" row last, then a TOTAL row
    whose Amount ties to the P&L income for the same period (see the route
    docstring). COGS, margin and margin % are blank for an untracked item."""
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Sales by Item", _range(data))
    w.writerow(
        [
            "Item",
            "Type",
            "Quantity",
            "Amount",
            "Avg price",
            "COGS",
            "Gross margin",
            "Margin %",
        ]
    )
    for r in data["items"]:
        w.writerow(
            [
                r["item_name"],
                r.get("item_type") or "",
                _qty_text(r["quantity"]),
                _money(r["amount"]),
                _money(r["average_price"]) if r["average_price"] is not None else "",
                _money(r["cogs"]) if r["cogs"] is not None else "",
                _money(r["gross_margin"]) if r["gross_margin"] is not None else "",
                _Percent(r["margin_pct"]) if r["margin_pct"] is not None else "",
            ]
        )
    w.writerow(
        [
            "TOTAL",
            "",
            _qty_text(data["total_quantity"]),
            _money(data["total_amount"]),
            "",
            _money(data["total_cogs"]),
            _money(data["total_margin"]),
            "",
        ]
    )
    return out.getvalue()


def purchases_by_item_csv(data: dict, company: str) -> str:
    """Item | Type | Quantity | Amount | Avg cost, one row per item with a
    "No item" row last, then a TOTAL row."""
    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Purchases by Item", _range(data))
    w.writerow(["Item", "Type", "Quantity", "Amount", "Avg cost"])
    for r in data["items"]:
        w.writerow(
            [
                r["item_name"],
                r.get("item_type") or "",
                _qty_text(r["quantity"]),
                _money(r["amount"]),
                _money(r["average_cost"]) if r["average_cost"] is not None else "",
            ]
        )
    w.writerow(
        [
            "TOTAL",
            "",
            _qty_text(data["total_quantity"]),
            _money(data["total_amount"]),
            "",
        ]
    )
    return out.getvalue()


def sales_by_item_sections(data: dict) -> list[dict]:
    """Sales by Item as report_pdf.html sections (one section)."""
    columns = [
        "Item",
        "Type",
        "Qty",
        "Amount",
        "Avg price",
        "COGS",
        "Margin",
        "Margin %",
    ]
    rows = []
    for r in data["items"]:
        rows.append(
            {
                "cells": [
                    r["item_name"],
                    r.get("item_type") or "",
                    _qty_text(r["quantity"]),
                    _pdf_money(r["amount"]),
                    (
                        _pdf_money(r["average_price"])
                        if r["average_price"] is not None
                        else ""
                    ),
                    _pdf_money(r["cogs"]) if r["cogs"] is not None else "",
                    (
                        _pdf_money(r["gross_margin"])
                        if r["gross_margin"] is not None
                        else ""
                    ),
                    f"{r['margin_pct']:.1f}%" if r["margin_pct"] is not None else "",
                ]
            }
        )
    rows.append(
        {
            "cells": [
                "TOTAL",
                "",
                _qty_text(data["total_quantity"]),
                _pdf_money(data["total_amount"]),
                "",
                _pdf_money(data["total_cogs"]),
                _pdf_money(data["total_margin"]),
                "",
            ],
            "style": "grand-total",
        }
    )
    return [
        {
            "title": "Sales by Item",
            "period": f"{data['start_date']} — {data['end_date']}",
            "columns": columns,
            "rows": rows,
        }
    ]


def purchases_by_item_sections(data: dict) -> list[dict]:
    """Purchases by Item as report_pdf.html sections (one section)."""
    columns = ["Item", "Type", "Qty", "Amount", "Avg cost"]
    rows = []
    for r in data["items"]:
        rows.append(
            {
                "cells": [
                    r["item_name"],
                    r.get("item_type") or "",
                    _qty_text(r["quantity"]),
                    _pdf_money(r["amount"]),
                    (
                        _pdf_money(r["average_cost"])
                        if r["average_cost"] is not None
                        else ""
                    ),
                ]
            }
        )
    rows.append(
        {
            "cells": [
                "TOTAL",
                "",
                _qty_text(data["total_quantity"]),
                _pdf_money(data["total_amount"]),
                "",
            ],
            "style": "grand-total",
        }
    )
    return [
        {
            "title": "Purchases by Item",
            "period": f"{data['start_date']} — {data['end_date']}",
            "columns": columns,
            "rows": rows,
        }
    ]
