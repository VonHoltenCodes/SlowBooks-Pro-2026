# ============================================================================
# Migrate from QuickBooks Online — without the Intuit API.
#
# QuickBooks Online's own Export data (the gear → Tools → Export data) hands
# every user a zip of Excel workbooks: the reports Balance Sheet, Profit and
# Loss, Trial Balance, General Ledger and Journal, and the lists Customers,
# Vendors and Employees. No Intuit developer app, no production keys, no
# questionnaire: the route Intuit's developer portal made impractical for a
# bookkeeper connecting their own books (the API connector was retired in
# 2.23.2 for that reason).
#
# This dialect feeds the shared migration engine (migration_common):
#   coa  the Balance Sheet and the P&L, one text — every leaf with a figure
#        is an account, typed by the section it sits under, a sub-account
#        named Parent:Child as QuickBooks writes it elsewhere
#   gl   the Journal — one block per transaction: a dated first line, its
#        split lines, a totals row, a blank row
#   tb   the Trial Balance — and the basis its footer names
#   customers / vendors / employees — the contact lists, names as
#        QuickBooks has them (an employee's name and contact details only:
#        the export carries nothing of pay or payroll)
#   ar_aging  the A/R Aging Detail report, a download of its own beside the
#        zip (Reports → A/R Aging Detail → Export to Excel): the invoices
#        still open and the customer payments not yet applied. The Journal
#        posts them to the export's own receivable account as history; they
#        are carried over to 1100 Accounts Receivable as documents, so the
#        aging and every customer's balance are right. When the report
#        carries all the Journal leaves there, that account is 1100 itself
#        (no empty one beside it, #704); otherwise they are moved from it.
#        An invoice the Journal holds at a larger total comes at that total
#        with a payment for the part paid (#711).
#
# The export's payable account ("Accounts Payable (A/P)") is 2000 Accounts
# Payable here, its balance on the control, no second account made (#704).
#
# The bundle's quirks, read as they come: a leading blank column on the
# journal and the lists, every figure as formula text ("=23683.16", the
# engine's parse_amount strips it), labels indented by three spaces per
# level, "Total …" rows and report arithmetic (Net Income …) that are not
# accounts, memo-only rows inside a block, and a footer that says which
# basis the reports were run on. A Trial Balance on the cash basis cannot
# be checked against the Journal, which is accrual by nature: it is set
# aside with a note rather than mistaken for opening balances.
#
# Built against a real export (a 24-year company, 9,510 transactions, every
# block balanced to the cent) and that company's A/R Aging Detail report
# (every open invoice and unapplied payment matched to its Journal block,
# the report's total equal to what the Journal leaves on receivables); the
# fixtures in tests/fixtures/qbo_export/ are invented books in that layout.
# Nothing of the real files is here.
# ============================================================================

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models.accounts import AccountType, DetailType
from app.models.contacts import Customer, Vendor
from app.services.account_label import label_for
from app.services.migration_common import (
    _missing_problem,
    books_by_name,
    dry_run_bundle,
    parse_amount,
    parse_date,
    planned_sub_account_note,
    run_import_bundle,
    spec_path,
    strip_code_suffix,
)
from app.services.safe_errors import DataProblem

LABEL = "QuickBooks Online export"
SOURCE_TYPE = "qbo_export_import"
_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")

# Filenames as the export names them (Balance_sheet.xlsx …), and as people
# save them. Order matters: "journal" before "general" (General_ledger is
# the journal again, by account, and is left alone).
_KINDS = (
    # A report downloaded beside the bundle, named by QuickBooks
    # "<Company>_A_R_Aging_Detail_Report.xlsx": before "customer" and
    # "vendor", which an aging report's name may carry too.
    ("aging", "ar_aging"),
    ("balance_sheet", "coa"),
    ("balance sheet", "coa"),
    ("balancesheet", "coa"),
    ("profit_and_loss", "coa"),
    ("profit and loss", "coa"),
    ("profitandloss", "coa"),
    ("chart", "coa"),
    ("journal", "gl"),
    ("general_ledger", "skip"),
    ("general ledger", "skip"),
    ("generalledger", "skip"),
    ("trial", "tb"),
    ("customer", "customers"),
    ("vendor", "vendors"),
    ("supplier", "vendors"),
    # the Employee Contact List: names and contact details, no pay (#711)
    ("employee", "employees"),
)

# The section a leaf sits under decides its type. "LIABILITIES AND EQUITY"
# decides nothing by itself; Liabilities and Equity below it do.
_SECTION_TYPES = {
    "assets": AccountType.ASSET,
    "liabilities": AccountType.LIABILITY,
    "equity": AccountType.EQUITY,
    "income": AccountType.INCOME,
    "other income": AccountType.INCOME,
    # above Gross Profit, as QuickBooks prints it (2.26 imported it as an
    # expense; deferred to the statements' change, #390)
    "cost of goods sold": AccountType.COGS,
    "expenses": AccountType.EXPENSE,
    "other expenses": AccountType.EXPENSE,
    # a nonprofit company's QuickBooks Online words for the same sections
    # (a Hacksburg export, 2.31.11: every account "sits under no section that
    # names its type" because its statement said Revenue and Expenditures)
    "revenue": AccountType.INCOME,
    "other revenue": AccountType.INCOME,
    "expenditures": AccountType.EXPENSE,
    "other expenditures": AccountType.EXPENSE,
    "net assets": AccountType.EQUITY,
}
_KIND_SECTIONS = {"bank accounts": "bank", "credit cards": "credit_card"}
# The reports' headings that name a detail type (#390); the innermost one an
# account sits under wins. Accounts Receivable / Payable hold only on 1100 /
# 2000, so an account under those headings takes the default here.
_DETAIL_SECTIONS = {
    "bank accounts": DetailType.BANK,
    "other current assets": DetailType.OTHER_CURRENT_ASSET,
    "fixed assets": DetailType.FIXED_ASSET,
    "other assets": DetailType.OTHER_ASSET,
    "credit cards": DetailType.CREDIT_CARD,
    "other current liabilities": DetailType.OTHER_CURRENT_LIABILITY,
    "long-term liabilities": DetailType.LONG_TERM_LIABILITY,
    "other income": DetailType.OTHER_INCOME,
    "other expenses": DetailType.OTHER_EXPENSE,
}
# The reports' own headings. A labelled row with no figure that is not one
# of these is a parent account whose children follow (QuickBooks shows a
# parent with no activity of its own as a bare heading over them).
_SECTIONS = {
    "assets",
    "current assets",
    "bank accounts",
    "accounts receivable",
    "other current assets",
    "fixed assets",
    "other assets",
    "liabilities and equity",
    "liabilities",
    "current liabilities",
    "accounts payable",
    "credit cards",
    "other current liabilities",
    "long-term liabilities",
    "equity",
    "income",
    "cost of goods sold",
    "expenses",
    "other income",
    "other expenses",
    "revenue",
    "expenditures",
    "other revenue",
    "other expenditures",
    "net assets",
    "liabilities and net assets",
}
# Report arithmetic that carries a figure but is not an account.
_ARITHMETIC = {
    "net income",
    "gross profit",
    "net operating income",
    "net other income",
    "retained earnings",
    # the nonprofit statement's words for the same lines
    "net revenue",
    "net operating revenue",
    "net other revenue",
}
_BASIS = re.compile(r"\b(cash|accrual)\s+basis\b", re.I)
# What parse_coa inferred about accounts the reports hide, for the dry run
# to say (the engine's parser contract returns accounts and errors only).
_COA_NOTES: list[str] = []
# Trial-balance accounts with a balance that parse_coa could not place (no
# parent the chart has): the import refuses them by name (#690, W-108).
_COA_UNPLACED: list[str] = []
# The export's payable account, mapped onto 2000, as (its path, the
# control's name in a sentence), for the A/P note (W-117).
_COA_PAYABLE: list[tuple[str, str]] = []


def classify_filename(name: str) -> str | None:
    lowered = (name or "").lower()
    for fragment, kind in _KINDS:
        if fragment in lowered:
            return kind
    return None


# ── rows ───────────────────────────────────────────────────────────────────


