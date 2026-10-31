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
# that passes imports. One that does not posts nothing on its first upload
# and answers with its problems instead (#663): each transaction that does
# not balance, each account not in the chart, and a notice when the file
# looks cut short or edited. The page lists them for the person to accept
# or leave out and sends the same file again with the answers: what checks
# out imports with what was accepted (an accepted transaction's difference
# on Import Differences, an accepted account made with the type chosen),
# and the rest is left out and counted. A second upload posts nothing twice.
# An accepted transaction is a partial entry. One rule (holds) says that a
# journal of a later file and an entry in the books are one QuickBooks
# transaction: the same Type, Num and Date, and every line of the one, its
# account and amount, among the other's, whatever names either file kept. A
# later file's journals that hold a partial entry and more are listed as
# replacing it, and ticked, the partial entry is voided and they post in its
# place; a cut copy that an entry in the books holds is skipped as a
# duplicate.
#
# Built against a real company's report (a year of a small practice's books:
# about 5,700 rows, every transaction balanced, the section totals matched);
# the fixture in tests/fixtures/qb_desktop/ is invented books in that layout.
# Nothing of the real file is here.
# ============================================================================

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, OrderedDict, defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.accounts import (
    BASE_TYPE_OF,
    DETAIL_TYPES,
    Account,
    AccountType,
    DetailType,
)
from app.models.transactions import Transaction
from app.services.account_label import account_label, label_for
from app.services.accounting import create_journal_entry
from app.services.safe_errors import DataProblem
from app.services.settings_service import use_account_numbers

