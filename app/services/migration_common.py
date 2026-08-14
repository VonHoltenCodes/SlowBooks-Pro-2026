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

from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.services.accounting import _q, create_journal_entry

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
    if not raw:
        return Decimal("0")
    negative = raw.startswith("(") and raw.endswith(")")
    if negative:
        raw = raw[1:-1]
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"unparseable amount: {raw!r}")
    return -value if negative else value


def parse_date(raw: str, formats: tuple[str, ...]):
    raw = (raw or "").strip()
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {raw!r}")


def sniff_reader(csv_text: str) -> csv.DictReader:
    """DictReader with the delimiter sniffed from the header line —
    MYOB classic exports are tab-separated, cloud exports are commas."""
    first_line = csv_text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first_line else ","
    return csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)


def strip_code_suffix(name: str) -> str:
    """'Name (Code)' → 'name' for cross-file account matching."""
    return name.split("(")[0].strip().lower()


# ── The engine ───────────────────────────────────────────────────────────


def dry_run_bundle(db: Session, bundle: dict, parsers: dict, source_label: str) -> dict:
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

    known = {a["name"].lower() for a in accounts}
    known |= {strip_code_suffix(a["name"]) for a in accounts}
    known |= {a.name.lower() for a in db.query(Account).all()}

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
        tb, tb_errors = parsers["tb"](bundle["tb"])
        errors.extend(tb_errors)
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
                    {"account": name, "amount": float(diff)} for name, diff in residuals
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
    else:
        warnings.append("No trial balance file supplied — balance verification skipped")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "accounts": len(accounts),
        "journals": len(journals),
        "opening_balances": opening_balances,
    }


def run_import_bundle(
    db: Session, bundle: dict, parsers: dict, source_type: str, source_label: str
) -> dict:
    """Execute the import. Refuses when the dry-run fails."""
    verdict = dry_run_bundle(db, bundle, parsers, source_label)
    if not verdict["ok"]:
        return {**verdict, "imported_accounts": 0, "imported_journals": 0}

    accounts, _ = parsers["coa"](bundle["coa"])
    created = 0
    by_name: dict[str, Account] = {a.name.lower(): a for a in db.query(Account).all()}
    for spec in accounts:
        key = spec["name"].lower()
        if key in by_name:
            continue
        acct = Account(
            name=spec["name"],
            account_number=spec["code"],
            account_type=spec["type"],
            description=spec["description"],
        )
        # Source-system codes can collide with the seeded chart's numbers —
        # the name stays authoritative; drop the code rather than fail.
        if spec["code"] and (
            db.query(Account).filter(Account.account_number == spec["code"]).first()
        ):
            acct.account_number = None
        db.add(acct)
        db.flush()
        by_name[key] = acct
        created += 1

    journals, _ = parsers["gl"](bundle["gl"])

    # Synthesized opening balances (see dry_run_bundle): one balanced
    # journal dated the day before the earliest imported transaction.
    if verdict.get("opening_balances"):
        from datetime import timedelta

        first_date = min(group[0]["date"] for group in journals if group)
        ob_lines = []
        for entry in verdict["opening_balances"]:
            amount = _q(Decimal(str(entry["amount"])))
            acct = by_name.get(entry["account"])
            if acct is None or amount == 0:
                continue
            ob_lines.append(
                {
                    "account_id": acct.id,
                    "debit": amount if amount > 0 else Decimal("0"),
                    "credit": -amount if amount < 0 else Decimal("0"),
                    "description": f"Opening balance — {acct.name}",
                }
            )
        if ob_lines:
            create_journal_entry(
                db,
                first_date - timedelta(days=1),
                f"{source_label} migration — opening balances",
                ob_lines,
                source_type="opening_balance",
                reference="OPENING",
            )

    posted = 0
    for group in journals:
        lines = []
        for row in group:
            # Exact name first (disambiguated duplicates keep their code
            # suffix); the stripped form covers "Name (Code)" GL exports.
            acct = (
                by_name.get(row["account"].lower())
                or by_name[strip_code_suffix(row["account"])]
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
        reference = first.get("journal") or first.get("reference") or None
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
    return {**verdict, "imported_accounts": created, "imported_journals": posted}


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
BASE_FILE_KINDS = (
    ("chart", "coa"),
    ("account", "coa"),
    ("acct", "coa"),  # real users type "acct_tree"
    ("general", "gl"),
    ("ledger", "gl"),
    ("journal", "gl"),
    ("transaction", "gl"),
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
