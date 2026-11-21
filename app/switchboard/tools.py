# Vendored from SlowBooks Switchboard: sbplive/tools.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The named tools, as plain functions over an ApiClient: server.py registers
# them with their schemas and annotations. Each returns JSON-able data kept
# compact (no PDF, CSV or HTML).
# ============================================================================

import difflib
import re
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

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
    return client.openapi()


# ---------------------------------------------------------------------------
# Which books the call works. The registry sets this around every tool call
# (registry.Registry.call), in the thread that runs the call, so two calls on
# two threads never see each other's books. A tool reads it only to name the
# books in what it tells the assistant to do next.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Called:
    books: str | None  # the handle; None when the connection names none
    name: str  # the company's name, as the connection keeps it
    file: str  # the company's file, "" when not known
    many: bool  # the connection reaches more than one company


CALLED: ContextVar[Called | None] = ContextVar("sbplive_called", default=None)


def named_books() -> str | None:
    """The handle to name in a next step, on a connection of several
    companies; None when one company needs no naming."""
    called = CALLED.get()
    if called is None or not called.many or not called.books:
        return None
    return called.books


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
    called = CALLED.get()
    if called is not None and called.books:
        out["books"] = called.books
        if called.file:
            out["company_file"] = called.file
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
    """name -> parameters, for the plain GET reports (no PDF, CSV, print
    page or id)."""
    out = {}
    for path, item in _spec(client).get("paths", {}).items():
        op = item.get("get")
        if not path.startswith("/api/reports/") or op is None or "{" in path:
            continue
        if path.endswith(("/pdf", "/csv", "/print")):
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
_UNKNOWN = "The outcome is unknown: the request may or may not have reached the books."
_RETRY_SAME_KEY = (
    f"{_UNKNOWN} Retry with the same key and the same data; the API books it once."
)
_CHECK_FIRST = f"{_UNKNOWN} Check with list_records or search before retrying."


def _retry_same_key() -> str:
    books = named_books()
    if books is None:
        return _RETRY_SAME_KEY
    return (
        f"{_UNKNOWN} Retry with the same key, the same data and the same books "
        f"({books}); the API books it once."
    )


def _check_first() -> str:
    books = named_books()
    if books is None:
        return _CHECK_FIRST
    return (
        f"{_UNKNOWN} Check with list_records or search, with books: {books}, "
        "before retrying."
    )


def _with_books(text: str) -> str:
    """' and books: <handle>' on a connection of several companies."""
    books = named_books()
    return f"{text} and books: {books}" if books else text


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
                f"{_retry_same_key() if key else _check_first()}"
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


# ---------------------------------------------------------------------------
# Ask before a bare create. A create of one of the main records that carries
# only what the API requires is not sent at first: the answer lists the empty
# fields worth having, as the app's forms name them, for the assistant to ask
# the person about. A second call with asked_user creates it; a record that
# already fills at least half of its fields (rounded down, at least one) is
# created at once. Every create's answer says, in left_blank, what is still
# empty. The gate is here, in Switchboard, and nowhere in the API: the app's
# screens, imports and API tokens used directly are unaffected.
# ---------------------------------------------------------------------------

_LINES = "@lines"  # every line has an item or a description


