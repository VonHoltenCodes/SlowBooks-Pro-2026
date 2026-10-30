# ============================================================================
# The tools, as plain functions over an ApiClient: the SDK wrapper lives in
# server.py. Each returns JSON-able data kept compact (no PDF, CSV or HTML).
# ============================================================================

import difflib
import re
from typing import Optional

from app.mcp.client import (
    ApiClient,
    ApiError,
    normalise_path,
    reachable,
    unreachable_sentence,
)

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
_SERVER_LIMIT = {"invoices", "bills", "bank_transactions"}
# The most rows one call returns: 50 invoices are already ~20k tokens.
MAX_LIMIT = 500


def _kind_path(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r}. Kinds: {', '.join(KINDS)}.")
    return KINDS[kind]


def _spec(client: ApiClient) -> dict:
    if client.spec is None:
        spec = client.get("/openapi.json")
        if not isinstance(spec, dict):
            return {}  # not the app on the port; nothing cached
        client.spec = spec
    return client.spec


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
    # The spec's version is the app's; /health and /api/system are not
    # reachable from here. No route reports the token's own role, so the key
    # is left out: the API enforces it and answers a refusal with a sentence.
    out["app_version"] = (_spec(client).get("info") or {}).get("version", "")
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


LISTING = 20


def api_schema(client: ApiClient, path: str):
    """How to call a route: methods, parameters and request body. A prefix
    (even /api) lists the routes under it, the first 20 with a count of the
    rest."""
    if path.rstrip("/") == "/api":
        path = "/api/"
    elif not reachable(path):
        # The schema of a route the server never sends to would only invite
        # the call; say so here as api_get would.
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
        if p.startswith(path) and reachable(p)  # a guarded route is not offered
    ]
    if len(rows) > LISTING:
        return rows[:LISTING] + [
            {"more": len(rows) - LISTING, "note": "Narrow the prefix to see the rest."}
        ]
    if rows:
        return rows
    return f"No route matches {path!r}. Try a shorter prefix such as /api/customers."


_HTTP = {"get", "post", "put", "patch", "delete"}


def search(client: ApiClient, q: str, limit: int = 20) -> dict:
    """Find records by name, number or amount across the books."""
    found = client.get("/api/search", {"q": q})
    out, left, dropped = {}, max(1, limit), 0
    for group, rows in found.items():
        if not rows:
            continue
        out[group] = rows[: max(0, left)]
        dropped += len(rows) - len(out[group])
        left -= len(out[group])
        if not out[group]:
            del out[group]
    if dropped:
        out["more"] = dropped  # matches left out: raise limit or narrow q
    return out


