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
#
# Which books: a connection can reach several companies' books, each through
# its own token (a Books provider). Every tool takes a `books` argument, the
# handle `context` lists. With one company on the connection it may be left
# out; with more it must be given, and a call that names none, an unknown
# handle, or a company's name or file is refused before anything is sent.
# One call works one company's books from start to end, and on a connection
# of several companies its result starts by naming them.
# ============================================================================

import inspect
import json
import logging
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.switchboard import tools as _tools
from app.switchboard.client import ApiClient, ApiError, Refused, Unconfirmed

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
# works the books of companies this connection was granted, through their
# own API, so none is open-world.
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


# The one argument every tool takes on top of its own. It is not required
# (one company needs no naming) and has no enum (the handles differ per
# connection, and the list of tools stays the same for everyone).
BOOKS = "books"
BOOKS_PROPERTY = {
    "type": "string",
    "description": (
        "Which company's books to work in: its handle exactly as context "
        "lists it (for example smith-plumbing~k3f9q2). Needed whenever this "
        "connection reaches more than one company."
    ),
}


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
        schema = schema_from_signature(self.fn, self.params)
        if BOOKS in schema["properties"]:
            raise TypeError(f"{self.name}: {BOOKS} is the registry's own argument")
        schema["properties"][BOOKS] = dict(BOOKS_PROPERTY)
        self.input_schema = schema

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


# ---------------------------------------------------------------------------
# Books providers: which companies a connection reaches, and the client for
# each. An entry is {"books", "name", "answers", "why"} and may carry "file";
# "books" is the handle (None for a connection that names none).
# ---------------------------------------------------------------------------

NOTHING_DONE = "Nothing was done."


@runtime_checkable
class Books(Protocol):
    def entries(self) -> list[dict]:
        """[{"books", "name", "answers": bool, "why": str}], plus "file"
        when it is known."""
        ...

    def pick(self, books: str | None, tool: str) -> tuple[ApiClient, dict]:
        """The client and entry for a handle the registry resolved; raises
        Refused(sentence) when that company cannot be reached now."""
        ...

    def count(self) -> int:
        """The companies on the connection, never only the ones open now."""
        ...

    def lost(self, books: str | None) -> None:
        """Told that the company's token was refused (401)."""
        ...


class SingleBooks:
    """One company through one client: the connection of a single token,
    which names no handle unless it is given one."""

    def __init__(
        self,
        client: ApiClient,
        books: str | None = None,
        name: str = "",
        file: str = "",
    ):
        self.client = client
        self.books = books
        self.name = name
        self.file = file

    def _entry(self) -> dict:
        return {
            "books": self.books,
            "name": self.name,
            "answers": True,
            "why": "",
            "file": self.file,
        }

    def entries(self) -> list[dict]:
        return [self._entry()]

    def pick(self, books: str | None, tool: str) -> tuple[ApiClient, dict]:
        return self.client, self._entry()

    def count(self) -> int:
        return 1

    def lost(self, books: str | None) -> None:
        return None


def _shown(entry: dict) -> str:
    return entry.get("name") or entry.get("file") or str(entry.get("books") or "")


def _handles(entries: list) -> str:
    return ", ".join(str(e.get("books")) for e in entries if e.get("books"))


def _quoted(value: str) -> str:
    text = value if len(value) <= 80 else value[:79] + "…"
    return "'" + text.replace("'", "") + "'"