SOURCE_TYPE = "qb_ledger_import"
# QuickBooks' Type for a journal entry on this report
GENERAL_JOURNAL_TYPES = frozenset({"General Journal", "Journal", "Journal Entry"})
LEDGER_COLUMNS = ("Type", "Date", "Num", "Name", "Memo", "Split", "Debit", "Credit")
_DATE_FORMATS = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")  # ISO: an .xlsx upload
# SlowBooks is US-only, and a date is read as QuickBooks Desktop writes one
# in the United States, month/day/year (#663): a file with any other date is
# refused, its rows listed, and never read another way. One garbled
# 13/01/2024 in a US file turned the whole file day/month/year, and every
# 01/05/2024 in it posted as May 1. These only tell the refusal's sentence
# that the dates look day first.
_DAY_FIRST_FORMATS = ("%d/%m/%Y", "%d/%m/%y")
# An amount as QuickBooks writes one: 1,234.56, digits in groups of three
# before the point. 1.000,00 (a decimal comma) read as 1.00000 before.
_AMOUNT = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d*)(?:\.\d*)?")
_UNREADABLE = (
    "Nothing was imported: {n} could not be read (listed below), and a "
    "guess would post a wrong date or amount."
)
_DAY_FIRST = (
    " The dates look day first ({first}), and a date is read as "
    "month/day/year (01/31/2024), the way QuickBooks Desktop writes it in the "
    "United States. QuickBooks Desktop writes its dates in the order of "
    "Windows' region setting: in Windows, set the regional format to English "
    "(United States) (Settings > Time & language > Language & region > "
    "Regional format, or Win+R, intl.cpl), restart QuickBooks, export the "
    "report again and import that."
)
_LOOK_AT_THE_ROWS = (
    " A date reads month/day/year (01/31/2024) and an amount like 1,234.56. "
    "Look at those rows in a text editor (a spreadsheet can change dates and "
    "amounts as it saves a file), or export the report from QuickBooks "
    "again, and import that."
)
_HOW = (
    "Export it from QuickBooks Desktop as Reports > Accountant & Taxes > "
    "Transaction Detail by Account, one year of dates per file (Excel > Create "
    "New Worksheet > Create a comma separated values (.csv) file)."
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
    """An amount as QuickBooks writes one: 1,234.56, with a $ or not, and a
    negative one as -75.00, (75.00) or, with that report preference,
    75.00-. Anything else raises ValueError: read as it came, 1.000,00 was
    one dollar."""
    text = (raw or "").strip()
    if not text:
        return Decimal("0")
    negative = 0
    if text.startswith("(") and text.endswith(")"):
        negative, text = negative + 1, text[1:-1].strip()
    if text.endswith("-"):
        negative, text = negative + 1, text[:-1].strip()
    text = text.removeprefix("$")
    if text.startswith("-"):
        negative, text = negative + 1, text[1:].strip()
    text = text.removeprefix("$")
    if (
        negative > 1
        or not _AMOUNT.fullmatch(text)
        or not any(c.isdigit() for c in text)
    ):
        raise ValueError(f"unreadable amount {raw!r}")
    value = Decimal(text.replace(",", ""))
    return -value if negative else value


def _date(raw: str, formats=_DATE_FORMATS):
    for fmt in formats:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unreadable date {raw!r}")


def _looks_day_first(raw: str) -> bool:
    """A date that is no month/day/year and reads as day/month/year
    (13/01/2024): what an export made under another region's date format
    holds. It is refused like any other (#663); this only names it."""
    try:
        _date(raw, _DAY_FIRST_FORMATS)
    except ValueError:
        return False
    return True


def _unreadable_words(n: int, day_first: str | None, look: bool) -> str:
    """The sentence above the rows that refuse a file (#663): what to do
    about dates that look day first, and about any other date or amount
    that could not be read."""
    words = _UNREADABLE.format(n=f"{n:,} row{'s' if n != 1 else ''}")
    if day_first:
        words += _DAY_FIRST.format(first=day_first)
    if look:
        words += _LOOK_AT_THE_ROWS
    return words


def parse_ledger(rows: list[list[str]], header_at: int, cols: dict):
    """(lines, errors, notes, cut): each transaction row as {path, type,
    date, num, name, memo, debit, credit, row}, its account the path of the
    headings above it ("Parent:Child"). `errors` refuse the file: rows with
    no account above them, and rows whose date is not month/day/year or
    whose amount cannot be read, every one listed. `cut` says where it
    looks cut short or edited: the sections whose lines do not add up to
    QuickBooks' total, the section it ends in (before that section's Total
    line), the section whose Total line it ends after, with no TOTAL row
    for the report, and a TOTAL row the lines do not add up to."""
    first = cols["Type"]  # the label columns are the ones before Type
    lines: list[dict] = []
    errors: list[str] = []
    notes: list[str] = []
    cut: dict = {
        "sections": [],
        "ends_in": None,
        "no_total": None,
        "grand": None,
    }
    # rows whose date is not month/day/year, or whose amount cannot be
    # read: the file is refused with them, the one refusal left (a guess
    # would post a wrong date or amount), and the sentence above them says
    # what to do: the first date that looks day first, and whether any
    # other date or amount is in the list
    unreadable: list[str] = []
    day_first = None
    look = False
    # the report's TOTAL row: whether it was there, what it says, and what
    # every line read comes to
    grand_seen = False
    grand_total = None
    every = [Decimal("0"), Decimal("0")]
    last_closed = None
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
                # the report's grand total, read against every line below
                grand_seen = True
                try:
                    grand_total = (
                        _money(cell(row, "Debit")),
                        _money(cell(row, "Credit")),
                    )
                except Exception:
                    notes.append(f"Row {n}: the report's TOTAL could not be read")
                continue
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
                            cut["sections"].append(
                                {
                                    "path": path,
                                    "debit": got_d,
                                    "credit": got_c,
                                    "total_debit": debit,
                                    "total_credit": credit,
                                }
                            )
                    last_closed = path
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
            return [], errors, notes, {}
        # the cells as they read, never the exception's own words
        try:
            day = _date(cell(row, "Date"))
        except ValueError:
            raw = cell(row, "Date")
            if _looks_day_first(raw):
                day_first = day_first or raw
            else:
                look = True
            unreadable.append(f"Row {n}: the date ({raw!r}) is not month/day/year")
            continue
        try:
            debit, credit = _money(cell(row, "Debit")), _money(cell(row, "Credit"))
        except Exception:
            look = True
            unreadable.append(
                f"Row {n}: an amount ({cell(row, 'Debit')!r} / "
                f"{cell(row, 'Credit')!r}) could not be read"
            )
            continue
        if debit == 0 and credit == 0:
            continue
        for _, _, sums in stack:
            sums[0] += debit
            sums[1] += credit
        every[0] += debit
        every[1] += credit
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
    if unreadable:
        words = _unreadable_words(len(unreadable), day_first, look)
        return [], [words] + unreadable, notes, {}
    # QuickBooks closes every section with its Total line and the report
    # with its TOTAL row; a file that stops inside a section, or after a
    # section's Total line with no TOTAL row, was cut there (Save as CSV
    # stops at 32,768 rows), however early: before any Total line was read,
    # or right after a top-level one
    if stack:
        cut["ends_in"] = ":".join(name for _, name, _ in stack)
    elif lines and not grand_seen:
        cut["no_total"] = last_closed
    if grand_total is not None and tuple(every) != grand_total:
        # every section adds up, and the report's own total says more: a
        # section was taken out whole, or the file edited
        cut["grand"] = {
            "debit": every[0],
            "credit": every[1],
            "total_debit": grand_total[0],
            "total_credit": grand_total[1],
        }
    if not any(cut.values()):
        cut = {}
    return lines, errors, notes, cut


def _off(group) -> Decimal:
    return sum((ln["debit"] - ln["credit"] for ln in group), Decimal("0"))


def group_journals(lines: list[dict]) -> tuple[list[list[dict]], list[str]]:
    """The lines as balanced journals: one per transaction (type, date,
    number), split by name when that is what tells same-key transactions
    apart. QuickBooks can print a journal entry's number on one of its lines
    and not the other: two halves of the same type and day that balance
    each other are one entry. A transaction that still does not balance is
    named."""
    journals, unbalanced = group_transactions(lines)
    return journals, [_unbalanced_words(group) for group in unbalanced]


def _unbalanced_words(group: list[dict]) -> str:
    """'Check 1002 of 01/31/2024 does not balance: 1,000.00 more in credits,
    on 1 line (row 8)', the side said in words, as the page says it (2.31.2
    gate: the signed figure and "1 lines" read wrong)."""
    first = group[0]
    label = f"{first['type']} {first['num']}".strip()
    off = _off(group)
    rows = [str(ln["row"]) for ln in group[:6]]
    more = " and more" if len(group) > 6 else ""
    lines = f"{len(group)} line" + ("" if len(group) == 1 else "s")
    where = ("row " if len(rows) == 1 else "rows ") + ", ".join(rows) + more
    return (
        f"{label} of {first['date']:%m/%d/%Y} does not balance: {abs(off):,.2f} "
        f"more in {'debits' if off > 0 else 'credits'}, on {lines} ({where})"
    )


def group_transactions(
    lines: list[dict],
) -> tuple[list[list[dict]], list[list[dict]]]:
    """(journals, unbalanced): the balanced journals group_journals makes,
    and the lines of each transaction that still does not balance."""
    by_key: OrderedDict = OrderedDict()
    for ln in lines:
        by_key.setdefault((ln["type"], ln["date"], ln["num"]), []).append(ln)

    journals: list[list[dict]] = []
    loose: list[list[dict]] = []
    for group in by_key.values():
        by_name: OrderedDict = OrderedDict()
        for ln in group:
            by_name.setdefault(ln["name"], []).append(ln)
        if len(by_name) > 1 and all(_off(part) == 0 for part in by_name.values()):
            journals.extend(by_name.values())
        elif _off(group) == 0:
            journals.append(group)
        else:
            loose.append(group)
    unbalanced: list[list[dict]] = []
    journals.extend(_combine_split_numbers(loose, _off))
    while loose:
        group = loose.pop(0)
        first = group[0]
        mate = next(
            (
                other
                for other in loose
                if other[0]["type"] == first["type"]
                and other[0]["date"] == first["date"]
                and _off(other) == -_off(group)
                and (not other[0]["num"] or not first["num"])
            ),
            None,
        )
        if mate is not None:
            loose.remove(mate)
            numbered = group if first["num"] else mate
            journals.append(numbered + (mate if numbered is group else group))
            continue
        unbalanced.append(group)
    journals.sort(key=lambda j: (j[0]["date"], j[0]["row"]))
    return journals, unbalanced


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


def _import_key(journal: list[dict], seen: dict) -> str:
    """The journal's key, what a second upload checks: type, number, date
    and its one name, numbered when a key repeats in the file. It was the
    reference until #663 and kept its form, so a file imported before still
    matches."""
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


def _other_section_target(path: str) -> str | None:
    """The path of the account a "<Parent> - Other" section belongs to, or
    None for any other section."""
    segments = [part.strip() for part in path.split(":") if part.strip()]
    if not segments or not segments[-1].endswith(OTHER_SECTION_SUFFIX):
        return None
    leaf = segments[-1][: -len(OTHER_SECTION_SUFFIX)].strip()
    if not leaf:
        return None
    parents = segments[:-1]
    if parents and parents[-1].lower() == leaf.lower():
        return ":".join(parents)
    return ":".join(parents + [leaf])


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

    target = _other_section_target(path)
    return _find_account(db, target) if target else None


# The account types an account missing from the chart can be made with
# (#663), QuickBooks' own as the chart's form offers them: Accounts
# Receivable and Payable belong to 1100 and 2000 alone.
ACCOUNT_TYPES = tuple(
    d
    for d in DETAIL_TYPES
    if d not in (DetailType.ACCOUNTS_RECEIVABLE, DetailType.ACCOUNTS_PAYABLE)
)
_BANK_KINDS = {DetailType.BANK: "bank", DetailType.CREDIT_CARD: "credit_card"}

# Where an accepted transaction that does not balance in the file puts its
# difference (#663), made on first use. Equity, as 3900 Opening Balance
# Equity holds what bringing books across leaves over: the balance stays on
# the balance sheet until someone clears it, where an expense account would
# close it into Retained Earnings at the year end. 3950 sits beside 3900 and
# is no control account, no seeded one and none another feature makes; a
# chart that numbers an account of its own 3950 (Partner Distributions)
# keeps it, and the import takes the next number free after it.
DIFFERENCES_NUMBER = "3950"
DIFFERENCES_NUMBERS = tuple(str(n) for n in range(3950, 4000))
DIFFERENCES_NAME = "Import Differences"
DIFFERENCE_MEMO = "Difference in the QuickBooks file"
_UNREAD_ANSWERS = "The answers to the file's problems could not be read."


FILE_CHANGED = "The file changed since the list was made; upload it again."
_NO_FILE = (
    "The answers do not carry the file_sha256 the list gave for the file; "
    "upload the file again."
)


def file_sha256(raw: bytes) -> str:
    """The file a problem list was made for (#663): its answers carry this,
    and answers made for another file are refused."""
    return hashlib.sha256(raw).hexdigest()


def check_answers_file(answers: dict | None, sha: str) -> None:
    """Answers are for the file the list was made for, by its sha256: a key
    alone matched a transaction 10,000.00 off in another file."""
    if answers is None:
        return
    if not answers.get("file_sha256"):
        raise DataProblem(_NO_FILE)
    if answers["file_sha256"] != sha:
        raise DataProblem(FILE_CHANGED)


def read_answers(raw) -> dict | None:
    """The person's answers to a file's problems, as the page sends them
    (#663): {"file_sha256": the list's, "unbalanced": [import key, ...],
    "replaces": [a replacement's key, ...], "accounts": {path: account
    type}, "cut_off": true}. None when none were sent (a first upload). A
    problem the answers do not accept is left out."""
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise DataProblem(_UNREAD_ANSWERS)
    keys = data.get("unbalanced") or []
    replaces = data.get("replaces") or []
    chosen = data.get("accounts") or {}
    sha = data.get("file_sha256")
    if (
        not all(
            isinstance(v, list) and all(isinstance(k, str) for k in v)
            for v in (keys, replaces)
        )
        or not isinstance(chosen, dict)
        or not (sha is None or isinstance(sha, str))
    ):
        raise DataProblem(_UNREAD_ANSWERS)
    offered = {d.value: d for d in ACCOUNT_TYPES}
    types = {}
    for path, value in chosen.items():
        if not isinstance(value, str) or value not in offered:
            raise DataProblem(
                f"The type chosen for {path} ({value}) is not one the import can make."
            )
        types[str(path)] = offered[value]
    return {
        "file_sha256": sha,
        "unbalanced": set(keys),
        "replaces": set(replaces),
        "accounts": types,
        "cut_off": data.get("cut_off") is True,
    }


_NO_ANSWERS = {
    "file_sha256": None,
    "unbalanced": set(),
    "replaces": set(),
    "accounts": {},
    "cut_off": False,
}


def _find_differences(db: Session):
    """The import's own Import Differences: an equity account of that name
    (the one it made first, else one the chart brought), or, renamed, the
    system account it made on a number of its own. Another equity account
    numbered 3950 is the chart's, never this one."""
    equity = db.query(Account).filter(Account.account_type == AccountType.EQUITY)
    named = equity.filter(Account.name == DIFFERENCES_NAME)
    return (
        named.filter(Account.is_system.is_(True)).order_by(Account.id).first()
        or named.order_by(Account.id).first()
        or equity.filter(
            Account.is_system.is_(True),
            Account.account_number.in_(DIFFERENCES_NUMBERS),
        )
        .order_by(Account.account_number)
        .first()
    )


def _differences_number(db: Session) -> str | None:
    """3950, or the next number free after it in 3950-3999: a chart that
    uses 3950 itself keeps it. None when all of them are taken."""
    taken = {
        n
        for (n,) in db.query(Account.account_number).filter(
            Account.account_number.in_(DIFFERENCES_NUMBERS)
        )
    }
    return next((n for n in DIFFERENCES_NUMBERS if n not in taken), None)


def _ensure_differences(db: Session) -> Account:
    """Import Differences, made the first time it is needed, as the system
    accounts are (ensure_account), on its own number."""
    found = _find_differences(db)
    if found is not None:
        return found
    acct = Account(
        name=DIFFERENCES_NAME,
        account_number=_differences_number(db),
        account_type=AccountType.EQUITY,
        is_system=True,
        balance=Decimal("0"),
    )
    db.add(acct)
    db.flush()
    return acct


def _differences_label(db: Session) -> str:
    found = _find_differences(db)
    if found is not None:
        return label_for(found, db)
    return account_label(
        _differences_number(db), DIFFERENCES_NAME, use_account_numbers(db), " "
    )


def _new_account(name: str, detail: DetailType, parent):
    """The account the chart's form would be sent for it (#663)."""
    from app.schemas.accounts import AccountCreate

    return AccountCreate(
        name=name[:200],
        account_type=BASE_TYPE_OF[detail],
        parent_id=parent.id if parent is not None else None,
        detail_type=detail,
        bank_kind=_BANK_KINDS.get(detail),
    )


def _sentence(text) -> str:
    text = str(text).strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _account_choices(db: Session, path: str) -> dict:
    """{types, type, why}: the types the account at `path` can be made with,
    by the chart's own rules (POST /api/accounts, check_new_account) against
    the account it would go under; the one to start on, its parent's; and,
    when none fits (an inactive parent, the chart's depth), why. A path
    whose parent the chart lacks takes any type: the parent is made with
    it (#663)."""
    from fastapi import HTTPException

    from app.routes.accounts import check_new_account
    from app.services.account_tree import MAX_DEPTH, chain_depth, depth_message
    from app.services.iif_import import _account_at_path

    segments = [part.strip() for part in path.split(":") if part.strip()]
    parent, new = None, segments
    for i in range(len(segments) - 1, 0, -1):
        found = _account_at_path(db, segments[:i])
        if found is not None:
            parent, new = found, segments[i:]
            break
    if parent is not None:
        depth = chain_depth(db, Account, parent.id) + len(new)
        if depth > MAX_DEPTH:
            return {"types": [], "type": None, "why": depth_message(path, depth)}
    allowed, refused = [], {}
    for d in ACCOUNT_TYPES:
        try:
            check_new_account(db, _new_account(new[0], d, parent))
        except HTTPException as e:
            refused[d.value] = _sentence(e.detail)
            continue
        allowed.append(d.value)
    start = why = None
    if parent is not None:
        own = parent.detail_type
        start = own if own in allowed else None
        if start is None:
            # under 1100 or 2000, whose own type is theirs alone: the first
            # of the parent's base type
            start = next(
                (
                    v
                    for v in allowed
                    if BASE_TYPE_OF[DetailType(v)] == parent.account_type
                ),
                None,
            )
        why = refused.get(own)
    if not allowed:
        return {"types": [], "type": None, "why": why or next(iter(refused.values()))}
    return {"types": allowed, "type": start, "why": None}


def _make_account(db: Session, path: str, detail: DetailType) -> Account:
    """The account at `path`, made with the type the person chose, with any
    parent the chart lacks made the same type (in QuickBooks a sub-account
    shares its parent's), each by the chart's own rules: one they refuse
    is a DataProblem in a sentence, before anything posts (#663)."""
    from fastapi import HTTPException

    from app.routes.accounts import check_new_account
    from app.services.iif_import import _account_at_path

    segments = [part.strip() for part in path.split(":") if part.strip()]
    parent = None
    for depth in range(1, len(segments) + 1):
        acct = _account_at_path(db, segments[:depth])
        if acct is None:
            data = _new_account(segments[depth - 1], detail, parent)
            try:
                check_new_account(db, data)
            except HTTPException as e:
                raise DataProblem(
                    f"{path} cannot be made as {DETAIL_TYPES[detail][0]}: "
                    + _sentence(e.detail)
                ) from None
            acct = Account(
                name=data.name,
                account_type=data.account_type,
                parent_id=data.parent_id,
                # as the rules settled it (a sub-account's is its parent's)
                detail_type=getattr(data.detail_type, "value", data.detail_type),
                bank_kind=data.bank_kind,
                balance=Decimal("0"),
            )
            db.add(acct)
            db.flush()
        parent = acct
    return parent


def _cut_off_problem(cut: dict) -> dict:
    details = [
        f"The lines under {s['path']} come to {s['debit']:,.2f} debit and "
        f"{s['credit']:,.2f} credit, and QuickBooks' total says "
        f"{s['total_debit']:,.2f} and {s['total_credit']:,.2f}."
        for s in cut["sections"]
    ]
    if cut["ends_in"]:
        details.append(
            f"The file ends inside {cut['ends_in']}, before QuickBooks' Total "
            "line for it."
        )
    if cut["no_total"]:
        details.append(
            f"The file ends after QuickBooks' Total line for {cut['no_total']}, "
            "without the report's TOTAL row."
        )
    grand = cut["grand"]
    if grand:
        details.append(
            f"The file's lines come to {grand['debit']:,.2f} debit and "
            f"{grand['credit']:,.2f} credit, and QuickBooks' TOTAL row says "
            f"{grand['total_debit']:,.2f} and {grand['total_credit']:,.2f}."
        )

    def text(values):
        return {k: (f"{v:.2f}" if isinstance(v, Decimal) else v) for k, v in values}

    out = {
        "kind": "cut_off",
        "key": "file",
        "details": details,
        "sections": [text(s.items()) for s in cut["sections"]],
        "text": " ".join(details),
    }
    if grand:
        out["total"] = text(grand.items())
    return out


def _cents(value) -> Decimal:
    return Decimal(value or 0).quantize(Decimal("0.01"))


def type_num_date(kind, num, day) -> tuple:
    """A transaction's QuickBooks Type, Num and Date as an entry stores them
    (source_kind, reference, date): with its lines (holds), what tells that
    a journal of a file and an entry are one QuickBooks transaction,
    whatever names either file kept (#663). A cut or filtered file can lose
    the line that carried the only other name, so the import key, which
    holds the journal's one name, can differ."""
    return ((kind or "")[:50], (num or "")[:100] or None, day)


def holds(outer: dict, inner: dict) -> bool:
    """Whether ``outer`` holds all of ``inner``: the one rule that says a
    journal of a file and an entry in the books (or two of either) are the
    same QuickBooks transaction, or a part of it (#663). The two have the
    same QuickBooks Type, Num and Date (``ttd``), and every line of
    ``inner``, its account and its debit less credit, is among the lines of
    ``outer`` (``amounts``), counted as a multiset: two lines of 50.00 on
    Checking need two there. A partial entry's own line on Import
    Differences is never one of its lines (entry_amounts).

    Type, Num and Date alone took a transaction of another name on the same
    day for the one in the books (a 5.00 interest deposit voided a 100.00
    walk-in's partial entry), and the import key (Type, Num, Date and the
    one name) missed the one a cut had lost the name of."""
    return outer["ttd"] == inner["ttd"] and not inner["amounts"] - outer["amounts"]


def _signed(debit, credit) -> Decimal:
    return _cents(debit) - _cents(credit)


def journal_amounts(journal: list[dict], accounts: dict) -> Counter:
    """A file's journal as holds reads it: each line's account and its debit
    less credit. A line on an account the chart lacks is one no entry in
    the books has."""
    out: Counter = Counter()
    for ln in journal:
        acct = accounts.get(ln["path"])
        where = acct.id if acct is not None else ("not in the chart", ln["path"])
        out[(where, _signed(ln["debit"], ln["credit"]))] += 1
    return out


def entry_amounts(db: Session, entries: dict) -> dict:
    """{id: Counter}: entries the import posted, as holds reads them, each
    line's account and its debit less credit (every line, whatever its
    basis). ``entries`` maps each id to the entry's difference, None for a
    whole entry: a partial entry's line for its difference (the import's
    memo, the difference the other way round, and the last line it wrote,
    so a file's line of that memo stays) is left out, and the entry reads
    as the lines its file had."""
    from app.models.transactions import LEDGER_BASIS_OPTION, READ_ALL, TransactionLine

    out = {i: Counter() for i in entries}
    plugged = set()
    for chunk in _chunks(entries):
        for tid, acct, debit, credit, memo in (
            db.query(
                TransactionLine.transaction_id,
                TransactionLine.account_id,
                TransactionLine.debit,
                TransactionLine.credit,
                TransactionLine.description,
            )
            .filter(TransactionLine.transaction_id.in_(chunk))
            .order_by(TransactionLine.id.desc())
            .execution_options(**{LEDGER_BASIS_OPTION: READ_ALL})
        ):
            signed = _signed(debit, credit)
            difference = entries[tid]
            if (
                difference is not None
                and tid not in plugged
                and memo == DIFFERENCE_MEMO
                and signed == -_cents(difference)
            ):
                plugged.add(tid)
                continue
            out[tid][(acct, signed)] += 1
    return out


_IN_CHUNK = 500  # ids per IN (...), under SQLite's parameter limit


def _chunks(items):
    items = list(items)
    for i in range(0, len(items), _IN_CHUNK):
        yield items[i : i + _IN_CHUNK]


def _entries_here(db: Session, keys: set, ttds: set) -> dict:
    """What the books hold of this import's entries, for a file with these
    import keys and Types, Nums and Dates (#663):

    - ``have``: every whole entry's key, voided or not: a second upload
      posts nothing twice, and a whole entry voided by hand stays voided;
    - ``standing``: those of ``keys`` that a whole entry that stands holds;
    - ``whole`` and ``partial``: the entries that stand with one of
      ``ttds``, whole and partial (posted with a difference on Import
      Differences), by Type, Num and Date, each {id, key, ttd, difference,
      amounts}, oldest first.

    A partial entry's key is never in ``have``. Voided, by hand or by a
    later file that replaced it, it holds nothing in the books, and a file
    that holds its transaction brings it in; standing, what a file holds of
    it is told by holds. A replaced one's key used to stay in ``have`` (its
    reversal said so), so that the old cut file brought nothing back: the
    entry that replaced it holds that file's journal now, and the key had
    hidden the next file's whole transaction of the same name."""
    from app.services.voids import TRANSACTION_KEYED_VOIDS

    rows = (
        db.query(
            Transaction.id,
            Transaction.source_ref,
            Transaction.reference,
            Transaction.date,
            Transaction.source_kind,
            Transaction.source_difference,
        )
        .filter(Transaction.source_type == SOURCE_TYPE)
        .order_by(Transaction.id)
        .all()
    )
    have = set()
    near = []  # the entries this file's journals can be matched to
    for r in rows:
        # its source_ref, else (an entry the 2.31.2 upgrade could not read
        # back) the reference that held it before
        key = r.source_ref or r.reference
        ttd = type_num_date(r.source_kind, r.reference, r.date)
        if r.source_difference is None:
            have.add(key)
        if ttd in ttds or (r.source_difference is None and key in keys):
            near.append((r, key, ttd))
    voided = set()
    for chunk in _chunks(r.id for r, _, _ in near):
        voided.update(
            sid
            for (sid,) in db.query(Transaction.source_id).filter(
                Transaction.source_type.in_(TRANSACTION_KEYED_VOIDS),
                Transaction.source_id.in_(chunk),
            )
        )
    stand = [(r, key, ttd) for r, key, ttd in near if r.id not in voided]
    amounts = entry_amounts(
        db, {r.id: r.source_difference for r, _, ttd in stand if ttd in ttds}
    )
    out = {
        "have": have,
        "standing": set(),
        "whole": defaultdict(list),
        "partial": defaultdict(list),
    }
    for r, key, ttd in stand:
        whole = r.source_difference is None
        if whole and key in keys:
            out["standing"].add(key)
        if ttd in ttds:
            out["whole" if whole else "partial"][ttd].append(
                {
                    "id": r.id,
                    "key": key,
                    "ttd": ttd,
                    "difference": None if whole else _cents(r.source_difference),
                    "amounts": amounts[r.id],
                }
            )
    return out


def _cover(partial: dict, pool: list[dict]) -> list[dict] | None:
    """The journals of a file that replace a partial entry (#663): the first
    that holds it and has more of it, else the first ones in file order
    that hold it together, each with a line of it the ones before lack. A
    cut file can merge the parts of several same-day transactions into one
    partial entry, which a fuller file has as a journal each; those replace
    it even when together they hold no more than it does, or the whole ones
    among them would post a second time. None when one journal of the file
    holds no more of it than the books (the same cut file again) and the
    file's journals do not hold it together (less of it)."""
    one = next(
        (j for j in pool if holds(j, partial) and j["amounts"] != partial["amounts"]),
        None,
    )
    if one is not None:
        return [one]
    left = Counter(partial["amounts"])
    chosen = []
    for j in pool:
        common = j["amounts"] & left
        if common:
            chosen.append(j)
            left -= common
            if not left:
                break
    together = sum((j["amounts"] for j in chosen), Counter())
    if len(chosen) < 2 or not holds(
        {"ttd": partial["ttd"], "amounts": together}, partial
    ):
        return None
    return chosen


def _replacements(txns: list[dict], partials: dict, closing) -> list[dict]:
    """The partial entries in the books that this file replaces (#663), as
    groups: {key, journals, partials}. A partial entry is replaced by the
    journals of the file that hold it (_cover), of its own Type, Num and
    Date: one of another name that day, holding none of its lines, is no
    copy of it and imports on its own. A journal one partial entry needs
    and another does too makes them one group. A journal here already can
    be in one: its lines count, and it posts unless a whole entry that
    stands has it (a key that only a voided entry holds is no reason to
    leave a replacement's part out). Nothing in a closed period is
    replaced: its void would be refused."""
    pools = defaultdict(list)
    for t in txns:
        if t["state"] in (None, "here"):
            pools[t["ttd"]].append(t)
    groups = []
    for ttd, standing in partials.items():
        pool = pools.get(ttd)
        if not pool or (closing is not None and ttd[2] <= closing):
            continue
        at = {id(j): n for n, j in enumerate(pool)}
        # (partial entries, the places of their journals in the pool): one
        # per partial entry, joined with any that shares a journal
        found: list[tuple[list, set]] = []
        for p in standing:
            cover = _cover(p, pool)
            if cover is None:
                continue
            ps, places = [p], {at[id(j)] for j in cover}
            for n in reversed([n for n, f in enumerate(found) if f[1] & places]):
                other, theirs = found.pop(n)
                ps, places = other + ps, places | theirs
            found.append((ps, places))
        found.sort(key=lambda f: min(f[1]))
        for ps, places in found:
            ps = sorted(ps, key=lambda p: p["id"])
            js = [pool[n] for n in sorted(places)]
            first = js[0]["lines"][0]
            key = f"{first['type']} {first['num']}".strip()
            key += f" · {first['date']:%Y-%m-%d}"
            if len(found) > 1:
                # several of one Type, Num and Date, each named by the entry
                # it replaces: an answer is for the entries the list showed,
                # and one replaced since matches nothing
                key += f" · entry {ps[0]['id']}"
            groups.append({"key": key, "journals": js, "partials": ps})
    groups.sort(
        key=lambda g: (
            g["journals"][0]["lines"][0]["date"],
            g["journals"][0]["lines"][0]["row"],
        )
    )
    return groups


def _amount(journal: list[dict]) -> Decimal:
    debit = sum((ln["debit"] for ln in journal), Decimal("0"))
    credit = sum((ln["credit"] for ln in journal), Decimal("0"))
    return max(debit, credit)


def _unbalanced_problem(txn: dict) -> dict:
    journal = txn["lines"]
    first = journal[0]
    names = [ln["name"] for ln in journal if ln["name"]]
    return {
        "kind": "unbalanced",
        "key": txn["key"],
        "date": first["date"].isoformat(),
        "type": first["type"],
        "num": first["num"],
        "name": names[0] if names else "",
        "amount": f"{_amount(journal):.2f}",
        "off": f"{txn['off']:.2f}",
        "rows": [ln["row"] for ln in journal],
        "text": _unbalanced_words(journal),
    }


def _partial_view(db: Session, info: dict) -> dict:
    """A partial entry in the books, as the list shows it beside the file's
    copy: its date, amount and difference."""
    txn = db.get(Transaction, info["id"])
    return {
        "id": txn.id,
        "date": txn.date.isoformat(),
        "num": txn.reference or "",
        "name": txn.description or "",
        "amount": f"{sum((ln.debit for ln in txn.lines), Decimal('0')):.2f}",
        "difference": f"{info['difference']:.2f}",
        "reconciled": any(ln.reconciliation_id for ln in txn.lines),
    }


def _copy_words(journals: list[dict], amount: Decimal, off: Decimal) -> str:
    """What a file has of a partial entry's transaction (#663): the whole
    transaction only when that is what it is. A copy still off has more of
    it, and the transactions a cut merged into one partial entry are as
    many in a fuller file."""
    n = len(journals)
    still = [j for j in journals if j["off"]]
    if n == 1:
        if not still:
            return f"this file has the whole transaction, {amount:,.2f}."
        return f"this file has more of it, {amount:,.2f}, still {abs(off):,.2f} off."
    words = f"this file has it as {n} transactions, {amount:,.2f} in all, "
    if not still:
        return words + "each whole."
    gap = abs(sum((j["off"] for j in still), Decimal("0")))
    k = len(still)
    return words + (
        f"{k} of them still off by {gap:,.2f}."
        if k > 1
        else f"one of them still off by {gap:,.2f}."
    )


def _replaces_problem(db: Session, group: dict) -> dict:
    """The journals of this file that hold a partial entry in the books and
    more of its transaction (_replacements): the file's copy beside the
    entries it would replace."""
    journals = group["journals"]
    first = journals[0]["lines"][0]
    label = f"{first['type']} {first['num']}".strip()
    names = list(
        OrderedDict.fromkeys(
            ln["name"] for j in journals for ln in j["lines"] if ln["name"]
        )
    )
    amount = sum((_amount(j["lines"]) for j in journals), Decimal("0"))
    off = sum((j["off"] for j in journals), Decimal("0"))
    partial = [_partial_view(db, p) for p in group["partials"]]
    voidable = not any(p["reconciled"] for p in partial)
    held = sum((Decimal(p["amount"]) for p in partial), Decimal("0"))
    plug = sum((Decimal(p["difference"]) for p in partial), Decimal("0"))
    entries = (
        f"a partial entry of {held:,.2f}"
        if len(partial) == 1
        else f"{len(partial)} partial entries of {held:,.2f} in all"
    )
    text = (
        f"{label} of {first['date']:%m/%d/%Y} is in the books as {entries}, "
        f"{abs(plug):,.2f} of it on {_differences_label(db)}; "
        + _copy_words(journals, amount, off)
    )
    known = sum(1 for j in journals if j.get("in_books"))
    if known and len(journals) == 1:
        text += (
            " That transaction is in the books already, whole, and is not "
            "posted again."
        )
    elif known == 1:
        text += " One of them is in the books already, whole, and is not posted again."
    elif known:
        text += (
            f" {known} of them are in the books already, whole, and are not "
            "posted again."
        )
    if not voidable:
        text += (
            " The entry in the books is in a completed reconciliation, so it "
            "is not replaced (a finished reconciliation is not reopened); leave "
            "it as it is and clear its difference on 3950 Import Differences "
            "with a journal entry."
        )
    return {
        "kind": "replaces",
        "key": group["key"],
        "date": first["date"].isoformat(),
        "type": first["type"],
        "num": first["num"],
        "name": names[0] if names else "",
        "names": names,
        "amount": f"{amount:.2f}",
        "off": f"{off:.2f}",
        "rows": [ln["row"] for j in journals for ln in j["lines"]],
        "journals": len(journals),
        # how many of the file's journals still do not balance: each posts
        # as a partial entry again
        "still_off": sum(1 for j in journals if j["off"]),
        "partial": partial,
        "voidable": voidable,
        "text": text,
    }


def _dates_words(days: list) -> str:
    """01/05/2024; 01/05/2024 and 01/12/2024; or, past five, the first and
    last of them and how many."""
    words = [f"{d:%m/%d/%Y}" for d in days]
    if len(words) > 5:
        return f"{words[0]} to {words[-1]} ({len(words):,} dates)"
    if len(words) > 1:
        return ", ".join(words[:-1]) + " and " + words[-1]
    return words[0]


def _closed_problem(closed: list[dict], closing) -> dict:
    """The journals dated on or before the closing date, left out (#663): a
    notice, with no box to tick. They were refused whole with a 403 at the
    second call, after the person had gone through the list."""
    days = sorted({t["lines"][0]["date"] for t in closed})
    n = len(closed)
    return {
        "kind": "closed_period",
        "key": "closed_period",
        "closing_date": closing.isoformat(),
        "transactions": n,
        "dates": [d.isoformat() for d in days],
        "text": (
            f"{n:,} transaction{'s' if n != 1 else ''} dated on or before the "
            f"closing date, {closing:%m/%d/%Y}: {_dates_words(days)}."
        ),
    }


def _closed_note(closing) -> str:
    return (
        f"The books are closed through {closing:%m/%d/%Y}, so a transaction "
        "dated on or before it is left out. To bring these in, move the "
        "closing date (Settings > Closing Date) and import the file again: "
        "what is here already is skipped."
    )


def _problems(
    db: Session,
    candidates: list[dict],
    cut: dict,
    missing: dict,
    groups: dict,
    closed: list[dict],
    closing,
) -> list[dict]:
    """What the person answers before anything posts, among the
    transactions not in the books already: a notice when the file looks
    cut short or edited, one for the transactions in a closed period (left
    out), each account not in the chart with how many transactions touch
    it, each transaction the books hold as a partial entry that this file
    has whole, and each transaction that does not balance, every one
    however many (discussion #349)."""
    if not candidates:
        return []
    out = [_cut_off_problem(cut)] if cut else []
    if closed:
        out.append(_closed_problem(closed, closing))
    for target in OrderedDict.fromkeys(missing.values()):
        n = sum(1 for t in candidates if target in t["needs"] and not t.get("in_books"))
        if not n:
            continue
        touching = f"{n} transaction{'s' if n != 1 else ''}"
        out.append(
            {
                "kind": "missing_account",
                "key": target,
                "path": target,
                "transactions": n,
                # the types the chart's rules let it be made with, the one
                # to start on and, when none fits, why
                **_account_choices(db, target),
                "text": f"{target} is not in the chart ({touching})",
            }
        )
    out += [_replaces_problem(db, g) for g in groups]
    out += [
        _unbalanced_problem(t)
        for t in candidates
        if t["state"] == "pending" and not t["balanced"]
    ]
    return out


def _problem_notes(db: Session, problems: list[dict]) -> dict:
    """Each kind's sentences for the page: what is likely wrong, and what a
    tick does."""
    kinds = {p["kind"] for p in problems}
    notes = {}
    closed = next((p for p in problems if p["kind"] == "closed_period"), None)
    if closed is not None:
        from datetime import date

        notes["closed_period"] = _closed_note(
            date.fromisoformat(closed["closing_date"])
        )
    if "cut_off" in kinds:
        notes["cut_off"] = (
            "The file looks cut short or edited. QuickBooks' Save as CSV stops a "
            "report at 32,768 rows without a warning, so export it again for a "
            "shorter date range: one year per file, or a year in two files split "
            "at a month end, imported oldest first. Ticked, the import takes what "
            "is there; unticked, nothing is imported."
        )
    if "missing_account" in kinds:
        notes["missing_account"] = (
            "These accounts are in the report and not in the chart. Import the "
            "lists first (in QuickBooks: File > Utilities > Export > Lists to IIF "
            "Files), then this report; or choose a type and tick an account to "
            "make it now. A transaction that touches an account left unticked is "
            "left out."
        )
    if "replaces" in kinds:
        label = _differences_label(db)
        off = any(p["still_off"] for p in problems if p["kind"] == "replaces")
        notes["replaces"] = (
            "An earlier file had these transactions in part, and each was "
            f"imported with its difference on {label}. "
            + (
                "This file has more of each, not all of it whole: a "
                f"transaction still off posts with its own difference on {label}, "
                "a partial entry again, which a later file that has it whole "
                "replaces in turn. "
                if off
                else "This file has them whole. "
            )
            + "Ticked, the partial entry is voided (its usual reversal, on its "
            "own date) and this file's posts in its place. Unticked, the entry "
            "in the books stays as it is and this file's is left out."
        )
    if "unbalanced" in kinds:
        notes["unbalanced"] = (
            "These transactions do not balance in the file. A report filtered to "
            "some accounts leaves their other side out: set Customize Report > "
            "Filters > Account to All accounts. Ticked, one posts with its "
            f"difference on {_differences_label(db)}, an equity account, for you "
            "or your accountant to clear, and a later file that has it whole is "
            "offered to replace it. Unticked, it is left out, and a later file "
            "that has it brings it in."
        )
    return notes


def import_ledger_report(
    db: Session,
    rows: list[list[str]],
    header_at: int,
    cols: dict,
    adjusting_only: bool = False,
    posted_by: str = None,
    answers: dict | None = None,
    file_sha: str | None = None,
) -> dict:
    """``adjusting_only`` (#344): the report is an accountant's adjustments
    — General Journal rows only, refused whole when it holds anything
    else, so a whole-ledger upload can never double the books — and what
    posts is marked adjusting (#341), in ``posted_by``'s name.

    ``answers`` (#663, read_answers): None on a first upload, which posts
    nothing when the file has problems and answers with ``ready``,
    ``problems``, ``problem_notes`` and ``file_sha256`` (``file_sha``, the
    file's, which the answers carry back). Given, what checks out posts
    with what they accept, and ``accepted`` and ``left_out`` say the rest."""
    from app.services.iif_import import _find_account

    result = {
        "journals": 0,
        "duplicates_skipped": 0,
        "errors": [],
        "warnings": [],
        "adjusting": bool(adjusting_only),
    }
    lines, errors, notes, cut = parse_ledger(rows, header_at, cols)
    result["warnings"].extend(notes)
    if errors:
        result["errors"] = errors
        return result
    if not lines:
        result["errors"] = ["The report has no transactions in it. " + _HOW]
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
    missing = {}  # a section's path: the path of the account it needs
    for path in OrderedDict.fromkeys(ln["path"] for ln in lines):
        acct = _find_account(db, path)
        if acct is None:
            acct = _parent_of_other_section(db, path)
        if acct is None:
            missing[path] = _other_section_target(path) or path
        accounts[path] = acct
    journals, unbalanced = group_transactions(lines)
    # the balanced ones are keyed first, as before #663, so their keys are
    # the ones an earlier upload of them made whatever else this file holds
    seen: dict = {}
    txns = [
        {"lines": j, "key": _import_key(j, seen), "balanced": True} for j in journals
    ]
    txns += [
        {"lines": g, "key": _import_key(g, seen), "balanced": False} for g in unbalanced
    ]
    txns.sort(key=lambda t: (t["lines"][0]["date"], t["lines"][0]["row"]))
    for t in txns:
        first = t["lines"][0]
        t["needs"] = {missing[ln["path"]] for ln in t["lines"] if ln["path"] in missing}
        t["off"] = _off(t["lines"])
        t["ttd"] = type_num_date(first["type"], first["num"], first["date"])
        t["amounts"] = journal_amounts(t["lines"], accounts)
        t["state"] = None
    books = _entries_here(db, {t["key"] for t in txns}, {t["ttd"] for t in txns})
    from app.services.closing_date import get_closing_date

    # a journal on or before the closing date is left out, and the list says
    # so: it was the second call's 403, after the person had answered
    closing = get_closing_date(db)
    # where each transaction stands (#663): here already, in a closed
    # period, replacing a partial entry, or to post
    for t in txns:
        if t["key"] in books["standing"] or (
            not t["balanced"]
            and any(holds(e, t) for e in books["whole"].get(t["ttd"], ()))
        ):
            # a whole entry that stands has it: its key's, or one that holds
            # what is left of it in a cut file (the corrected file came in
            # first, or replaced the partial entry that file made)
            t["state"], t["in_books"] = "here", True
        elif t["key"] in books["have"]:
            # its whole entry was voided, and stays so, unless a partial
            # entry's replacement needs it
            t["state"] = "here"
        elif closing is not None and t["lines"][0]["date"] <= closing:
            t["state"] = "closed"
    groups = _replacements(txns, books["partial"], closing)
    for group in groups:
        for t in group["journals"]:
            t["state"], t["group"] = "replaces", group
    for t in txns:
        if t["state"] is None:
            # a partial entry that stands holds what this file has of it: the
            # same cut file again, or one cut shorter
            inside = not t["balanced"] and any(
                holds(p, t) for p in books["partial"].get(t["ttd"], ())
            )
            t["state"] = "here" if inside else "pending"
    candidates = [t for t in txns if t["state"] in ("pending", "replaces")]
    closed = [t for t in txns if t["state"] == "closed"]
    problems = _problems(db, candidates, cut, missing, groups, closed, closing)
    if cut and not candidates:
        result["warnings"].append(_cut_off_problem(cut)["text"])
    if closed and not candidates:
        notice = _closed_problem(closed, closing)
        result["warnings"].append(f"{notice['text']} {_closed_note(closing)}")
    # skipped whatever the answers: what is here, and the journals of a
    # replacement that a whole entry that stands has already
    here = sum(1 for t in txns if t["state"] == "here" or t.get("in_books"))
    if problems and answers is None:
        # nothing posts before the person has seen them
        result["duplicates_skipped"] = here
        result["ready"] = sum(
            1
            for t in candidates
            if t["state"] == "pending" and t["balanced"] and not t["needs"]
        )
        result["problems"] = problems
        result["problem_notes"] = _problem_notes(db, problems)
        if file_sha:
            result["file_sha256"] = file_sha
        if any(p["kind"] == "missing_account" for p in problems):
            result["account_types"] = [
                {
                    "value": d.value,
                    "label": DETAIL_TYPES[d][0],
                    "account_type": BASE_TYPE_OF[d].value,
                }
                for d in ACCOUNT_TYPES
            ]
        return result
    answers = answers or _NO_ANSWERS
    if cut and problems and not answers["cut_off"]:
        result["duplicates_skipped"] = here
        result["errors"].append(
            "Nothing was imported: the file looks cut short or edited, and "
            "importing what is there was not chosen. Export it again for a "
            "shorter date range and import that."
        )
        return result
    made = {}
    for target in OrderedDict.fromkeys(missing.values()):
        detail = answers["accounts"].get(target)
        if detail is not None and any(target in t["needs"] for t in candidates):
            made[target] = _make_account(db, target, detail)
    for path, target in missing.items():
        if target in made:
            accounts[path] = made[target]
    left_out = {
        "unbalanced": 0,
        "missing_account": 0,
        "replaces": 0,
        "closed_period": 0,
    }
    accepted = 0
    replaced = 0
    posting = {"differences": None, "control": 0}
    from app.services.accounting import get_ap_account_id, get_ar_account_id

    try:
        control = {get_ar_account_id(db), get_ap_account_id(db)}
    except Exception:
        control = set()

    def post(t) -> None:
        """One journal, as a register entry reads (#663): QuickBooks' Name
        is the payee (none on an entry with no name), its Num the reference,
        each line's Memo on the line, its Type the register's Type; one that
        does not balance with its difference on Import Differences."""
        journal = t["lines"]
        first = journal[0]
        names = [ln["name"] for ln in journal if ln["name"]]
        lines_out = [
            {
                "account_id": accounts[ln["path"]].id,
                "debit": ln["debit"],
                "credit": ln["credit"],
                "description": (ln["memo"] or ln["name"] or first["type"])[:300],
            }
            for ln in journal
        ]
        off = t["off"]
        if off:
            if posting["differences"] is None:
                posting["differences"] = _ensure_differences(db)
            lines_out.append(
                {
                    "account_id": posting["differences"].id,
                    "debit": -off if off < 0 else Decimal("0"),
                    "credit": off if off > 0 else Decimal("0"),
                    "description": DIFFERENCE_MEMO,
                }
            )
        if any(ln["account_id"] in control for ln in lines_out):
            posting["control"] += 1
        txn = create_journal_entry(
            db,
            first["date"],
            (names[0] if names else "")[:200],
            lines_out,
            source_type=SOURCE_TYPE,
            reference=first["num"][:100] or None,
            is_adjusting=bool(adjusting_only),
            posted_by=posted_by,
            source_ref=t["key"],
            source_kind=first["type"][:50],
        )
        if off:
            # a partial entry: a later file that has more of it replaces it
            txn.source_difference = off
        result["journals"] += 1

    from app.routes.journal import reverse_journal_entry

    for t in txns:
        state = t["state"]
        if state == "here":
            result["duplicates_skipped"] += 1
            continue
        if state == "closed":
            left_out["closed_period"] += 1
            continue
        if state == "replaces":
            group = t["group"]
            if group.get("done"):
                continue
            group["done"] = True
            # what a whole entry that stands has already is never posted
            # again, whatever the answer; the rest posts with the void
            here_too = [j for j in group["journals"] if j.get("in_books")]
            journals = [j for j in group["journals"] if not j.get("in_books")]
            result["duplicates_skipped"] += len(here_too)
            if group["key"] not in answers["replaces"]:
                left_out["replaces"] += len(journals)
                continue
            standing = [db.get(Transaction, p["id"]) for p in group["partials"]]
            if any(ln.reconciliation_id for e in standing for ln in e.lines):
                left_out["replaces"] += len(journals)
                result["warnings"].append(
                    f"{group['key']}: the partial entry in the books is in a "
                    "completed reconciliation, so it was not replaced. Undo that "
                    "reconciliation and import the file again."
                )
                continue
            if any(j["needs"] - made.keys() for j in journals):
                # all of it or nothing: the partial entry covers every one
                left_out["missing_account"] += len(journals)
                continue
            for entry in standing:
                reverse_journal_entry(db, entry, acting=posted_by)
                replaced += 1
            for j in journals:
                post(j)
                if j["off"]:
                    accepted += 1
            continue
        # counted once, under the first reason it is left out
        if t["needs"] - made.keys():
            left_out["missing_account"] += 1
            continue
        if not t["balanced"] and t["key"] not in answers["unbalanced"]:
            left_out["unbalanced"] += 1
            continue
        post(t)
        if not t["balanced"]:
            accepted += 1
    if posting["control"]:
        result["warnings"].append(
            f"{posting['control']} of the journals post to Accounts Receivable or "
            "Accounts Payable. The A/R and A/P aging read invoices and bills, so "
            "enter the ones still open as documents after the import."
        )
    if problems:
        result["accepted"] = {
            "unbalanced": accepted,
            "accounts": list(made),
            "cut_off": bool(cut),
            "replaced": replaced,
        }
        if posting["differences"] is not None:
            result["differences_account"] = label_for(posting["differences"], db)
    if problems or closed:
        result["left_out"] = left_out
    db.commit()
    return result
