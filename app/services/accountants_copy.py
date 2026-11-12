"""SlowBooks' own Accountant's Copy (#346): the QBX/QBY round trip with no
server at all.

Send to accountant (an administrator, desktop or server on a company
file): pick a dividing date; the books are copied into a file encrypted
under a passphrase shown once (SQLCipher, the key derived from the
passphrase the way 2.23.2 derives it from the operator's secret), with the
copy's identity, the dividing date and a signing secret written inside it,
and the owner's sign-ins and secrets left out. The copy is registered here
so changes can come back from it.

Open accountant's copy (the accountant, their own install): the file and
the passphrase; the copy becomes a company file of its own, opened with
first-run setup, in copy mode: it takes journal entries dated on or before
the dividing date and nothing else (app.main's gate).

Send changes back: the entries posted in the copy, signed with the copy's
secret, as one small file. Apply accountant's changes (the owner): the file
is checked (this company's copy, the signature, the dividing date), each
entry is previewed, and the new ones post as adjusting entries in the
accountant's name; a file applied twice posts nothing twice; a file from
another company, or altered, is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import secrets
import shutil
import tempfile
import time
import uuid
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accountants_copies import AccountantsCopy
from app.models.accounts import Account
from app.models.transactions import Transaction
from app.services import company_file
from app.services.account_label import account_label
from app.services.auth import READABLE_ALPHABET
from app.services.settings_service import (
    ENCRYPTED_SETTINGS_KEYS,
    get_setting_raw,
    use_account_numbers,
)

logger = logging.getLogger(__name__)

FORMAT = 1
COPY_SUFFIX = ".sbacopy"
CHANGES_SUFFIX = ".sbchanges"
# the settings rows that make a company file an accountant's copy
KEY_ID = "accountants_copy_id"
KEY_TOKEN = "accountants_copy_token"
KEY_DIVIDING = "accountants_copy_dividing_date"
KEY_MADE = "accountants_copy_made_at"
KEY_COMPANY = "accountants_copy_company"
# the last transaction id at copy time: what the accountant posts comes after it
KEY_LAST_ID = "accountants_copy_last_txn_id"
COPY_KEYS = (KEY_ID, KEY_TOKEN, KEY_DIVIDING, KEY_MADE, KEY_COMPANY, KEY_LAST_ID)
SOURCE_TYPE = "accountants_copy"
NOT_A_FILE_MESSAGE = (
    "An accountant's copy is made from a company file. This server runs on "
    "PostgreSQL; give your accountant a sign-in (Settings → Users → Invite your "
    "accountant) or the Accountant's Package instead."
)
COPY_MODE_MESSAGE = (
    "This is an accountant's copy: it takes journal entries dated on or before "
    "{day}, and nothing else. Send changes back from the Companies page."
)
WRONG_PASSPHRASE_MESSAGE = "The passphrase is not the one this copy was made with."


# ---------------------------------------------------------------------------
# Copy mode: is the running company file an accountant's copy?
# ---------------------------------------------------------------------------

_mode_cache: dict = {"at": 0.0, "value": None, "url": None}


def mode_of(db: Session) -> dict | None:
    """The copy this company file is, from its own settings, or None."""
    copy_id = get_setting_raw(db, KEY_ID)
    if not copy_id:
        return None
    return {
        "copy_id": copy_id,
        "token": get_setting_raw(db, KEY_TOKEN) or "",
        "dividing_date": get_setting_raw(db, KEY_DIVIDING) or "",
        "made_at": get_setting_raw(db, KEY_MADE) or "",
        "company_name": get_setting_raw(db, KEY_COMPANY) or "",
        "last_txn_id": int(get_setting_raw(db, KEY_LAST_ID) or 0),
    }


def mode() -> dict | None:
    """The same, for the request gate, read at most every few seconds."""
    from app.config import DATABASE_URL
    from app.database import SessionLocal, another_company_url

    # per company: a Server Edition server serves several (#358)
    url = another_company_url() or DATABASE_URL
    now = time.monotonic()
    if _mode_cache["url"] == url and now - _mode_cache["at"] < 5:
        return _mode_cache["value"]
    try:
        db = SessionLocal()
        try:
            value = mode_of(db)
        finally:
            db.close()
    except Exception:
        value = None
    _mode_cache.update(at=now, value=value, url=url)
    return value


def forget_mode() -> None:
    _mode_cache.update(at=0.0, value=None, url=None)


def copy_gate(method: str, path: str) -> bool:
    """What an accountant's copy lets through: every read, and the journal
    (post, void, attach) — the one permission the copy has."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return True
    if method != "POST":
        return False
    if path == "/api/journal" or (
        path.startswith("/api/journal/") and path.endswith("/void")
    ):
        return True
    if path.startswith("/api/attachments/journal_entry/"):
        return True
    # a print page's one-time address (#823) is a read: the sheet is drawn
    # through this gate again, as a GET
    if path == "/api/print-tokens":
        return True
    return path.startswith("/api/auth/")


