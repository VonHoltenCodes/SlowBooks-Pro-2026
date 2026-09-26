"""Optional structured progress reporting for QBO imports.

The context is confined to the importing thread. Existing service callers
keep their usual results and behavior when no reporter is installed.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from app.services.safe_errors import DataProblem, safe_message

_reporter = ContextVar("qbo_import_reporter", default=None)


@contextmanager
def reporting(reporter):
    token = _reporter.set(reporter)
    try:
        yield
    finally:
        _reporter.reset(token)


def emit(action, message, **fields):
    reporter = _reporter.get()
    if reporter:
        reporter.emit(action, message, **fields)


def stage(entity):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            reporter = _reporter.get()
            if not reporter:
                return function(*args, **kwargs)
            reporter.begin_entity(entity)
            result = function(*args, **kwargs)
            reporter.end_entity(result)
            return result

        return run

    return decorate


def item(qbo_id, label=""):
    reporter = _reporter.get()
    if reporter:
        reporter.begin_item(str(qbo_id or ""), str(label or ""))


def posting(qbo_id, label=""):
    """Restore the source identity while posting an already validated batch."""
    reporter = _reporter.get()
    if reporter:
        reporter.item_id = str(qbo_id or "")
        reporter.item_label = str(label or "")
        reporter.error_code = ""
        reporter.resolved = True
        reporter.emit("post", "Posting validated source item")


def created(qbo_id=None):
    reporter = _reporter.get()
    if reporter:
        reporter.created(str(qbo_id) if qbo_id is not None else None)


def validated(qbo_id=None):
    reporter = _reporter.get()
    if reporter:
        reporter.resolved = True
        reporter.emit("validate", "Validated; awaiting posting", item_id=qbo_id)


def skipped(message="Existing record; no duplicate created"):
    reporter = _reporter.get()
    if reporter:
        reporter.skip(message)


def mapped(entity, qbo_id):
    reporter = _reporter.get()
    if reporter and reporter.matches(entity, qbo_id):
        reporter.emit("map", "Linked to a local record; pending commit")


def existing(entity, qbo_id):
    reporter = _reporter.get()
    # Journals must verify their saved posting before being called a duplicate.
    if reporter and entity != "journal_entry" and reporter.matches(entity, qbo_id):
        reporter.skip()


def error_message(exc, context="QBO import"):
    reporter = _reporter.get()
    if reporter:
        code = getattr(exc, "error_code", None)
        reporter.error_code = (
            str(code)
            if code
            else (
                "IMPORT_VALIDATION"
                if isinstance(exc, DataProblem)
                else (
                    "IMPORT_QUERY_FAILED"
                    if reporter.phase == "query"
                    else "IMPORT_OPERATION_FAILED"
                )
            )
        )
    return safe_message(exc, context)


def append_error(errors, error):
    reporter = _reporter.get()
    if reporter:
        error = {
            **error,
            "code": error.get("code") or reporter.error_code or "IMPORT_VALIDATION",
        }
        reporter.error(error)
    errors.append(error)
