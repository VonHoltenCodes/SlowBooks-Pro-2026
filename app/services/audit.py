# ============================================================================
# Audit Log Service — SQLAlchemy event hook for automatic change tracking
# Logs all INSERT/UPDATE/DELETE operations to the audit_log table
# ============================================================================

from datetime import datetime, timezone
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models.audit import AuditLog

# Tables to skip auditing
_SKIP_TABLES = {"audit_log"}


def _serialize_value(val):
    """Convert a value to JSON-serializable form."""
    if val is None:
        return None
    if isinstance(val, (int, float, bool, str)):
        return val
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


def register_audit_hooks(session_factory):
    """Register the after_flush hook on the session factory."""
    event.listen(session_factory, "after_flush", _after_flush)