def check_entry_date(db: Session, day: date) -> None:
    """In a copy, an entry after the dividing date is refused: that side of
    the line is the business's."""
    m = mode_of(db)
    if m and m["dividing_date"] and day.isoformat() > m["dividing_date"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This accountant's copy takes entries dated on or before "
                f"{m['dividing_date']}; {day.isoformat()} is after it."
            ),
        )


# ---------------------------------------------------------------------------
# Send to accountant
# ---------------------------------------------------------------------------


# No look-alikes (0/O, 1/l/I): the passphrase is read out or typed from a
# note (2.24.0 gate, NEW-155). 55 characters, 24 of them: ~139 bits. Built,
# not written out: a secret scanner read the alphabet as a password.
# The passphrase's alphabet (NEW-155): letters and digits with no look-alikes,
# the one the invitation's one-time password draws on too.
_PASSPHRASE_ALPHABET = READABLE_ALPHABET


def _passphrase() -> str:
    # 24 characters: typed once, by the accountant, from a note
    return "".join(secrets.choice(_PASSPHRASE_ALPHABET) for _ in range(24))


def _strip_owner(plain_db: Path, copy: dict) -> None:
    """In the plain copy: the owner's sign-ins and secrets out, the copy's
    own identity and name in."""
    conn = company_file.sqlite_module().connect(str(plain_db))
    try:
        cur = conn.cursor()
        tables = {
            r[0]
            for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in (
            "users",
            "api_tokens",
            "login_attempts",
            "portal_accesses",
            "request_keys",
        ):
            if table in tables:
                cur.execute(f"DELETE FROM {table}")  # table names from our own list
        gone = {
            "auth_password_hash",
            "session_secret",
            "company_session_id",
            *ENCRYPTED_SETTINGS_KEYS,
        }
        for key in gone:
            cur.execute("DELETE FROM settings WHERE key = ?", (key,))
        # a bank account's check account number (#739) is encrypted under
        # the same key as those settings, which the copy does not carry
        if "bank_check_details" in tables:
            cur.execute(
                "UPDATE bank_check_details "
                "SET account_number_enc = NULL, account_last_four = NULL"
            )
        # the audit log stays (seeing what moved is the job), its snapshots
        # of the owner's hashes and secret settings redacted (NEW-148)
        if "audit_log" in tables:
            from app.services.audit_redaction import redact_values

            rows = cur.execute(
                "SELECT id, table_name, old_values, new_values FROM audit_log "
                "WHERE table_name IN ('users', 'settings', 'api_tokens', "
                "'employee_bank_accounts', 'portal_accesses')"
            ).fetchall()
            for row_id, table, old_raw, new_raw in rows:
                new_pair = []
                for raw in (old_raw, new_raw):
                    if raw is None:
                        new_pair.append(None)
                        continue
                    try:
                        values = json.loads(raw) if isinstance(raw, str) else raw
                    except ValueError:
                        new_pair.append(raw)
                        continue
                    new_pair.append(json.dumps(redact_values(table, values)))
                cur.execute(
                    "UPDATE audit_log SET old_values = ?, new_values = ? WHERE id = ?",
                    (new_pair[0], new_pair[1], row_id),
                )
        # the copy names itself
        for key, value in (
            (KEY_ID, copy["copy_id"]),
            (KEY_TOKEN, copy["token"]),
            (KEY_DIVIDING, copy["dividing_date"]),
            (KEY_MADE, copy["made_at"]),
            (KEY_COMPANY, copy["company_name"]),
            (KEY_LAST_ID, str(copy["last_txn_id"])),
            ("company_name", f"{copy['company_name']} (accountant's copy)"),
        ):
            cur.execute("DELETE FROM settings WHERE key = ?", (key,))
            cur.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def make_copy(
    db: Session,
    dividing_date: date,
    *,
    source_path: Path | None = None,
    source_key: bytes | None = None,
    company_name: str | None = None,
) -> tuple[bytes, str, dict]:
    """The copy as zip bytes, its passphrase (shown once, kept nowhere), and
    its record. ``source_path`` is the running company file unless given
    (tests give one)."""
    path = source_path or company_file.active_path()
    if path is None:
        raise HTTPException(status_code=409, detail=NOT_A_FILE_MESSAGE)
    if not company_file.available():
        raise HTTPException(status_code=409, detail=company_file.UNAVAILABLE_MESSAGE)
    key = source_key if source_key is not None else company_file.resolve_key()
    if company_name is None:
        from app.services.backup_service import current_company_name

        company_name = current_company_name(db) or ""
    copy_id = str(uuid.uuid4())
    token = secrets.token_hex(32)
    made_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    last_id = db.query(Transaction.id).order_by(Transaction.id.desc()).first()
    record = {
        "format": FORMAT,
        "copy_id": copy_id,
        "token": token,
        "company_name": company_name,
        "dividing_date": dividing_date.isoformat(),
        "made_at": made_at,
        "last_txn_id": int(last_id[0]) if last_id else 0,
    }
    passphrase = _passphrase()
    copy_key = company_file.derive_key(passphrase)
    work = Path(tempfile.mkdtemp(prefix="sbacopy-"))
    try:
        plain = work / "company.db"
        # a consistent copy of the live file, WAL included, through the
        # sqlite backup API; the source opens keyed when it is encrypted
        src = company_file.connect_raw(
            Path(path), key if company_file.is_encrypted_file(path) else None
        )
        try:
            dst = company_file.sqlite_module().connect(str(plain))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        _strip_owner(plain, record)
        company_file.convert(plain, copy_key, encrypt=True)
        meta = {k: v for k, v in record.items() if k not in ("token", "last_txn_id")}
        meta["app_version"] = _version()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("copy.json", json.dumps(meta, indent=2))
            z.write(plain, "company.db")
        content = buf.getvalue()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.add(
        AccountantsCopy(
            copy_id=copy_id,
            token=token,
            company_name=company_name,
            dividing_date=dividing_date,
            applied=[],
        )
    )
    db.commit()
    logger.info(
        "accountant's copy %s made, dividing date %s", copy_id[:8], dividing_date
    )
    return content, passphrase, meta


def copy_file_name(company_name: str, dividing_date: date) -> str:
    from app.services.terminology import Terms

    slug = Terms().slug(company_name) or "company"
    return f"{slug}-accountants-copy-{dividing_date.isoformat()}{COPY_SUFFIX}"


def _version() -> str:
    from app import __version__

    return __version__


# ---------------------------------------------------------------------------
# Open accountant's copy
# ---------------------------------------------------------------------------


def open_copy(
    content: bytes,
    passphrase: str,
    *,
    companies_root: Path | None = None,
    local_key: bytes | None = None,
) -> dict:
    """The copy file unpacked into a company file of its own under the
    companies folder and registered on the picker; the passphrase opens it.
    Returns the manifest entry (name and file)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        meta = json.loads(z.read("copy.json").decode("utf-8"))
        db_bytes = z.read("company.db")
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="This is not an accountant's copy file (the .sbacopy SlowBooks Pro makes).",
        )
    if (
        not isinstance(meta, dict)
        or meta.get("format") != FORMAT
        or not meta.get("copy_id")
    ):
        raise HTTPException(
            status_code=400,
            detail="This accountant's copy is of a format this version does not read.",
        )
    if not company_file.available():
        raise HTTPException(status_code=409, detail=company_file.UNAVAILABLE_MESSAGE)
    from app.services.company_service import (
        _read_manifest,
        _write_manifest,
        companies_dir,
        company_filename_for,
    )

    root = Path(companies_root) if companies_root is not None else companies_dir()
    root.mkdir(parents=True, exist_ok=True)
    name = f"{meta.get('company_name') or 'Company'} (accountant's copy)"
    filename = (
        company_filename_for(name) or f"accountants-copy-{meta['copy_id'][:8]}.db"
    )
    target = root / filename
    if target.exists():
        raise HTTPException(
            status_code=409,
            detail=f"A company file named '{filename}' is already here. Open that one, or remove it first.",
        )
    work = Path(tempfile.mkdtemp(prefix="sbacopy-"))
    try:
        staged = work / "company.db"
        staged.write_bytes(db_bytes)
        try:
            company_file.convert(
                staged, company_file.derive_key(passphrase), encrypt=False
            )
        except company_file.CompanyFileLocked:
            raise HTTPException(status_code=400, detail=WRONG_PASSPHRASE_MESSAGE)
        if local_key is not None:
            # this install keeps its files encrypted: the copy joins them
            company_file.convert(staged, local_key, encrypt=True)
        shutil.move(str(staged), str(target))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if companies_root is None:
        manifest = _read_manifest()
        manifest["companies"].append({"name": name, "file": filename})
        _write_manifest(manifest)
    logger.info("accountant's copy %s opened as %s", meta["copy_id"][:8], filename)
    return {
        "name": name,
        "file": filename,
        "copy_id": meta["copy_id"],
        "dividing_date": meta.get("dividing_date"),
    }


# ---------------------------------------------------------------------------
# Send changes back
# ---------------------------------------------------------------------------


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(token: str, body: dict) -> str:
    return hmac.new(token.encode("utf-8"), _canonical(body), hashlib.sha256).hexdigest()


def changes(db: Session, *, accountant: str = "") -> dict:
    """The entries posted in this copy since it was made, signed."""
    m = mode_of(db)
    if not m:
        raise HTTPException(
            status_code=409,
            detail="This company is not an accountant's copy; there are no changes to send back.",
        )
    from app.services.qbo_documents import REVERSALS

    last_id = m.get("last_txn_id", 0)
    accounts = {a.id: a for a in db.query(Account).all()}
    voided_ids = {
        source_id
        for (source_id,) in db.query(Transaction.source_id).filter(
            Transaction.source_type.in_(REVERSALS), Transaction.source_id.isnot(None)
        )
    }
    entries = []
    for txn in (
        db.query(Transaction)
        .filter(Transaction.id > last_id, Transaction.source_type == "manual")
        .order_by(Transaction.id)
        .all()
    ):
        entries.append(
            {
                "ref": txn.id,
                "date": txn.date.isoformat(),
                "description": txn.description or "",
                "reference": txn.reference or "",
                "is_adjusting": bool(txn.is_adjusting),
                "posted_by": txn.posted_by or "",
                "voided": txn.id in voided_ids,
                "lines": [
                    {
                        "account_number": (
                            (accounts[ln.account_id].account_number or "")
                            if ln.account_id in accounts
                            else ""
                        ),
                        "account_name": (
                            accounts[ln.account_id].name
                            if ln.account_id in accounts
                            else ""
                        ),
                        "debit": f"{Decimal(str(ln.debit or 0)):.2f}",
                        "credit": f"{Decimal(str(ln.credit or 0)):.2f}",
                        "description": ln.description or "",
                    }
                    for ln in sorted(txn.lines, key=lambda ln: ln.id)
                ],
            }
        )
    body = {
        "format": FORMAT,
        "copy_id": m["copy_id"],
        "company_name": m["company_name"],
        "dividing_date": m["dividing_date"],
        "made_at": m["made_at"],
        "sent_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "accountant": accountant,
        "entries": entries,
    }
    body["signature"] = _sign(
        m["token"], {k: v for k, v in body.items() if k != "signature"}
    )
    return body


def changes_file_name(m: dict) -> str:
    from app.services.terminology import Terms

    slug = Terms().slug(m.get("company_name") or "") or "company"
    return f"{slug}-accountants-changes-{date.today().isoformat()}{CHANGES_SUFFIX}"


# ---------------------------------------------------------------------------
# Apply accountant's changes
# ---------------------------------------------------------------------------


def _read_changes(content: bytes) -> dict:
    try:
        body = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="This is not an accountant's changes file (the .sbchanges SlowBooks Pro makes).",
        )
    if (
        not isinstance(body, dict)
        or body.get("format") != FORMAT
        or not body.get("copy_id")
    ):
        raise HTTPException(
            status_code=400,
            detail="This changes file is of a format this version does not read.",
        )
    return body


def _chart(db: Session) -> dict:
    """Accounts by number, then by name, for the lines coming back."""
    by_number, by_name = {}, {}
    for a in db.query(Account).all():
        if a.account_number:
            by_number.setdefault(a.account_number, a)
        by_name.setdefault((a.name or "").strip().lower(), a)
    return {"number": by_number, "name": by_name}


def _resolve_account(chart: dict, line: dict):
    acct = chart["number"].get(line.get("account_number") or "")
    if acct is None:
        acct = chart["name"].get((line.get("account_name") or "").strip().lower())
    return acct


def preview(db: Session, content: bytes) -> dict:
    """What applying the file would do: each entry new, already posted,
    or unable to post (an account missing, a date past the line), after
    the file is proven this company's and unaltered."""
    body = _read_changes(content)
    record = db.get(AccountantsCopy, body["copy_id"])
    if record is None:
        raise HTTPException(
            status_code=400,
            detail="This changes file is from a copy these books did not make, or from another company. Nothing was posted.",
        )
    expected = _sign(record.token, {k: v for k, v in body.items() if k != "signature"})
    if not hmac.compare_digest(expected, str(body.get("signature") or "")):
        raise HTTPException(
            status_code=400,
            detail="This changes file has been altered since the accountant's copy wrote it. Nothing was posted.",
        )
    if body.get("dividing_date") != record.dividing_date.isoformat():
        raise HTTPException(
            status_code=400,
            detail="This changes file names a different dividing date from the copy's. Nothing was posted.",
        )
    applied = set(record.applied or [])
    chart = _chart(db)
    rows = []
    numbered = use_account_numbers(db)
    for e in body.get("entries", []):
        ref = int(e["ref"])
        status, why = "new", ""
        if ref in applied:
            status = "applied"
        elif e.get("voided"):
            status, why = "skipped", "voided in the copy"
        elif e["date"] > record.dividing_date.isoformat():
            status, why = (
                "refused",
                f"dated after the dividing date {record.dividing_date.isoformat()}",
            )
        else:
            missing = [
                account_label(
                    ln.get("account_number"), ln.get("account_name"), numbered, " "
                )
                for ln in e.get("lines", [])
                if _resolve_account(chart, ln) is None
            ]
            if missing:
                status, why = "refused", "no account here for " + ", ".join(
                    m.strip() for m in missing
                )
        rows.append(
            {
                "ref": ref,
                "date": e["date"],
                "description": e.get("description", ""),
                "reference": e.get("reference", ""),
                "posted_by": e.get("posted_by", ""),
                "total": f"{sum(Decimal(ln['debit']) for ln in e.get('lines', [])):.2f}",
                "lines": e.get("lines", []),
                "status": status,
                "why": why,
            }
        )
    counts = {
        k: sum(1 for r in rows if r["status"] == k)
        for k in ("new", "applied", "skipped", "refused")
    }
    return {
        "copy_id": body["copy_id"],
        "company_name": body.get("company_name", ""),
        "dividing_date": body.get("dividing_date", ""),
        "accountant": body.get("accountant", ""),
        "sent_at": body.get("sent_at", ""),
        "entries": rows,
        "counts": counts,
    }


def apply(db: Session, content: bytes, *, posted_by: str = "") -> dict:
    """Post the new entries as adjusting entries in the accountant's name,
    and remember them; the rest of the preview stands."""
    from app.services.accounting import create_journal_entry

    seen = preview(db, content)
    record = db.get(AccountantsCopy, seen["copy_id"])
    chart = _chart(db)
    applied = list(record.applied or [])
    who = seen.get("accountant") or "accountant"
    posted = 0
    for e in seen["entries"]:
        if e["status"] != "new":
            continue
        lines = [
            {
                "account_id": _resolve_account(chart, ln).id,
                "debit": Decimal(ln["debit"]),
                "credit": Decimal(ln["credit"]),
                "description": ln.get("description") or e["description"],
            }
            for ln in e["lines"]
        ]
        create_journal_entry(
            db,
            date.fromisoformat(e["date"]),
            e["description"] or "Accountant's adjustment",
            lines,
            source_type=SOURCE_TYPE,
            # the accountant's own numbering when they gave one (AJE-7),
            # else the copy and the entry it came from
            reference=(e.get("reference") or "").strip()
            or f"AC:{seen['copy_id'][:8]}:{e['ref']}",
            is_adjusting=True,
            posted_by=f"accountant:{who}"[:100],
        )
        applied.append(e["ref"])
        e["status"] = "posted"
        posted += 1
    record.applied = applied
    db.commit()
    seen["counts"]["posted"] = posted
    logger.info(
        "accountant's changes applied: %s posted from copy %s",
        posted,
        seen["copy_id"][:8],
    )
    return seen


def is_copy_file_name(name: str) -> bool:
    return (name or "").lower().endswith(COPY_SUFFIX)


def is_changes_file_name(name: str) -> bool:
    return (name or "").lower().endswith(CHANGES_SUFFIX)
