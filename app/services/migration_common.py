# ============================================================================
# Migration engine — shared by every "migrate from X" CSV importer
# (Xero today, MYOB now, whatever comes next).
#
# The engine owns the contract; source modules own only their dialect:
#   parsers = {
#     "coa": text -> ([{code,name,type,description}], errors),
#     "gl":  text -> ([journal_groups], errors)   # rows w/ date/account/
#                                                 # debit/credit/desc/ref
#     "tb":  text -> ({account_name_lower: net_debit_minus_credit}, errors),
#   }
#
# DRY-RUN is the contract: parse everything, require every reconstructed
# journal to balance, flag GL accounts missing from the chart, verify GL
# nets against the trial balance when supplied — and write NOTHING.
# run_import() re-runs the dry-run and refuses when it fails.
# ============================================================================

import csv
import io
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.models.accounts import (
    Account,
    default_detail_type,
    detail_label,
    detail_type_problem,
)
from app.models.transactions import Transaction
from app.services.account_label import account_label
from app.services.account_tree import inherited_detail_type, parent_link_problem
from app.services.accounting import _q, create_journal_entry
from app.services.safe_errors import DataProblem
from app.services.settings_service import use_account_numbers

logger = logging.getLogger(__name__)


# ── Dialect-agnostic cell helpers ────────────────────────────────────────


def field(row: dict, *aliases: str) -> str:
    """Case-insensitive, BOM-tolerant header lookup across aliases."""
    for alias in aliases:
        for key, value in row.items():
            if key and key.strip().lstrip("﻿").lower() == alias:
                return (value or "").strip()
    return ""


def parse_amount(raw: str) -> Decimal:
    """Money cell → Decimal. Tolerates $, thousands separators, and
    accounting-style parentheses negatives."""
    raw = (raw or "").strip().replace(",", "").replace("$", "")
    # QuickBooks Online's Excel exports write every figure as formula text
    # ("=23683.16"), which a sheet-to-CSV pass keeps verbatim.
    raw = raw.lstrip("=").strip()
    if not raw:
        return Decimal("0")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise DataProblem(f"unparseable amount: {raw!r}")
    return -value if negative else value


def parse_date(raw: str, formats: tuple[str, ...]):
    raw = (raw or "").strip()
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise DataProblem(f"unparseable date: {raw!r}")


def sniff_reader(csv_text: str) -> csv.DictReader:
    """DictReader with the delimiter sniffed from the header line —
    MYOB classic exports are tab-separated, cloud exports are commas. A
    value a spreadsheet export guarded with a leading apostrophe
    ("'=Name") reads as the value itself (csv_export.strip_formula_guard)."""
    from app.services.csv_import import UnguardedDictReader

    first_line = csv_text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first_line else ","
    return UnguardedDictReader(io.StringIO(csv_text), delimiter=delimiter)


def strip_code_suffix(name: str) -> str:
    """An account's matching key, the one every side of an import uses (the
    chart files, the journal, the trial balance): 'Name (Code)' → 'name',
    and the same for each level of a 'Parent:Child' path, so
    'Payroll Liabilities (2100):Federal Unemployment (940)' and
    'Payroll Liabilities:Federal Unemployment' are one account (#690).
    Before #690 only the text before the first '(' was kept, which cut a
    parent's suffix off together with every child under it."""
    return ":".join(part.split("(")[0].strip() for part in name.lower().split(":"))


def spec_path(spec: dict) -> str:
    """A chart spec's full 'Parent:Child' path, the name every side of an
    import matches it by. A dialect that names a sub-account by its leaf
    (the QuickBooks Online export, NEW-203) gives the path beside the name;
    for the others the name is the path."""
    return spec.get("path") or spec["name"]


