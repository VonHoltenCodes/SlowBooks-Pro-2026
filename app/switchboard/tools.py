# Vendored from SlowBooks Switchboard: sbplive/tools.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The named tools, as plain functions over an ApiClient: server.py registers
# them with their schemas and annotations. Each returns JSON-able data kept
# compact (no PDF, CSV or HTML).
# ============================================================================

import difflib
import re
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


# ---------------------------------------------------------------------------
# Ask before a bare create (the owner, 2026-11-19: "if you don't ask you
# don't get"). A create of one of the main records that carries only what
# the API requires is not sent at first: the answer lists the empty fields
# worth having, as the app's forms name them, for the assistant to ask the
# person about. A second call with asked_user creates it; a record that
# already fills at least half of its fields (rounded down, at least one) is
# created at once. Every create's answer says, in left_blank, what is still
# empty. The gate is here, in Switchboard, and nowhere in the API: the
# app's screens, imports and API tokens used directly are unaffected.
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
    return {
        "created": False,
        "ask_first": True,
        "record": rule.record.split(" ", 1)[1],
        "empty": rows,
        "next": (
            f"Nothing was created yet: {rule.record} with this little on it "
            f"may need checking first. Ask the user whether they have any of "
            f"these: {labels}. Do not make any up. Then call {tool} again with "
            f"what they gave and asked_user: true, or with asked_user: true "
            f"alone if they want it created as it is."
        ),
    }


# ---------------------------------------------------------------------------
# Sales tax on a new sales document. The API takes a missing tax_rate as 0%,
# while the app's own forms start a new invoice, estimate, sales receipt or
# credit memo at the company's Default Tax Rate (Settings), and a credit memo
# for an invoice at that invoice's rate. So when the assistant gives no
# tax_rate, Switchboard does what the form does and says so in the answer
# (tax_rate_source, tax_note); a tax_rate the assistant gives, 0 included, is
# always sent as given. A non-taxable customer or a document marked
# tax_exempt is charged no tax by the app whatever the rate (the form keeps
# the default rate on it too). With no default rate (0%) and a taxable
# customer, "Sales tax rate" is one of the fields to ask about. The API's
# own default is not changed: that is for every API user to keep.
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


def _exempt_reason(client: ApiClient, data: dict):
    """Why the app charges this document no tax whatever its rate, or None."""
    if data.get("tax_exempt") is True:
        return "the document is marked tax exempt"
    cid = data.get("customer_id")
    if isinstance(cid, bool) or not isinstance(cid, (int, str)) or cid == "":
        return None
    try:
        customer = client.get(f"/api/customers/{cid}")
    except ApiError:
        return None  # the API says what is wrong with the customer
    if isinstance(customer, dict) and customer.get("is_taxable") is False:
        name = customer.get("name") or "the customer"
        return f"{name} is non-taxable (reseller or exempt)"
    return None


def sales_tax(client: ApiClient, route: str, data):
    """The plan for tax_rate on a new sales document sent without one:
    {"rate": the fraction to send or None (with "shown", the percent),
    "source", "note", "ask"}; None
    when there is nothing to do (another record, or a tax_rate given)."""
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
        }
    pct = _default_percent(client)
    exempt = _exempt_reason(client, data)
    if pct is None:
        return {
            "rate": None,
            "source": "none: the company's default rate could not be read",
            "note": (
                "No tax_rate was given and the company's Default Tax Rate "
                "could not be read, so the document has a 0% sales tax rate. "
                "Ask the user what rate applies."
            ),
            "ask": exempt is None,
        }
    if pct > 0:
        shown = _percent(pct)
        if exempt:
            note = (
                f"No tax_rate was given, so the company's default rate, {shown}, "
                f"was used, as SlowBooks Pro's form does; {exempt}, so no sales "
                "tax is charged."
            )
        else:
            note = (
                f"No tax_rate was given, so SlowBooks Pro used the company's "
                f"default sales tax rate, {shown}, as its own form does. Tell "
                "the user; if this sale is taxed at another rate, change the "
                "document's tax_rate (a fraction: 0.0159 is 1.59%)."
            )
        return {
            "rate": (pct / 100).quantize(_RATE_PLACES),
            "shown": shown,
            "source": f"company default ({shown})",
            "note": note,
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


def with_left_blank(route: str, data, answer, tax=None):
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
        if value is None:
            blank.append(label)
        else:
            defaulted.append({"label": label, "value": value})
    extra: dict = {}
    if tax is not None:
        if tax["rate"] is not None:
            defaulted.append(
                {"label": TAX_LABEL, "value": f"{tax['shown']} (the company's default)"}
            )
        elif tax["ask"]:
            blank.append(TAX_LABEL)
        extra = {"tax_rate_source": tax["source"], "tax_note": tax["note"]}
    out = {"left_blank": blank, "defaulted": defaulted, **extra}
    if isinstance(answer, dict):
        return {**answer, **out}
    return {"created": True, "answer": answer, **out}


def _create(client: ApiClient, route: str, data, asked_user: bool, tool: str):
    tax = sales_tax(client, route, data)
    held = None
    if not asked_user and route in ASK_FIRST:
        held = ask_first(route, data, asked_user, tool, body_fields(client, route), tax)
    if held is not None:
        return held
    answer = _write(client, route, with_tax_rate(data, tax))
    return with_left_blank(route, data, answer, tax)


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
