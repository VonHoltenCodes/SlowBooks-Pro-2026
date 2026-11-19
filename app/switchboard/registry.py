# Vendored from SlowBooks Switchboard: sbplive/registry.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The tool registry: each tool's name, description, input schema and
# annotations, the check of a call's arguments against that schema, and the
# shape of what goes back to the host (compact JSON text, cut when it would
# not fit, or an error sentence a bookkeeper can act on).
#
# Input schemas are JSON Schema 2020-12 (the protocol's default dialect),
# built from the function's signature plus a sentence per parameter. No
# third-party validator: the schemas use a handful of keywords, and the check
# here covers exactly those.
# ============================================================================

import inspect
import json
import logging
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field

from app.switchboard.client import ApiClient, ApiError, Refused

log = logging.getLogger("sbplive")

# About 15,000 tokens: hosts warn or cut well above this, and a model reads a
# narrowed answer better than a long one.
MAX_RESULT_CHARS = 60_000

NARROW = (
    "Narrow it: pass filters or a smaller limit, a shorter date range, or "
    "ask for one record by id."
)

# Annotations, as 2025-11-25 and 2026-07-28 define them (the same):
# destructiveHint true "may perform destructive updates", false "performs
# only additive updates"; Anthropic's connector review asks for
# destructiveHint true on "tools that modify or delete data", and
# destructive tools always prompt. In a ledger nothing a write does is only
# additive: a new invoice or bill moves A/R or A/P and the accounts it posts
# to, a payment changes what its invoices or bills still owe, a void
# reverses, a PUT overwrites. So every tool that writes the books is
# destructive, and only the reads are not. idempotentHint is true where the
# same call again changes nothing more: a payment under the same key books
# once, a second void or delete of the same record is refused. Every tool
# works one company's books through one API, so none is open-world.
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
WRITE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}
KEYED_WRITE = {**WRITE, "idempotentHint": True}
DESTRUCTIVE = WRITE  # every write is; kept as a name for the general tools
ONCE_WRITE = KEYED_WRITE  # the same call again changes nothing more

# Tools an earlier version had, with where their work went, so a host or a
# skill that still asks for one hears the way on.
RETIRED = {
    "call_operation": (
        "It was split so reads and writes are separate tools: api_get reads, "
        "post_operation, update_operation and delete_operation change."
    ),
}

_JSON_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def _json_type(annotation) -> str | None:
    """The JSON Schema type for a parameter annotation, None for 'any'."""
    if annotation in _JSON_TYPES:
        return _JSON_TYPES[annotation]
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
        return None
    if origin in _JSON_TYPES:
        return _JSON_TYPES[origin]
    return None


def schema_from_signature(
    fn: Callable, descriptions: dict, skip_first: bool = True
) -> dict:
    """An object schema of fn's parameters (after the bound client)."""
    params = list(inspect.signature(fn).parameters.values())
    if skip_first:
        params = params[1:]
    props: dict = {}
    required = []
    for p in params:
        prop: dict = {}
        kind = _json_type(p.annotation)
        if kind:
            prop["type"] = kind
        if p.name in descriptions:
            prop["description"] = descriptions[p.name]
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
        elif p.default is not None:
            prop["default"] = p.default
        props[p.name] = prop
    missing = set(descriptions) - set(props)
    if missing:
        raise TypeError(f"{fn.__name__}: descriptions for unknown {sorted(missing)}")
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


def _type_ok(value, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    return True


_ARTICLE = {
    "integer": "a whole number",
    "number": "a number",
    "boolean": "true or false",
    "string": "text",
    "object": "an object",
    "array": "a list",
}


def check_arguments(name: str, schema: dict, args: dict) -> str | None:
    """None when args fit the schema, else the sentence saying what is wrong."""
    props = schema.get("properties", {})
    stray = sorted(set(args) - set(props))
    if stray and schema.get("additionalProperties") is False:
        known = ", ".join(props) or "none"
        return (
            f"{name} does not take {', '.join(stray)}. " f"Its arguments are: {known}."
        )
    missing = [r for r in schema.get("required", []) if args.get(r) is None]
    if missing:
        return f"{name} needs {', '.join(missing)}."
    for key, value in args.items():
        kind = props.get(key, {}).get("type")
        if value is None and key not in schema.get("required", []):
            continue
        if kind and not _type_ok(value, kind):
            return f"{name}: {key} must be {_ARTICLE[kind]}."
    return None


@dataclass
class Tool:
    name: str
    title: str
    description: str
    fn: Callable
    annotations: dict
    params: dict = field(default_factory=dict)
    input_schema: dict = field(init=False)

    def __post_init__(self):
        self.input_schema = schema_from_signature(self.fn, self.params)

    def definition(self) -> dict:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"title": self.title, **self.annotations},
        }


# ---------------------------------------------------------------------------
# What goes back: text content, compact JSON, cut to fit; errors as sentences.
# ---------------------------------------------------------------------------


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def render(value, limit: int | None = None) -> list:
    """A tool's return value as content blocks, cut with a sentence if large."""
    if limit is None:
        limit = MAX_RESULT_CHARS
    if value is None or value == "":
        return [_text("Done. SlowBooks Pro answered with no content.")]
    if isinstance(value, str):
        text = value
    else:
        text = compact(value)
    if len(text) <= limit:
        return [_text(text)]
    if isinstance(value, list):
        # Keep whole records: the first k that fit, still valid JSON.
        lo, hi = 0, len(value)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(compact(value[:mid])) <= limit:
                lo = mid
            else:
                hi = mid - 1
        return [
            _text(compact(value[:lo])),
            _text(
                f"The result was cut: these are the first {lo} of "
                f"{len(value)} records. {NARROW}"
            ),
        ]
    return [
        _text(text[:limit]),
        _text(
            f"The result was cut at {limit:,} of {len(text):,} characters, "
            f"so the text above is incomplete. {NARROW}"
        ),
    ]