def account_paths(db: Session) -> list[tuple[Account, str]]:
    """Every account in the books with its 'Parent:Child' path: the names
    up its parent chain joined by ':' (a top-level account's path is its
    name). An account named by its leaf under a parent is found by the
    path the journal and the trial balance write (NEW-203)."""
    accounts = db.query(Account).all()
    by_id = {a.id: a for a in accounts}
    out = []
    for acct in accounts:
        parts: list[str] = []
        seen: set = set()
        cur = acct
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            parts.append(cur.name)
            cur = by_id.get(cur.parent_id)
        out.append((acct, ":".join(reversed(parts))))
    return out


def books_by_name(db: Session) -> dict:
    """{lowercased path or name → Account}: paths first, so a leaf name
    never stands in for a top-level account the path names."""
    pairs = account_paths(db)
    found: dict = {}
    for acct, path in pairs:
        found.setdefault(path.lower(), acct)
    for acct, _ in pairs:
        found.setdefault(acct.name.lower(), acct)
    return found


def onto_accounts(db: Session, accounts: list[dict]) -> dict:
    """{lowercased path → Account} for the chart specs a dialect maps onto
    an account the books have (a spec's "onto", an account id): QuickBooks
    Online's "Accounts Payable (A/P)" lands on the 2000 control (#704). Such
    a spec is not created; the journal, the trial balance and any child of
    it resolve to that account. A path the books already hold (an earlier
    import made it) keeps its own account."""
    in_books = books_by_name(db)
    found: dict = {}
    for spec in accounts:
        if spec.get("onto") is None:
            continue
        path = spec_path(spec).lower()
        if path in in_books:
            continue
        acct = db.get(Account, spec["onto"])
        if acct is not None:
            found.setdefault(path, acct)
    return found


def planned_sub_account_note(name: str, parent_name: str, acct_type) -> str:
    """The words for a trial-balance account planned under its parent
    (#690), one sentence for the engine and for a dialect that plans it."""
    return (
        f"Account {name!r} is on the trial balance but not in the chart "
        f"files; it will be created as a sub-account of {parent_name!r} "
        f"({_value(acct_type)}) to carry its opening balance"
    )


def _account_keys(db: Session, accounts: list[dict]) -> dict:
    """{matching key → (name, type)} for the accounts in the books and the
    chart files' specs: the planned chart, as the import will have it. The
    name is the one a spec's "parent" resolves through (the path)."""
    keys: dict = {}
    pairs = account_paths(db)
    for acct, path in pairs:
        keys.setdefault(strip_code_suffix(path), (path, acct.account_type))
    for acct, _ in pairs:
        keys.setdefault(strip_code_suffix(acct.name), (acct.name, acct.account_type))
    for spec in accounts:
        path = spec_path(spec)
        keys.setdefault(strip_code_suffix(path), (path, spec["type"]))
    return keys


def _opening_accounts(
    db: Session, accounts: list[dict], opening_balances: list[dict]
) -> tuple[list[dict], list[str], list[str]]:
    """The accounts the opening balances need that the chart files do not
    list (#690): (specs to create, notes, names that cannot be planned).

    A trial balance may name an account the Balance Sheet and the P&L leave
    out. A sub-account of one the chart has is planned under it, with its
    parent's type; any other is named, and the import refuses, rather than
    dropping its balance from the opening journal at apply. Decided the same
    way for the dry run and the apply."""
    keys = _account_keys(db, accounts)
    extra: list[dict] = []
    notes: list[str] = []
    missing: list[str] = []
    # parents before their children, so a missing parent can be planned first
    for entry in sorted(opening_balances, key=lambda e: e["account"].count(":")):
        if entry["account"] in keys:
            continue
        name = (entry.get("name") or entry["account"]).strip()
        parent = name.rsplit(":", 1)[0] if ":" in name else ""
        found = keys.get(strip_code_suffix(parent)) if parent else None
        if found is None:
            missing.append(name)
            continue
        parent_name, acct_type = found
        # named by its leaf under the parent, the path kept beside it, so the
        # statements print the parent once (NEW-203) and the trial balance's
        # "Parent:Child" spelling still finds it
        leaf = name.rsplit(":", 1)[1].strip() or name
        extra.append(
            {
                "code": None,
                "name": leaf,
                "type": acct_type,
                "description": None,
                "bank_kind": None,
                "detail_type": None,
                "parent": parent_name,
                "path": name,
            }
        )
        keys[entry["account"]] = (name, acct_type)
        notes.append(planned_sub_account_note(name, parent_name, acct_type))
    return extra, notes, missing


