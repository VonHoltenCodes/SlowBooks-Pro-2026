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
exactly that plan. The fifteen control accounts the posting code finds by
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

from app.models.accounts import Account, AccountType
from app.models.transactions import TransactionLine
from app.services import control_accounts
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
        if typed:
            acct_type, bank_kind = typed
        elif cell("type"):
            errors.append(
                f"Row {n}: unknown account type '{cell('type')}' for '{name}' "
                f"(use asset, liability, equity, income, cogs or expense)"
            )
            continue
        else:
            acct_type, bank_kind = _type_from_number(number), None
            if not acct_type:
                errors.append(
                    f"Row {n}: '{name}' has no type and no number to infer it from"
                )
                continue
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
                if s not in self.used:
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
                if control_accounts.is_control_number(target.account_number):
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

    if replace:
        for a in existing:
            if a.id in matched or not a.is_active:
                continue
            if control_accounts.is_control_number(a.account_number):
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
                }
            )
    return plan


# --- applying ---------------------------------------------------------------


def apply_chart_import(db: Session, plan: Plan) -> None:
    created: dict[int, Account] = {}  # row index -> account
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
            is_active=r.active is not False,
            is_system=False,
        )
        db.add(a)
        created[i] = a
    db.flush()  # ids for the parent links
    for i, entry in enumerate(plan.rows):
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
        elif entry["action"] == "deactivate":
            db.get(Account, entry["existing_id"]).is_active = False
            continue
        else:
            continue
        parent = entry.get("_parent")
        if parent:
            kind, ref = parent
            pid = created[ref].id if kind == "new" else ref
            if pid != a.id:
                a.parent_id = pid
    db.commit()


def _public(plan: Plan, applied: bool) -> dict:
    rows = []
    for e in plan.rows:
        rows.append({k: v for k, v in e.items() if not k.startswith("_")})
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
