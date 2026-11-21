# Vendored from SlowBooks Switchboard: sbplive/server.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The server: every tool registered with its description, input schema and
# annotations, over one ApiClient, the stdio door's entry point run(), and
# serve_mcp(), the one answer every HTTP-shaped door gives an MCP message.
# The self-hosted HTTP door's runner (serve-http) is serve_http.py; both
# doors serve the same registry through the same protocol.Session.
# Standard library only. A refusal from the API reaches the model as the
# API's own sentence, in a tool result marked as an error.
# ============================================================================

import logging
import sys

from app.switchboard import logs, operations, tools
from app.switchboard.config import ConfigError, load_config
from app.switchboard.protocol import (
    HANDSHAKE_VERSIONS,
    META_VERSION,
    MODERN_VERSIONS,
    Session,
    icons_at,
    make_server_info,
    serve_stdio,
)
from app.switchboard.registry import (
    DESTRUCTIVE,
    KEYED_WRITE,
    ONCE_WRITE,
    READ_ONLY,
    WRITE,
    Registry,
    Tool,
)

log = logging.getLogger("sbplive")

INSTRUCTIONS = (
    "Tools for working the books of a running SlowBooks Pro company through "
    "its own API. Call `context` first. The named tools cover everyday work; "
    "for anything else, `find_operations` finds the API operation and "
    "`describe_operation` says what it takes; `api_get` reads, and "
    "`post_operation`, `update_operation` and `delete_operation` make "
    "changes. Amounts are decimal dollars as the API returns them; dates are ISO "
    "(YYYY-MM-DD). The API enforces the token's role, the closing date and "
    "validation: a refusal is a sentence to pass on to the person, not a "
    "reason to retry. Every write is audited as token:<label>. Use one stable key "
    "per payment so a retry can never book it twice; void instead of delete. "
    "Never invent a value for a record. A create with little on it comes back "
    "with ask_first and the empty fields: ask the person for them before "
    "calling again. After a create, tell the person what left_blank lists "
    "(still empty) and what defaulted lists (not given, so SlowBooks Pro used "
    "its default: the value is there). A sales document sent without "
    "tax_rate takes the company's default rate, as the app's form does; pass "
    "tax_note on. A connection can reach several companies' books. Call "
    "context first: it lists them. Name the books on every call with books, "
    "using the handle context gives; ids, keys and answers belong to those "
    "books only. Never carry an id from one company's books to another's. A "
    "customer's or vendor's company field is part of the record and never "
    "chooses the books."
)

_ASKS = (
    " {what} with little on it is not created at first: the answer lists the "
    "empty fields worth asking the user for (ask_first), and a second call "
    "with asked_user creates it. A created record's answer lists what is "
    "still empty in left_blank, and in defaulted what SlowBooks Pro filled "
    "with its default."
)

_TAX = (
    " {what} sent without tax_rate takes the company's Default Tax Rate (a "
    "credit memo for an invoice, that invoice's rate), as the app's form "
    "does; the answer's tax_note says so. A tax_rate you give, 0 included, "
    "is used as given."
)

_DATA = (
    "The record's fields as the API takes them; describe_operation with "
    "POST and the route lists every field. Unknown fields are refused."
)
_KEY = (
    "A stable key for this one intended payment: 1 to 128 characters of "
    "A-Z a-z 0-9 _ . : -. The same key with the same data books once; the "
    "same key with other data is refused."
)
_KIND = "One of: " + ", ".join(tools.KINDS) + "."
_API = tools.API_REFERENCE
_WRITE_PATH = (
    "The path from find_operations, such as /api/invoices/{invoice_id}; a "
    "concrete path also works."
)
_PARAMS = "Path and query parameters by name, such as {'invoice_id': 12}."


def _tool(tool_name, title, fn, annotations, description, /, **params) -> Tool:
    return Tool(
        name=tool_name,
        title=title,
        description=description,
        fn=fn,
        annotations=annotations,
        params=params,
    )


