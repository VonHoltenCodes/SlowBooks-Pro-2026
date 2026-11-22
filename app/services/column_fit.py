# ============================================================================
# Column fit for imports: a value longer than its column is cut to the
# column's length and the import says so, instead of failing.
#
# SQLite does not enforce String(n) lengths, so an over-long value from a
# file imported fine there; PostgreSQL refuses the row
# (StringDataRightTruncation) and the whole import answered "nothing was
# saved" — a QuickBooks Online journal line memo over 300 characters did it.
# Every importer runs its writes under ``fit_to_columns``: before each flush,
# each new or changed row has its text columns cut to their mapped length,
# and each cut is a warning naming the record and the field in words.
# ``fit_notes`` gives the same sentences for a record that is not written
# (a dry run), so the dry run's warnings are what the import will do.
# ============================================================================

import re
from contextlib import contextmanager

from sqlalchemy import Enum, String, event, inspect
from sqlalchemy.orm import Session

_SINKS = "_column_fit_sinks"
_LISTENER = "_column_fit_listener"

# A column's name in words where the plain spelling does not read well.
_FIELD_WORDS = {
    ("TransactionLine", "description"): "line memo",
    ("Transaction", "reference"): "reference",
    ("Customer", "qb_name"): "QuickBooks name",
    ("Vendor", "qb_name"): "QuickBooks name",
}

_MODEL_WORDS = {
    "TransactionLine": "Journal line",
    "Transaction": "Journal entry",
    "Item": "Item",
    "Account": "Account",
}


def _short(text, limit: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def field_words(model_name: str, column: str) -> str:
    """A column in words: bill_address1 → "billing address line 1"."""
    words = _FIELD_WORDS.get((model_name, column))
    if words:
        return words
    words = column
    words = re.sub(r"^bill_", "billing_", words)
    words = re.sub(r"^ship_", "shipping_", words)
    words = re.sub(r"(\D)(\d+)$", r"\1 line \2", words)
    return words.replace("_", " ").strip()


def journal_label(reference, entry_date) -> str:
    """A journal entry as a warning names it: its reference and date."""
    when = f" of {entry_date}" if entry_date else ""
    if reference:
        return f"Journal entry '{_short(reference)}'{when}"
    return f"The journal entry{when}"


def line_label(journal: str, description) -> str:
    """One line of a journal entry, by the start of its memo."""
    if description:
        return f"{journal}, line '{_short(description, 40)}'"
    return f"{journal}, a line"


def _model_words(name: str) -> str:
    if name in _MODEL_WORDS:
        return _MODEL_WORDS[name]
    # CamelCase → "Camel case"
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower()
    return spaced[:1].upper() + spaced[1:]


def record_label(obj, session: Session | None = None) -> str:
    """The record a warning names: "Customer 'Acme Supply'"."""
    name = type(obj).__name__
    if name == "TransactionLine":
        txn = None
        if session is not None and obj.transaction_id:
            with session.no_autoflush:
                from app.models.transactions import Transaction

                txn = session.get(Transaction, obj.transaction_id)
        journal = (
            journal_label(txn.reference, txn.date)
            if txn is not None
            else "A journal entry"
        )
        return line_label(journal, obj.description)
    if name == "Transaction":
        return journal_label(obj.reference, obj.date)
    words = _model_words(name)
    label = getattr(obj, "name", None)
    if not label and hasattr(obj, "first_name"):
        label = " ".join(
            p for p in (obj.first_name or "", obj.last_name or "") if p
        ).strip()
    if label:
        return f"{words} '{_short(label)}'"
    return f"A {words.lower()}"


def _text_columns(obj):
    """(attribute key, length) of each String(n) column of the record's
    model: enum columns (a String underneath) are left alone."""
    mapper = inspect(type(obj))
    for attr in mapper.column_attrs:
        for column in attr.columns:
            kind = column.type
            if isinstance(kind, Enum) or not isinstance(kind, String):
                continue
            if kind.length:
                yield attr.key, kind.length


def note(label: str, model_name: str, column: str, length: int) -> str:
    words = field_words(model_name, column)
    verb = "were" if words.endswith("notes") else "was"
    return f"{label} — the {words} {verb} cut to {length} characters"


def fit_record(
    obj, session: Session | None = None, label: str | None = None
) -> list[str]:
    """Cut each over-long text value of the record to its column's length;
    one sentence per cut. Values that are not text are untouched."""
    cuts = []
    for key, length in _text_columns(obj):
        value = obj.__dict__.get(key)
        if isinstance(value, str) and len(value) > length:
            cuts.append((key, length))
    if not cuts:
        return []
    label = label or record_label(obj, session)
    notes = []
    for key, length in cuts:
        setattr(obj, key, obj.__dict__[key][:length])
        notes.append(note(label, type(obj).__name__, key, length))
    return notes


def fit_notes(obj, label: str | None = None) -> list[str]:
    """The sentences ``fit_record`` would give for a record not written
    (the dry run's), named by ``label`` when the record cannot name
    itself (a journal line before its entry exists)."""
    return fit_record(obj, label=label)


def _before_flush(session, _context, _instances):
    sinks = session.info.get(_SINKS) or []
    for obj in list(session.new) + list(session.dirty):
        for sentence in fit_record(obj, session):
            for sink in sinks:
                if sentence not in sink:
                    sink.append(sentence)


@contextmanager
def fit_to_columns(db: Session, warnings: list[str] | None = None):
    """Every row written inside the block fits its columns; each cut is
    added to ``warnings`` (and to every enclosing block's), once."""
    sink = warnings if warnings is not None else []
    sinks = db.info.setdefault(_SINKS, [])
    if not sinks:
        db.info[_LISTENER] = _before_flush
        event.listen(db, "before_flush", _before_flush)
    sinks.append(sink)
    try:
        yield sink
    finally:
        for i, held in enumerate(sinks):
            if held is sink:
                del sinks[i]
                break
        if not sinks:
            event.remove(db, "before_flush", db.info.pop(_LISTENER))


def add_notes(result, notes: list[str]):
    """The cuts said in an import's answer, each once."""
    if not notes or not isinstance(result, dict):
        return result
    warnings = result.get("warnings")
    if warnings is None:
        warnings = result["warnings"] = []
    for sentence in notes:
        if sentence not in warnings:
            warnings.append(sentence)
    return result
