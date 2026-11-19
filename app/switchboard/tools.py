# Vendored from SlowBooks Switchboard: sbplive/tools.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The named tools, as plain functions over an ApiClient: server.py registers
# them with their schemas and annotations. Each returns JSON-able data kept
# compact (no PDF, CSV or HTML).
# ============================================================================

import difflib
import re

from app.switchboard.client import (
    ApiClient,
    ApiError,
    reachable,
    unreachable_sentence,
)

# The API the free-form tools reach (api_get, api_schema, the operations
# tools), named in their descriptions with this page. One constant, so the
# address changes in one place.
API_NAME = "the SlowBooks Pro REST API"
API_DOCS_URL = "https://www.slowbookspro.com/docs/api/"
API_REFERENCE = f"{API_NAME} (documented at {API_DOCS_URL})"

# kind -> list route. The first eight are the books' main records.
KINDS = {
    "customers": "/api/customers",
    "vendors": "/api/vendors",
    "items": "/api/items",
    "accounts": "/api/accounts",
    "invoices": "/api/invoices",
    "bills": "/api/bills",
    "expenses": "/api/expenses",
    "journal_entries": "/api/journal",
    "payments": "/api/payments",
    "bill_payments": "/api/bill-payments",
    # Statement lines (feeds and imports), the review queue.
    "bank_transactions": "/api/banking/transactions",
}
# Routes that take their own `limit`, so the page is cut server-side too.
_SERVER_LIMIT = {"invoices", "bills", "bank_transactions", "journal_entries"}


def _kind_path(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r}. Kinds: {', '.join(KINDS)}.")
    return KINDS[kind]


def _spec(client: ApiClient) -> dict:
    if client.spec is None:
        client.spec = client.get("/openapi.json")
    return client.spec


ROLE_PHRASES = {
    "bookkeeper": "works as a bookkeeper (the daily books but not settings or payroll)",
    "accountant": "works as an accountant (reads the books and posts journal "
    "entries)",
    "readonly": "is read-only (it reads the books and changes nothing)",
}


def role_of(client: ApiClient) -> str | None:
    """The token's role, from what the API lets it read.

    No route reports a token's own role, so two reads tell the three roles a
    token can have apart (app/main.py, _role_allows): a read-only token may
    not read the audit log, an accountant's may not read the employee
    directory, and a bookkeeper's reads both. None when the answers fit none
    of them (an older version, or the app not answering)."""

    def refused(path, params=None):
        try:
            client.get(path, params)
        except ApiError as exc:
            return True if exc.status == 403 else None
        return False

    audit = refused("/api/audit", {"limit": 1})
    if audit is True:
        return "readonly"
    if audit is None:
        return None
    employees = refused("/api/employees")
    if employees is True:
        return "accountant"
    return "bookkeeper" if employees is False else None


def context(client: ApiClient) -> dict:
    """Company and app facts to read before working."""
    out: dict = {}
    status = client.get("/api/auth/status")
    out["company"] = status.get("company_name") or ""
    if status.get("accountants_copy"):
        out["accountants_copy"] = status["accountants_copy"]
    settings = client.get("/api/settings")
    out["company_type"] = settings.get("company_type") or ""
    out["closing_date"] = settings.get("closing_date") or None
    entity = settings.get("entity")
    if entity:
        out["entity"] = entity
    # The spec's version is the app's.
    out["app_version"] = (_spec(client).get("info") or {}).get("version", "")
    role = role_of(client)
    if role:
        out["token_role"] = role
        out["token_role_means"] = "The API token " + ROLE_PHRASES[role] + "."
    try:
        out["companies"] = [
            c.get("name") for c in client.get("/api/companies") if isinstance(c, dict)
        ]
    except ApiError:
        pass
    return out


def _resolve(client: ApiClient, node, depth=1):
    """Follow a $ref one level so a request body reads without a second call."""
    if isinstance(node, dict) and "$ref" in node and depth >= 0:
        target = _spec(client)
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {})
        return _resolve(client, target, depth - 1)
    if isinstance(node, dict):
        return {k: _resolve(client, v, depth) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(client, v, depth) for v in node]
    return node