def build_registry(client) -> Registry:
    """Every tool over client: one ApiClient (one company) or a Books
    provider (registry.Books), which may reach several companies' books."""
    reg = Registry(client)
    add = reg.add

    # -- reading --------------------------------------------------------------
    add(
        _tool(
            "context",
            "Company context",
            tools.context,
            READ_ONLY,
            "The company's name, type, closing date and the app's version. Read "
            "it before working: a change dated on or before the closing date is "
            "refused by the app. On a connection that reaches several "
            "companies, context without books lists their handles instead; "
            "call it again with books for one company's facts.",
        )
    )
    add(
        _tool(
            "search",
            "Search the books",
            tools.search,
            READ_ONLY,
            "Find customers, vendors, items, invoices, bills and other records "
            "by name, number or amount, grouped by kind.",
            q="The words, number or amount to look for.",
            limit="The most records to return across all groups.",
        )
    )
    add(
        _tool(
            "list_records",
            "List records",
            tools.list_records,
            READ_ONLY,
            "List one kind of record. Filters pass through to the API as query "
            "parameters (for customers, {'search': 'Acme'}); describe_operation "
            "with GET and the kind's route lists them. journal_entries lists "
            "the current fiscal year unless the filters give start_date or "
            "end_date.",
            kind=_KIND,
            filters="Query parameters for the list route, by name.",
            limit="The most records to return.",
        )
    )
    add(
        _tool(
            "get_record",
            "Get one record",
            tools.get_record,
            READ_ONLY,
            "One record by kind and id, with its lines and balances.",
            kind=_KIND,
            record_id="The record's id.",
        )
    )
    add(
        _tool(
            "list_reports",
            "List reports",
            tools.list_reports,
            READ_ONLY,
            "The reports that can be run, each with its parameters.",
        )
    )
    add(
        _tool(
            "report",
            "Run a report",
            tools.report,
            READ_ONLY,
            "Run a report by name (see list_reports), such as profit-loss or "
            "balance-sheet, and return its figures. Dates and other options go "
            "in params, never beside name.",
            name="The report's name from list_reports.",
            params="The report's parameters, such as "
            "{'start_date': '2026-01-01', 'end_date': '2026-12-31'}.",
        )
    )
    add(
        _tool(
            "api_schema",
            "Route schema",
            tools.api_schema,
            READ_ONLY,
            "How to call one route of " + _API + ": its methods, parameters "
            "and request body. A prefix lists the routes under it.",
            path="A route such as /api/invoices, or a prefix such as /api/inv.",
        )
    )
    add(
        _tool(
            "api_get",
            "Read any route",
            operations.api_get,
            READ_ONLY,
            "A read-only GET of any route of " + _API + " not covered by "
            "another tool (see api_schema or find_operations). It sends GET "
            "only; changes go through post_operation, update_operation and "
            "delete_operation.",
            path="The route, such as /api/accounts or /api/invoices/12; a "
            "template such as /api/invoices/{invoice_id} takes its value in "
            "params.",
            params="Query parameters (and a template's path values) by name.",
        )
    )

    # -- reaching every operation ----------------------------------------------
    add(
        _tool(
            "find_operations",
            "Find API operations",
            operations.find_operations,
            READ_ONLY,
            "Search " + _API + ", as the installed app's OpenAPI document "
            "describes it, for operations whose path, summary or tags match "
            "the words, with a line on what each does and the tool that sends "
            "it. For anything the named tools do not cover.",
            query="Words such as 'credit memo', 'deposit', 'class' or 'budget'.",
            limit="The most operations to return, up to 50.",
        )
    )
    add(
        _tool(
            "describe_operation",
            "Describe an API operation",
            operations.describe_operation,
            READ_ONLY,
            "One operation of " + _API + ": its parameters and request body, "
            "with the schemas it refers to resolved, whether it needs an "
            "idempotency key, and the tool that sends it. Nothing is sent.",
            method="GET, POST, PUT, PATCH or DELETE.",
            path="The path from find_operations, such as "
            "/api/invoices/{invoice_id}; a concrete path also works.",
        )
    )
    # Writes, one tool per kind of change; like every write here they carry
    # destructiveHint (registry.py says why); reads go through api_get.
    _REFUSED = (
        " The API decides what is allowed; a refusal comes back as its " "sentence."
    )
    add(
        _tool(
            "post_operation",
            "Create or act through the API",
            operations.post_operation,
            DESTRUCTIVE,
            "POST to one operation of " + _API + ", as the token, and return "
            "the API's answer: create a record, or run an action such as a "
            "void, a match or an apply. Path and query parameters go in "
            "params by name, the record in body. A payment-recording "
            "operation needs idempotency_key."
            + _REFUSED
            + _ASKS.format(
                what="A new customer, vendor, item, employee, account, invoice, "
                "bill, expense, estimate or purchase order"
            )
            + _TAX.format(what="A new invoice, estimate, sales receipt or credit memo"),
            path=_WRITE_PATH,
            params=_PARAMS,
            body="The request body, when the operation takes one.",
            idempotency_key="Required where describe_operation says so: " + _KEY,
            asked_user="For a create of one of the records above: "
            + tools.ASKED_USER[0].lower()
            + tools.ASKED_USER[1:],
        )
    )
    add(
        _tool(
            "update_operation",
            "Update through the API",
            operations.update_operation,
            DESTRUCTIVE,
            "PUT or PATCH one operation of " + _API + ", as the token, and "
            "return the API's answer: change a record's fields. Path and "
            "query parameters go in params by name, the fields in body." + _REFUSED,
            path=_WRITE_PATH,
            params=_PARAMS,
            body="The request body: the record's fields.",
            method="PUT or PATCH. Leave it out unless the route takes both.",
        )
    )
    add(
        _tool(
            "delete_operation",
            "Delete through the API",
            operations.delete_operation,
            ONCE_WRITE,
            "DELETE through one operation of " + _API + ", as the token, and "
            "return the API's answer: remove a record, or, where SlowBooks Pro "
            "keeps its history (a customer, vendor or item), make it inactive; "
            "the answer's result says which (deleted or deactivated). Posted "
            "documents are voided with post_operation instead; the API answers "
            "a DELETE on one with the void route." + _REFUSED,
            path=_WRITE_PATH,
            params=_PARAMS,
        )
    )

    # -- everyday writes -------------------------------------------------------
    for name, title, fn, what, asks in (
        ("create_customer", "Create a customer", tools.create_customer,
         "Create a customer. Required: name.", "A customer"),
        ("create_vendor", "Create a vendor", tools.create_vendor,
         "Create a vendor. Required: name.", "A vendor"),
        ("create_item", "Create an item", tools.create_item,
         "Create an item. Required: name and item_type.", "An item"),
        ("create_invoice", "Create an invoice", tools.create_invoice,
         "Create an invoice. Required: customer_id, date and lines.",
         "An invoice"),
        ("create_bill", "Enter a bill", tools.create_bill,
         "Enter a vendor's bill. Required: vendor_id, date and lines.",
         "A bill"),
        ("create_expense", "Record an expense", tools.create_expense,
         ("Record a paid expense. Required: date, paid_from_account_id, and "
          "either amount with expense_account_id or lines. Without "
          "paid_from_account_id nothing is sent: the answer suggests the "
          "account the app's form starts at, to confirm with the user."),
         "An expense"),
        ("create_journal_entry", "Post a journal entry",
         tools.create_journal_entry,
         ("Post a manual journal entry. Required: date, description and "
          "lines; debits must equal credits."), None),
    ):  # fmt: skip
        params = {"data": _DATA}
        if asks:
            params["asked_user"] = tools.ASKED_USER
        add(
            _tool(
                name,
                title,
                fn,
                WRITE,
                what + " If the answer is lost, check with search before "
                "trying again."
                + (_ASKS.format(what=asks) if asks else "")
                + (_TAX.format(what="An invoice") if name == "create_invoice" else ""),
                **params,
            )
        )
    add(
        _tool(
            "record_payment",
            "Receive a payment",
            tools.record_payment,
            KEYED_WRITE,
            "Record a customer's payment. Required data: customer_id, date and "
            "amount; allocations apply it to invoices. Without "
            "deposit_to_account_id it waits in Undeposited Funds until a bank "
            "deposit; the answer's money_note says where the money went: pass "
            "it on. A retry with the same key and data books it once.",
            data=_DATA,
            key=_KEY,
        )
    )
    add(
        _tool(
            "record_bill_payment",
            "Pay a bill",
            tools.record_bill_payment,
            KEYED_WRITE,
            "Pay a vendor. Required data: vendor_id, date and amount; "
            "allocations apply it to bills. Without pay_from_account_id it is "
            "paid from the default bank account; the answer's money_note says "
            "which. A retry with the same key and data books it once.",
            data=_DATA,
            key=_KEY,
        )
    )
    add(
        _tool(
            "void_record",
            "Void a record",
            tools.void_record,
            ONCE_WRITE,
            "Void an invoice, bill, expense, journal entry, payment or bill "
            "payment with a reversing entry. Nothing is deleted, but the record "
            "stops counting.",
            kind="One of: " + ", ".join(tools._VOID_PATHS) + ".",
            record_id="The record's id.",
            fee_refunded="Payments only: the provider gave the processing fee " "back.",
        )
    )
    return reg


