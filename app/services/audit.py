# ============================================================================
# Audit Log Service — SQLAlchemy event hook for automatic change tracking
# Logs all INSERT/UPDATE/DELETE operations to the audit_log table
# ============================================================================

from datetime import datetime
from decimal import Decimal
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.audit import AuditLog

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
    "email_log",  # outbound email send log
}


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


def _get_instance_dict(instance):
    """Get a dict of column values from a model instance."""
    mapper = inspect(type(instance))
    result = {}
    for col in mapper.columns:
        key = col.key
        val = getattr(instance, key, None)
        result[key] = _serialize_value(val)
    return result


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
    )
    db.add(entry)


def _after_flush(session, flush_context):
    """SQLAlchemy after_flush event — captures all changes."""
    audit_entries = []

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
                source="api",
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
        for attr in insp.attrs:
            hist = attr.history
            if hist.has_changes():
                key = attr.key
                old_val = hist.deleted[0] if hist.deleted else None
                new_val = hist.added[0] if hist.added else None
                old_vals[key] = _serialize_value(old_val)
                new_vals[key] = _serialize_value(new_val)
                changed.append(key)

        if changed:
            audit_entries.append(
                AuditLog(
                    table_name=table,
                    record_id=record_id,
                    action="UPDATE",
                    old_values=old_vals,
                    new_values=new_vals,
                    changed_fields=changed,
                    source="api",
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
                source="api",
            )
        )

    # Add audit entries to session (they'll be flushed in the next flush)
    for entry in audit_entries:
        session.add(entry)


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
    event.listen(session_factory, "after_flush", _after_flush)
    setattr(session_factory, _HOOKED_FLAG, True)