def _item(client: ApiClient, op: dict) -> dict:
    out = {"summary": op.get("summary", "")}
    if op.get("parameters"):
        out["parameters"] = _resolve(client, op["parameters"])
    body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
    if body:
        out["request_body"] = _resolve(client, body.get("schema", {}))
    return out


def api_schema(client: ApiClient, path: str):
    """How to call a route: methods, parameters and request body."""
    if not reachable(path):
        # The schema of a route the server never sends to would only invite
        # the call; say so here as api_get would (operations.py).
        return unreachable_sentence(path)
    paths = _spec(client).get("paths", {})
    if path in paths:
        return {
            method: _item(client, op)
            for method, op in paths[path].items()
            if isinstance(op, dict)
        }
    rows = [
        {
            "path": p,
            "methods": sorted(m for m in item if m in _HTTP),
            "summary": next(
                (o.get("summary", "") for o in item.values() if isinstance(o, dict)),
                "",
            ),
        }
        for p, item in paths.items()
        if p.startswith(path)
    ]
    if rows:
        return rows[:20]
    return f"No route matches {path!r}. Try a shorter prefix such as /api/customers."


_HTTP = {"get", "post", "put", "patch", "delete"}


def search(client: ApiClient, q: str, limit: int = 20) -> dict:
    """Find records by name, number or amount across the books."""
    found = client.get("/api/search", {"q": q})
    out, left = {}, max(1, limit)
    for group, rows in found.items():
        if rows and left > 0:
            out[group] = rows[:left]
            left -= len(out[group])
    return out


def list_records(
    client: ApiClient, kind: str, filters: dict | None = None, limit: int = 50
):
    """List one kind of record; filters pass through as query parameters."""
    path = _kind_path(kind)
    params = dict(filters or {})
    if kind in _SERVER_LIMIT:
        params.setdefault("limit", limit)
    rows = client.get(path, params)
    # Since SlowBooks Pro 2.32.0 the journal list answers a page envelope,
    # {entries, total, skip, limit, start_date, end_date, default_range}, the
    # current fiscal year unless the filters name a date; earlier servers
    # answer a bare list. The tool hands back the rows either way.
    if isinstance(rows, dict) and isinstance(rows.get("entries"), list):
        rows = rows["entries"]
    return rows[:limit] if isinstance(rows, list) else rows


def get_record(client: ApiClient, kind: str, record_id: int):
    """One record by id."""
    path = _kind_path(kind)
    if kind == "bank_transactions":
        raise ValueError(
            "A statement line has no single-record route; use list_records "
            "with filters like {'bank_account_id': 1}."
        )
    return client.get(f"{path}/{int(record_id)}")


def _reports(client: ApiClient) -> dict:
    """name -> parameters, for the plain GET reports (no PDF, CSV or id)."""
    out = {}
    for path, item in _spec(client).get("paths", {}).items():
        op = item.get("get")
        if not path.startswith("/api/reports/") or op is None or "{" in path:
            continue
        if path.endswith(("/pdf", "/csv")):
            continue
        out[path[len("/api/reports/") :]] = [
            {
                "name": p["name"],
                "required": bool(p.get("required")),
                "type": (p.get("schema") or {}).get("type", "string"),
            }
            for p in op.get("parameters", [])
            if p.get("in") == "query"
        ]
    return out


def list_reports(client: ApiClient) -> list:
    """The reports that can be run, with their parameters."""
    return [{"name": n, "params": p} for n, p in sorted(_reports(client).items())]


def report(client: ApiClient, name: str, params: dict | None = None):
    """Run a report by name (see list_reports)."""
    known = _reports(client)
    if name not in known:
        close = difflib.get_close_matches(name, known, n=5, cutoff=0.4)
        hint = f" Closest: {', '.join(close)}." if close else ""
        raise ValueError(f"No report named {name!r}.{hint} Use list_reports.")
    return client.get(f"/api/reports/{name}", params)


# ---------------------------------------------------------------------------
# Writes. Each posts the caller's `data` to the API as-is: the API's strict
# schemas validate it and answer a 422 sentence, which the tool passes on.
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_RETRY_SAME_KEY = (
    "The outcome is unknown: the request may or may not have reached the "
    "books. Retry with the same key and the same data; the API books it once."
)
_CHECK_FIRST = (
    "The outcome is unknown: the request may or may not have reached the "
    "books. Check with list_records or search before retrying."
)