# -- the MCP core of the HTTP-shaped doors ---------------------------------------
#
# An HTTP-shaped door (the remote door, remote.py) receives one JSON-RPC
# message with HTTP-style headers and answers a status, headers and a
# JSON-RPC answer. That answer is decided here, once, for every such door: the era
# is picked from the message itself (a version in _meta is 2026-07-28, served
# statelessly with its routing headers checked; initialize opens a
# 2025-11-25 session named by Mcp-Session-Id), exactly as docs/PROTOCOL.md
# says for the remote door. The transport around it (HTTP bodies and media
# types) stays with each door.


class Mcp:
    """The core every HTTP-shaped door serves: the registry, the open 2025-11-25 sessions
    and one Session for every stateless request."""

    def __init__(self, registry: Registry, instructions: str, sessions=None):
        from app.switchboard.remote import Sessions

        self.registry = registry
        self.instructions = instructions
        self.sessions = Sessions() if sessions is None else sessions
        # serverInfo, its icons as data: URIs until use_origin gives the
        # door a web address of its own.
        self.server_info = make_server_info()
        # The modern era needs no state: one Session that never sees
        # initialize answers every stateless request (and, for a request
        # with neither a version nor a session, says how to begin).
        self.stateless = self.session()

    def use_origin(self, origin: str) -> None:
        """Point serverInfo's icons at the files served at origin's root
        (a door with a web address of its own that serves them)."""
        self.server_info = make_server_info(icons_at(origin))
        self.stateless.server_info = self.server_info

    def session(self) -> Session:
        return Session(self.registry, self.instructions, self.server_info)