@dataclass(frozen=True)
class AskFirst:
    record: str  # "a customer"
    required: tuple  # the API's own required fields; one missing, it decides
    fields: tuple  # (label, keys): given when any key has a value
    route: str = ""

    @property
    def need(self) -> int:
        return max(1, len(self.fields) // 2)


_LINE_FIELD = ("An item or description on each line", (_LINES,))
_TERMS = ("Terms or Due Date", ("terms", "due_date", "payment_term_id"))
ASK_FIRST = {
    "/api/customers": AskFirst(
        "a customer",
        ("name",),
        (
            ("Company", ("company",)),
            ("Email", ("email",)),
            ("Phone", ("phone", "mobile")),
            ("Billing address", ("bill_address1", "bill_city", "bill_zip")),
            ("Terms", ("terms",)),
            ("Sales Tax (taxable or exempt)", ("is_taxable",)),
        ),
    ),
    "/api/vendors": AskFirst(
        "a vendor",
        ("name",),
        (
            ("Email", ("email",)),
            ("Phone", ("phone",)),
            ("Address", ("address1", "city", "zip")),
            ("Terms", ("terms",)),
            ("Tax ID", ("tax_id",)),
            ("1099 Vendor", ("is_1099_vendor",)),
        ),
    ),
    "/api/items": AskFirst(
        "an item",
        ("name", "item_type"),
        (
            ("Description", ("description",)),
            ("Rate (sell price)", ("rate",)),
            ("Cost", ("cost",)),
            ("Income Account", ("income_account_id",)),
            ("Expense or COGS Account", ("expense_account_id",)),
        ),
    ),
    "/api/employees": AskFirst(
        "an employee",
        ("first_name", "last_name"),
        (
            ("Email", ("email",)),
            ("Address", ("address1", "city", "zip")),
            ("Hire Date", ("hire_date",)),
            ("Pay Type", ("pay_type",)),
            ("Pay Rate", ("pay_rate",)),
            ("Pay Frequency", ("pay_frequency",)),
            ("Filing Status", ("filing_status",)),
            ("Work State", ("work_state",)),
        ),
    ),
    "/api/accounts": AskFirst(
        "an account",
        ("name", "account_type"),
        (
            ("Number", ("account_number",)),
            ("Description", ("description",)),
            ("Detail Type", ("detail_type",)),
        ),
    ),
    "/api/invoices": AskFirst(
        "an invoice",
        ("customer_id", "date", "lines"),
        (_TERMS, ("PO #", ("po_number",)), _LINE_FIELD),
    ),
    "/api/bills": AskFirst(
        "a bill",
        ("vendor_id", "date", "lines"),
        (("Bill Number", ("bill_number", "ref_number")), _TERMS, _LINE_FIELD),
    ),
    "/api/estimates": AskFirst(
        "an estimate",
        ("customer_id", "date", "lines"),
        (
            ("Expiration Date", ("expiration_date",)),
            ("Terms, inclusions and exclusions", ("terms",)),
            _LINE_FIELD,
        ),
    ),
    "/api/purchase-orders": AskFirst(
        "a purchase order",
        ("vendor_id", "date", "lines"),
        (
            ("Expected Date", ("expected_date",)),
            ("Notes", ("notes",)),
            _LINE_FIELD,
        ),
    ),
    # paid_from_account_id is required by the API, but its absence is not
    # left to the API's 422: paid_from_hold names the account the app's
    # form would start at, for the assistant to confirm.
    "/api/expenses": AskFirst(
        "an expense",
        ("date",),
        (
            ("Payee or vendor", ("payee", "vendor_id")),
            ("Paid from account", ("paid_from_account_id",)),
            ("Memo", ("memo",)),
            ("Reference (check number)", ("reference",)),
        ),
    ),
}

ASKED_USER = (
    "True only after you asked the user for the empty fields an earlier "
    "call listed (ask_first), or they said to create the record as it is. It "
    "says you asked; it adds nothing to the record."
)


def _has(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True  # a number or true/false, sent on purpose


def _lines_described(data: dict) -> bool:
    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        return False
    return all(
        isinstance(line, dict)
        and (_has(line.get("item_id")) or _has(line.get("description")))
        for line in lines
    )


def _given(data: dict, keys: tuple) -> bool:
    if keys == (_LINES,):
        return _lines_described(data)
    return any(_has(data.get(k)) for k in keys)


def left_blank(rule: AskFirst, data: dict) -> list:
    """The labels of the rule's fields that data leaves empty."""
    return [label for label, keys in rule.fields if not _given(data, keys)]


def _field_names(keys: tuple) -> str:
    if keys == (_LINES,):
        return "lines[].item_id or lines[].description"
    return " or ".join(keys)


def body_fields(client: ApiClient, route: str):
    """The field names POST route takes, from the app's own document; None
    when the document does not say."""
    try:
        op = _spec(client).get("paths", {}).get(route, {}).get("post") or {}
    except ApiError:
        return None
    body = (op.get("requestBody") or {}).get("content", {}).get("application/json")
    schema = _resolve(client, (body or {}).get("schema") or {}, depth=2)
    props = schema.get("properties") if isinstance(schema, dict) else None
    return set(props) if isinstance(props, dict) else None


def ask_first(route: str, data, asked_user: bool, tool: str, known=None, tax=None):
    """The ask-first answer for a bare create, or None to send it. known is
    the set of fields the route takes, when the app's document says; tax is
    sales_tax's plan, whose ask adds the sales tax rate to the empty fields."""
    rule = ASK_FIRST.get(route)
    if rule is None or asked_user is True or not isinstance(data, dict):
        return None
    if not all(_has(data.get(k)) for k in rule.required):
        return None  # the API says what is missing, in its own words
    if known is not None and set(data) - known:
        return None  # nor would it take an unknown field: its 422 says so
    empty = left_blank(rule, data)
    if len(rule.fields) - len(empty) >= rule.need:
        return None
    rows = [
        {"label": label, "field": _field_names(keys)}
        for label, keys in rule.fields
        if label in empty
    ]
    if tax is not None and tax["ask"]:
        empty.append(TAX_LABEL)
        rows.append({"label": TAX_LABEL, "field": "tax_rate"})
    labels = ", ".join(empty)
    books = named_books()
    if books:
        again = (
            f"Then call {tool} again with what they gave, asked_user: true and "
            f"books: {books}, or with asked_user: true and books: {books} alone "
            "if they want it created as it is."
        )
    else:
        again = (
            f"Then call {tool} again with what they gave and asked_user: true, "
            "or with asked_user: true alone if they want it created as it is."
        )
    return {
        "created": False,
        "ask_first": True,
        "record": rule.record.split(" ", 1)[1],
        "empty": rows,
        "next": (
            f"Nothing was created yet: {rule.record} with this little on it "
            f"may need checking first. Ask the user whether they have any of "
            f"these: {labels}. Do not make any up. {again}"
        ),
    }


# ---------------------------------------------------------------------------
# Sales tax on a new sales document. The app's own forms start a new invoice,
# estimate, sales receipt or credit memo at the company's Default Tax Rate
# (Settings), and a credit memo for an invoice at that invoice's rate. The
# API does the same from SlowBooks Pro 2.32.0; before 2.32.0 it took a
# missing tax_rate as 0%. So when the assistant gives no tax_rate,
# Switchboard sends what the form would and says so in the answer
# (tax_rate_source, tax_note); a tax_rate the assistant gives, 0 included, is
# always sent as given.
#
# A document can carry a rate and still be charged no tax: the app charges
# none to a non-taxable customer, on a document marked tax_exempt, or (from
# 2.32.0) on an invoice, estimate or sales receipt whose job is tax exempt
# and whose own switch was not sent. The answer then says that no sales tax
# was charged and why, never that the default rate was applied. The saved
# document is the evidence: its tax_exempt where the app answers one, and
# its tax_amount. A credit memo for an invoice has no switch of its own: it
# gives back no tax when that invoice was exempt or the customer is
# non-taxable, and its own tax_amount confirms it. With no default rate (0%)
# and nothing exempt, "Sales tax rate" is one of the fields to ask about.
#
# When the company's default cannot be read, no rate is sent and the app
# decides (0% before 2.32.0, its own default from 2.32.0): the answer says
# what the saved document carries.
# ---------------------------------------------------------------------------

SALES_TAX_ROUTES = (
    "/api/invoices",
    "/api/estimates",
    "/api/sales-receipts",
    "/api/credit-memos",
)
TAX_LABEL = "Sales tax rate"
_RATE_PLACES = Decimal("0.000001")  # the API keeps a rate to six places


def _percent(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) + "%"


def _default_percent(client: ApiClient):
    """The company's Default Tax Rate as a percent, or None unreadable."""
    try:
        settings = client.get("/api/settings")
    except ApiError:
        return None
    raw = settings.get("default_tax_rate") if isinstance(settings, dict) else None
    try:
        pct = Decimal(str(raw if raw not in (None, "") else "0"))
    except InvalidOperation:
        return None
    return pct if pct.is_finite() and 0 <= pct <= 100 else None


def _record_id(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, str)) and value != ""


def _exempt_reason(client: ApiClient, data: dict):
    """Why the app charges this document no tax whatever its rate, as far as
    the request and its customer say, or None."""
    if data.get("tax_exempt") is True:
        return "the document is marked tax exempt"
    cid = data.get("customer_id")
    if not _record_id(cid):
        return None
    try:
        customer = client.get(f"/api/customers/{cid}")
    except ApiError:
        return None  # the API says what is wrong with the customer
    if isinstance(customer, dict) and customer.get("is_taxable") is False:
        name = customer.get("name") or "the customer"
        return f"{name} is non-taxable (reseller or exempt)"
    return None


def _exempt_job(client: ApiClient, route: str, data: dict):
    """The reason when the document's job is tax exempt and so decides for
    it, or None. A job decides only where the document has a switch of its
    own (a credit memo has none) and the request left the switch out; an app
    whose jobs have no is_taxable has no exempt jobs."""
    if route == "/api/credit-memos" or data.get("tax_exempt") is not None:
        return None
    jid = data.get("job_id")
    if not _record_id(jid):
        return None
    try:
        job = client.get(f"/api/jobs/{jid}")
    except ApiError:
        return None  # the API says what is wrong with the job
    if isinstance(job, dict) and job.get("is_taxable") is False:
        name = job.get("name")
        return f"the job {name} is tax exempt" if name else "its job is tax exempt"
    return None


def _credited_exempt(client: ApiClient, data: dict):
    """Why a credit memo for an invoice gives back no tax, or None: its
    customer is non-taxable, or the invoice it credits is tax exempt
    (2.32.0 on; an earlier app's invoice has no tax_exempt)."""
    reason = _exempt_reason(client, {"customer_id": data.get("customer_id")})
    oid = data.get("original_invoice_id")
    if reason or not _record_id(oid):
        return reason
    try:
        invoice = client.get(f"/api/invoices/{oid}")
    except ApiError:
        return None  # the API says what is wrong with the invoice
    if isinstance(invoice, dict) and invoice.get("tax_exempt") is True:
        return "the invoice being credited is tax exempt"
    return None


_UNREAD = (
    "No tax_rate was given and the company's Default Tax Rate could not be "
    "read, so no rate was sent"
)


def sales_tax(client: ApiClient, route: str, data):
    """The plan for tax_rate on a new sales document sent without one:
    {"rate": the fraction to send or None (with "shown", the percent),
    "source", "note", "ask"}; None
    when there is nothing to do (another record, or a tax_rate given). A plan
    with a rate also says why no tax may be charged at it ("exempt", from the
    request and the customer; "exempt_by_job", for the saved document to
    confirm) and the note for a document that was taxed ("taxed_note"). A
    credit memo for an invoice says why it may give back no tax
    ("credited"); a plan made without the company's default says so
    ("unread") for the saved document to settle."""
    if route not in SALES_TAX_ROUTES or not isinstance(data, dict):
        return None
    if data.get("tax_rate") is not None:
        return None  # the assistant's rate, 0 included, is sent as given
    if route == "/api/credit-memos" and _has(data.get("original_invoice_id")):
        return {
            "rate": None,
            "source": "the credited invoice's rate",
            "note": (
                "No tax_rate was given, so SlowBooks Pro used the rate of the "
                "invoice being credited, as its own form does: the tax goes "
                "back the way it was charged."
            ),
            "ask": False,
            "credited": _credited_exempt(client, data),
        }
    pct = _default_percent(client)
    certain = _exempt_reason(client, data)
    by_job = None if certain else _exempt_job(client, route, data)
    exempt = certain or by_job
    if pct is None:
        return {
            "rate": None,
            "source": "none: the company's default rate could not be read",
            "note": f"{_UNREAD}. Ask the user what rate applies.",
            "ask": exempt is None,
            "unread": True,
            "exempt": certain,
            "exempt_by_job": by_job,
        }
    if pct > 0:
        shown = _percent(pct)
        taxed = (
            f"No tax_rate was given, so SlowBooks Pro used the company's "
            f"default sales tax rate, {shown}, as its own form does. Tell "
            "the user; if this sale is taxed at another rate, change the "
            "document's tax_rate (a fraction: 0.0159 is 1.59%)."
        )
        return {
            "rate": (pct / 100).quantize(_RATE_PLACES),
            "shown": shown,
            "source": f"company default ({shown})",
            "note": _no_tax_note(shown, certain) if certain else taxed,
            "taxed_note": taxed,
            "exempt": certain,
            "exempt_by_job": by_job,
            "ask": False,
        }
    if exempt:
        return {
            "rate": None,
            "source": "none: no sales tax applies",
            "note": f"No tax_rate was given; {exempt}, so no sales tax is charged.",
            "ask": False,
        }
    return {
        "rate": None,
        "source": "none: the company's default rate is 0%",
        "note": (
            "No tax_rate was given and the company's Default Tax Rate is 0%, "
            "so no sales tax was charged. Ask the user whether this sale is "
            "taxed, and at what rate."
        ),
        "ask": True,
    }


def _no_tax_note(shown: str, reason: str) -> str:
    return (
        f"No tax_rate was given, so the document carries the company's "
        f"default rate, {shown}, as SlowBooks Pro's form does; {reason}, so no "
        "sales tax is charged. Tell the user that no sales tax was charged "
        f"and why, not that {shown} was applied."
    )


def _no_tax_reason(route: str, tax: dict, record: dict):
    """Why a document sent with the default rate was charged no sales tax,
    or None when it was taxed at that rate. The saved document decides: its
    tax_exempt where the app answers one (2.32.0 on; a sales receipt's is on
    its invoice), and a tax_amount that is not zero means it was taxed
    whatever the request suggested. A job's exemption counts only when the saved
    document says it is exempt."""
    saved = record.get("invoice") if route == "/api/sales-receipts" else record
    saved = saved if isinstance(saved, dict) else {}
    switch = saved.get("tax_exempt")
    if switch is True:
        reason = (
            tax["exempt"]
            or tax["exempt_by_job"]
            or "SlowBooks Pro saved the document as tax exempt"
        )
    elif switch is False:
        reason = None
    else:
        reason = tax["exempt"]
    return reason if reason and _no_tax_on(saved) else None


def _no_tax_on(saved: dict) -> bool:
    """Whether the saved document's tax_amount is zero (or not a number)."""
    try:
        return Decimal(str(saved.get("tax_amount") or "0")) == 0
    except InvalidOperation:
        return True


def _unread_default(route: str, tax: dict, record: dict):
    """(source, note, the defaulted value or None, whether to ask) for a
    document created without a rate because the company's default could not
    be read. The app chose the rate, so the saved document says which."""
    source = tax["source"]
    reason = _no_tax_reason(route, tax, record)
    if reason:
        note = (
            f"{_UNREAD}; {reason}, so no sales tax is charged. Tell the user "
            "that no sales tax was charged and why."
        )
        return f"{source}; no sales tax was charged", note, None, False
    saved = record.get("invoice") if route == "/api/sales-receipts" else record
    raw = saved.get("tax_rate") if isinstance(saved, dict) else None
    try:
        rate = None if raw in (None, "") or isinstance(raw, bool) else Decimal(str(raw))
    except InvalidOperation:
        rate = None
    if rate is None or not rate.is_finite() or not 0 <= rate <= 1:
        return source, tax["note"], None, True
    if rate == 0:
        note = (
            f"{_UNREAD} and the document has a 0% sales tax rate. Ask the "
            "user what rate applies."
        )
        return source, note, None, True
    shown = _percent(rate * 100)
    source = (
        f"SlowBooks Pro's default ({shown}); the company's default rate could "
        "not be read"
    )
    note = (
        f"{_UNREAD} and SlowBooks Pro used its own default, {shown}. Tell the "
        "user; if this sale is taxed at another rate, change the document's "
        "tax_rate (a fraction: 0.0159 is 1.59%)."
    )
    return source, note, f"{shown} (SlowBooks Pro's default)", False


def with_tax_rate(data, tax):
    """data with the plan's rate in it (a number, as the API's schema says)."""
    if tax is None or tax["rate"] is None or not isinstance(data, dict):
        return data
    return {**data, "tax_rate": float(tax["rate"])}


def _shown(label: str, value) -> str:
    if isinstance(value, bool):
        if label.startswith("Sales Tax"):
            return "taxable" if value else "exempt"
        return "yes" if value else "no"
    return str(value)


def _defaulted_value(label: str, keys: tuple, answer: dict):
    """What the app filled in for a field the assistant did not give, from
    the created record; None when it is still empty there."""
    if keys == (_LINES,):
        return None
    present = [(k, answer.get(k)) for k in keys if _has(answer.get(k))]
    if not present:
        return None
    # an id (payment_term_id) says nothing to a person beside the words
    named = [(k, v) for k, v in present if not k.endswith("_id")]
    present = named or present
    if len(present) == 1:
        return _shown(label, present[0][1])
    return ", ".join(f"{k.replace('_', ' ')} {_shown(label, v)}" for k, v in present)


def with_left_blank(route: str, data, answer, tax=None, fallbacks=None):
    """A create's answer with left_blank (the rule's fields still empty on
    the record) and defaulted (those the assistant did not give that
    SlowBooks Pro filled with its default: [{label, value}]), and, for a
    sales document sent without a tax_rate, tax_rate_source and tax_note."""
    rule = ASK_FIRST.get(route)
    if (rule is None and tax is None) or not isinstance(data, dict):
        return answer
    record = answer if isinstance(answer, dict) else {}
    blank, defaulted = [], []
    for label, keys in rule.fields if rule else ():
        if _given(data, keys):
            continue
        value = _defaulted_value(label, keys, record)
        if value is None and fallbacks:
            value = fallbacks.get(label)
        if value is None:
            blank.append(label)
        else:
            defaulted.append({"label": label, "value": value})
    extra: dict = {}
    if tax is not None:
        source, note = tax["source"], tax["note"]
        if tax["rate"] is not None:
            shown = tax["shown"]
            value = f"{shown} (the company's default)"
            reason = _no_tax_reason(route, tax, record)
            note = tax["taxed_note"]
            if reason:
                # the rate is on the document, and no tax was charged at it
                value = f"{shown} (the company's default; no sales tax was charged)"
                source = f"company default ({shown}); no sales tax was charged"
                note = _no_tax_note(shown, reason)
            defaulted.append({"label": TAX_LABEL, "value": value})
        elif tax.get("unread"):
            source, note, value, ask = _unread_default(route, tax, record)
            if value is not None:
                defaulted.append({"label": TAX_LABEL, "value": value})
            elif ask:
                blank.append(TAX_LABEL)
        elif tax.get("credited") and "tax_amount" in record and _no_tax_on(record):
            # the invoice's rate is on the memo, and no tax went back at it
            source = f"{source}; no sales tax was charged"
            note = (
                "No tax_rate was given, so the credit memo carries the rate "
                "of the invoice being credited, as SlowBooks Pro's form does; "
                f"{tax['credited']}, so no sales tax is given back. Tell the "
                "user that no sales tax was credited and why, not that tax "
                "was given back."
            )
        elif tax["ask"]:
            blank.append(TAX_LABEL)
        # SlowBooks Pro may answer with a note of its own on the document (a
        # missing or expired exemption certificate): it is kept, in front of
        # ours, never written over
        theirs = record.get("tax_note")
        if isinstance(theirs, str) and theirs.strip() and theirs.strip() not in note:
            note = f"{theirs.strip()} {note}"
        extra = {"tax_rate_source": source, "tax_note": note}
    out = {"left_blank": blank, "defaulted": defaulted, **extra}
    if isinstance(answer, dict):
        return {**answer, **out}
    return {"created": True, "answer": answer, **out}


# ---------------------------------------------------------------------------
# The accounts SlowBooks Pro uses when a record leaves one out. The app finds
# these by their number in its own registry of control accounts: a control
# account's number cannot be changed (the app refuses), so the number is the
# app's own key; the name shown is read from the company's chart, never
# assumed.
# ---------------------------------------------------------------------------

MONEY_ROUTES = ("/api/payments", "/api/bill-payments")
UNDEPOSITED_FUNDS = "1200"  # a payment received with no deposit account
CHECKING = "1000"  # a bill payment with no pay-from account
DEFAULT_INCOME = "4000"  # a sales line whose item names no income account


def _chart(client: ApiClient) -> list:
    try:
        rows = client.get("/api/accounts")
    except ApiError:
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def account_label(row) -> str:
    """ "4000 - Service Income", or the name alone with no number."""
    if not isinstance(row, dict):
        return ""
    number, name = row.get("account_number"), row.get("name") or ""
    return f"{number} - {name}" if number else name


def _by_id(chart: list, account_id):
    return next((r for r in chart if r.get("id") == account_id), None)


def _control(chart: list, number: str):
    return next(
        (
            r
            for r in chart
            if r.get("account_number") == number and r.get("is_control") is not False
        ),
        None,
    )


def _first_cogs(chart: list):
    cogs = [
        r
        for r in chart
        if r.get("account_type") == "cogs" and r.get("is_active") is not False
    ]
    cogs.sort(key=lambda r: str(r.get("account_number") or ""))
    return cogs[0] if cogs else None


def default_paid_from(client: ApiClient):
    """The account the app's Enter Expense form starts at: Settings' default
    deposit account when it is an active bank account, else the first
    active one named Checking, else the first bank account; None if none."""
    chart = _chart(client)
    banks = [r for r in chart if r.get("bank_kind") and r.get("is_active") is not False]
    if not banks:  # an older app, or a chart without bank kinds
        banks = [r for r in chart if r.get("account_number") == CHECKING]
    try:
        settings = client.get("/api/settings")
    except ApiError:
        settings = {}
    chosen = ""
    if isinstance(settings, dict):
        chosen = str(settings.get("default_deposit_account_id") or "").strip()
    return (
        next((r for r in banks if str(r.get("id")) == chosen), None)
        or next(
            (r for r in banks if "checking" in str(r.get("name", "")).lower()), None
        )
        or (banks[0] if banks else None)
    )


def paid_from_hold(client: ApiClient, route: str, data, tool: str):
    """An expense without paid_from_account_id is not sent: the answer names
    the account the app's form would start at, for the assistant to confirm
    with the user. Asked or not, the account is never guessed."""
    if route != "/api/expenses" or not isinstance(data, dict):
        return None
    if _has(data.get("paid_from_account_id")):
        return None
    suggested = default_paid_from(client)
    out = {
        "created": False,
        "ask_first": True,
        "record": "expense",
        "empty": [{"label": "Paid from account", "field": "paid_from_account_id"}],
    }
    if suggested is not None:
        label = account_label(suggested)
        out["suggested"] = {
            "paid_from_account_id": suggested.get("id"),
            "account": label,
        }
        out["next"] = (
            "Nothing was created yet: an expense needs the account it was paid "
            f"from. SlowBooks Pro's form would start at {label}. Ask the user "
            "whether it was paid from that account or which one, then call "
            f"{tool} again with {_with_books('paid_from_account_id')}."
        )
    else:
        out["next"] = (
            "Nothing was created yet: an expense needs the account it was paid "
            "from, and the company has no bank account to suggest. Ask the "
            f"user which account paid it, then call {tool} again with "
            f"{_with_books('paid_from_account_id')}."
        )
    return out


def item_fallbacks(client: ApiClient, route: str, data) -> dict:
    """For a new item that names no income or expense account: what the app
    posts to instead, as {label: sentence}, read from the chart."""
    if route != "/api/items" or not isinstance(data, dict):
        return {}
    if _has(data.get("income_account_id")) and _has(data.get("expense_account_id")):
        return {}
    chart = _chart(client)
    out = {}
    income = _control(chart, DEFAULT_INCOME)
    if income is not None:
        out["Income Account"] = (
            f"sales of this item post to {account_label(income)} (SlowBooks "
            "Pro's default income account) unless you set one"
        )
    buying = (
        "a bill or expense line for it posts to the vendor's default expense "
        "account; with none, SlowBooks Pro refuses the line until an account "
        "is chosen"
    )
    cogs = _first_cogs(chart)
    stocked = data.get("item_type") == "product" or data.get("track_inventory") is True
    if stocked and cogs is not None:
        buying = (
            f"the cost of its sales posts to {account_label(cogs)} (the first "
            f"cost-of-goods account); {buying}"
        )
    out["Expense or COGS Account"] = buying
    return out


def money_went(client: ApiClient, route: str, answer):
    """A payment's answer with where the money went: money_to (the account)
    and money_note (a sentence to pass on), from the payment and the chart."""
    if not isinstance(answer, dict) or route not in MONEY_ROUTES:
        return answer
    chart = _chart(client)
    if route == "/api/payments":
        chosen = answer.get("deposit_to_account_id")
        if chosen:
            row = _by_id(chart, chosen)
            label = account_label(row) or f"account {chosen}"
            note = f"The payment went into {label}."
        else:
            row = _control(chart, UNDEPOSITED_FUNDS)
            label = account_label(row) or "Undeposited Funds"
            note = (
                "No deposit account was given, so SlowBooks Pro holds the "
                f"payment in {label}: it is not in a bank account yet. It goes "
                "to the bank when it is put on a bank deposit (Make Deposits)."
            )
    else:
        chosen = answer.get("pay_from_account_id")
        if chosen:
            row = _by_id(chart, chosen)
            label = account_label(row) or f"account {chosen}"
            note = f"The payment was paid from {label}."
        else:
            row = _control(chart, CHECKING)
            label = account_label(row) or "Checking"
            note = (
                "No pay-from account was given, so SlowBooks Pro paid it from "
                f"{label}, its default bank account."
            )
    money_to = {"account_id": row.get("id") if row else chosen, "account": label}
    return {**answer, "money_to": money_to, "money_note": note}


def _create(client: ApiClient, route: str, data, asked_user: bool, tool: str):
    held = paid_from_hold(client, route, data, tool)
    if held is not None:
        return held
    tax = sales_tax(client, route, data)
    if not asked_user and route in ASK_FIRST:
        held = ask_first(route, data, asked_user, tool, body_fields(client, route), tax)
    if held is not None:
        return held
    fallbacks = item_fallbacks(client, route, data)
    answer = _write(client, route, with_tax_rate(data, tax))
    return with_left_blank(route, data, answer, tax, fallbacks)


def create_customer(client: ApiClient, data: dict, asked_user: bool = False):
    """Create a customer. Required: name. api_schema("/api/customers") has the rest."""
    return _create(client, "/api/customers", data, asked_user, "create_customer")


def create_vendor(client: ApiClient, data: dict, asked_user: bool = False):
    """Create a vendor. Required: name. api_schema("/api/vendors") has the rest."""
    return _create(client, "/api/vendors", data, asked_user, "create_vendor")


def create_item(client: ApiClient, data: dict, asked_user: bool = False):
    """Create an item. Required: name, item_type. api_schema("/api/items") has the rest."""
    return _create(client, "/api/items", data, asked_user, "create_item")


def create_invoice(client: ApiClient, data: dict, asked_user: bool = False):
    """Create an invoice. Required: customer_id, date, lines. api_schema("/api/invoices") has the rest."""
    return _create(client, "/api/invoices", data, asked_user, "create_invoice")


def create_bill(client: ApiClient, data: dict, asked_user: bool = False):
    """Create a bill. Required: vendor_id, date, lines. api_schema("/api/bills") has the rest."""
    return _create(client, "/api/bills", data, asked_user, "create_bill")


def create_expense(client: ApiClient, data: dict, asked_user: bool = False):
    """Record a paid expense. Required: date, paid_from_account_id, and either
    amount + expense_account_id or lines. api_schema("/api/expenses") has the rest."""
    return _create(client, "/api/expenses", data, asked_user, "create_expense")


def create_journal_entry(client: ApiClient, data: dict):
    """Post a manual journal entry. Required: date, description, lines (debits
    must equal credits). api_schema("/api/journal") has the rest."""
    return _write(client, "/api/journal", data)


def record_payment(client: ApiClient, data: dict, key: str):
    """Record a customer payment. Required data: customer_id, date, amount;
    allocations apply it to invoices (api_schema("/api/payments") has the rest).
    key is required: 1 to 128 characters of A-Z a-z 0-9 _ . : -. The same key
    with the same data books once; the same key with other data is refused."""
    answer = _keyed(client, "/api/payments", data, key)
    return money_went(client, "/api/payments", answer)


def record_bill_payment(client: ApiClient, data: dict, key: str):
    """Pay a vendor. Required data: vendor_id, date, amount; allocations apply
    it to bills (api_schema("/api/bill-payments") has the rest). key is
    required: 1 to 128 characters of A-Z a-z 0-9 _ . : -. The same key with the
    same data books once; the same key with other data is refused."""
    answer = _keyed(client, "/api/bill-payments", data, key)
    return money_went(client, "/api/bill-payments", answer)


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
