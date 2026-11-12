# ============================================================================
# Audit Log Service — SQLAlchemy event hook for automatic change tracking
# Logs all INSERT/UPDATE/DELETE operations to the audit_log table
# ============================================================================

from datetime import datetime
from decimal import Decimal
from sqlalchemy import LargeBinary, event, inspect
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.services.audit_redaction import REDACTED, is_secret_column, redact_values
from app.services.request_context import acting_source, acting_username


def _actor(session) -> str | None:
    """Who is acting: the Session's own info dict (stamped by get_db —
    travels with the object, immune to task/context propagation quirks),
    falling back to the request contextvar for sessions created outside
    the request cycle."""
    # Fall back to an explicit "system" principal rather than NULL. A
    # tamper-evident trail with anonymous rows is weaker than it looks:
    # NULL is ambiguous between "the system did it" (token last_used_at
    # stamps, startup writes) and "attribution failed". Naming the machine
    # principal makes the distinction auditable — a NULL username is now
    # always a bug, never a normal state.
    return session.info.get("acting_username") or acting_username.get() or "system"


# Tables to skip auditing.
# audit_log itself must be skipped to prevent infinite recursion (the
# audit entries we're about to write would themselves trigger writes).
# The others are already audit/log-style tables — every insert into them
# IS the audit event, and double-logging in audit_log only adds noise
# (and risks the same recursion if a future migration adds an `id` set
# via trigger). Keep this set in sync with new audit-style models.
_SKIP_TABLES = {
    "audit_log",  # primary audit table (recursion guard)
    "portal_accesses",  # portal cookie / token claim access log
    "login_attempts",  # admin failed-login tracking
    "document_audits",  # hash-chain document audit
    "request_keys",  # retry-safe request identities (#292): the payment is the event
    "email_log",  # outbound email send log
}

# Column values that must never be snapshotted into audit JSON — the users
# table is audited (who created/changed accounts matters), its hashes are not.
# The one list, for every action and for settings rows, is
# app/services/audit_redaction.py (2.24.0 gate, NEW-148: an UPDATE copied the
# hash before and after, and a settings row carried its secret value).
_REDACT_COLUMNS = {"password_hash"}


def _serialize_value(val):
    """Convert a value to JSON-serializable form."""
    if val is None:
        return None
    if isinstance(val, (int, bool, str, float)):
        return val
    # Decimal (money) BEFORE the float fallback — float() would round-trip
    # money through binary float and silently lose precision in the audit
    # record (the ledger's record of record). Keep it exact as a string.
    if isinstance(val, Decimal):
        return str(val)
    if isinstance(val, datetime):
        return val.isoformat()
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if hasattr(val, "value"):  # enum
        return val.value
    try:
        return float(val)
    except (TypeError, ValueError):
        return str(val)


def _is_file_content(column) -> bool:
    """A file's bytes (stored_files.data). Never copied into the audit log:
    a deleted W-4 must be gone, not kept as text in audit_log, and a deleted
    row's deferred bytes cannot be loaded after the DELETE anyway. The row's
    name, size and sha256 are logged; they say what the file was."""
    return isinstance(getattr(column, "type", None), LargeBinary)


def _get_instance_dict(instance):
    """Get a dict of column values from a model instance."""
    mapper = inspect(type(instance))
    result = {}
    for col in mapper.columns:
        key = col.key
        if _is_file_content(col):
            continue
        val = getattr(instance, key, None)
        if (
            (key in _REDACT_COLUMNS or is_secret_column(key))
            and isinstance(val, str)
            and val
        ):
            result[key] = REDACTED
            continue
        result[key] = _serialize_value(val)
    return redact_values(getattr(instance, "__tablename__", ""), result)


def log_event(
    db: Session,
    table_name: str,
    record_id: int,
    action: str,
    old_values: dict = None,
    new_values: dict = None,
    changed_fields: list = None,
    source: str = "api",
):
    """Manually log an audit event."""
    entry = AuditLog(
        table_name=table_name,
        record_id=record_id,
        action=action,
        old_values=old_values,
        new_values=new_values,
        changed_fields=changed_fields,
        source=source,
        username=_actor(db),
    )
    db.add(entry)


def audited_bulk_delete(db: Session, query) -> int:
    """Delete the rows a query names, with a DELETE audit event for each —
    the event, with the old_values, that deleting the instance through
    the session writes; returns how many.

    The contract for a posted document's line replacement (#289): editing
    a bill's or an invoice's lines, and re-posting its journal, removed
    the old lines with Query.delete(), which never passes through
    session.deleted, so the after_flush hook saw nothing — the edit's own
    UPDATE and the new lines' INSERTs were in the log, the old lines'
    amounts were not. Every caller that replaces lines goes through here,
    in the same transaction as the replacement, so the log carries each
    old line (its account and amounts) and each new one.

    Each row is deleted through the session and flushed here: the hook
    writes its event, and the identity map lets the row go. The first
    version logged each row itself and then bulk-deleted with
    synchronize_session=False, which left the loaded rows in the session;
    when SQLite reused their ids for the new lines, SQLAlchemy warned
    "Identity map already had an identity" on every edit (2.23.0 gate,
    NEW-91)."""
    rows = query.all()
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