def _missing_problem(missing: list[str]) -> str:
    return (
        f"The trial balance has a balance on {len(missing)} account(s) that the "
        f"chart files do not list: {', '.join(repr(n) for n in missing)}. Add "
        f"them to the chart of accounts with their type, then import again"
    )


# ── The engine ───────────────────────────────────────────────────────────


def _group_reference(group: list[dict]) -> str | None:
    """The dedupe key for a journal group, cut to Transaction.reference's
    column width (String(100)). The import posts the journal with this same
    value (run_import_bundle), so what is stored equals what already_imported()
    matches against — a source reference over 100 chars is not re-imported as a
    new journal (#498)."""
    first = group[0]
    ref = first.get("journal") or first.get("reference") or None
    return str(ref)[:100] if ref else None


def already_imported(db: Session, source_type: str | None) -> set[str]:
    """References this source has already posted. A second click on Import —
    the reporter of #169 clicked four times — must not double the books."""
    if not source_type:
        return set()
    rows = (
        db.query(Transaction.reference)
        .filter(Transaction.source_type == source_type)
        .filter(Transaction.reference.isnot(None))
        .all()
    )
    # Cut stored references the way _group_reference() cuts the key: books
    # imported before #498 hold the full source reference, and an uncut set
    # would never match the cut key, so a re-import would double them.
    return {r[0][:100] for r in rows}


def _spec_parent(spec: dict):
    """A parsed COA spec seen as a parent for the parent rule, for the dry run
    where the parent is another row not yet created (#416). A new account is
    active."""
    return SimpleNamespace(
        name=spec["name"],
        account_number=spec.get("code"),
        account_type=spec["type"],
        detail_type=spec.get("detail_type"),
        is_active=True,
    )


def _parent_decision(
    parent, spec: dict, numbered: bool = True
) -> tuple[bool, str | None, str | None]:
    """The migration's parent rule in one place (#416), so the dry run
    previews exactly what the import applies: (link it, warning, the detail
    type the child inherits). `parent` is the resolved parent (an Account, or
    a spec seen through _spec_parent) or None."""
    if parent is None:
        return False, None, None
    named = account_label(parent.account_number, parent.name, numbered, " ")
    if not parent.is_active:
        # same rule and words as the API / chart import, minus "Reactivate it"
        return (
            False,
            f"'{spec['name']}' was not made a sub-account of '{parent.name}': "
            f"{named} is inactive and cannot take sub-accounts",
            None,
        )
    problem = parent_link_problem(
        parent.name,
        parent.account_type,
        parent.detail_type,
        spec["type"],
        spec.get("detail_type"),
        spec.get("bank_kind"),
        spec["code"],
    )
    if problem:
        return (
            False,
            f"'{spec['name']}' was not made a sub-account of '{parent.name}': {problem}",
            None,
        )
    inherited = inherited_detail_type(parent.detail_type, spec.get("detail_type"))
    warning = None
    if inherited and spec.get("detail_type"):
        # the link stands but the child takes the parent's detail type (#390)
        warning = (
            f"'{spec['name']}' takes {detail_label(inherited)}, the detail type "
            f"of its parent '{parent.name}' (the file said "
            f"{detail_label(spec['detail_type'])})"
        )
    return True, warning, inherited


def _value(v):
    return getattr(v, "value", v)