def _query_params(client: ApiClient, path: str) -> Optional[list]:
    """The names a route's GET accepts, or None when the spec has no entry."""
    op = (_spec(client).get("paths", {}).get(path) or {}).get("get")
    if not isinstance(op, dict):
        return None
    return [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"]


def list_records(
    client: ApiClient, kind: str, filters: Optional[dict] = None, limit: int = 50
):
    """List one kind of record; filters are the route's own query parameters
    (api_schema on the route lists them; an unknown one is refused, not
    ignored). limit is 1 to 500."""
    path = _kind_path(kind)
    limit = max(1, min(int(limit), MAX_LIMIT))
    params = dict(filters or {})
    for key, value in params.items():
        if isinstance(value, (dict, list)):
            raise ValueError(
                f"Filter {key!r} must be a single value, not a list or object."
            )
    known = _query_params(client, path)
    if known is not None:
        # The API ignores a parameter it does not take, so {"customer": 3}
        # would list every invoice as if it were a filter. Refuse instead.
        unknown = sorted(k for k in params if k not in known)
        if unknown:
            raise ValueError(
                f"{path} does not take {', '.join(unknown)}. "
                f"Filters: {', '.join(known) or 'none'}."
            )
    if kind in _SERVER_LIMIT:
        params.setdefault("limit", limit)
    rows = client.get(path, params)
    if isinstance(rows, list) and len(rows) > limit:
        # Never a silent cut: an agent asked "how many" must not answer 50.
        return {"rows": rows[:limit], "shown": limit, "total": len(rows)}
    return rows


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


def _scalar_params(params: Optional[dict], known: Optional[list], what: str) -> dict:
    """Query parameters checked against the route: an unknown name or a
    list/object value is refused, since the API would ignore it silently.
    known=None (no spec entry for the route) checks the values only."""
    params = dict(params or {})
    for key, value in params.items():
        if isinstance(value, (dict, list)):
            raise ValueError(f"{key!r} must be a single value, not a list or object.")
    if known is None:
        return params
    unknown = sorted(k for k in params if k not in known)
    if unknown:
        raise ValueError(
            f"{what} does not take {', '.join(unknown)}. "
            f"Parameters: {', '.join(known) or 'none'}."
        )
    return params


def report(client: ApiClient, name: str, params: Optional[dict] = None):
    """Run a report by name (see list_reports for its parameters; an unknown
    one is refused, not ignored)."""
    known = _reports(client)
    if name not in known:
        close = difflib.get_close_matches(name, known, n=5, cutoff=0.4)
        hint = f" Closest: {', '.join(close)}." if close else ""
        raise ValueError(f"No report named {name!r}.{hint} Use list_reports.")
    params = _scalar_params(params, [p["name"] for p in known[name]], f"Report {name}")
    return client.get(f"/api/reports/{name}", params)


# Reads that made a link on app versions before 2.31.2 (#659): a read tool
# never makes one, so they are refused here whatever the app's version.
_MINTING_READS = re.compile(
    r"^/api/(payments/payment-link/\d+|employees/\d+/portal-token)$"
)


def _spec_params_for(client: ApiClient, clean: str) -> Optional[list]:
    """The GET query parameters of the spec route that matches `clean`,
    templated segments included; None when no route matches."""
    paths = _spec(client).get("paths", {})
    if clean in paths:
        return _query_params(client, clean)
    for spec_path in paths:
        if "{" not in spec_path:
            continue
        pattern = (
            "^"
            + re.sub(
                r"\{[^/}]+\}",
                r"[^/]+",
                re.escape(spec_path).replace(r"\{", "{").replace(r"\}", "}"),
            )
            + "$"
        )
        if re.match(pattern, clean):
            return _query_params(client, spec_path)
    return None


def api_get(client: ApiClient, path: str, params: Optional[dict] = None):
    """Read-only GET of any other API route that answers JSON (see api_schema
    for the route and its parameters; the query goes in params, and a
    parameter the route does not take is refused, not ignored)."""
    clean = normalise_path(path) if isinstance(path, str) else ""
    if clean == "/openapi.json":
        raise ValueError("The spec is too large to return; use api_schema on a route.")
    if _MINTING_READS.match(clean):
        raise ValueError(
            f"{clean} is not read here: on app versions before 2.31.2 that read "
            "made the link. Links are made in the app."
        )
    known = _spec_params_for(client, clean) if clean else None
    params = _scalar_params(params, known, clean or path)
    result = client.get(path, params)
    if isinstance(result, str):
        raise ValueError(
            f"api_get returns JSON only; {path} answered text (a PDF, CSV or page). "
            "Open that in the app."
        )
    return result


# ---------------------------------------------------------------------------
# Writes. Each posts the caller's `data` to the API as-is: the API's strict
# schemas validate it and answer a 422 sentence, which the tool relays.
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


# The API's 409s name a URL parameter; here it is a tool argument.
_OVERRIDE_WORDING = (
    (
        "Pass ?force=true to create anyway.",
        "Call again with force: true once the user agrees.",
    ),
    ("?allow_duplicate_check_number=true", "allow_duplicate_check_number: true"),
    ("allow_duplicate_check_number=true", "allow_duplicate_check_number: true"),
)


def _write(client: ApiClient, path: str, data, *, key=None, params=None):
    """POST a write; a lost connection or timeout becomes an unknown-outcome sentence."""
    headers = {"Idempotency-Key": key} if key else None
    try:
        return client.post(path, data, headers=headers, params=params)
    except ApiError as exc:
        if exc.status == 0 and exc.sent:
            raise ValueError(
                f"{exc.detail} {_RETRY_SAME_KEY if key else _CHECK_FIRST}"
            ) from None
        if exc.status == 409:
            for theirs, ours in _OVERRIDE_WORDING:
                exc.detail = exc.detail.replace(theirs, ours)
                if isinstance(exc.payload, dict) and isinstance(
                    exc.payload.get("message"), str
                ):
                    exc.payload["message"] = exc.payload["message"].replace(
                        theirs, ours
                    )
            code = (exc.payload or {}).get("error") or (exc.payload or {}).get("code")
            if code == "duplicate_check_number" and "allow_duplicate" not in exc.detail:
                exc.detail += (
                    " Call again with allow_duplicate_check_number: true once the "
                    "user agrees."
                )
        raise


_DUP_CHECK = {"allow_duplicate_check_number": "true"}


def _keyed(client: ApiClient, path: str, data: dict, key: str, params=None):
    if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
        raise ValueError(
            "key must be a string of 1 to 128 characters from A-Z a-z 0-9 _ . : -. "
            "Use one stable key per intended payment."
        )
    return _write(client, path, data, key=key, params=params)


def create_customer(client: ApiClient, data: dict, force: bool = False):
    """Create a customer. Required: name. api_schema("/api/customers") has the rest.
    Set force only after the user says yes to the similar-name 409's question."""
    return _write(
        client, "/api/customers", data, params={"force": "true"} if force else None
    )


def create_vendor(client: ApiClient, data: dict, force: bool = False):
    """Create a vendor. Required: name. api_schema("/api/vendors") has the rest.
    Set force only after the user says yes to the similar-name 409's question."""
    return _write(
        client, "/api/vendors", data, params={"force": "true"} if force else None
    )


def create_item(client: ApiClient, data: dict):
    """Create an item. Required: name, item_type. api_schema("/api/items") has the rest."""
    return _write(client, "/api/items", data)


def create_invoice(client: ApiClient, data: dict):
    """Create an invoice. Required: customer_id, date, lines. api_schema("/api/invoices") has the rest.
    The credit-limit 409 is answered by allow_over_credit_limit: true inside data,
    only after the user says yes."""
    return _write(client, "/api/invoices", data)


def create_bill(client: ApiClient, data: dict):
    """Create a bill. Required: vendor_id, date, lines. api_schema("/api/bills") has the rest."""
    return _write(client, "/api/bills", data)


def create_expense(
    client: ApiClient, data: dict, allow_duplicate_check_number: bool = False
):
    """Record a paid expense. Required: date, paid_from_account_id, and either
    amount + expense_account_id or lines. api_schema("/api/expenses") has the rest.
    Set allow_duplicate_check_number only after the user says yes to the
    duplicate-check-number 409's question."""
    return _write(
        client,
        "/api/expenses",
        data,
        params=_DUP_CHECK if allow_duplicate_check_number else None,
    )


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


def record_bill_payment(
    client: ApiClient, data: dict, key: str, allow_duplicate_check_number: bool = False
):
    """Pay a vendor. Required data: vendor_id, date, amount; allocations apply
    it to bills (api_schema("/api/bill-payments") has the rest). key is
    required: 1 to 128 characters of A-Z a-z 0-9 _ . : -. The same key with the
    same data books once; the same key with other data is refused.
    Set allow_duplicate_check_number only after the user says yes to the
    duplicate-check-number 409's question."""
    return _keyed(
        client,
        "/api/bill-payments",
        data,
        key,
        params=_DUP_CHECK if allow_duplicate_check_number else None,
    )


_VOID_PATHS = {
    "invoices": "/api/invoices",
    "bills": "/api/bills",
    "expenses": "/api/expenses",
    "journal_entries": "/api/journal",
    "payments": "/api/payments",
    "bill_payments": "/api/bill-payments",
}


# A journal entry the server will void: one that is a journal of its own.
# A document's posting (invoice, bill, payment, ...) is voided through the
# document, which reverses the posting with it; voiding the posting here as
# well would take it off the books twice.
_OWN_JOURNAL_SOURCES = {
    "",
    "manual",
    "qbo_journal",
    "qbo_ledger",
    "iif_import",
    "opening_balance",
    "accountants_copy",
}
_SOURCE_KIND = {
    "invoice": "invoices",
    "bill": "bills",
    "expense": "expenses",
    "check": "expenses",
    "payment": "payments",
    "bill_payment": "bill_payments",
}


def void_record(
    client: ApiClient, kind: str, record_id: int, fee_refunded: bool = False
):
    """Void a record with a reversing entry (nothing is deleted). kind: invoices,
    bills, expenses, journal_entries, payments, bill_payments. A journal entry
    that is a document's posting is refused: void the document. fee_refunded
    applies to payments only: the provider gave the processing fee back."""
    if kind not in _VOID_PATHS:
        raise ValueError(f"Unknown kind {kind!r}. Kinds: {', '.join(_VOID_PATHS)}.")
    if fee_refunded and kind != "payments":
        raise ValueError("fee_refunded applies to payments only.")
    if kind == "journal_entries":
        entry = client.get(f"/api/journal/{int(record_id)}")
        source = (entry.get("source_type") or "") if isinstance(entry, dict) else ""
        own = (
            source in _OWN_JOURNAL_SOURCES
            or source.endswith("_import")
            or source.endswith("_void")  # a reversal: the route says so itself
        )
        if not own:
            noun = source.replace("_", " ")
            what = ("an " if noun[0] in "aeiou" else "a ") + noun
            if source in _SOURCE_KIND:
                hint = (
                    f" Find the {noun} by its number and use "
                    f"void_record(kind={_SOURCE_KIND[source]!r}, record_id=<its id>)."
                )
            else:
                hint = " Void it in the app, from its own record."
            raise ValueError(
                f"Journal entry {int(record_id)} is the posting of {what}, not "
                f"a journal of its own; voiding it here would reverse it twice.{hint}"
            )
    params = {"fee_refunded": "true"} if fee_refunded and kind == "payments" else None
    return _write(
        client, f"{_VOID_PATHS[kind]}/{int(record_id)}/void", None, params=params
    )
