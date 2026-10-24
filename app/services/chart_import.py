"""Import a chart of accounts from a file the operator already has.

Issue #139 / #161 (tresero, coming from hledger): a company that arrives with
a chart of its own should not have to retype it over ours. Two shapes are
accepted, both measured against real files rather than guessed at:

1. **A CSV with a header** — the columns SlowBooks' own export writes
   (Number, Name, Type, ...) or any spreadsheet with those words in its header
   row, plus optional Parent and Description. hledger's `balance -O csv`
   ("account","balance") is a CSV too; its `total` row is ignored and the
   account column is read as a path.
2. **hledger's account list** — `hledger accounts` (one colon-separated path
   per line), or `hledger accounts --types` (the same with a `; type: X`
   tag). Type comes from the tag when present, else from the top segment
   (assets / liabilities / equity / revenues / expenses); the top segment
   itself is the category, not an account; intermediate segments that the
   file never lists on their own are created so the hierarchy survives.

Nothing is written on a dry run: `import_chart(..., dry_run=True)` returns
the plan — what would be created, renamed, updated, skipped or deactivated,
row by row, with a reason — and the same call with `dry_run=False` applies
exactly that plan. The sixteen control accounts the posting code finds by
number are never removed and never renumbered; when the file names one of
them (an account called Receivable, Payable, Checking, Sales tax payable,
...) the control account is **renamed to the file's name** so the operator's
chart replaces ours without a document ever failing to find its account.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.accounts import (
    BASE_TYPE_OF,
    DETAIL_TYPES,
    Account,
    AccountType,
    DetailType,
    default_detail_type,
    detail_label,
    detail_type_problem,
)
from app.models.transactions import TransactionLine
from app.services import control_accounts
from app.services.account_tree import (
    MAX_DEPTH,
    depth_message,
    inherited_detail_type,
    loop_message,
    parent_link_problem,
    takes_no_new_parent,
)
from app.services.csv_export import strip_formula_guard

# --- vocabulary -------------------------------------------------------------

# Words a Type column may carry, from ours, QuickBooks', Wave's and hledger's
# charts, mapped to (account_type, bank_kind).
TYPE_WORDS: dict[str, tuple[str, str | None]] = {
    "asset": ("asset", None),
    "assets": ("asset", None),
    "bank": ("asset", "bank"),
    "cash": ("asset", "bank"),
    "current asset": ("asset", None),
    "other current asset": ("asset", None),
    "fixed asset": ("asset", None),
    "other asset": ("asset", None),
    "accounts receivable": ("asset", None),
    "liability": ("liability", None),
    "liabilities": ("liability", None),
    "credit card": ("liability", "credit_card"),
    "current liability": ("liability", None),
    "other current liability": ("liability", None),
    "long term liability": ("liability", None),
    "long-term liability": ("liability", None),
    "accounts payable": ("liability", None),
    "equity": ("equity", None),
    "income": ("income", None),
    "revenue": ("income", None),
    "revenues": ("income", None),
    "sales": ("income", None),
    "other income": ("income", None),
    "cogs": ("cogs", None),
    "cost of goods sold": ("cogs", None),
    "cost of sales": ("cogs", None),
    "expense": ("expense", None),
    "expenses": ("expense", None),
    "other expense": ("expense", None),
}

# Type words that also name a detail type (#390). A word not here (asset,
# expense, ...) leaves the account the default for its type.
DETAIL_WORDS: dict[str, str] = {
    "bank": "bank",
    "cash": "bank",
    "accounts receivable": "accounts_receivable",
    "current asset": "other_current_asset",
    "other current asset": "other_current_asset",
    "fixed asset": "fixed_asset",
    "other asset": "other_asset",
    "credit card": "credit_card",
    "accounts payable": "accounts_payable",
    "current liability": "other_current_liability",
    "other current liability": "other_current_liability",
    "long term liability": "long_term_liability",
    "long-term liability": "long_term_liability",
    "other income": "other_income",
    "other expense": "other_expense",
}


_DETAIL_BANK_KIND = {"bank": "bank", "credit_card": "credit_card"}


def _norm_detail(word: str | None) -> str | None:
    """A Detail type cell — the label the export writes ("Long Term
    Liability") or the API value ("long_term_liability") — or None."""
    if not word:
        return None
    w = word.strip().lower().replace("_", " ")
    for d, (label, _base) in DETAIL_TYPES.items():
        if w in (label.lower(), d.value.replace("_", " ")):
            return d.value
    return DETAIL_WORDS.get(w)


# hledger's one-letter account types: Asset, Liability, Equity, Revenue,
# eXpense, Cash (an asset you can spend from), conVersion (equity).
HLEDGER_TYPES: dict[str, tuple[str, str | None]] = {
    "A": ("asset", None),
    "L": ("liability", None),
    "E": ("equity", None),
    "R": ("income", None),
    "X": ("expense", None),
    "C": ("asset", "bank"),
    "V": ("equity", None),
}

# The top segment of an hledger path is the category, not an account.
ROOT_WORDS: dict[str, str] = {
    "asset": "asset",
    "assets": "asset",
    "liability": "liability",
    "liabilities": "liability",
    "equity": "equity",
    "revenue": "income",
    "revenues": "income",
    "income": "income",
    "expense": "expense",
    "expenses": "expense",
    "cogs": "cogs",
    "cost of goods sold": "cogs",
}

# Numbers handed to rows that arrive without one, by type.
NUMBER_RANGES: dict[str, tuple[int, int]] = {
    "asset": (1000, 1999),
    "liability": (2000, 2999),
    "equity": (3000, 3999),
    "income": (4000, 4999),
    "cogs": (5000, 5999),
    "expense": (6000, 9999),
}

# A file naming one of these is naming a control account: the posting code
# finds it by number, so it is renamed to the file's name rather than
# duplicated beside it. Keyed by the account's own name, lower-cased.
CONTROL_ALIASES: dict[str, str] = {
    "checking": "1000",
    "receivable": "1100",
    "accounts receivable": "1100",
    "undeposited funds": "1200",
    "inventory": "1300",
    "payable": "2000",
    "accounts payable": "2000",
    "credit card": "2100",
    "sales tax payable": "2200",
    "sales tax": "2200",
    "retained earnings": "3200",
    "cost of goods sold": "5000",
    "cogs": "5000",
}

_TYPE_TAG_RE = re.compile(r"^(?P<path>.*?)\s*;\s*type:\s*(?P<tag>[A-Za-z])\s*$")
_HEADER_ALIASES = {
    "account number": "number",
    "acct number": "number",
    "acct #": "number",
    "no": "number",
    "no.": "number",
    "#": "number",
    "number": "number",
    "code": "number",
    "account name": "name",
    "name": "name",
    "account": "name",
    "account type": "type",
    "type": "type",
    "detail type": "detail_type",
    "parent": "parent",
    "parent account": "parent",
    "sub-account of": "parent",
    "description": "description",
    "memo": "description",
    "active": "active",
    "balance": "balance",
}


@dataclass
class Row:
    line: int
    name: str
    type: str | None = None
    number: str | None = None
    parent_ref: str | None = None  # a path (hledger) or a number/name (CSV)
    description: str | None = None
    bank_kind: str | None = None
    detail_type: str | None = None  # #390; None takes the type's default
    path: str | None = None  # hledger only: the full colon path
    active: bool | None = None
    synthesized: bool = False  # an hledger parent the file never listed


@dataclass
class Plan:
    format: str
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    replace: bool = False

    def count(self, action: str) -> int:
        return sum(1 for r in self.rows if r["action"] == action)


# --- parsing ----------------------------------------------------------------


def _norm_type(word: str | None) -> tuple[str, str | None] | None:
    if not word:
        return None
    return TYPE_WORDS.get(word.strip().lower().replace("_", " "))


def _type_from_number(number: str | None) -> str | None:
    if not number or not number[0].isdigit():
        return None
    return {
        "1": "asset",
        "2": "liability",
        "3": "equity",
        "4": "income",
        "5": "cogs",
    }.get(number[0], "expense")


def _titled(segment: str) -> str:
    seg = segment.strip()
    return seg[:1].upper() + seg[1:] if seg else seg


def _bank_kind_for_path(acct_type: str, segments: list[str]) -> str | None:
    low = [s.lower() for s in segments]
    if acct_type == "asset" and any(
        s in ("bank", "checking", "savings", "cash", "petty cash") for s in low
    ):
        return "bank"
    if acct_type == "liability" and any(
        "credit card" in s or s in ("visa", "mastercard", "amex", "discover")
        for s in low
    ):
        return "credit_card"
    return None


def _path_row(line: int, path: str, tag: str | None, errors: list[str]) -> Row | None:
    """One hledger account path -> a Row, or None for a root/category line."""
    segments = [s.strip() for s in path.split(":") if s.strip()]
    if not segments:
        return None
    root_type = ROOT_WORDS.get(segments[0].lower())
    if len(segments) == 1:
        if root_type:
            return None  # "assets" on its own is the category
        # a flat, single-word account with no root: take the tag or refuse
    tagged = HLEDGER_TYPES.get((tag or "").upper()) if tag else None
    if tagged:
        acct_type, bank_kind = tagged
    elif root_type:
        acct_type, bank_kind = root_type, None
    else:
        errors.append(
            f"Line {line}: '{path}' — cannot tell its type; the top segment is not "
            f"assets / liabilities / equity / revenues / expenses and there is no "
            f"'; type:' tag"
        )
        return None
    bank_kind = bank_kind or _bank_kind_for_path(acct_type, segments)
    parent_ref = ":".join(segments[:-1]) if len(segments) > 1 else None
    if parent_ref and parent_ref.lower() in ROOT_WORDS:
        parent_ref = None
    return Row(
        line=line,
        name=_titled(segments[-1]),
        type=acct_type,
        parent_ref=parent_ref,
        description=path,
        bank_kind=bank_kind,
        path=path,
    )


def _add_missing_parents(rows: list[Row], errors: list[str]) -> list[Row]:
    """hledger lists only declared or used accounts, so `assets:cash:petty
    cash` can arrive with no `assets:cash` line. Synthesize the intermediate
    so the child has something to hang from, in front of its first child."""
    known = {r.path for r in rows if r.path}
    out: list[Row] = []
    for r in rows:
        ref = r.parent_ref
        chain: list[str] = []
        while ref and ref not in known:
            chain.append(ref)
            ref = ref.rsplit(":", 1)[0] if ":" in ref else None
            if ref and ref.lower() in ROOT_WORDS:
                ref = None
        for path in reversed(chain):
            parent = _path_row(r.line, path, None, errors)
            if parent:
                parent.synthesized = True
                known.add(path)
                out.append(parent)
        out.append(r)
    return out


def _looks_like_csv_header(first_line: str) -> bool:
    cells = [c.strip().strip('"').lower() for c in first_line.split(",")]
    return len(cells) >= 2 and any(c in _HEADER_ALIASES for c in cells)


def parse_chart(text: str) -> tuple[list[Row], list[str], str]:
    """-> (rows, errors, format) where format is 'csv' or 'hledger'."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    first = next(
        (ln for ln in lines if ln.strip() and not ln.lstrip().startswith((";", "#"))),
        "",
    )
    errors: list[str] = []
    if _looks_like_csv_header(first):
        return _parse_csv(text, errors)
    rows: list[Row] = []
    for n, raw in enumerate(lines, start=1):
        s = raw.strip()
        if not s or s.startswith((";", "#")):
            continue
        if s.lower().startswith("account "):
            s = s[len("account ") :].strip()
        m = _TYPE_TAG_RE.match(s)
        path, tag = (m.group("path"), m.group("tag")) if m else (s, None)
        row = _path_row(n, path.strip(), tag, errors)
        if row:
            rows.append(row)
    return _add_missing_parents(rows, errors), errors, "hledger"


def _parse_csv(text: str, errors: list[str]) -> tuple[list[Row], list[str], str]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None) or []
    cols = [_HEADER_ALIASES.get(h.strip().strip('"').lower(), None) for h in header]
    if "name" not in cols:
        errors.append(
            "Header row has no account name column (Name, Account, or Account Name)"
        )
        return [], errors, "csv"
    idx = {c: i for i, c in enumerate(cols) if c}
    hledger_balance = set(idx) <= {"name", "balance"} and "balance" in idx
    rows: list[Row] = []
    for n, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue

        def cell(key: str) -> str:
            # strip_formula_guard: our own chart export writes "'=Name" for
            # a formula-shaped name; the name is "=Name" (W-M15)
            i = idx.get(key)
            if i is None or i >= len(cells):
                return ""
            return strip_formula_guard(cells[i]).strip()

        name = cell("name")
        if not name:
            errors.append(f"Row {n}: missing account name")
            continue
        if hledger_balance:
            if name.lower() == "total":
                continue
            row = _path_row(n, name, None, errors)
            if row:
                rows.append(row)
            continue
        number = cell("number") or None
        typed = _norm_type(cell("type"))
        detail = _norm_detail(cell("detail_type"))
        if cell("detail_type") and detail is None:
            errors.append(
                f"Row {n}: unknown detail type '{cell('detail_type')}' for '{name}'"
            )
            continue
        if typed:
            acct_type, bank_kind = typed
            detail = detail or DETAIL_WORDS.get(cell("type").lower().replace("_", " "))
            base = BASE_TYPE_OF[DetailType(detail)] if detail else None
            if base is not None and base.value != acct_type:
                errors.append(
                    f"Row {n}: '{name}' is typed {acct_type} and its detail type "
                    f"'{cell('detail_type')}' is a {base.value} type"
                )
                continue
        elif cell("type"):
            # an unknown Type is an error whatever the Detail type says
            errors.append(
                f"Row {n}: unknown account type '{cell('type')}' for '{name}' "
                f"(use asset, liability, equity, income, cogs or expense)"
            )
            continue
        elif detail:
            acct_type, bank_kind = BASE_TYPE_OF[DetailType(detail)].value, None
        else:
            acct_type, bank_kind = _type_from_number(number), None
            if not acct_type:
                errors.append(
                    f"Row {n}: '{name}' has no type and no number to infer it from"
                )
                continue
        # a Bank / Credit Card detail type is a register, as the type words
        # "bank" and "credit card" are
        bank_kind = bank_kind or _DETAIL_BANK_KIND.get(detail)
        active_cell = cell("active").lower()
        active = None
        if active_cell in ("false", "no", "0", "inactive"):
            active = False
        elif active_cell in ("true", "yes", "1", "active"):
            active = True
        rows.append(
            Row(
                line=n,
                name=name[:200],
                type=acct_type,
                number=number[:20] if number else None,
                parent_ref=cell("parent") or None,
                description=(cell("description") or None),
                bank_kind=bank_kind,
                detail_type=detail,
                active=active,
            )
        )
    if hledger_balance:
        rows = _add_missing_parents(rows, errors)
        return rows, errors, "hledger"
    return rows, errors, "csv"


# --- planning ---------------------------------------------------------------


class _Numbers:
    def __init__(self, used: set[str]):
        self.used = set(used)

    def claim(self, number: str | None) -> bool:
        if not number or number in self.used:
            return False
        self.used.add(number)
        return True

    def next_free(self, acct_type: str) -> str:
        lo, hi = NUMBER_RANGES[acct_type]
        for step in (10, 1):
            n = lo
            while n <= hi:
                s = str(n)
                # never hand a numberless row a control account's number:
                # the reserved-number guard (#425) would refuse it, and on an
                # empty chart nothing else holds the slot
                if s not in self.used and not control_accounts.is_control_number(s):
                    self.used.add(s)
                    return s
                n += step
        # the range is full: fall back to the next integer above everything
        n = max((int(x) for x in self.used if x.isdigit()), default=9999) + 1
        self.used.add(str(n))
        return str(n)


def _posted(db: Session, account_id: int) -> int:
    return (
        db.query(func.count(TransactionLine.id))
        .filter(TransactionLine.account_id == account_id)
        .scalar()
        or 0
    )


def plan_chart_import(db: Session, text: str, replace: bool = False) -> Plan:
    rows, errors, fmt = parse_chart(text)
    plan = Plan(format=fmt, errors=list(errors), replace=replace)
    if not rows and not errors:
        plan.errors.append("No accounts found in the file")
        return plan

    existing = db.query(Account).order_by(Account.id).all()
    by_number = {a.account_number: a for a in existing if a.account_number}
    by_name: dict[str, Account] = {}
    for a in existing:
        by_name.setdefault(a.name.strip().lower(), a)
    numbers = _Numbers(set(by_number))
    matched: set[int] = set()
    # what each row resolves to, for parent links: key = path (hledger) or
    # number or name (csv) -> ("existing", id) | ("new", row index)
    resolved: dict[str, tuple[str, int | str]] = {}
    credit_card_claimed = False
    # by_name keeps the FIRST account of a name, so the seeded control account
    # wins over any twin an earlier run left behind: a re-import matches the
    # control account, and the twin is disclosed on the create it would cause.

    for i, r in enumerate(rows):
        entry = {
            "row": r.line,
            "number": r.number,
            "name": r.name,
            "type": r.type,
            "action": "create",
            "note": "",
        }
        target: Account | None = None
        # 1. the file gives a number we already have
        if r.number and r.number in by_number:
            target = by_number[r.number]
            if target.id in matched:
                entry["action"] = "error"
                entry["note"] = f"number {r.number} appears twice in the file"
                entry["_row"] = r
                plan.rows.append(entry)
                continue
        # 2. the file names one of our control accounts. A parent segment
        #    counts: `assets:inventory` IS 1300 Inventory, and the tree under
        #    it hangs from the account the ledger posts to — the 2.15.0 gate
        #    found that parent being created as an active twin of 1300, once
        #    per import (skytech). The first row naming a card takes 2100; with
        #    a `liabilities:credit card` folder that is the folder, and the
        #    cards inside it are its children.
        if target is None:
            alias = CONTROL_ALIASES.get(r.name.strip().lower())
            if alias is None and r.type == "liability" and r.bank_kind == "credit_card":
                alias = "2100"
            if alias and alias in by_number and int(by_number[alias].id) not in matched:
                if alias != "2100" or not credit_card_claimed:
                    target = by_number[alias]
                    if alias == "2100":
                        credit_card_claimed = True
        # 3. an account with exactly this name
        if target is None:
            same = by_name.get(r.name.strip().lower())
            if same is not None and same.id not in matched:
                if (
                    not r.number
                    or not same.account_number
                    or same.account_number == r.number
                ):
                    target = same

        if target is not None:
            matched.add(target.id)
            changes: list[str] = []
            entry["number"] = target.account_number
            entry["existing_id"] = target.id
            if target.name != r.name:
                changes.append(f"rename '{target.name}' → '{r.name}'")
            if r.type and r.type != target.account_type.value:
                if control_accounts.is_control(
                    target.account_number, target.account_type
                ):
                    entry["note"] = (
                        f"type stays {target.account_type.value}: control account "
                        f"(the software posts to it by number)"
                    )
                elif _posted(db, target.id):
                    entry["note"] = (
                        f"type stays {target.account_type.value}: the account has history"
                    )
                else:
                    changes.append(f"type {target.account_type.value} → {r.type}")
            if r.number and not target.account_number and numbers.claim(r.number):
                changes.append(f"number {r.number}")
                entry["number"] = r.number
            if r.description and (target.description or "") != r.description:
                changes.append("description")
            if r.bank_kind and not target.bank_kind:
                changes.append(f"marked as {r.bank_kind.replace('_', ' ')}")
            if r.active is False and target.is_active:
                changes.append("deactivate")
            if r.active is True and not target.is_active:
                changes.append("reactivate")
            entry["action"] = "update" if changes else "skip"
            entry["changes"] = changes
            if not changes and not entry["note"]:
                entry["note"] = "already in the chart"
            resolved[r.path or target.account_number or r.name.lower()] = (
                "existing",
                target.id,
            )
            if r.number:
                resolved[r.number] = ("existing", target.id)
            resolved[r.name.lower()] = ("existing", target.id)
        else:
            # A new row may not land on a control-account number under a
            # foreign name or type — it would silently become where that
            # posting path resolves (a `5900,Marketing,expense` row becomes
            # inventory adjustments). The same guard POST /api/accounts
            # applies; a row that IS the control account (its registry name
            # and type) is allowed through, restoring an empty slot.
            reserved = control_accounts.restore_conflict(r.number, r.name, r.type)
            if reserved:
                entry["action"] = "error"
                entry["note"] = reserved
                entry["_row"] = r
                plan.rows.append(entry)
                continue
            if r.number and numbers.claim(r.number):
                number = r.number
            else:
                number = numbers.next_free(r.type)
                if r.number:
                    entry["note"] = f"number {r.number} is taken; assigned {number}"
            entry["number"] = number
            entry["note"] = entry["note"] or (
                "parent of a listed account" if r.synthesized else ""
            )
            twin = by_name.get(r.name.strip().lower())
            if twin is not None:
                entry["note"] = (entry["note"] + "; " if entry["note"] else "") + (
                    f"an account named '{twin.name}' already exists as "
                    f"{twin.account_number or 'unnumbered'} and is matched by another row"
                )
            resolved[r.path or number] = ("new", i)
            resolved[number] = ("new", i)
            resolved[r.name.lower()] = ("new", i)
        entry["detail_type"] = _planned_detail(r, target, entry)
        entry["_row"] = r
        plan.rows.append(entry)

    # parent links, now that every row has a home
    for entry in plan.rows:
        r: Row = entry["_row"]
        if not r.parent_ref:
            continue
        key = r.parent_ref if r.path else r.parent_ref.strip().lower()
        hit = resolved.get(r.parent_ref) or resolved.get(key)
        if hit is None and not r.path:
            # a CSV parent may be a number or a name of an existing account
            a = by_number.get(r.parent_ref) or by_name.get(key)
            hit = ("existing", a.id) if a else None
        if hit is None:
            entry["note"] = (entry["note"] + "; " if entry["note"] else "") + (
                f"parent '{r.parent_ref}' not found — left at top level"
            )
        else:
            entry["_parent"] = hit

    _flag_parent_types(db, plan)
    _flag_parent_loops(db, plan)
    _flag_moves(db, plan)
    _flag_inherited_detail(db, plan)
    _describe_rows(db, plan)

    if replace:
        label = _parent_labeler(db, plan)
        for a in existing:
            if a.id in matched or not a.is_active:
                continue
            if control_accounts.is_control(a.account_number, a.account_type):
                plan.rows.append(
                    {
                        "row": None,
                        "number": a.account_number,
                        "name": a.name,
                        "type": a.account_type.value,
                        "action": "keep",
                        "note": "control account not named in the file — kept, "
                        "the software posts to it by number",
                        "existing_id": a.id,
                        **_as_it_stands(a, label),
                    }
                )
                continue
            n = _posted(db, a.id)
            if n:
                plan.rows.append(
                    {
                        "row": None,
                        "number": a.account_number,
                        "name": a.name,
                        "type": a.account_type.value,
                        "action": "keep",
                        "note": f"not in the file but carries {n} posted line(s) — kept",
                        "existing_id": a.id,
                        **_as_it_stands(a, label),
                    }
                )
                continue
            plan.rows.append(
                {
                    "row": None,
                    "number": a.account_number,
                    "name": a.name,
                    "type": a.account_type.value,
                    "action": "deactivate",
                    "note": "not in the file, never used — hidden from new entries "
                    "(Reactivate on the chart page brings it back)",
                    "existing_id": a.id,
                    **_as_it_stands(a, label),
                }
            )
    _note_balance_sheet_group(db, plan)
    return plan


def _as_it_stands(a: Account, label) -> dict:
    """A kept or deactivated account the file does not name: the detail type
    and parent it already has, which the import leaves alone (#461). The
    parent is labelled as _describe_rows labels one, so a parent the file
    renames shows its new name."""
    number, name = label("existing", a.parent_id) if a.parent_id else (None, None)
    return {
        "detail_type": a.detail_type,
        "parent_number": number,
        "parent_name": name,
    }


def _parent_labeler(db: Session, plan: Plan):
    """(number, name) of a parent node as the import leaves it: a new row's,
    an existing account the plan updates (or skips) by the file's name, else
    the chart's own."""
    by_existing = {
        e["existing_id"]: e for e in plan.rows if "existing_id" in e and "_row" in e
    }

    def label(kind, ref):
        if kind == "new":
            pe = plan.rows[ref]
            return pe.get("number"), pe["name"]
        pe = by_existing.get(ref)
        acct = db.get(Account, ref)
        if acct is None:
            return None, None
        if pe is not None and pe["action"] in ("update", "skip"):
            return pe.get("number"), pe["name"]
        return acct.account_number, acct.name

    return label


def _describe_rows(db: Session, plan: Plan) -> None:
    """Put on each row the parent it will sit under (#461), by number and
    name as the import leaves them: the link the plan sets, else, for an
    account already in the chart, the parent it has now (the apply never
    unlinks one). A parent the plan renames shows the file's name. Runs
    after every link check, so a link left off shows no parent."""
    label = _parent_labeler(db, plan)
    for entry in plan.rows:
        number = name = None
        if entry["action"] != "error":
            hit = entry.get("_parent")
            if hit is None and "existing_id" in entry:
                acct = db.get(Account, entry["existing_id"])
                if acct is not None and acct.parent_id:
                    hit = ("existing", acct.parent_id)
            if hit is not None:
                number, name = label(*hit)
        entry["parent_number"] = number
        entry["parent_name"] = name


def _note_detail_fallback(entry: dict, declared: str, actual: str) -> None:
    """One wording, used by the dry run and the apply, so the preview and
    the import read the same when a declared detail type cannot stand."""
    note = (
        f"detail type {detail_label(declared)} in the file; came in "
        f"as {detail_label(actual)}"
    )
    if note not in (entry.get("note") or ""):
        entry["note"] = (entry["note"] + "; " if entry["note"] else "") + note


def _planned_detail(r: Row, target: Account | None, entry: dict) -> str:
    """The detail type the row will have (#390): the chart's own for an
    account already there (an import never changes it, unless its type
    changes), else the file's, else the default. A sub-account given none
    takes its parent's when applied. A file detail type the account cannot
    carry (Accounts Receivable away from 1100, Bank without a bank kind)
    falls back to the default here exactly as the apply path does, so the
    preview shows what the import will actually write, with the same note.

    The apply writes exactly this value on an updated account (#461 review),
    so a retyped account given none takes the number and name rule here and
    there alike, not the plain default the model's update hook would pick. An
    account the file marks as a bank or card takes that kind's detail type
    (Bank, Credit Card) when its own no longer fits."""
    retyped = any(c.startswith("type ") for c in entry.get("changes", []))
    kind = (target.bank_kind if target is not None else None) or r.bank_kind
    number = entry.get("number")
    if target is not None and not retyped:
        keep = target.detail_type
        if detail_type_problem(keep, target.account_type, kind, number) is not None:
            # marked as a bank or card: the old one cannot stand
            keep = default_detail_type(target.account_type, kind, number).value
        if r.detail_type and r.detail_type != keep:
            if keep == target.detail_type:
                entry["note"] = (entry["note"] + "; " if entry["note"] else "") + (
                    f"detail type stays {detail_label(keep)} (the "
                    f"chart's), not {detail_label(r.detail_type)}"
                )
            else:
                _note_detail_fallback(entry, r.detail_type, keep)
        if keep != target.detail_type:
            entry["changes"].append(f"detail type → {detail_label(keep)}")
        return keep
    if r.detail_type:
        problem = detail_type_problem(r.detail_type, r.type, kind, number)
        if problem is None:
            return r.detail_type
        fell_back = default_detail_type(r.type, kind, number, name=r.name).value
        _note_detail_fallback(entry, r.detail_type, fell_back)
        return fell_back
    # none in the file: the default a new account gets, by 2.27.0's number
    # and name rule (default_detail_type), as the model gives it on apply
    return default_detail_type(r.type, kind, number, name=r.name).value


def _flag_parent_types(db: Session, plan: Plan) -> None:
    """Leave off a parent link the account API would refuse (#416): a
    sub-account of another base type (an expense under Checking or under an
    income account), one that cannot carry a plain parent's detail type (a
    bank account under an expense folder), and a new parent for a control
    account or an Accounts Receivable / Payable one. The row says why, in
    the dry run as when applied, as a loop does."""
    by_existing = {
        e["existing_id"]: e for e in plan.rows if "existing_id" in e and "_row" in e
    }

    def planned(entry: dict | None, acct: Account | None):
        """(name, base type, detail type, bank kind, number) as applied."""
        r: Row | None = entry.get("_row") if entry else None
        if entry is None or r is None:
            return (
                acct.name,
                acct.account_type.value,
                acct.detail_type,
                acct.bank_kind,
                acct.account_number,
            )
        if acct is None:  # a new row: the file's own detail type, if any
            return (r.name, r.type, r.detail_type, r.bank_kind, entry.get("number"))
        retyped = any(c.startswith("type ") for c in entry.get("changes", []))
        return (
            r.name,
            r.type if retyped else acct.account_type.value,
            r.detail_type if retyped else acct.detail_type,
            acct.bank_kind or r.bank_kind,
            acct.account_number,
        )

    def node(kind, ref):
        if kind == "new":
            return planned(plan.rows[ref], None)
        acct = db.get(Account, ref)
        return planned(by_existing.get(ref), acct) if acct else None

    def parent_inactive(hit) -> bool:
        """The parent an importer cannot link under: an existing account
        already inactive (or a plan row deactivating it), or a new row the
        file marks inactive. Same rule as the API's _check_parent_usable."""
        kind, ref = hit
        if kind == "new":
            r = plan.rows[ref].get("_row")
            return getattr(r, "active", None) is False
        pe = by_existing.get(ref)
        if pe is not None:
            if pe.get("action") == "deactivate" or "deactivate" in pe.get(
                "changes", []
            ):
                return True
            if "reactivate" in pe.get("changes", []):
                return False
        acct = db.get(Account, ref)
        return acct is not None and not acct.is_active

    for entry in plan.rows:
        hit = entry.get("_parent")
        if not hit or entry["action"] in ("error", "deactivate", "keep"):
            continue
        acct = db.get(Account, entry["existing_id"]) if "existing_id" in entry else None
        child = planned(entry, acct)
        parent = node(*hit)
        if parent is None:
            continue
        problem = None
        # an inactive parent takes no sub-accounts, as the API refuses one
        # (#416); the same words, minus the "Reactivate it" an importer cannot
        if parent_inactive(hit):
            label = " ".join(p for p in (parent[4], parent[0]) if p)
            problem = f"{label} is inactive and cannot take sub-accounts"
        moved = acct is None or hit != ("existing", acct.parent_id)
        if (
            problem is None
            and acct is not None
            and moved
            and takes_no_new_parent(child[2], child[4], child[1])
        ):
            problem = f"{child[0]} is found by its number and takes no parent"
        if problem is None:
            label = " ".join(p for p in (parent[4], parent[0]) if p)
            # the planned detail type of a parent row is its default when the
            # file names none; the rule reads the one it will have
            pe = plan.rows[hit[1]] if hit[0] == "new" else by_existing.get(hit[1])
            pdetail = (pe or {}).get("detail_type") or parent[2]
            problem = parent_link_problem(
                label, parent[1], pdetail, child[1], child[2], child[3], child[4]
            )
        if problem:
            entry["note"] = (entry["note"] + "; " if entry.get("note") else "") + (
                problem[0].upper() + problem[1:] + ". The link was left off."
            )
            entry.pop("_parent", None)


def _flag_parent_loops(db: Session, plan: Plan) -> None:
    """A file whose rows name each other as parents (Testing #101) would make
    an account a sub-account of itself. Walk the parent links the plan would
    set — through new rows and existing accounts both — and leave off any that
    closes a loop, noting it on the row. Runs at plan time, so the dry-run
    preview shows the note, not only the applied import.

    Links are considered in row order, as apply would set them, so whichever
    link would close the loop is the one dropped and the rest stand."""

    # the parent each entry would get, keyed by a node id stable across new
    # rows (("new", row index)) and existing accounts (("existing", id))
    def node_of(i: int, entry: dict):
        if entry["action"] == "create":
            return ("new", i)
        if "existing_id" in entry:
            return ("existing", entry["existing_id"])
        return None

    def name_of(node) -> str:
        if node[0] == "new":
            return plan.rows[node[1]]["name"]
        acct = db.get(Account, node[1])
        return acct.name if acct else "?"

    accepted: dict[tuple, tuple] = {}  # node -> parent node, links that stand

    def parent_of(node):
        if node in accepted:
            return accepted[node]
        if node[0] == "existing":
            acct = db.get(Account, node[1])
            if acct and acct.parent_id:
                return ("existing", acct.parent_id)
        return None

    for i, entry in enumerate(plan.rows):
        hit = entry.get("_parent")
        if not hit:
            continue
        node = node_of(i, entry)
        parent = (hit[0], hit[1])
        if node is None or parent == node:
            entry.pop("_parent", None)  # self-parent: just not set
            continue
        # walk up from the proposed parent through the links already accepted;
        # reaching this node or a repeat loops, past MAX_DEPTH is too deep —
        # each gets its own note, not the loop one for both (#416)
        cur, seen, looped, too_deep = parent, set(), False, False
        while cur is not None:
            if cur == node:
                looped = True
                break
            if cur in seen:
                looped = True  # a pre-existing loop
                break
            if len(seen) >= MAX_DEPTH:
                too_deep = True
                break
            seen.add(cur)
            cur = parent_of(cur)
        if looped or too_deep:
            why = (
                depth_message(entry["name"], len(seen) + 1)
                if too_deep
                else loop_message(entry["name"], name_of(parent))
            )
            entry["note"] = (entry["note"] + "; " if entry.get("note") else "") + (
                why + " The link was left off."
            )
            entry.pop("_parent", None)
        else:
            accepted[node] = parent


def _flag_moves(db: Session, plan: Plan) -> None:
    """An account already in the chart that the file puts under a different
    parent is moved there (the apply links every updated row). A row with no
    other change would be a "skip", which the apply passes over: the dry run
    then previewed a link (and the detail type it hands down) that was never
    written. Record the move as the row's change so both say the same."""
    for entry in plan.rows:
        hit = entry.get("_parent")
        if not hit or entry["action"] not in ("skip", "update"):
            continue
        acct = db.get(Account, entry["existing_id"])
        if acct is None or hit == ("existing", acct.parent_id):
            continue
        if hit[0] == "new":
            label = " ".join(
                p
                for p in (plan.rows[hit[1]].get("number"), plan.rows[hit[1]]["name"])
                if p
            )
        else:
            pacct = db.get(Account, hit[1])
            label = " ".join(p for p in (pacct.account_number, pacct.name) if p)
        entry["changes"].append(f"parent → {label}")
        if entry["action"] == "skip":
            entry["action"] = "update"
            if entry.get("note") == "already in the chart":
                entry["note"] = ""


def _flag_inherited_detail(db: Session, plan: Plan) -> None:
    """A sub-account whose link stands takes its parent's detail type (#390),
    exactly as the account API sets it, even when the file declared a
    different one that would fit on its own. Run after the parent links are
    settled (refusals and loops already dropped). Walk the parent graph from
    the roots, not the rows top to bottom (Trent, #416): a file can list a
    grandchild above its parent, so settle each parent's inherited type —
    however deep — before any child reads it, and the dry run and the apply
    then agree on the detail type and its note.

    A "skip" row (already in the chart, same parent, nothing to change) is
    not settled: the apply writes nothing to it, and a sub-account keeps the
    detail type it has, as the account API leaves one whose parent does not
    change. Where 2.28.0's display rule prints it in its parent's Balance
    Sheet group, _note_balance_sheet_group says so on the row."""

    # the plan entry that is each parent node, for a parent that is a plan row
    entry_of: dict[tuple, dict] = {}
    for i, entry in enumerate(plan.rows):
        if entry["action"] == "create":
            entry_of[("new", i)] = entry
        elif "existing_id" in entry and "_row" in entry:
            entry_of[("existing", entry["existing_id"])] = entry

    def eligible(entry: dict) -> bool:
        return bool(entry.get("_parent")) and entry["action"] not in (
            "error",
            "deactivate",
            "keep",
            "skip",
        )

    def parent_detail(hit):
        """The settled detail type of a parent node, its own walk done first."""
        entry = entry_of.get(hit)
        if entry is not None:
            if eligible(entry):
                settle(entry)
            return entry.get("detail_type")
        if hit[0] == "existing":  # a parent not in the plan: the chart's own
            acct = db.get(Account, hit[1])
            return acct.detail_type if acct else None
        return None

    settled: set[int] = set()

    def settle(entry: dict) -> None:
        if id(entry) in settled:
            return
        settled.add(id(entry))  # DAG (loops already dropped): safe before recursing
        inherited = inherited_detail_type(
            parent_detail(entry["_parent"]), entry.get("detail_type")
        )
        if not inherited or inherited == entry.get("detail_type"):
            return
        # note only when the file named a type that gives way; one built
        # with none takes the parent's silently, as the model does
        r = entry.get("_row")
        if getattr(r, "detail_type", None):
            _note_detail_fallback(entry, entry["detail_type"], inherited)
        entry["detail_type"] = inherited

    for entry in plan.rows:
        if eligible(entry):
            settle(entry)


_BS_NOTE = "_bs_note"


def _bs_group_heading(detail) -> str:
    from app.services.statement_layout import BS_GROUPS

    for groups in BS_GROUPS.values():
        for heading, _key, details in groups:
            if detail in details:
                return heading
    return detail_label(detail)


def _plan_keys(plan: Plan, created: dict | None = None) -> dict[int, object]:
    """{row index: the account's key in a chart index}: its id, or, in the
    dry run, ("new", i) for a row still to be created."""
    keys = {}
    for i, e in enumerate(plan.rows):
        if e["action"] == "error":
            continue
        if e["action"] == "create":
            if created is None:
                keys[i] = ("new", i)
            elif i in created:
                keys[i] = created[i].id
        elif "existing_id" in e:
            keys[i] = e["existing_id"]
    return keys


def _planned_chart(db: Session, plan: Plan, keys: dict) -> dict:
    """The chart as the plan will leave it, in bs_print_details' index shape
    ({key: (number, name, parent, account type, detail type)})."""
    index = {
        a.id: (
            a.account_number or "",
            a.name or "",
            a.parent_id,
            a.account_type,
            a.detail_type,
        )
        for a in db.query(Account)
    }
    for i, key in keys.items():
        e = plan.rows[i]
        r = e.get("_row")
        old = index.get(key)
        writes = r is not None and e["action"] in ("create", "update")
        hit = e.get("_parent") if writes else None
        if hit:
            parent = hit if hit[0] == "new" else hit[1]
        else:
            parent = old[2] if old else None
        retyped = any(c.startswith("type ") for c in e.get("changes", []))
        atype = r.type if writes and (old is None or retyped) else old[3]
        index[key] = (
            e.get("number") or "",
            r.name if writes else old[1],
            parent,
            atype,
            e.get("detail_type"),
        )
    return index


def _note_balance_sheet_group(
    db: Session, plan: Plan, created: dict | None = None
) -> None:
    """Say on a row when the Balance Sheet prints the account in its
    parent's group rather than by its own detail type: 2.28.0's display rule
    (statement_layout.bs_print_details) for a sub-account still at its
    default. Display only, so the row's detail type stays what is written.
    The dry run reads the chart as planned, the apply the chart as written
    (`created`), and the apply's note replaces the dry run's."""
    from app.services.statement_layout import bs_print_details

    keys = _plan_keys(plan, created)
    if created is None:
        placed = bs_print_details(index=_planned_chart(db, plan, keys))
    else:
        placed = bs_print_details(db)
    for i, key in keys.items():
        entry = plan.rows[i]
        old = entry.pop(_BS_NOTE, None)
        if old:
            parts = [p for p in (entry.get("note") or "").split("; ") if p != old]
            entry["note"] = "; ".join(parts)
        group = placed.get(key)
        if not group or entry["action"] == "deactivate":
            continue
        note = (
            f"on the Balance Sheet it prints with its parent under "
            f"{_bs_group_heading(group)} (display only, its detail type "
            f"stays {detail_label(entry.get('detail_type'))})"
        )
        entry["note"] = (entry["note"] + "; " if entry.get("note") else "") + note
        entry[_BS_NOTE] = note


# --- applying ---------------------------------------------------------------


def _apply_order(plan: Plan) -> list[int]:
    """Row indices with every parent before its children (Trent, #416), so a
    grandchild reads its parent's settled detail type however the file ordered
    the rows. Loops are already dropped, so the parent links form a forest;
    any remnant is appended in row order."""
    index_of_existing = {
        e["existing_id"]: i for i, e in enumerate(plan.rows) if "existing_id" in e
    }
    children: dict[int, list[int]] = {}
    parent_of: dict[int, int] = {}
    for i, entry in enumerate(plan.rows):
        hit = entry.get("_parent")
        if not hit:
            continue
        pi = hit[1] if hit[0] == "new" else index_of_existing.get(hit[1])
        if pi is not None and pi != i:
            children.setdefault(pi, []).append(i)
            parent_of[i] = pi
    order: list[int] = []
    seen: set[int] = set()

    def visit(i: int) -> None:
        if i in seen:
            return
        seen.add(i)
        order.append(i)
        for c in children.get(i, []):
            visit(c)

    for i in range(len(plan.rows)):
        if i not in parent_of:  # a root: no parent that is also a plan row
            visit(i)
    for i in range(len(plan.rows)):  # a loop remnant, should not happen
        visit(i)
    return order


def apply_chart_import(db: Session, plan: Plan) -> None:
    created: dict[int, Account] = {}  # row index -> account
    declared: dict[int, Account] = {}  # rows whose file named a detail type
    for i, entry in enumerate(plan.rows):
        if entry["action"] != "create":
            continue
        r: Row = entry["_row"]
        a = Account(
            name=r.name,
            account_number=entry["number"],
            account_type=AccountType(r.type),
            description=r.description,
            bank_kind=r.bank_kind,
            detail_type=r.detail_type,
            is_active=r.active is not False,
            is_system=False,
        )
        db.add(a)
        created[i] = a
    db.flush()  # ids for the parent links
    # parents before children, so a grandchild reads its parent's settled
    # detail type whatever order the file listed the rows in (Trent, #416)
    for i in _apply_order(plan):
        entry = plan.rows[i]
        r = entry.get("_row")
        if entry["action"] == "create":
            a = created[i]
        elif entry["action"] == "update":
            a = db.get(Account, entry["existing_id"])
            for change in entry["changes"]:
                if change.startswith("rename"):
                    a.name = r.name
                elif change.startswith("type "):
                    a.account_type = AccountType(r.type)
                elif change.startswith("number "):
                    a.account_number = r.number
                elif change == "description":
                    a.description = r.description
                elif change.startswith("marked as"):
                    a.bank_kind = r.bank_kind
                elif change == "deactivate":
                    a.is_active = False
                elif change == "reactivate":
                    a.is_active = True
            # the detail type the dry run planned (#461 review): for a retype
            # with none in the file the number and name rule, for a new bank
            # kind that kind's, else the chart's own. Left to the model's
            # update hook, a retype took the plain default instead.
            a.detail_type = entry["detail_type"]
        elif entry["action"] == "deactivate":
            db.get(Account, entry["existing_id"]).is_active = False
            continue
        else:
            continue
        # Looping links were dropped at plan time (_flag_parent_loops), with
        # the note already on the row; only safe links remain here.
        parent = entry.get("_parent")
        if parent:
            kind, ref = parent
            pid = created[ref].id if kind == "new" else ref
            if pid != a.id:
                a.parent_id = pid
                # the child takes the parent's detail type (#390); the ORM
                # hook keeps a declared one that fits, so settle it here,
                # after the link, to match the dry run's planned type
                pacct = created[ref] if kind == "new" else db.get(Account, ref)
                inherited = inherited_detail_type(
                    pacct.detail_type if pacct else None, a.detail_type
                )
                if inherited:
                    a.detail_type = inherited
        if r.detail_type:
            declared[i] = a
    db.flush()
    # A declared detail type the account cannot carry (Bank without a bank
    # kind, AR away from 1100) fell back; say so on the row
    for i, a in declared.items():
        entry, r = plan.rows[i], plan.rows[i]["_row"]
        if entry["action"] == "update" and not any(
            c.startswith("type ") for c in entry["changes"]
        ):
            continue
        if a.detail_type != r.detail_type:
            _note_detail_fallback(entry, r.detail_type, a.detail_type)
    # the response reports what was written, not what was planned: every
    # row's detail type and parent read back from the chart
    for i, entry in enumerate(plan.rows):
        if entry["action"] == "error":
            continue
        a = created.get(i) if entry["action"] == "create" else None
        if a is None and "existing_id" in entry:
            a = db.get(Account, entry["existing_id"])
        if a is None:
            continue
        entry["detail_type"] = a.detail_type
        p = db.get(Account, a.parent_id) if a.parent_id else None
        entry["parent_number"] = p.account_number if p else None
        entry["parent_name"] = p.name if p else None
    _note_balance_sheet_group(db, plan, created)
    db.commit()


def _public(plan: Plan, applied: bool) -> dict:
    rows = []
    for e in plan.rows:
        row = {k: v for k, v in e.items() if not k.startswith("_")}
        # the label the chart shows for the detail type the row is written
        # with (#461) — read after an apply, which may settle it again
        row["detail_type_label"] = detail_label(e.get("detail_type"))
        rows.append(row)
    return {
        "format": plan.format,
        "dry_run": not applied,
        "replace": plan.replace,
        "created": plan.count("create"),
        "updated": plan.count("update"),
        "skipped": plan.count("skip"),
        "deactivated": plan.count("deactivate"),
        "kept": plan.count("keep"),
        "row_errors": plan.count("error"),
        "rows": rows,
        "errors": plan.errors,
    }


def import_chart(
    db: Session, text: str, replace: bool = False, dry_run: bool = True
) -> dict:
    """The one entry point: plan, and unless `dry_run`, apply that plan."""
    plan = plan_chart_import(db, text, replace=replace)
    if not dry_run and (plan.rows or not plan.errors):
        apply_chart_import(db, plan)
        return _public(plan, applied=True)
    return _public(plan, applied=False)