def _effective_detail(spec: dict, number, parent, link: bool, inherited):
    """The detail type the account is created with, as the model would settle
    it: the parent's when it hands one down, else the file's while it fits,
    else a linked parent's when that fits, else the default for its type."""
    if inherited:
        return _value(inherited)

    def fits(detail) -> bool:
        if not detail:
            return False
        try:
            problem = detail_type_problem(
                detail, spec["type"], spec.get("bank_kind"), number
            )
        except ValueError:
            return False
        return problem is None

    declared = _value(spec.get("detail_type"))
    if fits(declared):
        return declared
    if link and fits(_value(parent.detail_type)):
        return _value(parent.detail_type)
    # a new account's default: 2.27.0's number and name rule (2.28.0)
    return default_detail_type(
        spec["type"], spec.get("bank_kind"), number, name=spec.get("name") or ""
    ).value


def _plan_accounts(db: Session, accounts: list[dict]) -> list[dict]:
    """What the import does with the chart's accounts, decided once for the
    dry run and the apply alike (#416), so the preview is what lands.

    A spec whose name is already in the books is not created, and a parent
    name resolves to the account in the books first (an inactive one too,
    which refuses sub-accounts), then to another spec of the bundle. New
    accounts are planned parents first, so a child listed above its parent
    still links and a grandchild inherits through its parent's settled detail
    type. Returns one decision per account to create, in creation order."""
    in_books = books_by_name(db)
    for path, acct in onto_accounts(db, accounts).items():
        in_books.setdefault(path, acct)  # mapped onto it: not created (#704)
    used_numbers = {
        n for (n,) in db.query(Account.account_number).all() if n is not None
    }
    new_index: dict[str, int] = {}
    for i, spec in enumerate(accounts):
        key = spec_path(spec).lower()
        if key not in in_books and key not in new_index:
            new_index[key] = i

    order: list[int] = []
    state: dict[int, str] = {}

    def visit(i: int) -> None:
        if i in state:
            return  # done, or a loop back to a row still being visited
        state[i] = "visiting"
        pname = (accounts[i].get("parent") or "").lower()
        j = new_index.get(pname) if pname not in in_books else None
        if j is not None and j != i:
            visit(j)
        state[i] = "done"
        order.append(i)

    for i in sorted(new_index.values()):
        visit(i)

    planned: dict[int, SimpleNamespace] = {}
    decisions: list[dict] = []
    numbered = use_account_numbers(db)
    for i in order:
        spec = accounts[i]
        pname = (spec.get("parent") or "").lower()
        parent = parent_ref = None
        if pname in in_books:
            parent = in_books[pname]
            parent_ref = ("books", parent)
        elif new_index.get(pname) in planned:
            parent = planned[new_index[pname]]
            parent_ref = ("new", new_index[pname])
        link, warning, inherited = _parent_decision(parent, spec, numbered)
        # source-system codes can collide with the books' numbers (or an
        # earlier row's): the name stays authoritative, the code is dropped
        code = spec.get("code")
        number = code if code and code not in used_numbers else None
        if number:
            used_numbers.add(number)
        detail = _effective_detail(spec, number, parent, link, inherited)
        planned[i] = SimpleNamespace(
            name=spec["name"],
            account_number=number,
            account_type=spec["type"],
            detail_type=detail,
            is_active=True,
        )
        decisions.append(
            {
                "index": i,
                "spec": spec,
                "number": number,
                "parent": parent_ref if link else None,
                "warning": warning,
                "inherited": inherited,
                "detail_type": detail,
            }
        )
    return decisions