def serve_mcp(mcp: Mcp, message, headers, owner: str = "secret") -> tuple:
    """Serve one decoded JSON-RPC message for whichever era it belongs to.

    owner names the sign-in making the request (the connector secret, or an
    OAuth principal): a 2025-11-25 session belongs to the sign-in that opened
    it, and a request from another sign-in does not find it.

    headers answers get(name) and get_all(name), case-insensitively (an
    email.message.Message, as http.server gives them). Returns (status,
    headers to add, the JSON-RPC answer or None when there is none to send,
    as for a notification)."""
    if not isinstance(message, dict):
        # A batch, or not an object: the core's own sentence.
        return 400, {}, mcp.stateless.handle_message(message)
    if "id" not in message:
        return _notification(mcp, message, headers, owner)
    if "method" not in message:
        if "result" in message or "error" in message:
            return 202, {}, None  # a response: this server asks nothing
        return 400, {}, mcp.stateless.handle_message(message)
    method = message.get("method")
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    if method != "initialize" and isinstance(meta, dict) and META_VERSION in meta:
        return _modern(mcp, message, headers)
    if method == "initialize":
        return _initialize(mcp, message, owner)
    return _handshake_era(mcp, message, headers, owner)


def _session_for(mcp: Mcp, sid: str, owner: str):
    """The open session sid, when it belongs to this sign-in."""
    session = mcp.sessions.get(sid)
    if session is None or getattr(session, "owner", owner) != owner:
        return None
    return session


def _notification(mcp: Mcp, message: dict, headers, owner: str = "secret") -> tuple:
    from app.switchboard.remote import SESSION_HEADER

    sid = headers.get(SESSION_HEADER)
    target = mcp.stateless
    if sid is not None:
        target = _session_for(mcp, sid, owner)
        if target is None:
            return 404, {}, None
    reply = target.handle_message(message)
    return (202, {}, None) if reply is None else (400, {}, reply)