def _same(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.casefold() == b.casefold()


def _stem(file) -> str | None:
    if not isinstance(file, str) or not file:
        return None
    return file[: -len(".db")] if file.lower().endswith(".db") else file


def overview(entries: list) -> dict:
    """context's answer on a connection of several companies, when no books
    are named: which books it reaches, and no books data."""
    return {
        "books": [
            {
                "books": e.get("books"),
                "name": e.get("name") or "",
                "answers": bool(e.get("answers")),
                "why": e.get("why") or "",
            }
            for e in entries
        ],
        "next": (
            "Every call names one of these with books. Ids, keys and answers "
            "belong to one company's books only."
        ),
    }


class _Stop(Exception):
    """A call refused before anything was sent; the sentence is the answer."""

    def __init__(self, sentence: str, value=None):
        super().__init__(sentence)
        self.sentence = sentence
        self.value = value


def choose(entries: list, count: int, books, tool: str) -> dict:
    """The entry a call works, or _Stop with the refusal (or, for context
    with no books on several companies, the overview as its value)."""
    if books is None:
        if count == 1 and len(entries) == 1:
            return entries[0]
        if not entries:
            raise _Stop(
                "This connection reaches no company's books yet. " + NOTHING_DONE
            )
        if tool == "context":
            raise _Stop("", overview(entries))
        raise _Stop(
            f"This connection reaches {count} companies' books, so every call "
            "names one with books. Call context to see them, then call "
            f"{tool} again with books set to one of: {_handles(entries)}. "
            + NOTHING_DONE
        )
    for entry in entries:
        if entry.get("books") and books == entry["books"]:
            return entry
    named = [
        e
        for e in entries
        if e.get("books")
        and (
            _same(books, e.get("name"))
            or _same(books, e.get("file"))
            or _same(books, _stem(e.get("file")))
        )
    ]
    if named:
        handles = " or ".join(str(e["books"]) for e in named)
        raise _Stop(
            f"Name the books by their handle, {handles}, not by the company's "
            "name or file (names repeat and change). " + NOTHING_DONE
        )
    if not any(e.get("books") for e in entries):
        raise _Stop(
            f"This connection does not reach the books {_quoted(books)}. It "
            "reaches one company's books, which need no handle: leave books "
            "out. " + NOTHING_DONE
        )
    raise _Stop(
        f"This connection does not reach the books {_quoted(books)}. It "
        f"reaches: {_handles(entries)}. " + NOTHING_DONE
    )


def _lost_sentence(name: str) -> str:
    return (
        f"{name} no longer accepts this connection: its token was turned off "
        "there, or the person who added it is no longer an administrator there. "
        f"An administrator of {name} can add it again. " + NOTHING_DONE
    )


def _unconfirmed_sentence(name: str) -> str:
    return (
        f"SlowBooks Pro did not confirm it answered from {name}'s books, so "
        "this call stopped. Tell the person; do not retry."
    )


def _unserved_sentence(name: str) -> str:
    return (
        f"{name} is not served by this SlowBooks Pro server right now. Ask its "
        "administrator. " + NOTHING_DONE
    )


def _books_error(exc: ApiError, entry: dict) -> str | None:
    """The sentence for an answer about which books, on a connection that
    names its books; None for every other answer."""
    if not entry.get("books"):
        return None
    name = _shown(entry)
    if exc.status == 401:
        return _lost_sentence(name)
    if exc.status == 421:
        return _unserved_sentence(name)
    if exc.status == 503 and not exc.stamped:
        # Locked, its own key, or behind the schema: the app's own sentence,
        # given before the token was read, so nothing was done.
        reason = _plain_reason(str(exc.detail or ""))
        if reason:
            return f"{_end(reason)} {NOTHING_DONE}"
    return None


class Registry:
    def __init__(self, books):
        """books: a Books provider, or one ApiClient (one company, no handle)."""
        if isinstance(books, ApiClient) or not isinstance(books, Books):
            books = SingleBooks(books)
        self.books = books
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
        args = dict(args)
        books = args.pop(BOOKS, None)
        if books is not None and not isinstance(books, str):
            return _error([], f"{name}: {BOOKS} must be text. " + NOTHING_DONE)
        try:
            count = self.books.count()
            entry = choose(self.books.entries(), count, books, name)
        except _Stop as stop:
            if stop.value is not None:
                return {"content": render(stop.value), "isError": False}
            return _error([], stop.sentence)
        many = count > 1
        stamp = [_text(f"Books: {_shown(entry)} ({entry['books']}).")] if many else []
        try:
            client, entry = self.books.pick(entry.get("books"), name)
        except Refused as exc:
            return _error(stamp, error_sentence(exc))
        problem = check_arguments(name, tool.input_schema, args)
        if problem:
            return _error(stamp, problem)
        called = _tools.Called(
            books=entry.get("books"),
            name=_shown(entry),
            file=entry.get("file") or "",
            many=many,
        )
        token = _tools.CALLED.set(called)
        try:
            value = tool.fn(client, **args)
        except Unconfirmed:
            log.warning("tool %s: the answer did not confirm its company", name)
            return _error(stamp, _unconfirmed_sentence(_shown(entry)))
        except ApiError as exc:
            if exc.status == 401:
                self._lost(entry.get("books"))
            sentence = _books_error(exc, entry) or error_sentence(exc)
            return _error(stamp, sentence)
        except ValueError as exc:
            return _error(stamp, error_sentence(exc))
        except Exception:  # a bug here must not end the session
            log.exception("tool %s failed", name)
            return _error(
                stamp,
                f"SlowBooks Switchboard hit an internal error running {name}. "
                "The details are in its log on standard error. Try "
                "again, or another way to the same answer.",
            )
        finally:
            _tools.CALLED.reset(token)
        return {"content": stamp + render(value), "isError": False}

    def _lost(self, books) -> None:
        try:
            self.books.lost(books)
        except Exception:  # the answer still goes back
            log.exception("the books provider failed on a refused token")


def _error(stamp: list, sentence: str) -> dict:
    return {"content": stamp + [_text(sentence)], "isError": True}