def dry_run_bundle(
    db: Session,
    bundle: dict,
    parsers: dict,
    source_label: str,
    source_type: str | None = None,
) -> dict:
    """Validate a bundle {kind: csv_text}. Nothing is written."""
    errors: list[str] = []
    warnings: list[str] = []

    if "coa" not in bundle:
        errors.append(
            "Missing chart of accounts file (filename containing 'chart' or 'accounts')"
        )
    if "gl" not in bundle:
        errors.append(
            "Missing general ledger / journal file (filename containing "
            "'general', 'ledger', or 'journal')"
        )
    if errors:
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "accounts": 0,
            "journals": 0,
        }

    accounts, coa_errors = parsers["coa"](bundle["coa"])
    errors.extend(coa_errors)
    journals, gl_errors = parsers["gl"](bundle["gl"])
    errors.extend(gl_errors)

    # A file that parsed to nothing is named — unless its rows were read and
    # refused one by one (a ledger whose accounts an empty chart does not
    # list): those errors already say what happened, and "no records were
    # recognised" beside them blamed a good file (2.23.1 gate, W-1 / NEW-97).
    for kind, records, kind_errors, label in (
        ("coa", accounts, coa_errors, "chart of accounts"),
        ("gl", journals, gl_errors, "general ledger"),
    ):
        if not records and not kind_errors:
            header = (bundle[kind].lstrip("\ufeff").splitlines() or [""])[0]
            errors.append(
                f"No {label} records were recognised in the {source_label} file. "
                f"Check that the export contains data and the expected columns. "
                f"The file's header row is: {header[:300]}"
            )

    known = {spec_path(a).lower() for a in accounts}
    known |= {strip_code_suffix(spec_path(a)) for a in accounts}
    for acct, path in account_paths(db):
        known |= {acct.name.lower(), strip_code_suffix(acct.name)}
        known |= {path.lower(), strip_code_suffix(path)}

    # A journal whose every line is 0.00 "balances" — and then posts nothing.
    # That is what an unrecognised amount column looks like from here: the
    # dry run passed 14,372 journals and the import wrote none (#169; the
    # same shape as 2.11.1's Wave report fix, which fixed the headers and not
    # the hole). A file with amounts we could not read is refused, by name.
    empty = [
        g
        for g in journals
        if g and all(r["debit"] == 0 and r["credit"] == 0 for r in g)
    ]
    if journals and len(empty) == len(journals):
        header = (bundle["gl"].lstrip("\ufeff").splitlines() or [""])[0]
        errors.append(
            f"Every one of the {len(journals)} journals came out as 0.00 — the "
            f"debit / credit columns in the ledger file were not recognised, so "
            f"nothing would be imported. The file's header row is: {header[:300]}"
        )
    elif empty:
        refs = [
            str(g[0].get("journal") or g[0].get("reference") or g[0]["date"])
            for g in empty[:5]
        ]
        warnings.append(
            f"{len(empty)} journal(s) have no amounts and will be skipped "
            f"(first: {', '.join(refs)})"
        )

    seen = already_imported(db, source_type)
    duplicates = [g for g in journals if g and _group_reference(g) in seen]
    if duplicates:
        warnings.append(
            f"{len(duplicates)} of the {len(journals)} journals are already in "
            f"the books from an earlier import (same transaction id) and will "
            f"be skipped, not imported twice"
        )

    simulated: dict[str, Decimal] = {}
    for group in journals:
        total = sum(r["debit"] - r["credit"] for r in group)
        # Must be EXACT: create_journal_entry() rejects any non-zero
        # imbalance, so a tolerance here only defers the failure to the
        # middle of the import. A one-cent rounding drift is the single
        # most common real-world imbalance, and the old "> 0.01" test let
        # exactly that case through the gate.
        if _q(abs(total)) != 0:
            ref = (
                group[0].get("journal") or group[0].get("reference") or group[0]["date"]
            )
            errors.append(f"Journal {ref}: unbalanced by {_q(total)}")
        for row in group:
            name = strip_code_suffix(row["account"])
            if name not in known:
                errors.append(
                    f"GL references account {row['account']!r} not present in "
                    f"the chart of accounts"
                )
                known.add(name)  # report once
            simulated[name] = (
                simulated.get(name, Decimal("0")) + row["debit"] - row["credit"]
            )

    opening_balances: list[dict] = []
    if "tb" in bundle and "tb" in parsers:
        tb_read, tb_errors = parsers["tb"](bundle["tb"])
        errors.extend(tb_errors)
        # the trial balance keyed the way the journal is (#690); a name as
        # the file wrote it is kept for an account the chart lacks
        tb: dict[str, Decimal] = {}
        tb_names: dict[str, str] = {}
        for raw_name, net in tb_read.items():
            key = strip_code_suffix(raw_name)
            tb[key] = tb.get(key, Decimal("0")) + net
            tb_names.setdefault(key, raw_name)
        if not tb:
            header = (bundle["tb"].lstrip("\ufeff").splitlines() or [""])[0]
            errors.append(
                f"No trial balance records were recognised in the {source_label} "
                f"file, so balances could not be verified. Check that the export "
                f"contains data and the expected columns. "
                f"The file's header row is: {header[:300]}"
            )
        residuals = []
        for name, expected in tb.items():
            got = simulated.get(name, Decimal("0"))
            diff = _q(expected - got)
            if diff != 0:
                residuals.append((name, diff))
        if residuals:
            # Opening balances are entered as account setup in the source
            # system, so they never appear in a journal export — the
            # fingerprint is per-account residuals that NET TO ZERO
            # (typically offset through a "historical balancing" account).
            # That case is resolvable: import synthesizes one balanced
            # opening journal. Residuals that DON'T net to zero mean data
            # is genuinely missing and stay hard errors.
            # Same exactness rule: the synthesized opening journal is
            # posted through create_journal_entry(), so residuals must net
            # to exactly zero or that post fails mid-import.
            if _q(abs(sum(d for _, d in residuals))) == 0:
                opening_balances = [
                    {"account": name, "name": tb_names[name], "amount": float(diff)}
                    for name, diff in residuals
                ]
                warnings.append(
                    f"Trial balance differs from the journals on "
                    f"{len(residuals)} account(s) by amounts that net to zero "
                    f"— treated as opening balances; import will post one "
                    f"balanced opening journal for the difference"
                )
            else:
                for name, diff in residuals:
                    errors.append(
                        f"Trial balance mismatch for {name!r}: GL nets "
                        f"{_q(tb[name] - diff)}, trial balance says {_q(tb[name])}"
                    )
    elif not bundle.get("tb_set_aside"):
        # A dialect that set the trial balance aside (one on the cash basis,
        # beside an accrual journal) has said so in a note of its own.
        warnings.append("No trial balance file supplied — balance verification skipped")

    # Every account an opening balance lands on is in the planned chart, or
    # is planned now, or the import is refused with its name (#690): the
    # apply used to skip one it could not find and post a short entry.
    extra, notes, missing = _opening_accounts(db, accounts, opening_balances)
    accounts = accounts + extra
    warnings.extend(notes)
    if missing:
        errors.append(_missing_problem(missing))

    # Preview the parent rules the import applies (#416): the same plan the
    # apply follows, so the inheritance and refusal warnings — identical
    # words — are what the import will do with the links.
    for decision in sorted(_plan_accounts(db, accounts), key=lambda d: d["index"]):
        if decision["warning"]:
            warnings.append(decision["warning"])

    existing = set(books_by_name(db)) | set(onto_accounts(db, accounts))
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "accounts": len(accounts),
        # the chart's accounts not in the books yet: what an import would make
        "new_accounts": len(
            [a for a in accounts if spec_path(a).lower() not in existing]
        ),
        "journals": len(journals),
        "duplicate_journals": len(duplicates),
        "opening_balances": opening_balances,
    }


