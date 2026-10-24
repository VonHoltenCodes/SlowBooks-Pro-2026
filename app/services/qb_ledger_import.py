# ============================================================================
# QuickBooks Desktop's Transaction Detail by Account, as journal entries
# (2.24.0).
#
# The report QuickBooks Desktop gives an accountant for "everything": every
# transaction of every type (deposits, checks, transfers, general journal
# entries, opening balances), grouped by account. Each account is a heading
# row, its sub-accounts indented one column under it; each transaction shows
# once in every account it touches, with the other side named in Split
# ("-SPLIT-" when there are several); every section ends with QuickBooks'
# own "Total <account>" row, and the report with a TOTAL row. The columns
# are interleaved with empty ones, and the Clr column carries QuickBooks'
# check glyph in the Windows code page.
#
# Read as it comes, each row is one line on its section's account; the rows
# of one transaction (its type, date and number, across every section) make
# one balanced journal entry. Same-day transactions with no number that
# share a key are kept apart by their name when each name balances on its
# own. Before anything is written the whole file is checked: every account
# in the chart (the lists IIF first), every transaction balanced, every
# section's lines adding up to the total QuickBooks printed for it. A file
# that fails is refused whole, never imported in part. A second upload
# posts nothing twice.
#
# Built against a real company's report (a year of a small practice's books:
# about 5,700 rows, every transaction balanced, the section totals matched);
# the fixture in tests/fixtures/qb_desktop/ is invented books in that layout.
# Nothing of the real file is here.
# ============================================================================

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.transactions import Transaction
from app.services.accounting import create_journal_entry

SOURCE_TYPE = "qb_ledger_import"
# QuickBooks' Type for a journal entry on this report
GENERAL_JOURNAL_TYPES = frozenset({"General Journal", "Journal", "Journal Entry"})
LEDGER_COLUMNS = ("Type", "Date", "Num", "Name", "Memo", "Split", "Debit", "Credit")
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y")
_HOW = (
    "Export it from QuickBooks Desktop as Reports > Accountant & Taxes > "
    "Transaction Detail by Account, with the dates set to All, and save it as CSV."
)


def is_ledger_header(cols: dict | None) -> bool:
    """The account-grouped layout: the ledger's columns and no Account
    column (the account is each section's heading)."""
    return (
        bool(cols) and all(c in cols for c in LEDGER_COLUMNS) and "Account" not in cols
    )


def has_account_sections(rows: list[list[str]], header_at: int, cols: dict) -> bool:
    """Recognize ledger grouping even when an added Account column masks it.

    The section headings sit left of the report's own columns, so they are
    looked for left of the first of those; the added Account column is left
    out of that reach and of the heading read, wherever it was inserted (as
    column A ahead of the headings, 2.24.1 gate W-27, or anywhere else)."""
    heading = None
    own = [v for k, v in cols.items() if k != "Account"]
    first = min(own) if own else 0
    skip = cols.get("Account")
    for row in rows[header_at + 1 :]:
        ttype = row[cols["Type"]].strip() if cols["Type"] < len(row) else ""
        if not ttype:
            label = next(
                (
                    c.strip()
                    for i, c in enumerate(row[:first])
                    if i != skip and c.strip()
                ),
                "",
            )
            if label:
                heading = (
                    None if label.startswith("Total ") or label == "TOTAL" else label
                )
            continue
        account = row[cols["Account"]].strip() if cols["Account"] < len(row) else ""
        # Sub-account sections carry a leaf name; Account may carry the full path.
        if (
            heading
            and account
            and heading.casefold()
            in (
                account.casefold(),
                account.rsplit(":", 1)[-1].strip().casefold(),
            )
        ):
            return True
    return False


def _money(raw: str) -> Decimal:
    raw = (raw or "").replace(",", "").replace("$", "").strip()
    if not raw:
        return Decimal("0")
    negative = raw.startswith("(") and raw.endswith(")")
    value = Decimal(raw.strip("()"))
    return -value if negative else value