def _validation_lines(detail: str) -> str | None:
    """A FastAPI validation list as 'field: message' sentences."""
    try:
        rows = json.loads(detail)
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None
    parts = []
    for r in rows:
        loc = [str(x) for x in (r.get("loc") or []) if x != "body"]
        msg = str(r.get("msg") or "is not valid")
        parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return "; ".join(parts) if parts else None


def _end(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


# The app's short refusals, as sentences a bookkeeper can act on. Anything
# else the API says is passed on in its own words.
_ROLE_REFUSALS = {
    "admin role required": (
        "SlowBooks Pro refused this: only an administrator signed in to "
        "SlowBooks Pro can do it, and an API token is never an administrator."
    ),
    "your role doesn't allow this action": (
        "SlowBooks Pro refused this: the API token's role does not allow it. "
        "Someone whose role does can do it in SlowBooks Pro, or an "
        "administrator can make a token with that role under Settings, API "
        "Tokens."
    ),
}


def _refused_sentence(detail: str) -> str:
    known = _ROLE_REFUSALS.get(detail.strip().rstrip(".").lower())
    return known or f"SlowBooks Pro refused this: {_end(detail)}"


# A server failure's own words are passed on only when they are a short
# sentence the app chose to say; an empty body, a stock "Internal Server
# Error", an HTML page or a traceback is not shown (it says nothing a
# bookkeeper can act on, and a traceback can carry the books' data).
_PLAIN_REASON = 300
_STOCK = {"internal server error", "bad gateway", "service unavailable",
          "gateway timeout"}  # fmt: skip


def _plain_reason(detail: str) -> str | None:
    text = (detail or "").strip()
    if (
        not text
        or len(text) > _PLAIN_REASON
        or any(c in text for c in "<>\n\r{}")
        or "traceback" in text.lower()
        or text.rstrip(".").lower() in _STOCK
        or text.startswith("the request failed with status")
    ):
        return None
    return text


def _server_failed(status: int, detail: str) -> str:
    reason = _plain_reason(detail)
    said = f": {_end(reason)}" if reason else " and gave no reason."
    return (
        f"SlowBooks Pro failed while doing this (HTTP {status}){said} If this "
        "was a change, whether it was made is not known: check with search or "
        "list_records before trying again. If it keeps happening, SlowBooks "
        "Pro's own log has the cause; check that it is up to date and running."
    )


def error_sentence(exc: Exception) -> str:
    """The sentence for a failed tool call; the API's own words are kept."""
    if isinstance(exc, Refused):
        return _end(str(exc.detail))
    if isinstance(exc, ApiError):
        detail = str(exc.detail or "").strip()
        detail = _validation_lines(detail) or detail
        if exc.status == 0:
            return _end(detail) + " Open SlowBooks Pro, then try again."
        if exc.status == 401:
            return (
                f"SlowBooks Pro did not accept the API token ({_end(detail)}) "
                "It may have been revoked: make a new one under Settings, "
                "API Tokens, and save it in the token file."
            )
        if exc.status == 422:
            return f"SlowBooks Pro did not accept the data: {_end(detail)}"
        if exc.status == 404:
            return f"SlowBooks Pro found nothing there: {_end(detail)}"
        if exc.status >= 500:
            return _server_failed(exc.status, detail)
        if exc.status == 403:
            return _refused_sentence(detail)
        return f"SlowBooks Pro refused this: {_end(detail)}"
    return _end(str(exc))


class Registry:
    def __init__(self, client: ApiClient):
        self.client = client
        self._tools: dict = {}

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name} is registered twice.")
        self._tools[tool.name] = tool

    def __contains__(self, name) -> bool:
        return name in self._tools

    def __getitem__(self, name) -> Tool:
        return self._tools[name]

    def unknown_sentence(self, name) -> str:
        """The protocol error's message for a tool that is not here."""
        retired = RETIRED.get(name) if isinstance(name, str) else None
        return f"Unknown tool: {name}" + (f". {retired}" if retired else "")

    def names(self) -> list:
        return list(self._tools)

    def definitions(self) -> list:
        return [t.definition() for t in self._tools.values()]

    def call(self, name: str, args: dict) -> dict:
        """A tools/call result. The tool must exist (the caller checks)."""
        tool = self._tools[name]
        problem = check_arguments(name, tool.input_schema, args)
        if problem:
            return {"content": [_text(problem)], "isError": True}
        try:
            value = tool.fn(self.client, **args)
        except (ApiError, ValueError) as exc:
            return {"content": [_text(error_sentence(exc))], "isError": True}
        except Exception:  # a bug here must not end the session
            log.exception("tool %s failed", name)
            return {
                "content": [
                    _text(
                        f"SlowBooks Switchboard hit an internal error running {name}. "
                        "The details are in its log on standard error. Try "
                        "again, or another way to the same answer."
                    )
                ],
                "isError": True,
            }
        return {"content": render(value), "isError": False}