def run_import_bundle(
    db: Session, bundle: dict, parsers: dict, source_type: str, source_label: str
) -> dict:
    """Execute the import. Refuses when the dry-run fails."""
    verdict = dry_run_bundle(db, bundle, parsers, source_label, source_type)
    if not verdict["ok"]:
        return {**verdict, "imported_accounts": 0, "imported_journals": 0}

    accounts, _ = parsers["coa"](bundle["coa"])
    # the accounts the dry run planned for the opening balances (#690),
    # created with the chart; one it could not plan refuses before writing
    extra, _, missing = _opening_accounts(
        db, accounts, verdict.get("opening_balances") or []
    )
    if missing:
        return {
            **verdict,
            "ok": False,
            "errors": list(verdict["errors"]) + [_missing_problem(missing)],
            "imported_accounts": 0,
            "imported_journals": 0,
        }
    accounts = accounts + extra
    created = 0
    warnings = list(verdict.get("warnings") or [])
    # the books' accounts by path, then by name (NEW-203)
    by_name: dict[str, Account] = books_by_name(db)
    onto = onto_accounts(db, accounts)
    for path, acct in onto.items():
        by_name.setdefault(path, acct)
    new_paths: list[tuple[str, Account]] = []
    # The dry run's plan, parents first. Its parent warnings are already in
    # `warnings` (carried from the verdict), so they are not added twice.
    made: dict[int, Account] = {}
    for decision in _plan_accounts(db, accounts):
        spec = decision["spec"]
        acct = Account(
            name=spec["name"],
            account_number=decision["number"],
            account_type=spec["type"],
            description=spec["description"],
            # the dialect's finer type (#390), the parent's when it hands one
            # down, else the default where the file's does not fit
            detail_type=decision["detail_type"],
        )
        # A dialect may say what kind of account it is (a bank, a card) and
        # whose sub-account it is ("Parent:Child").
        if spec.get("bank_kind"):
            acct.bank_kind = spec["bank_kind"]
        if decision["parent"] is not None:
            kind, ref = decision["parent"]
            acct.parent_id = ref.id if kind == "books" else made[ref].id
        db.add(acct)
        db.flush()
        declared = getattr(spec.get("detail_type"), "value", spec.get("detail_type"))
        if declared and acct.detail_type != declared and not decision["inherited"]:
            # an AR away from 1100, a Bank with no bank kind: the model gave
            # the default (#390); say so rather than drop the file's word
            # (a type that gave way to the parent's is already noted above)
            warnings.append(
                f"'{acct.name}' is {detail_label(declared)} in the file and came in "
                f"as {detail_label(acct.detail_type)}"
            )
        made[decision["index"]] = acct
        by_name[spec_path(spec).lower()] = acct
        new_paths.append((spec_path(spec), acct))
        created += 1
    for _, acct in new_paths:
        by_name.setdefault(acct.name.lower(), acct)
    # the matching key of every account, books' and new (#690): what the
    # trial balance and the journal's "Name (Code)" forms resolve through,
    # paths before leaf names (NEW-203)
    by_key: dict[str, Account] = {}
    for acct, path in account_paths(db):
        by_key.setdefault(strip_code_suffix(path), acct)
    for path, acct in onto.items():
        by_key.setdefault(strip_code_suffix(path), acct)
    for acct in by_name.values():
        by_key.setdefault(strip_code_suffix(acct.name), acct)

    journals, _ = parsers["gl"](bundle["gl"])

    # Synthesized opening balances (see dry_run_bundle): one balanced
    # journal dated the day before the earliest imported transaction.
    if verdict.get("opening_balances"):
        from datetime import timedelta

        first_date = min(group[0]["date"] for group in journals if group)
        ob_lines = []
        unresolved = []
        for entry in verdict["opening_balances"]:
            amount = _q(Decimal(str(entry["amount"])))
            acct = by_name.get(entry["account"]) or by_key.get(entry["account"])
            if acct is None:
                unresolved.append(entry.get("name") or entry["account"])
                continue
            if amount == 0:
                continue
            ob_lines.append(
                {
                    "account_id": acct.id,
                    "debit": amount if amount > 0 else Decimal("0"),
                    "credit": -amount if amount < 0 else Decimal("0"),
                    "description": f"Opening balance — {acct.name}",
                }
            )
        if unresolved:
            # never a short opening entry (#690); nothing is committed yet
            db.rollback()
            raise DataProblem(_missing_problem(unresolved))
        opening_desc = f"{source_label} migration — opening balances"
        already_opened = (
            db.query(Transaction.id)
            .filter(Transaction.source_type == "opening_balance")
            .filter(Transaction.description == opening_desc)
            .first()
        )
        if ob_lines and not already_opened:
            create_journal_entry(
                db,
                first_date - timedelta(days=1),
                f"{source_label} migration — opening balances",
                ob_lines,
                source_type="opening_balance",
                reference="OPENING",
            )

    posted = 0
    duplicates = 0
    seen = already_imported(db, source_type)
    for group in journals:
        if group and _group_reference(group) in seen:
            duplicates += 1
            continue
        lines = []
        for row in group:
            # Exact name first (disambiguated duplicates keep their code
            # suffix); the stripped form covers "Name (Code)" GL exports.
            acct = (
                by_name.get(row["account"].lower())
                or by_key[strip_code_suffix(row["account"])]
            )
            debit = _q(row["debit"])
            credit = _q(row["credit"])
            # MYOB (and hand-edited files) express contra lines as a
            # NEGATIVE amount in the same column; the journal engine only
            # accepts non-negative sides, so normalize sign to side here.
            if debit < 0:
                credit += -debit
                debit = _q(0)
            if credit < 0:
                debit += -credit
                credit = _q(0)
            if debit == 0 and credit == 0:
                continue
            lines.append(
                {
                    "account_id": acct.id,
                    "debit": debit,
                    "credit": credit,
                    "description": row.get("description") or None,
                }
            )
        if not lines:
            continue
        first = group[0]
        # Store the same value the dedupe key uses (cut to the column width),
        # so a re-import recognises this journal (#498).
        reference = _group_reference(group)
        create_journal_entry(
            db,
            first["date"],
            first.get("description")
            or f"{source_label} import {reference or ''}".strip(),
            lines,
            source_type=source_type,
            reference=reference,
        )
        posted += 1

    db.commit()
    return {
        **verdict,
        "warnings": warnings,
        "imported_accounts": created,
        "imported_journals": posted,
        # journals the file held that posted nothing (all-zero lines)
        "skipped_journals": len([g for g in journals if g]) - posted - duplicates,
        # journals an earlier import already posted (same reference)
        "duplicate_journals": duplicates,
    }