def _write(
    client: ApiClient,
    path: str,
    data,
    *,
    key=None,
    params=None,
    method="POST",
    headers=None,
):
    """Send a write; a lost connection or timeout becomes an unknown-outcome sentence."""
    headers = {**(headers or {}), **({"Idempotency-Key": key} if key else {})}
    headers = headers or None
    try:
        if method == "POST":
            return client.post(path, data, headers=headers, params=params)
        return client.request(method, path, params, data, headers)
    except ApiError as exc:
        if exc.status == 0:
            raise ValueError(
                f"{str(exc.detail).rstrip('.')}. "
                f"{_RETRY_SAME_KEY if key else _CHECK_FIRST}"
            ) from None
        raise


def check_key(key, name="key") -> None:
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise ValueError(
            f"{name} must be a string of 1 to 128 characters from "
            "A-Z a-z 0-9 _ . : -. Use one stable key per intended payment."
        )


def _keyed(client: ApiClient, path: str, data: dict, key: str):
    check_key(key)
    return _write(client, path, data, key=key)


def create_customer(client: ApiClient, data: dict):
    """Create a customer. Required: name. api_schema("/api/customers") has the rest."""
    return _write(client, "/api/customers", data)


def create_vendor(client: ApiClient, data: dict):
    """Create a vendor. Required: name. api_schema("/api/vendors") has the rest."""
    return _write(client, "/api/vendors", data)


def create_item(client: ApiClient, data: dict):
    """Create an item. Required: name, item_type. api_schema("/api/items") has the rest."""
    return _write(client, "/api/items", data)


def create_invoice(client: ApiClient, data: dict):
    """Create an invoice. Required: customer_id, date, lines. api_schema("/api/invoices") has the rest."""
    return _write(client, "/api/invoices", data)


def create_bill(client: ApiClient, data: dict):
    """Create a bill. Required: vendor_id, date, lines. api_schema("/api/bills") has the rest."""
    return _write(client, "/api/bills", data)


def create_expense(client: ApiClient, data: dict):
    """Record a paid expense. Required: date, paid_from_account_id, and either
    amount + expense_account_id or lines. api_schema("/api/expenses") has the rest."""
    return _write(client, "/api/expenses", data)


def create_journal_entry(client: ApiClient, data: dict):
    """Post a manual journal entry. Required: date, description, lines (debits
    must equal credits). api_schema("/api/journal") has the rest."""
    return _write(client, "/api/journal", data)


def record_payment(client: ApiClient, data: dict, key: str):
    """Record a customer payment. Required data: customer_id, date, amount;
    allocations apply it to invoices (api_schema("/api/payments") has the rest).
    key is required: 1 to 128 characters of A-Z a-z 0-9 _ . : -. The same key
    with the same data books once; the same key with other data is refused."""
    return _keyed(client, "/api/payments", data, key)


def record_bill_payment(client: ApiClient, data: dict, key: str):
    """Pay a vendor. Required data: vendor_id, date, amount; allocations apply
    it to bills (api_schema("/api/bill-payments") has the rest). key is
    required: 1 to 128 characters of A-Z a-z 0-9 _ . : -. The same key with the
    same data books once; the same key with other data is refused."""
    return _keyed(client, "/api/bill-payments", data, key)


_VOID_PATHS = {
    "invoices": "/api/invoices",
    "bills": "/api/bills",
    "expenses": "/api/expenses",
    "journal_entries": "/api/journal",
    "payments": "/api/payments",
    "bill_payments": "/api/bill-payments",
}


def void_record(
    client: ApiClient, kind: str, record_id: int, fee_refunded: bool = False
):
    """Void a record with a reversing entry (nothing is deleted). kind: invoices,
    bills, expenses, journal_entries, payments, bill_payments. fee_refunded
    applies to payments only: the provider gave the processing fee back."""
    if kind not in _VOID_PATHS:
        raise ValueError(f"Unknown kind {kind!r}. Kinds: {', '.join(_VOID_PATHS)}.")
    params = {"fee_refunded": "true"} if fee_refunded and kind == "payments" else None
    return _write(
        client, f"{_VOID_PATHS[kind]}/{int(record_id)}/void", None, params=params
    )