def _rows(text: str) -> list[list[str]]:
    """The file's rows as lists of cells, the delimiter sniffed from the
    first line, cells kept verbatim (a label's leading spaces are its
    indentation)."""
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first else ","
    return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _cell(row: list[str], i: int) -> str:
    return row[i] if i < len(row) else ""


# ── the chart: the balance sheet and the P&L ───────────────────────────────


def parse_coa(
    csv_text: str,
    also: tuple[str, ...] = (),
    ordered: tuple[str, ...] = (),
    amounts: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """Accounts from one or both reports (the route joins them). A row with
    a label and a figure is an account; a labelled row with no figure is a
    section (ASSETS, Bank Accounts …) or a parent whose own total follows.
    Indentation says what sits under what.

    ``also`` names accounts the books use (the Journal's, the Trial
    Balance's) that the reports may not show: QuickBooks Online leaves a
    zero-balance row off the Balance Sheet and the Profit and Loss. A
    sub-account of one the reports do show is planned under it with its
    parent's type, and a Trial Balance account so planned is noted in the
    engine's words (#690). Any other is placed by the type of its neighbour
    in ``ordered`` (the Trial Balance's names, in the chart's order) when
    the accrual Trial Balance's ``amounts`` give it no balance (a
    zero-balance row the reports leave off); one with a balance is not
    placed, and the import refuses it by name (W-108). With neither it is
    named, for the person to add first.

    A sub-account is named by its leaf ("Federal Unemployment (940)"), its
    parent set, and carries its full "Parent:Child" ``path``, the name the
    Journal and the Trial Balance write and the import matches it by: the
    statements join the parents' names to it (NEW-203)."""
    accounts: list[dict] = []
    errors: list[str] = []
    notes = _COA_NOTES
    notes.clear()
    unplaced = _COA_UNPLACED
    unplaced.clear()
    seen: set[str] = set()
    stack: list[tuple[int, str, bool]] = []  # (indent, name, is_account)
    in_report = False
    for row in _rows(csv_text):
        raw = _cell(row, 0)
        value = _cell(row, 1).strip()
        name = raw.strip()
        if not name:
            if value.lower() == "total":
                in_report = True  # the header row; a new report starts here
                stack = []
            continue
        if not in_report:
            continue  # the preamble: company, title, date range
        low = name.lower()
        if _BASIS.search(name) and value == "":
            in_report = False  # the footer: "… - Cash Basis"
            stack = []
            continue
        if (
            low.startswith("total ")
            or low in _ARITHMETIC
            or low in ("total", "total assets", "total liabilities and equity")
        ):
            continue
        if low.startswith("total"):  # "TOTAL ASSETS"
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        is_account = value != "" or low not in _SECTIONS
        if is_account:
            sections = [n.lower() for _, n, acct in stack if not acct]
            parents = [n for _, n, acct in stack if acct]
            acct_type = None
            for section in sections:
                if section in _SECTION_TYPES:
                    acct_type = _SECTION_TYPES[section]
            if acct_type is None:
                errors.append(
                    f"Chart: {name!r} sits under no section that names its type"
                )
            else:
                full = ":".join(parents + [name])
                if full.lower() not in seen:
                    seen.add(full.lower())
                    kind = next(
                        (k for s, k in _KIND_SECTIONS.items() if s in sections), None
                    )
                    accounts.append(
                        {
                            "code": None,
                            "name": name,
                            "path": full,
                            "type": acct_type,
                            "description": None,
                            "bank_kind": kind,
                            "detail_type": next(
                                (
                                    _DETAIL_SECTIONS[s]
                                    for s in reversed(sections)
                                    if s in _DETAIL_SECTIONS
                                ),
                                None,
                            ),
                            "parent": ":".join(parents) if parents else None,
                            # the account the Journal's invoices and
                            # payments post to (QuickBooks names it
                            # "Accounts Receivable (A/R)")
                            "receivable": "accounts receivable" in sections,
                            # the account its bills post to ("Accounts
                            # Payable (A/P)"): it lands on 2000 (#704)
                            "payable": "accounts payable" in sections,
                        }
                    )
        stack.append((indent, name, is_account))
    by_name = {spec_path(a).lower(): a for a in accounts}
    # an account the books name another way ("Parent:Child (940)" beside the
    # report's indented child) is the same account, not a new one (#690)
    by_key = {strip_code_suffix(spec_path(a)): a for a in accounts}
    on_tb = {n.strip().lower() for n in ordered}
    for name in also:
        key = name.strip().lower()
        if not key or key in by_name or key in _ARITHMETIC:
            continue
        if strip_code_suffix(name) in by_key:
            continue
        parent = name.rsplit(":", 1)[0] if ":" in name else ""
        parent_spec = (
            by_name.get(parent.lower()) or by_key.get(strip_code_suffix(parent))
            if parent
            else None
        )
        if parent_spec is not None:
            acct_type, parent_path = parent_spec["type"], spec_path(parent_spec)
            leaf = name.rsplit(":", 1)[1].strip()
            path = f"{parent_path}:{leaf}"
            if key in on_tb:
                # the engine's own words (#690): one note, and the engine
                # finds the account planned here and plans no second one
                notes.append(
                    planned_sub_account_note(name.strip(), parent_path, acct_type)
                )
        else:
            balance = (amounts or {}).get(name.strip(), Decimal("0"))
            if amounts is not None and key in on_tb and balance != 0:
                # A balance on an account with no parent the chart has: not
                # a zero row the reports left off. Guessing its type would
                # put the balance on a made-up top-level account; it is
                # named and the import refuses (W-108, #690).
                unplaced.append(name.strip())
                continue
            # The Trial Balance lists accounts in the chart's order, assets
            # through expenses: the nearest listed neighbour the reports do
            # show says what kind this one is.
            acct_type = _type_from_neighbours(name, ordered, by_name, by_key)
            parent_path, leaf, path = None, name.strip(), name.strip()
            if acct_type is None:
                errors.append(
                    f"Account {name!r} is in the books but not on the Balance Sheet "
                    f"or the Profit and Loss (QuickBooks Online leaves a zero-balance "
                    f"row off both): add it to the chart with its type, then import "
                    f"again"
                )
                continue
            notes.append(
                f"Account {name!r} is not on the Balance Sheet or the Profit and Loss; "
                f"from its place in the Trial Balance it is a{'n' if acct_type.value[0] in 'aeiou' else ''} "
                f"{acct_type.value} account. Correct it in Chart of Accounts if that is wrong."
            )
        spec = {
            "code": None,
            "name": leaf,
            "path": path,
            "type": acct_type,
            "description": None,
            "bank_kind": None,
            # a sub-account takes its parent's (the model's rule); a
            # top-level one guessed from its neighbours, the default
            "detail_type": None,
            "parent": parent_path,
        }
        accounts.append(spec)
        by_name[key] = spec
        by_name.setdefault(path.lower(), spec)
        by_key.setdefault(strip_code_suffix(name), spec)
    return accounts, errors


def _type_from_neighbours(
    name: str, listed: tuple[str, ...], by_name: dict, by_key: dict | None = None
):
    """The type of the nearest account before ``name`` in ``listed`` (the
    Trial Balance's order) that the chart knows, else the nearest after; a
    neighbour the Trial Balance spells another way is found by its matching
    key (#690)."""
    lowered = [n.strip().lower() for n in listed]
    key = name.strip().lower()
    if key not in lowered:
        return None
    i = lowered.index(key)
    for j in list(range(i - 1, -1, -1)) + list(range(i + 1, len(lowered))):
        spec = by_name.get(lowered[j]) or (by_key or {}).get(
            strip_code_suffix(lowered[j])
        )
        if spec is not None:
            return spec["type"]
    return None


# ── the journal ────────────────────────────────────────────────────────────


def parse_gl(csv_text: str) -> tuple[list[dict], list[str]]:
    """Journal blocks as the engine's groups: rows of {journal, date,
    account, description, reference, debit, credit}. A block begins with a
    dated line and ends at its totals row (no account, the sums) or at a
    blank row; memo-only rows in between are not lines."""
    rows = _rows(csv_text)
    errors: list[str] = []
    groups: list[list[dict]] = []
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if (
            "date" in cells
            and "account" in cells
            and ("debit" in cells or "credit" in cells)
        ):
            header_at = i
            break
    if header_at is None:
        return [], ["Journal: no header row with Date, Account, Debit and Credit"]
    header = [c.strip().lower() for c in rows[header_at]]

    def idx(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_date, i_type, i_num, i_name = (
        idx("date"),
        idx("transaction type", "type"),
        idx("num", "no.", "number"),
        idx("name"),
    )
    i_memo, i_acct, i_debit, i_credit = (
        idx("memo/description", "memo", "description"),
        idx("account"),
        idx("debit"),
        idx("credit"),
    )
    if None in (i_date, i_acct, i_debit, i_credit):
        return [], ["Journal: the header row lacks Date, Account, Debit or Credit"]
    current: list[dict] | None = None
    block_name = block_type = block_num = ""
    counter: dict[str, int] = {}
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        date_raw = _cell(row, i_date).strip()
        ttype = _cell(row, i_type).strip() if i_type is not None else ""
        account = _cell(row, i_acct).strip()
        debit_raw = _cell(row, i_debit).strip()
        credit_raw = _cell(row, i_credit).strip()
        if date_raw and ttype:
            if current:
                groups.append(current)
            try:
                date = parse_date(date_raw, _DATE_FORMATS)
            except Exception as exc:  # DataProblem: an unreadable date
                errors.append(f"Journal row {n}: {exc}")
                current = None
                continue
            block_type = ttype
            block_num = _cell(row, i_num).strip() if i_num is not None else ""
            block_name = _cell(row, i_name).strip() if i_name is not None else ""
            key = f"{block_type} {block_num}".strip() + f" · {date.isoformat()}"
            counter[key] = counter.get(key, 0) + 1
            if counter[key] > 1:
                key = f"{key} #{counter[key]}"
            current = []
            current_date = date
            current_key = key[:100]
            current_desc = (
                block_type
                + (f" {block_num}" if block_num else "")
                + (f" — {block_name}" if block_name else "")
            )[:200]
        if current is None:
            continue
        if not account:
            if debit_raw or credit_raw:
                # A row with amounts and no account is the block's totals
                # row when it carries the block's sums; an item at 0.00
                # inside an invoice comes out the same way with a bare 0,
                # and is not the end of anything.
                sd = sum(r["debit"] for r in current)
                sc = sum(r["credit"] for r in current)
                try:
                    td, tc = parse_amount(debit_raw), parse_amount(credit_raw)
                except Exception:
                    errors.append(
                        f"Journal row {n}: unreadable amount beside no account"
                    )
                    continue
                if (td, tc) == (sd, sc) and current:
                    groups.append(current)
                    current = None
                elif td == 0 and tc == 0:
                    continue  # a zero line with no account
                else:
                    errors.append(
                        f"Journal {current_key}: a line with no account carries {td}/{tc}"
                    )
            continue  # a memo-only row (Subtotal, an address line), or a blank
        try:
            debit = parse_amount(debit_raw)
            credit = parse_amount(credit_raw)
        except Exception as exc:
            errors.append(f"Journal row {n}: {exc}")
            continue
        memo = _cell(row, i_memo).strip() if i_memo is not None else ""
        line_name = _cell(row, i_name).strip() if i_name is not None else ""
        current.append(
            {
                "journal": current_key,
                "date": current_date,
                "account": account,
                "description": (
                    current_desc
                    + (f": {memo}" if memo else "")
                    + (
                        f" ({line_name})"
                        if line_name and line_name != block_name
                        else ""
                    )
                )[:500],
                "reference": current_key,
                "debit": debit,
                "credit": credit,
                # the block's own words, for the carry-over to find an
                # invoice and its payments by (#711)
                "type": block_type,
                "num": block_num,
                "name": block_name,
            }
        )
    if current:
        groups.append(current)
    return groups, errors


# ── the trial balance ──────────────────────────────────────────────────────


def trial_balance_basis(csv_text: str) -> str | None:
    """ "cash" or "accrual" from the report's footer, else None."""
    for row in reversed(_rows(csv_text)):
        for cell in row:
            m = _BASIS.search(cell)
            if m:
                return m.group(1).lower()
    return None


def parse_tb(csv_text: str) -> tuple[dict, list[str]]:
    balances: dict[str, Decimal] = {}
    errors: list[str] = []
    rows = _rows(csv_text)
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if "debit" in cells and "credit" in cells:
            header_at = i
            break
    if header_at is None:
        return {}, ["Trial balance: no header row with Debit and Credit"]
    header = [c.strip().lower() for c in rows[header_at]]
    i_debit, i_credit = header.index("debit"), header.index("credit")
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        name = _cell(row, 0).strip()
        if not name or name.lower().startswith("total") or _BASIS.search(name):
            continue
        try:
            net = parse_amount(_cell(row, i_debit)) - parse_amount(_cell(row, i_credit))
        except Exception as exc:
            errors.append(f"Trial balance row {n}: {exc}")
            continue
        # the name as written: the engine keys it as it keys the journal
        # (strip_code_suffix), and names it so if the chart lacks it (#690)
        balances[name] = balances.get(name, Decimal("0")) + net
    return balances, errors


# ── the lists ──────────────────────────────────────────────────────────────


def parse_contacts(csv_text: str, label: str) -> list[dict]:
    """Rows of the Customer, Vendor or Employee Contact List: {name,
    email, phone, address} with the list's own column names."""
    rows = _rows(csv_text)
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if label.lower() in cells and any("phone" in c or "email" in c for c in cells):
            header_at = i
            break
    if header_at is None:
        return []
    header = [c.strip().lower() for c in rows[header_at]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_name = col(label.lower())
    i_phone, i_email = col("phone numbers", "phone"), col("email")
    i_addr = col("billing address", "address")
    i_full = col("full name")
    out = []
    for row in rows[header_at + 1 :]:
        name = _cell(row, i_name).strip()
        if not name:
            continue
        out.append(
            {
                "name": name[:200],
                "email": (_cell(row, i_email).strip() if i_email is not None else "")[
                    :200
                ],
                "phone": (_cell(row, i_phone).strip() if i_phone is not None else "")[
                    :50
                ],
                "address": (_cell(row, i_addr).strip() if i_addr is not None else ""),
                "full_name": (_cell(row, i_full).strip() if i_full is not None else ""),
            }
        )
    return out


def _phone(raw: str) -> tuple[str, str]:
    """ "Mobile: (555) 010-2001" → ("", "(555) 010-2001"); "Phone: …" → phone."""
    mobile = ""
    phone = raw
    m = re.match(r"(?i)^(mobile|cell)\s*:\s*(.+)$", raw)
    if m:
        return "", m.group(2).strip()
    m = re.match(r"(?i)^(phone|work|office)\s*:\s*(.+)$", raw)
    if m:
        phone = m.group(2).strip()
    return phone, mobile


def _address(raw: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]
    if not lines:
        return "", ""
    return lines[0][:200], ", ".join(lines[1:])[:200]


def _import_contacts(db: Session, bundle: dict) -> tuple[int, int]:
    """The Customer list's names as QuickBooks Online spells them: a
    sub-customer is "Parent:Sub" (the export carries no parent column; the
    colon, which a QuickBooks name cannot otherwise hold, is the parent
    reference), and is made a sub-customer under its parent here (#638),
    the parent first if the list did not carry it."""
    from app.services.jobs_service import resolve_customer_and_job

    created_customers = created_vendors = 0
    if "customers" in bundle:
        have = {c.qb_name.lower() for c in db.query(Customer).all()}
        for spec in parse_contacts(bundle["customers"], "Customer"):
            if spec["name"].lower() in have:
                continue
            phone, mobile = _phone(spec["phone"])
            a1, a2 = _address(spec["address"])
            if ":" in spec["name"]:
                db.flush()
                before = db.query(Customer).count()
                customer, job = resolve_customer_and_job(
                    db, spec["name"], sub_customers=True
                )
                if job is not None:
                    # on file as a job (an earlier IIF import): not a customer
                    have.add(spec["name"].lower())
                    continue
                if customer is None or customer.parent_customer_id is None:
                    # "A:" or ":B": no sub-customer in it; the name as given
                    customer = Customer(name=spec["name"])
                    db.add(customer)
                    db.flush()
                created_customers += db.query(Customer).count() - before
            else:
                customer = Customer(name=spec["name"])
                db.add(customer)
                created_customers += 1
            customer.email = spec["email"] or None
            customer.phone = phone or None
            customer.mobile = mobile or None
            customer.bill_address1 = a1 or None
            customer.bill_address2 = a2 or None
            have.add(spec["name"].lower())
    if "vendors" in bundle:
        have = {v.name.lower() for v in db.query(Vendor.name).all()}
        for spec in parse_contacts(bundle["vendors"], "Vendor"):
            if spec["name"].lower() in have:
                continue
            phone, mobile = _phone(spec["phone"])
            a1, a2 = _address(spec["address"])
            vendor = Vendor(
                name=spec["name"], email=spec["email"] or None, phone=phone or None
            )
            for attr, value in (
                ("mobile", mobile),
                ("address1", a1),
                ("address2", a2),
                ("bill_address1", a1),
                ("bill_address2", a2),
            ):
                if value and hasattr(Vendor, attr):
                    setattr(vendor, attr, value)
            db.add(vendor)
            have.add(spec["name"].lower())
            created_vendors += 1
    db.flush()
    return created_customers, created_vendors


def _city_state_zip(line: str) -> tuple[str, str, str] | None:
    """ "Cedar Hollow IL 60999" or "Springfield, IL 62701" → its parts."""
    m = re.match(r"^(.+?)[,\s]+([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$", line.strip())
    if not m:
        return None
    return m.group(1).strip(" ,"), m.group(2).upper(), m.group(3)


def _new_employees(db: Session, bundle: dict) -> list[dict]:
    """The Employee Contact List's people not on the Employees list yet,
    matched by name under every spelling the list may use (#711)."""
    from app.services.qb_time_import import _employees, _key

    if "employees" not in bundle:
        return []
    have = set(_employees(db))
    out = []
    for spec in parse_contacts(bundle["employees"], "Employee"):
        key = _key(spec["name"])
        if key in have:
            continue
        have.add(key)
        out.append(spec)
    return out


def _import_employees(db: Session, bundle: dict) -> int:
    """The Employee Contact List into Employees: the name, the email and
    the address, the fields an employee has here. Nothing about pay: the
    export carries none, and payroll is set up on the employee when it is
    wanted. The list's phone has no field on an employee and is left."""
    from app.models.payroll import Employee
    from app.services.qb_time_import import _split_name

    made = 0
    for spec in _new_employees(db, bundle):
        first, last = _split_name(spec["name"])
        employee = Employee(
            first_name=first[:100], last_name=last[:100], is_active=True
        )
        employee.email = spec["email"] or None
        lines = [
            ln.strip()
            for ln in spec["address"].replace("\r", "").split("\n")
            if ln.strip()
        ]
        if lines:
            employee.address1 = lines[0][:200]
            rest = lines[1:]
            parts = _city_state_zip(rest[-1]) if rest else None
            if parts:
                employee.city, employee.state, employee.zip = parts
                rest = rest[:-1]
            if rest:
                employee.address2 = ", ".join(rest)[:200]
        db.add(employee)
        made += 1
    db.flush()
    return made


# ── the open invoices: the A/R Aging Detail report ─────────────────────────

# The item a carried-over invoice's line is on. Its income account is the
# export's own receivable account, on purpose: the sale is in the imported
# history already, so the invoice's posting moves its open balance from
# that account to 1100 Accounts Receivable and touches no income (when that
# account is 1100 itself, nothing moves). An edit or a void of the invoice
# posts through the same item, so it stays true. It is inactive after the
# import: on the carried invoices, in no item picker (#711).
CARRY_ITEM = "QuickBooks Online open balance"


def parse_ar_aging(csv_text: str) -> tuple[list[dict], list[str], list[str]]:
    """The A/R Aging Detail report's rows as {kind, type, date, num,
    customer, due_date, amount, open}: kind "invoice" for an invoice with
    an open balance, "payment" for a payment not (fully) applied, whose
    open balance is negative. The report's own layout: the title rows, a
    header row (Date, Transaction type, Num, Customer full name, Due date,
    Amount, Open balance), a section per aging bucket with its "Total for"
    row, a TOTAL row and a footer with the time it was run. Returns the
    rows, the errors (nothing can be carried) and the notes (a row left
    where it is)."""
    rows = _rows(csv_text)
    items: list[dict] = []
    errors: list[str] = []
    notes: list[str] = []
    header_at = None
    for i, row in enumerate(rows):
        cells = [c.strip().lower() for c in row]
        if "date" in cells and "open balance" in cells:
            header_at = i
            break
    if header_at is None:
        errors.append(
            "The A/R Aging Detail report has no row with Date and Open balance "
            "columns: export it from QuickBooks Online (Reports → A/R Aging "
            "Detail → Export to Excel) and upload it as it downloads"
        )
        return items, errors, notes
    title = " ".join(c.strip() for row in rows[:header_at] for c in row if c.strip())
    if not re.search(r"\bA/?R\s+aging\b", title, re.I):
        notes.append(
            f"{title[:120] or 'The aging report'} is not read: the open invoices "
            "come from the A/R Aging Detail report. Unpaid bills are entered "
            "after the import."
        )
        return items, errors, notes
    header = [c.strip().lower() for c in rows[header_at]]

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_date, i_type = col("date"), col("transaction type", "type")
    i_num, i_due = col("num", "no.", "number"), col("due date")
    i_cust = col("customer full name", "customer", "name")
    i_amount, i_open = col("amount"), col("open balance")
    if None in (i_type, i_cust):
        errors.append(
            "The A/R Aging Detail report lacks its Transaction type or Customer "
            "column: export it with the report's own columns"
        )
        return items, errors, notes
    stated_total = None
    left_alone = Decimal("0")  # the open balances of rows not carried over
    numbers: dict[str, int] = {}
    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        label = _cell(row, 0).strip()
        if label.upper() == "TOTAL":
            try:
                stated_total = parse_amount(_cell(row, i_open))
            except Exception:  # the total as a formula ("=H29+H33"): no figure
                stated_total = None
            continue
        raw_date = _cell(row, i_date).strip()
        ttype = _cell(row, i_type).strip()
        if not raw_date or not ttype:
            continue  # a section's heading or its total, a blank, the footer
        customer = _cell(row, i_cust).strip()
        num = _cell(row, i_num).strip() if i_num is not None else ""
        try:
            day = parse_date(raw_date, _DATE_FORMATS)
            raw_due = _cell(row, i_due).strip() if i_due is not None else ""
            due = parse_date(raw_due, _DATE_FORMATS) if raw_due else day
            open_ = parse_amount(_cell(row, i_open))
            amount = (
                parse_amount(_cell(row, i_amount)) if i_amount is not None else open_
            )
        except Exception as exc:  # DataProblem: an unreadable cell
            errors.append(f"A/R Aging Detail row {n}: {exc}")
            continue
        what = f"{ttype} {num}".strip() + f" of {day:%m/%d/%Y} for {customer}"
        if not customer:
            errors.append(f"A/R Aging Detail row {n}: {what} names no customer")
            continue
        kind = {"invoice": "invoice", "payment": "payment"}.get(ttype.lower())
        if kind is None or (kind == "invoice") != (open_ > 0) or open_ == 0:
            left_alone += open_
            notes.append(
                f"{what} ({open_:,.2f} open) is not carried over: it stays on the "
                "export's receivable account. Enter it after the import."
            )
            continue
        if kind == "invoice":
            # QuickBooks Online lets two invoices share a number; ours are unique
            base = num or f"QBO-{day:%Y%m%d}"
            numbers[base] = numbers.get(base, 0) + 1
            if numbers[base] > 1 or not num:
                why = (
                    "the report gives it no number"
                    if not num
                    else "another open invoice has its number"
                )
                num = f"{base}-{numbers[base]}"
                notes.append(f"{what}: carried over as invoice {num} ({why})")
        items.append(
            {
                "kind": kind,
                "type": ttype,
                "date": day,
                "num": num[:50],
                "customer": customer[:200],
                "due_date": due,
                "amount": amount,
                "open": open_,
            }
        )
    carried = sum((i["open"] for i in items), Decimal("0"))
    if stated_total is not None and not errors and carried + left_alone != stated_total:
        errors.append(
            f"The A/R Aging Detail report's TOTAL ({stated_total:,.2f}) is not "
            "the sum of its rows: export it again"
        )
    return items, errors, notes


def _receivable_name(bundle: dict, groups: list[list[dict]]) -> str | None:
    """The export's receivable account: the one the Balance Sheet lists
    under Accounts Receivable (if there are several, the one the Journal's
    invoices post to), else the account the invoices debit."""
    saved = list(_COA_NOTES)  # parse_coa keeps its notes for the dry run
    saved_unplaced = list(_COA_UNPLACED)
    try:
        accounts, _ = parse_coa(bundle.get("coa", ""))
    finally:
        _COA_NOTES[:] = saved
        _COA_UNPLACED[:] = saved_unplaced
    # the path, the name the Journal posts to (NEW-203)
    listed = [spec_path(a) for a in accounts if a.get("receivable")]
    used: dict[str, int] = {}
    for group in groups:
        if group and group[0]["journal"].startswith("Invoice "):
            for row in group:
                if row["debit"] > 0:
                    used[row["account"]] = used.get(row["account"], 0) + 1
    if len(listed) == 1:
        return listed[0]
    for name in sorted(used, key=used.get, reverse=True):
        if not listed or name in listed:
            return name
    return listed[0] if listed else None


def _customer_names(db: Session, bundle: dict) -> dict[str, str]:
    """Every name a customer may go by, lowercased, to the name it has
    here: the books' customers, then the Customer list's names and (when
    one person has it) its Full Name column."""
    names = {c.qb_name.lower(): c.qb_name for c in db.query(Customer).all()}
    if "customers" in bundle:
        listed = parse_contacts(bundle["customers"], "Customer")
        for spec in listed:
            names.setdefault(spec["name"].lower(), spec["name"])
        by_full: dict[str, list[str]] = {}
        for spec in listed:
            if spec.get("full_name"):
                by_full.setdefault(spec["full_name"].lower(), []).append(spec["name"])
        for full, display in by_full.items():
            if len(display) == 1:
                names.setdefault(full, display[0])
    return names


# The words a carried invoice's payment for its paid part opens its notes
# with: it is no unapplied credit, and a second upload does not take it
# for one (#711).
_PAID_PART = "The part of QuickBooks Online invoice"


def _journal_invoices(groups: list[list[dict]], receivable: str) -> dict:
    """What the Journal says of each invoice and customer payment, for
    carrying an open invoice at its total (#711): {"invoices": {(number,
    name): (date, total on the receivable)}, "payments": [(name, date,
    amount off the receivable, number)]}, names lowercased."""
    invoices: dict = {}
    payments: list = []
    for group in groups:
        first = group[0] if group else None
        if first is None:
            continue
        kind = (first.get("type") or "").lower()
        on_ar = sum(
            (r["debit"] - r["credit"] for r in group if r["account"] == receivable),
            Decimal("0"),
        )
        name = (first.get("name") or "").lower()
        if kind == "invoice" and first.get("num"):
            invoices.setdefault((first["num"], name), (first["date"], on_ar))
        elif kind == "payment" and on_ar < 0:
            payments.append((name, first["date"], -on_ar, first.get("num") or ""))
    return {"invoices": invoices, "payments": payments}


def _at_total(item: dict, journal: dict) -> bool:
    """Whether the Journal holds the open invoice ``item`` (its number and
    customer) at a total above what is open: then it is carried at that
    total, dated as the Journal dates it, with a payment for the part paid,
    dated as the Journal's payment of that amount from the customer on or
    after the invoice (else on the invoice's date). Sets item["total"],
    item["paid"], item["paid_on"] and item["paid_ref"]."""
    names = {item["customer"].lower(), item.get("listed_as", "").lower()}
    hit = next(
        (
            journal["invoices"][(item["num"], n)]
            for n in names
            if (item["num"], n) in journal["invoices"]
        ),
        None,
    )
    if hit is None or hit[1] <= item["open"]:
        return False
    day, total = hit
    paid = total - item["open"]
    paid_on, paid_ref = day, ""
    for name, when, amount, num in sorted(
        journal["payments"], key=lambda p: (p[1], p[3])
    ):
        if name in names and when >= day and amount == paid:
            paid_on, paid_ref = when, num
            break
    item.update(date=day, total=total, paid=paid, paid_on=paid_on, paid_ref=paid_ref)
    return True


def _carry_plan(db: Session, bundle: dict) -> dict:
    """What the A/R Aging Detail report carries over, checked against the
    Journal and the books before anything is written: the invoices and
    payments to carry (the ones already here left out), the export's
    receivable account, the errors and the notes. Without the report, a
    note says what the Journal leaves on receivables and how to bring it."""
    from app.models.accounts import Account
    from app.models.invoices import Invoice
    from app.models.payments import Payment
    from app.services.accounting import get_ar_account_id

    plan = {
        "given": "ar_aging" in bundle,
        "invoices": [],
        "payments": [],
        "duplicates": 0,
        "errors": [],
        "notes": [],
        "receivable": None,
        # the Journal's receivable is 1100 here (#704)
        "onto_control": False,
        # invoices carried at their Journal total, with a payment (#711)
        "at_total": 0,
    }
    if "gl" not in bundle or "coa" not in bundle:
        return plan  # the engine names what is missing
    groups, _ = parse_gl(bundle["gl"])
    receivable = _receivable_name(bundle, groups)
    plan["receivable"] = receivable
    left = sum(
        (
            row["debit"] - row["credit"]
            for group in groups
            for row in group
            if row["account"] == receivable
        ),
        Decimal("0"),
    )
    if not plan["given"]:
        if receivable and left:
            plan["notes"].append(
                f"The Journal leaves {left:,.2f} on {receivable}: the invoices still "
                "open and the payments not yet applied. To carry them over as "
                "invoices and customer credits, download QuickBooks Online's A/R "
                "Aging Detail report (Reports → A/R Aging Detail → Export to Excel) "
                "and upload it with the export."
            )
        return plan
    items, errors, notes = parse_ar_aging(bundle["ar_aging"])
    plan["errors"].extend(errors)
    plan["notes"].extend(notes)
    if not items:
        return plan
    if receivable is None:
        plan["errors"].append(
            "The export has no receivable account (the Balance Sheet's Accounts "
            "Receivable): the open invoices cannot be carried over"
        )
        return plan
    try:
        control = db.get(Account, get_ar_account_id(db))
    except Exception:
        control = None
    total = sum((i["open"] for i in items), Decimal("0"))
    found = books_by_name(db).get(receivable.lower())
    # The report carries every cent the Journal leaves on the receivable:
    # the Journal's receivable lands on 1100 itself, as the A/P account
    # lands on 2000, and no empty account is left beside it (#704). The
    # carried documents then post nothing: their balance is in 1100 by the
    # history already. A receivable an earlier import made stays as it is.
    plan["onto_control"] = (
        control is not None
        and total == left
        and (found is None or found.id == control.id)
    )
    if (
        control is not None
        and not plan["onto_control"]
        and control.name.lower() == receivable.lower()
    ):
        plan["errors"].append(
            f"The export's receivable account is named {receivable!r}, as 1100 is "
            "here, so the history already posts to 1100 and the open invoices "
            "would count twice. Rename it in QuickBooks Online (its default name "
            "is Accounts Receivable (A/R)) and export again, or import without the "
            "A/R Aging Detail report."
        )
        return plan
    names = _customer_names(db, bundle)
    journal = _journal_invoices(groups, receivable)
    unknown = set()
    for item in items:
        item["listed_as"] = item["customer"]  # as the report writes it
        name = names.get(item["customer"].lower())
        if name is None:
            unknown.add(item["customer"])
            name = item["customer"]
        item["customer"] = name
    for name in sorted(unknown):
        plan["notes"].append(
            f"{name} is on the A/R Aging Detail report but not on the Customer "
            "list: the customer is created with the carried-over documents"
        )
    if plan["onto_control"]:
        receivable_id = control.id
    else:
        receivable_id = found.id if found is not None else None
    here = {
        inv.invoice_number: inv.customer.qb_name
        for inv in db.query(Invoice).options(joinedload(Invoice.customer))
        if inv.customer is not None
    }
    carried_payments: dict[tuple, int] = {}
    if receivable_id is not None:
        for pay in (
            db.query(Payment)
            .options(joinedload(Payment.customer))
            .filter(
                Payment.deposit_to_account_id == receivable_id,
                Payment.is_voided.isnot(True),
            )
        ):
            if (pay.notes or "").startswith(_PAID_PART):
                continue  # the paid part of a carried invoice, not a credit
            cname = pay.customer.qb_name if pay.customer is not None else ""
            key = (cname.lower(), pay.date, Decimal(str(pay.amount)))
            carried_payments[key] = carried_payments.get(key, 0) + 1
    for item in items:
        if item["kind"] == "invoice":
            owner = here.get(item["num"])
            if owner is None:
                if _at_total(item, journal):
                    plan["at_total"] += 1
                plan["invoices"].append(item)
            elif owner.lower() == item["customer"].lower():
                plan["duplicates"] += 1
            else:
                plan["errors"].append(
                    f"Number {item['num']} is on an invoice in the books already, "
                    f"for {owner}, not {item['customer']}"
                )
        else:
            key = (item["customer"].lower(), item["date"], -item["open"])
            if carried_payments.get(key):
                carried_payments[key] -= 1
                plan["duplicates"] += 1
            else:
                plan["payments"].append(item)
    control_name = (
        label_for(control, db) if control is not None else "Accounts Receivable"
    )
    if plan["duplicates"] == len(items):
        plan["notes"].append(
            f"All {len(items)} rows of the A/R Aging Detail report are in the books "
            "already from an earlier import: nothing is carried over twice."
        )
        return plan
    if plan["duplicates"]:
        plan["notes"].append(
            f"{plan['duplicates']} of the A/R Aging Detail report's {len(items)} "
            "rows are in the books already from an earlier import and will be "
            "skipped, not carried over twice"
        )
    if plan["at_total"]:
        plan["notes"].append(
            f"{plan['at_total']} of the open invoices "
            f"{'is' if plan['at_total'] == 1 else 'are'} carried at "
            f"{'its' if plan['at_total'] == 1 else 'their'} total from the "
            "Journal, with a customer payment for the part already paid, so "
            "the customer's history reads as it did in QuickBooks Online."
        )
    if plan["onto_control"]:
        plan["notes"].append(
            f"The A/R Aging Detail report's open invoices and unapplied payments "
            f"come to {total:,.2f}, what the Journal leaves on {receivable}: "
            f"{receivable} is {control_name} here, so the Journal posts to it "
            "and no second receivable account is made; they come across as its "
            "invoices and customer credits."
        )
    elif total == left:
        plan["notes"].append(
            f"The A/R Aging Detail report's open invoices and unapplied payments "
            f"come to {total:,.2f}, what the Journal leaves on {receivable}: they "
            f"move to {control_name} as invoices and customer credits, and "
            f"{receivable} ends at zero."
        )
    else:
        plan["notes"].append(
            f"The A/R Aging Detail report's open balances come to {total:,.2f}, and "
            f"the Journal leaves {left:,.2f} on {receivable}: the difference, "
            f"{left - total:,.2f}, stays on {receivable} (a report run on another "
            "day than the export, or a row not carried over)."
        )
    return plan


def _carry_item(db: Session, receivable_id: int):
    from app.models.items import Item, ItemType

    item = db.query(Item).filter(Item.name == CARRY_ITEM).first()
    if item is None:
        item = Item(
            name=CARRY_ITEM,
            item_type=ItemType.SERVICE,
            description=(
                "Open balances carried over from QuickBooks Online. Its income "
                "account is the imported receivable account on purpose: the "
                "sales are in the imported history already."
            ),
            rate=Decimal("0"),
            is_taxable=False,
            is_active=True,
        )
        db.add(item)
    item.income_account_id = receivable_id
    db.flush()
    return item


def _import_carried(db: Session, plan: dict) -> tuple[int, int]:
    """Carry the plan's invoices and payments over: each invoice with its
    number, dates and open balance on the CARRY_ITEM line (its posting:
    1100 debited, the receivable account credited), each unapplied
    payment for its open part, unapplied (its posting: the receivable
    account debited, 1100 credited). Returns (invoices, payments).

    An invoice the Journal holds at a larger total is carried at that
    total, with a customer payment for the part already paid applied to it
    (its posting: the receivable account debited, 1100 credited), so it
    reads as it did and 1100 moves by its open balance alone (#711).

    When the Journal's receivable is 1100 itself (plan["onto_control"],
    #704) the history has put the open balances there already: the
    documents post nothing, and CARRY_ITEM's account is 1100, so an edit of
    one posts nothing either.

    CARRY_ITEM is made inactive once they are written: it stays on them and
    out of every item picker (#711)."""
    from app.models.accounts import Account
    from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
    from app.models.payments import Payment, PaymentAllocation
    from app.routes.invoices.helpers import _post_invoice_journal
    from app.services.accounting import create_journal_entry, get_ar_account_id
    from app.services.sub_customers import invoice_addresses

    if not (plan["invoices"] or plan["payments"]):
        return 0, 0
    ar_id = get_ar_account_id(db)
    if plan.get("onto_control"):
        receivable = db.get(Account, ar_id)
    else:
        receivable = books_by_name(db)[plan["receivable"].lower()]
    post = not plan.get("onto_control")
    item = _carry_item(db, receivable.id)
    from app.services.jobs_service import resolve_customer_and_job

    customers = {c.qb_name.lower(): c for c in db.query(Customer).all()}

    def customer(name):
        """The customer the report names; "Parent:Sub" is the sub-customer
        (#638), made under its parent when the list did not carry it."""
        found = customers.get(name.lower())
        if found is None and ":" in name:
            found, _job = resolve_customer_and_job(db, name, sub_customers=True)
        if found is None:
            found = Customer(name=name)
            db.add(found)
            db.flush()
        customers[name.lower()] = found
        return found

    def payment_posting(payment, who, amount, memo):
        """The receivable debited and 1100 credited by ``amount``."""
        if not post:
            return
        txn = create_journal_entry(
            db,
            payment.date,
            f"Payment from {who.name} (carried over from QuickBooks Online)",
            [
                {
                    "account_id": receivable.id,
                    "debit": amount,
                    "credit": Decimal("0"),
                    "description": memo,
                },
                {
                    "account_id": ar_id,
                    "debit": Decimal("0"),
                    "credit": amount,
                    "description": memo,
                },
            ],
            source_type="payment",
            source_id=payment.id,
            reference=payment.reference or "",
        )
        payment.transaction_id = txn.id

    for spec in plan["invoices"]:
        who = customer(spec["customer"])
        amount = spec["open"]
        total = spec.get("total") or amount
        paid = total - amount
        partly = spec["amount"] != amount
        invoice = Invoice(
            invoice_number=spec["num"],
            customer_id=who.id,
            status=InvoiceStatus.PARTIAL if paid else InvoiceStatus.SENT,
            date=spec["date"],
            due_date=spec["due_date"],
            terms="",
            subtotal=total,
            tax_rate=Decimal("0"),
            tax_amount=Decimal("0"),
            total=total,
            amount_paid=Decimal("0"),
            balance_due=total,
            notes=(
                "Carried over from QuickBooks Online's A/R Aging Detail report: "
                + (
                    f"the sale is in the imported history, at its total, with "
                    f"the {paid:,.2f} already paid."
                    if paid
                    else "the sale is in the imported history, and this is "
                    "what was open."
                )
            ),
            # The report carries no address: billed as a new invoice is,
            # through the one helper, the payer's Bill To (the property's
            # street under its name for a tenant billed with it) and the
            # customer's ship-to.
            **invoice_addresses(db, who),
        )
        db.add(invoice)
        db.flush()
        line = InvoiceLine(
            invoice_id=invoice.id,
            item_id=item.id,
            description=(
                f"QuickBooks Online invoice {spec['num']} of {spec['date']:%m/%d/%Y}"
                + (
                    f" for {spec['amount']:,.2f}, {amount:,.2f} of it open"
                    if partly and not paid
                    else ""
                )
            ),
            quantity=Decimal("1"),
            rate=total,
            amount=total,
            is_taxable=False,
            line_order=0,
        )
        db.add(line)
        db.flush()
        if post:
            txn = _post_invoice_journal(db, invoice, [line], who.name)
            # A $0.00 line posts no journal (#499).
            invoice.transaction_id = txn.id if txn is not None else None
        if paid:
            payment = Payment(
                customer_id=who.id,
                date=spec.get("paid_on") or spec["date"],
                amount=paid,
                reference=spec.get("paid_ref") or None,
                deposit_to_account_id=receivable.id,
                notes=(
                    f"{_PAID_PART} {spec['num']} already paid there. The money "
                    "is in the imported history."
                ),
            )
            db.add(payment)
            db.flush()
            db.add(
                PaymentAllocation(
                    payment_id=payment.id, invoice_id=invoice.id, amount=paid
                )
            )
            invoice.amount_paid = paid
            invoice.balance_due = amount
            payment_posting(payment, who, paid, "Paid part carried over")
    for spec in plan["payments"]:
        who = customer(spec["customer"])
        amount = -spec["open"]
        payment = Payment(
            customer_id=who.id,
            date=spec["date"],
            amount=amount,
            reference=spec["num"] or None,
            deposit_to_account_id=receivable.id,
            notes=(
                "Carried over from QuickBooks Online's A/R Aging Detail report: "
                + (
                    f"a payment of {-spec['amount']:,.2f}, {amount:,.2f} of it not "
                    "applied there"
                    if spec["amount"] != spec["open"]
                    else "a payment not applied there"
                )
                + ". The money is in the imported history."
            ),
        )
        db.add(payment)
        db.flush()
        payment_posting(payment, who, amount, "Unapplied payment carried over")
    if not post:
        # the name a void of one of them moves its balance to (#704)
        from app.services.settings_service import set_setting

        set_setting(db, RECEIVABLE_SETTING, plan["receivable"])
    # on the carried invoices, in no picker (#711)
    item.is_active = False
    db.flush()
    return len(plan["invoices"]), len(plan["payments"])


# ── voiding a document carried onto 1100 (#704) ────────────────────────────
#
# With the export's receivable on 1100 itself, a carried invoice or payment
# has no posting of its own: its balance is in 1100 by the history. A void
# takes it off the A/R aging, so it also moves the amount off 1100, to
# QuickBooks Online's own receivable account (by the name the export used,
# made when first needed), as the carry-over through that account did
# before: the aging still equals 1100, and nothing leaves the books.

RECEIVABLE_SETTING = "qbo_export_receivable_name"
_DEFAULT_RECEIVABLE = "Accounts Receivable (A/R)"


def _ar_control(db: Session):
    from app.models.accounts import Account
    from app.services.accounting import get_ar_account_id

    try:
        return db.get(Account, get_ar_account_id(db))
    except Exception:  # MissingControlAccount: nothing carried onto it
        return None


def is_carried_invoice(db: Session, invoice) -> bool:
    """An invoice the carry-over wrote onto 1100 with no posting."""
    if invoice.transaction_id is not None:
        return False
    control = _ar_control(db)
    if control is None:
        return False
    return any(
        line.item is not None
        and line.item.name == CARRY_ITEM
        and line.item.income_account_id == control.id
        for line in invoice.lines
    )


def is_carried_payment(db: Session, payment) -> bool:
    """A customer payment the carry-over wrote onto 1100 with no posting:
    an unapplied credit, or the paid part of an invoice."""
    if payment.transaction_id is not None:
        return False
    control = _ar_control(db)
    if control is None or payment.deposit_to_account_id != control.id:
        return False
    notes = payment.notes or ""
    return notes.startswith(_PAID_PART) or notes.startswith(
        "Carried over from QuickBooks Online"
    )


def _receivable_account(db: Session, control):
    """QuickBooks Online's receivable account, made now if the books lack
    it: an Other Current Asset, not a control."""
    from app.models.accounts import Account, AccountType, DetailType
    from app.services.settings_service import get_setting_raw

    name = (get_setting_raw(db, RECEIVABLE_SETTING) or "").strip()
    name = name or _DEFAULT_RECEIVABLE
    found = books_by_name(db).get(name.lower())
    if found is not None and found.id == control.id:
        # the export named it as 1100 is named here
        name = f"{name} (QuickBooks Online)"
        found = books_by_name(db).get(name.lower())
    if found is not None:
        return found
    account = Account(
        name=name[:200],
        account_type=AccountType.ASSET,
        detail_type=DetailType.OTHER_CURRENT_ASSET.value,
        description=(
            "QuickBooks Online's receivable: the balances of carried-over "
            "invoices and customer credits voided here."
        ),
    )
    db.add(account)
    db.flush()
    return account


def void_carried_posting(db: Session, *, invoice=None, payment=None) -> str | None:
    """For a carried document voided: the entry that moves what the void
    takes off the aging off 1100 (an invoice's open balance to QuickBooks'
    receivable; a payment's amount back from it), dated today. Returns the
    sentence for the person, or None for any other document. Call it
    before the document's own amounts change."""
    from datetime import date

    from app.services.accounting import create_journal_entry

    if invoice is not None:
        if not is_carried_invoice(db, invoice):
            return None
        amount = Decimal(str(invoice.balance_due or 0))
        from app.services.donor_documents import document_label
        from app.services.terminology import terms_from_db

        what = f"{document_label(invoice, terms_from_db(db))} {invoice.invoice_number}"
        source_type, source_id = "invoice_void", invoice.id
        reference = invoice.invoice_number or ""
    else:
        if payment is None or not is_carried_payment(db, payment):
            return None
        amount = Decimal(str(payment.amount or 0))
        what = "Payment" + (f" {payment.reference}" if payment.reference else "")
        source_type, source_id = "payment_void", payment.id
        reference = payment.reference or ""
    if amount <= 0:
        return None
    control = _ar_control(db)
    receivable = _receivable_account(db, control)
    memo = f"VOID {what}, carried over from QuickBooks Online"
    off_control = invoice is not None  # an invoice leaves 1100; a payment returns
    create_journal_entry(
        db,
        date.today(),
        memo,
        [
            {
                "account_id": receivable.id if off_control else control.id,
                "debit": amount,
                "credit": Decimal("0"),
                "description": memo,
            },
            {
                "account_id": control.id if off_control else receivable.id,
                "debit": Decimal("0"),
                "credit": amount,
                "description": memo,
            },
        ],
        source_type=source_type,
        source_id=source_id,
        reference=reference,
    )
    control_name = label_for(control, db)
    if off_control:
        return (
            f"{what} was carried over from QuickBooks Online: its {amount:,.2f} "
            f"moved from {control_name} to {receivable.name}, where it sat in "
            "QuickBooks Online."
        )
    return (
        f"{what} was carried over from QuickBooks Online: its {amount:,.2f} "
        f"moved from {receivable.name} back to {control_name}."
    )


# ── the engine's hooks ─────────────────────────────────────────────────────


def _names_in_use(bundle: dict) -> tuple[str, ...]:
    """Every account the Journal posts to and the Trial Balance lists, in
    first-seen order, so the chart parser can fill what the reports hide."""
    seen: dict[str, None] = {}
    tb_text = bundle.get("tb") or bundle.get("tb_names_only")
    if tb_text:  # first: its order is the chart's, assets through expenses
        for name in _tb_names(tb_text):
            seen.setdefault(name, None)
    if "gl" in bundle:
        groups, _ = parse_gl(bundle["gl"])
        for group in groups:
            for row in group:
                seen.setdefault(row["account"], None)
    return tuple(seen)


def _tb_names(csv_text: str) -> list[str]:
    """The Trial Balance's account names as written, after its header row
    and before its footer; nothing from the preamble."""
    names: list[str] = []
    started = False
    for row in _rows(csv_text):
        cells = [c.strip().lower() for c in row]
        if not started:
            started = "debit" in cells and "credit" in cells
            continue
        name = _cell(row, 0).strip()
        if name and not name.lower().startswith("total") and not _BASIS.search(name):
            names.append(name)
    return names


def _onto_controls(db: Session, accounts: list[dict], plan: dict | None) -> None:
    """Map the export's control accounts onto the books' (#704): its
    payable account ("Accounts Payable (A/P)", the one under the Balance
    Sheet's Accounts Payable; of several, the one by that name) onto 2000,
    always; its receivable onto 1100 when the carry-over says so
    (plan["onto_control"]). The engine then makes no second account, and
    the Journal's and the Trial Balance's lines land on the control, which
    keeps its type. A note says so for the payable."""
    from app.services.accounting import get_ap_account_id, get_ar_account_id
    from app.services.migration_common import onto_accounts

    payables = [a for a in accounts if a.get("payable")]
    if len(payables) > 1:
        payables = [
            a for a in payables if spec_path(a).lower() == "accounts payable (a/p)"
        ]
    mapped = []
    if len(payables) == 1:
        try:
            payables[0]["onto"] = get_ap_account_id(db)
            mapped.append(payables[0])
        except Exception:  # MissingControlAccount: the account is made as before
            pass
    if plan and plan.get("onto_control") and plan.get("receivable"):
        wanted = plan["receivable"].lower()
        for spec in accounts:
            if spec_path(spec).lower() == wanted:
                spec["onto"] = get_ar_account_id(db)
    landed = onto_accounts(db, mapped)
    _COA_PAYABLE.clear()
    for spec in mapped:
        control = landed.get(spec_path(spec).lower())
        if control is not None:
            _COA_PAYABLE.append((spec_path(spec), label_for(control, db)))
            _COA_NOTES.append(
                f"{spec_path(spec)} is {label_for(control, db)} here: its balance "
                "lands there, and no second payable account is made."
            )


def _parsers(bundle: dict, db: Session | None = None, plan: dict | None = None):
    also = _names_in_use(bundle)
    tb_text = bundle.get("tb") or bundle.get("tb_names_only")
    ordered = tuple(_tb_names(tb_text)) if tb_text else ()
    # the accrual Trial Balance's figures, the ones the opening balances
    # come from: a cash-basis one is set aside and gives none
    amounts = parse_tb(bundle["tb"])[0] if "tb" in bundle else None

    def coa(text):
        accounts, errors = parse_coa(text, also, ordered, amounts)
        if db is not None:
            _onto_controls(db, accounts, plan)
        return accounts, errors

    return {
        "coa": coa,
        "gl": parse_gl,
        "tb": parse_tb,
    }


PARSERS = {"coa": parse_coa, "gl": parse_gl, "tb": parse_tb}


def _prepare(bundle: dict) -> tuple[dict, list[str]]:
    """The bundle the engine sees, and the notes to add. A cash-basis Trial
    Balance is set aside: the Journal is accrual, and the engine would read
    the difference (open invoices, unpaid bills) as opening balances."""
    bundle = dict(bundle)
    notes: list[str] = []
    if "tb" in bundle and trial_balance_basis(bundle["tb"]) == "cash":
        bundle["tb_names_only"] = bundle.pop("tb")
        bundle["tb_set_aside"] = True  # the engine's "no trial balance" note stays out
        notes.append(
            "The Trial Balance was exported on the cash basis, and the Journal is "
            "accrual, so the two cannot be compared: balances were not verified. "
            "To verify, set the company's report basis to Accrual in QuickBooks "
            "Online, export again, and dry-run with that Trial Balance."
        )
    return bundle, notes


def _counts(db: Session, bundle: dict) -> dict:
    """The customers and vendors an import would make: the lists' names not
    in the books yet, as _import_contacts counts them."""

    def new(kind, label, model):
        if kind not in bundle:
            return 0
        have = {row.name.lower() for row in db.query(model.name).all()}
        names = {spec["name"].lower() for spec in parse_contacts(bundle[kind], label)}
        return len(names - have)

    return {
        "customers": new("customers", "Customer", Customer),
        "vendors": new("vendors", "Vendor", Vendor),
        "employees": len(_new_employees(db, bundle)),
    }


def _unplaced_problem(errors: list[str]) -> str | None:
    """The refusal for the Trial Balance accounts parse_coa could not place
    (W-108), unless the engine has already named them in its own words."""
    unplaced = list(_COA_UNPLACED)
    if not unplaced:
        return None
    problem = _missing_problem(unplaced)
    if any("do not list" in e and all(repr(n) in e for n in unplaced) for e in errors):
        return None
    return problem


def _payable_balance(prepared: dict, path: str) -> Decimal:
    """What the export leaves owed on its payable account, a credit as a
    positive figure: the accrual Trial Balance's, else the Journal's sum."""
    key = strip_code_suffix(path)
    if "tb" in prepared:
        balances, _ = parse_tb(prepared["tb"])
        found = [v for n, v in balances.items() if strip_code_suffix(n) == key]
        if found:
            return -sum(found, Decimal("0"))
    total = Decimal("0")
    if "gl" in prepared:
        groups, _ = parse_gl(prepared["gl"])
        for group in groups:
            for row in group:
                if strip_code_suffix(row["account"]) == key:
                    total += (row["credit"] or Decimal("0")) - (
                        row["debit"] or Decimal("0")
                    )
    return total


def _payable_note(prepared: dict) -> list[str]:
    """W-117: the export brings the payable account's balance and none of
    the bills behind it, so A/P Aging cannot show it; said when it is not
    zero."""
    if len(_COA_PAYABLE) != 1:
        return []
    path, control = _COA_PAYABLE[0]
    owed = _payable_balance(prepared, path)
    if not owed:
        return []
    what = (
        f"{owed:,.2f} owed to vendors"
        if owed > 0
        else f"{-owed:,.2f} paid to vendors ahead of their bills"
    )
    return [
        f"The export brings {what} as a balance on {control}, with no bills "
        f"behind it. A/P Aging lists only bills entered here, so it will not "
        f"show that {abs(owed):,.2f} until those bills are entered here or "
        f"paid off against {control}."
    ]


def dry_run(db: Session, bundle: dict) -> dict:
    plan = _carry_plan(db, bundle)
    prepared, notes = _prepare(bundle)
    _COA_PAYABLE.clear()
    verdict = dry_run_bundle(
        db, prepared, _parsers(prepared, db, plan), LABEL, SOURCE_TYPE
    )
    verdict["warnings"] = (
        notes + list(_COA_NOTES) + _payable_note(prepared) + verdict["warnings"]
    )
    unplaced = _unplaced_problem(verdict["errors"])
    if unplaced:
        verdict["errors"] = verdict["errors"] + [unplaced]
        verdict["ok"] = False
    verdict.update(_counts(db, bundle))
    verdict["errors"] = verdict["errors"] + plan["errors"]
    verdict["ok"] = verdict["ok"] and not plan["errors"]
    verdict["warnings"] = verdict["warnings"] + plan["notes"]
    if plan["given"]:
        verdict["open_invoices"] = len(plan["invoices"])
        verdict["unapplied_payments"] = len(plan["payments"])
    return verdict


def run_import(db: Session, bundle: dict) -> dict:
    plan = _carry_plan(db, bundle)  # checked before anything is written
    if plan["errors"]:
        refused = dry_run(db, bundle)
        refused.update(
            imported_accounts=0,
            imported_journals=0,
            imported_customers=0,
            imported_vendors=0,
            imported_employees=0,
        )
        if plan["given"]:
            refused.update(imported_open_invoices=0, imported_unapplied_payments=0)
        return refused
    prepared, notes = _prepare(bundle)
    _COA_PAYABLE.clear()
    parsers = _parsers(prepared, db, plan)
    if "coa" in prepared:
        # a balance the chart has no place for refuses before anything is
        # written, as a 422 with its names (#690, W-108)
        parsers["coa"](prepared["coa"])
        if _COA_UNPLACED:
            raise DataProblem(_missing_problem(list(_COA_UNPLACED)))
    result = run_import_bundle(db, prepared, parsers, SOURCE_TYPE, LABEL)
    result["warnings"] = (
        notes
        + list(_COA_NOTES)
        + _payable_note(prepared)
        + result["warnings"]
        + plan["notes"]
    )
    result.update(_counts(db, bundle))
    invoices = payments = 0
    if result["ok"]:
        customers, vendors = _import_contacts(db, bundle)
        employees = _import_employees(db, bundle)
        invoices, payments = _import_carried(db, plan)
        db.commit()
        # the payments it created get their cash-basis lines now (#483)
        from app.services.cash_basis import backfill_after_import

        backfill_after_import(db)
    else:
        customers = vendors = employees = 0
    result["imported_customers"] = customers
    result["imported_vendors"] = vendors
    result["imported_employees"] = employees
    if plan["given"]:
        result["imported_open_invoices"] = invoices
        result["imported_unapplied_payments"] = payments
    return result