def build_code_map(accounts: list[dict]) -> dict[str, str]:
    """{account code → account name} from parsed COA specs, tolerating
    dashed and flat code forms (MYOB '1-1100' vs '11100', Sage IDs)."""
    mapping: dict[str, str] = {}
    for spec in accounts:
        code = (spec.get("code") or "").strip()
        if code:
            mapping[code] = spec["name"]
            mapping[code.replace("-", "")] = spec["name"]
    return mapping


# Filename fragments shared by every source's bundle classifier; dialects
# can extend (e.g. Wave/GnuCash "transactions" exports are the GL).
#
# GL fragments are checked before "account"/"acct": Wave's own export for
# this bundle's GL file is literally named "Account Transactions.csv", and
# with "account" checked first that unambiguously-GL file matched "coa"
# instead, before "transaction" ever got a look.
BASE_FILE_KINDS = (
    ("chart", "coa"),
    ("general", "gl"),
    ("ledger", "gl"),
    ("journal", "gl"),
    ("transaction", "gl"),
    ("account", "coa"),
    ("acct", "coa"),  # real users type "acct_tree"
    ("trial", "tb"),
)


def make_classifier(extra_kinds: tuple = ()):
    kinds = tuple(extra_kinds) + BASE_FILE_KINDS

    def classify_filename(name: str) -> str | None:
        lowered = (name or "").lower()
        for fragment, kind in kinds:
            if fragment in lowered:
                return kind
        return None

    return classify_filename


def skip_report_preamble(
    csv_text: str, required: tuple[str, ...] = ("debit", "credit")
) -> str:
    """Drop report-style preamble (company address, title, period, print
    date) before the actual column header row.

    MYOB (and friends) export REPORTS with a decorative header block; the
    real table starts at the first line whose cells include all the
    required tokens. Returns the text from that line on — or unchanged
    when no such line exists (plain table exports)."""
    lines = csv_text.splitlines()
    for idx, line in enumerate(lines):
        delimiter = "\t" if "\t" in line else ","
        cells = {c.strip().lstrip("﻿").lower() for c in line.split(delimiter)}
        if all(any(tok in cell for cell in cells) for tok in required):
            return "\n".join(lines[idx:])
    return csv_text