def _date(raw: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unreadable date {raw!r}")


def parse_ledger(rows: list[list[str]], header_at: int, cols: dict):
    """(lines, errors, notes): each transaction row as {path, type, date,
    num, name, memo, debit, credit, row}, its account the path of the
    headings above it ("Parent:Child")."""
    first = cols["Type"]  # the label columns are the ones before Type
    lines: list[dict] = []
    errors: list[str] = []
    notes: list[str] = []
    # each open section: (depth, name, [debit, credit] of the lines under it
    # so far, its sub-accounts' included)
    stack: list[tuple[int, str, list[Decimal]]] = []

    def cell(row, name):
        i = cols.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    for n, row in enumerate(rows[header_at + 1 :], start=header_at + 2):
        ttype = cell(row, "Type")
        if not ttype:
            label = next(
                (
                    (j, row[j].strip())
                    for j in range(min(first, len(row)))
                    if row[j].strip()
                ),
                None,
            )
            if label is None:
                continue
            depth, text = label
            if text.upper() == "TOTAL":
                continue  # the report's grand total
            if text.startswith("Total "):
                while stack and stack[-1][0] > depth:
                    stack.pop()
                if stack and stack[-1][0] == depth:
                    # QuickBooks' own total: the section's lines (and its
                    # sub-accounts') add up to it, or the file is not what
                    # it says. Checked against the section being closed, so
                    # an account the report lists as two sections has each
                    # total measured against its own lines.
                    path = ":".join(name for _, name, _ in stack)
                    got_d, got_c = stack[-1][2]
                    try:
                        debit = _money(cell(row, "Debit"))
                        credit = _money(cell(row, "Credit"))
                    except Exception:
                        notes.append(
                            f"Row {n}: the total for {text[6:]} could not be read"
                        )
                    else:
                        if (got_d, got_c) != (debit, credit):
                            errors.append(
                                f"The lines under {path} come to {got_d:,.2f} debit "
                                f"and {got_c:,.2f} credit, and QuickBooks' total says "
                                f"{debit:,.2f} and {credit:,.2f}: the file looks cut "
                                "short or edited. Export it again."
                            )
                    stack.pop()
                continue
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack.append((depth, text, [Decimal("0"), Decimal("0")]))
            continue
        if not stack:
            errors.append(
                "This report lists its transactions without the account each one "
                "belongs to above it, so they cannot be posted. " + _HOW
            )
            return [], errors, notes
        try:
            debit, credit = _money(cell(row, "Debit")), _money(cell(row, "Credit"))
            day = _date(cell(row, "Date"))
        except Exception:
            # the cells as they read, never the exception's own words
            errors.append(
                f"Row {n}: the date ({cell(row, 'Date')!r}) or an amount "
                f"({cell(row, 'Debit')!r} / {cell(row, 'Credit')!r}) could not be read"
            )
            continue
        if debit == 0 and credit == 0:
            continue
        for _, _, sums in stack:
            sums[0] += debit
            sums[1] += credit
        lines.append(
            {
                "path": ":".join(name for _, name, _ in stack),
                "type": ttype,
                "date": day,
                "num": cell(row, "Num"),
                "name": cell(row, "Name"),
                "memo": cell(row, "Memo"),
                "debit": debit,
                "credit": credit,
                "row": n,
            }
        )
    return lines, errors, notes


def group_journals(lines: list[dict]) -> tuple[list[list[dict]], list[str]]:
    """The lines as balanced journals: one per transaction (type, date,
    number), split by name when that is what tells same-key transactions
    apart. QuickBooks can print a journal entry's number on one of its lines
    and not the other: two halves of the same type and day that balance
    each other are one entry. A transaction that still does not balance is
    named."""
    by_key: OrderedDict = OrderedDict()
    for ln in lines:
        by_key.setdefault((ln["type"], ln["date"], ln["num"]), []).append(ln)

    def off(group):
        return sum((ln["debit"] - ln["credit"] for ln in group), Decimal("0"))

    journals: list[list[dict]] = []
    loose: list[list[dict]] = []
    for group in by_key.values():
        by_name: OrderedDict = OrderedDict()
        for ln in group:
            by_name.setdefault(ln["name"], []).append(ln)
        if len(by_name) > 1 and all(off(part) == 0 for part in by_name.values()):
            journals.extend(by_name.values())
        elif off(group) == 0:
            journals.append(group)
        else:
            loose.append(group)
    errors: list[str] = []
    journals.extend(_combine_split_numbers(loose, off))
    while loose:
        group = loose.pop(0)
        first = group[0]
        mate = next(
            (
                other
                for other in loose
                if other[0]["type"] == first["type"]
                and other[0]["date"] == first["date"]
                and off(other) == -off(group)
                and (not other[0]["num"] or not first["num"])
            ),
            None,
        )
        if mate is not None:
            loose.remove(mate)
            numbered = group if first["num"] else mate
            journals.append(numbered + (mate if numbered is group else group))
            continue
        label = f"{first['type']} {first['num']}".strip()
        errors.append(
            f"{label} of {first['date']:%m/%d/%Y} does not balance ({off(group):,.2f} "
            f"over {len(group)} lines, rows {', '.join(str(ln['row']) for ln in group[:6])})"
        )
    journals.sort(key=lambda j: (j[0]["date"], j[0]["row"]))
    return journals, errors


# How many same-day numbered halves one unnumbered half is tried against.
# A deposit of a dozen customer checks is common; past this the search
# (every subset) would grow too large, and the halves are named instead.
_SPLIT_SEARCH_LIMIT = 12


def _combine_split_numbers(loose: list[list[dict]], off) -> list[list[dict]]:
    """One transaction QuickBooks printed under several numbers: a bank
    deposit of two customer checks shows its bank line with no number and
    each income line with that check's own number, so grouping by number
    splits it into an unnumbered half and two numbered halves that only
    balance together (a real 2013 deposit of 182.86 = 137.82 + 45.04, on
    discussion #349). An unnumbered half and the same-day, same-type
    numbered halves that add up to it are one journal. Removes what it joins
    from ``loose`` and returns the joined journals."""
    from itertools import combinations

    joined: list[list[dict]] = []
    for group in [g for g in loose if not g[0]["num"]]:
        if group not in loose:
            continue
        first = group[0]
        target = -off(group)
        candidates = [
            other
            for other in loose
            if other is not group
            and other[0]["num"]
            and other[0]["type"] == first["type"]
            and other[0]["date"] == first["date"]
        ]
        if len(candidates) < 2 or len(candidates) > _SPLIT_SEARCH_LIMIT:
            continue
        match = None
        for size in range(2, len(candidates) + 1):
            match = next(
                (
                    combo
                    for combo in combinations(candidates, size)
                    if sum((off(part) for part in combo), Decimal("0")) == target
                ),
                None,
            )
            if match:
                break
        if not match:
            continue
        journal = [ln for part in match for ln in part] + group
        for part in match:
            loose.remove(part)
        loose.remove(group)
        joined.append(journal)
    return joined


def _reference(journal: list[dict], seen: dict) -> str:
    first = journal[0]
    base = f"{first['type']} {first['num']}".strip() + f" · {first['date']:%Y-%m-%d}"
    names = {ln["name"] for ln in journal if ln["name"]}
    if len(names) == 1:
        base += f" · {next(iter(names))}"
    seen[base] = seen.get(base, 0) + 1
    if seen[base] > 1:
        base += f" #{seen[base]}"
    return base[:100]


OTHER_SECTION_SUFFIX = " - Other"


def _parent_of_other_section(db: Session, path: str):
    """The account a "<Parent> - Other" section belongs to. When a parent
    account has sub-accounts, QuickBooks reports the parent's own postings
    in a section it names "<Parent> - Other", though no such account
    exists in its chart (a 16-year export on discussion #349 was refused
    as "accounts not in the chart" for every one of them). Those lines
    belong to the parent itself. The section sits under its parent in the
    report ("Parent:Parent - Other"), or stands by its name alone when the
    export carries one label column."""
    from app.services.iif_import import _find_account

    segments = [part.strip() for part in path.split(":") if part.strip()]
    if not segments or not segments[-1].endswith(OTHER_SECTION_SUFFIX):
        return None
    leaf = segments[-1][: -len(OTHER_SECTION_SUFFIX)].strip()
    if not leaf:
        return None
    parents = segments[:-1]
    if parents and parents[-1].lower() == leaf.lower():
        return _find_account(db, ":".join(parents))
    return _find_account(db, ":".join(parents + [leaf]))


def import_ledger_report(
    db: Session,
    rows: list[list[str]],
    header_at: int,
    cols: dict,
    adjusting_only: bool = False,
    posted_by: str = None,
) -> dict:
    """``adjusting_only`` (#344): the report is an accountant's adjustments
    — General Journal rows only, refused whole when it holds anything
    else, so a whole-ledger upload can never double the books — and what
    posts is marked adjusting (#341), in ``posted_by``'s name."""
    from app.services.iif_import import _find_account

    result = {
        "journals": 0,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
        "adjusting": bool(adjusting_only),
    }
    lines, errors, notes = parse_ledger(rows, header_at, cols)
    result["warnings"].extend(notes)
    if errors:
        result["errors"] = errors
        return result
    if not lines:
        result["errors"].append("The report has no transactions in it. " + _HOW)
        return result
    if adjusting_only:
        others: dict[str, int] = {}
        for ln in lines:
            if ln["type"] not in GENERAL_JOURNAL_TYPES:
                others[ln["type"]] = others.get(ln["type"], 0) + 1
        if others:
            named = ", ".join(
                f"{n} {t} row{'s' if n != 1 else ''}" for t, n in others.items()
            )
            result["errors"].append(
                "The adjusting-entries import takes General Journal rows only, and "
                f"this report also holds {named}. Nothing was posted. In QuickBooks, "
                "filter the report to Transaction Type = Journal (Customize Report > "
                "Filters) and export it again, or untick the box to import the whole "
                "report as ordinary journal entries."
            )
            return result
    accounts = {}
    missing = []
    for path in OrderedDict.fromkeys(ln["path"] for ln in lines):
        acct = _find_account(db, path)
        if acct is None:
            acct = _parent_of_other_section(db, path)
        if acct is None:
            missing.append(path)
        accounts[path] = acct
    if missing:
        result["errors"].append(
            f"{len(missing)} account(s) in the report are not in the chart: "
            + ", ".join(missing[:12])
            + (" …" if len(missing) > 12 else "")
            + ". Import the lists first (in QuickBooks: File > Utilities > Export > "
            "Lists to IIF Files), then this report."
        )
        return result
    journals, errors = group_journals(lines)
    if errors:
        # The whole list, however long: it stopped at twenty with "and 14,070
        # more", and the person asked where the rest were (discussion #349).
        # The page counts them and saves the list as a file.
        result["errors"] = errors
        return result
    have = {
        ref
        for (ref,) in db.query(Transaction.reference).filter(
            Transaction.source_type == SOURCE_TYPE
        )
    }
    seen: dict = {}
    touches_control = 0
    from app.services.accounting import get_ap_account_id, get_ar_account_id

    try:
        control = {get_ar_account_id(db), get_ap_account_id(db)}
    except Exception:
        control = set()
    for journal in journals:
        ref = _reference(journal, seen)
        if ref in have:
            result["duplicates_skipped"] += 1
            continue
        first = journal[0]
        names = [ln["name"] for ln in journal if ln["name"]]
        memos = [ln["memo"] for ln in journal if ln["memo"]]
        description = (
            f"{first['type']} {first['num']}".strip()
            + (f" — {names[0]}" if names else "")
            + (f": {memos[0]}" if memos else "")
        )[:200]
        posted = [
            {
                "account_id": accounts[ln["path"]].id,
                "debit": ln["debit"],
                "credit": ln["credit"],
                "description": (ln["memo"] or ln["name"] or first["type"])[:300],
            }
            for ln in journal
        ]
        if any(p["account_id"] in control for p in posted):
            touches_control += 1
        create_journal_entry(
            db,
            first["date"],
            description,
            posted,
            source_type=SOURCE_TYPE,
            reference=ref,
            is_adjusting=bool(adjusting_only),
            posted_by=posted_by,
        )
        have.add(ref)
        result["journals"] += 1
    if touches_control:
        result["warnings"].append(
            f"{touches_control} of the journals post to Accounts Receivable or "
            "Accounts Payable. The A/R and A/P aging read invoices and bills, so "
            "enter the ones still open as documents after the import."
        )
    db.commit()
    return result