def _modern(mcp: Mcp, message: dict, headers) -> tuple:
    from app.switchboard import remote

    problem = _header_problem(message, headers)
    if problem:
        return (
            400,
            {},
            remote._rpc_error(
                message.get("id"), remote.HEADER_MISMATCH, f"Header mismatch: {problem}"
            ),
        )
    reply = mcp.stateless.handle_message(message)
    status = 200
    if "error" in reply:
        status = remote._MODERN_STATUS.get(reply["error"].get("code"), 200)
    return status, {}, reply


def _initialize(mcp: Mcp, message: dict, owner: str = "secret") -> tuple:
    from app.switchboard import remote

    session = mcp.session()
    session.owner = owner  # later requests must come from the same sign-in
    reply = session.handle_message(message)
    if "error" in reply:
        return 400, {}, reply
    sid = mcp.sessions.add(session)
    if sid is None:
        return (
            503,
            {},
            remote._rpc_error(
                message.get("id"),
                remote.INVALID_REQUEST,
                "Too many open sessions; try again later.",
            ),
        )
    return 200, {remote.SESSION_HEADER: sid}, reply


def _handshake_era(mcp: Mcp, message: dict, headers, owner: str = "secret") -> tuple:
    from app.switchboard import remote

    id_ = message.get("id")
    version = headers.get(remote.VERSION_HEADER)
    if version in MODERN_VERSIONS:
        return (
            400,
            {},
            remote._rpc_error(
                id_,
                remote.INVALID_PARAMS,
                f"Invalid params: a {version} request carries {META_VERSION} "
                "and the client's capabilities in params._meta.",
            ),
        )
    sid = headers.get(remote.SESSION_HEADER)
    if sid is None:
        # No session: ping is answered; anything else is told how to
        # begin (400, as 2025-11-25 says for a missing session id).
        reply = mcp.stateless.handle_message(message)
        return (400 if "error" in reply else 200), {}, reply
    session = _session_for(mcp, sid, owner)
    if session is None:
        return (
            404,
            {},
            remote._rpc_error(
                id_,
                remote.INVALID_REQUEST,
                "Session not found: it ended or expired. Send initialize "
                "again without Mcp-Session-Id.",
            ),
        )
    if version is not None and version not in HANDSHAKE_VERSIONS:
        return (
            400,
            {},
            remote._rpc_error(
                id_,
                remote.INVALID_REQUEST,
                f"Unsupported {remote.VERSION_HEADER}: {version}; this session "
                f"speaks {', '.join(HANDSHAKE_VERSIONS)}.",
            ),
        )
    return 200, {}, session.handle_message(message)


def _header_problem(message: dict, headers) -> str | None:
    """Why a 2026-07-28 request's headers do not match its body, or None."""
    from app.switchboard import remote

    for name in remote._ROUTING_HEADERS:
        if len(headers.get_all(name) or []) > 1:
            return f"{name} appears more than once."
    params = message.get("params") or {}
    version = params["_meta"].get(META_VERSION)
    sent = headers.get(remote.VERSION_HEADER)
    if sent is None:
        return f"the {remote.VERSION_HEADER} header is missing."
    if sent != version:
        return f"the {remote.VERSION_HEADER} header does not match the body's version."
    method = message.get("method")
    sent = headers.get(remote.METHOD_HEADER)
    if sent is None:
        return f"the {remote.METHOD_HEADER} header is missing."
    if sent != method:
        return f"the {remote.METHOD_HEADER} header does not match the body's method."
    key = remote._NAME_BEARING.get(method)
    if key is not None and params.get(key) is not None:
        sent = headers.get(remote.NAME_HEADER)
        if sent is None:
            return f"the {remote.NAME_HEADER} header is missing."
        if remote.decode_header_value(sent) != params.get(key):
            return f"the {remote.NAME_HEADER} header does not match the body's {key}."
    return None


def run() -> int:
    # Imported here: a program that embeds the tools (server.Mcp) does not
    # need the store of companies.
    from app.switchboard import books

    logs.setup()
    try:
        config = load_config()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    log.info("started for %s", config.base_url)
    if config.books:
        log.info("with %s companies' books", len(config.books))
    # Only protocol messages go to stdout: anything printed by mistake goes
    # to stderr instead of corrupting the stream.
    out = sys.stdout.buffer
    sys.stdout = sys.stderr
    session = Session(build_registry(books.provider(config)), INSTRUCTIONS)
    try:
        return serve_stdio(session, sys.stdin.buffer, out)
    except KeyboardInterrupt:
        return 0