def _after_flush(session, flush_context):
    """SQLAlchemy after_flush event — captures all changes."""
    audit_entries = []
    # A change made into a closed period with the closing-date password
    # (#342): the override's own row is written by closing_date, and every
    # row this request changes after it carries the same source, so the
    # Closing Date Exceptions report can list what the override touched.
    source = "api"
    info = getattr(session, "info", None)
    if isinstance(info, dict) and info.get("_closing_override_recorded"):
        source = "closing_date_override"
    else:
        # a request running an import (#894): "import:<its name>", so a
        # document's Activity says which import made it
        source = (
            (info.get("audit_source") if isinstance(info, dict) else None)
            or acting_source.get()
            or source
        )

    # New objects (INSERT)
    for obj in session.new:
        table = getattr(obj, "__tablename__", None)
        if not table or table in _SKIP_TABLES:
            continue
        record_id = getattr(obj, "id", None)
        if record_id is None:
            continue
        new_vals = _get_instance_dict(obj)
        audit_entries.append(
            AuditLog(
                table_name=table,
                record_id=record_id,
                action="INSERT",
                old_values=None,
                new_values=new_vals,
                changed_fields=list(new_vals.keys()),
                source=source,
                username=_actor(session),
            )
        )

    # Modified objects (UPDATE)
    for obj in session.dirty:
        table = getattr(obj, "__tablename__", None)
        if not table or table in _SKIP_TABLES:
            continue
        if not session.is_modified(obj, include_collections=False):
            continue
        record_id = getattr(obj, "id", None)
        if record_id is None:
            continue

        insp = inspect(obj)
        old_vals = {}
        new_vals = {}
        changed = []
        file_content = {c.key for c in insp.mapper.columns if _is_file_content(c)}
        for attr in insp.attrs:
            if attr.key in file_content:
                continue
            hist = attr.history
            if hist.has_changes():
                key = attr.key
                old_val = hist.deleted[0] if hist.deleted else None
                new_val = hist.added[0] if hist.added else None
                old_vals[key] = _serialize_value(old_val)
                new_vals[key] = _serialize_value(new_val)
                changed.append(key)
        # a secret moved is noted as moved, never as what it was
        old_vals = redact_values(table, old_vals) or old_vals
        new_vals = redact_values(table, new_vals) or new_vals
        if table == "settings":
            # the row's key is what tells a secret setting from the rest
            key_now = getattr(obj, "key", None)
            from app.services.audit_redaction import sensitive_setting_keys

            if key_now in sensitive_setting_keys():
                for vals in (old_vals, new_vals):
                    if vals.get("value") not in (None, ""):
                        vals["value"] = REDACTED

        if changed:
            audit_entries.append(
                AuditLog(
                    table_name=table,
                    record_id=record_id,
                    action="UPDATE",
                    old_values=old_vals,
                    new_values=new_vals,
                    changed_fields=changed,
                    source=source,
                    username=_actor(session),
                )
            )

    # Deleted objects (DELETE)
    for obj in session.deleted:
        table = getattr(obj, "__tablename__", None)
        if not table or table in _SKIP_TABLES:
            continue
        record_id = getattr(obj, "id", None)
        if record_id is None:
            continue
        old_vals = _get_instance_dict(obj)
        audit_entries.append(
            AuditLog(
                table_name=table,
                record_id=record_id,
                action="DELETE",
                old_values=old_vals,
                new_values=None,
                changed_fields=None,
                source=source,
                username=_actor(session),
            )
        )

    # Add audit entries to session (they'll be flushed in the next flush)
    for entry in audit_entries:
        session.add(entry)


# Log-style tables that keep their own actor column (#894): the audit log
# skips them, so the row itself says who did it. Stamped here, once, for
# every place that writes one.
_ACTOR_STAMPED_TABLES = {"email_log"}


def _before_flush(session, flush_context, instances):
    """Name the actor on a new email-log row that does not name one."""
    for obj in session.new:
        if getattr(obj, "__tablename__", None) not in _ACTOR_STAMPED_TABLES:
            continue
        if getattr(obj, "username", None) is None:
            try:
                obj.username = _actor(session)
            except Exception:
                pass  # attribution must never break a write


_HOOKED_FLAG = "_slowbooks_audit_hook_attached"


def register_audit_hooks(session_factory):
    """Register the after_flush hook on the session factory.

    Idempotent: registering the same factory twice would otherwise attach
    the listener twice and write duplicate audit_log rows for every change.

    Uses a sentinel attribute on the factory rather than `event.contains`.
    Under the pytest test suite many short-lived sessionmakers are created
    and GC'd in sequence; CPython's allocator readily reuses the freed id,
    and SQLAlchemy's event registry can hold a stale entry against that
    id from a prior (collected) factory. `event.contains` then returns a
    false-positive True for the *new* factory, so `event.listen` is skipped
    and the new factory ends up with no listener attached — every flush
    bypasses the audit hook silently. The sentinel attribute lives on the
    factory instance itself, so id-reuse cannot leak across instances.
    """
    if getattr(session_factory, _HOOKED_FLAG, False):
        return
    event.listen(session_factory, "before_flush", _before_flush)
    event.listen(session_factory, "after_flush", _after_flush)
    setattr(session_factory, _HOOKED_FLAG, True)
