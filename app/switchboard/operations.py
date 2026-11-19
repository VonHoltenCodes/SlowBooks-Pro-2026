# Vendored from SlowBooks Switchboard: sbplive/operations.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# The general tools: every operation in the app's own /openapi.json is
# reachable through them, so they are never out of date with the installed
# version. The document is read once per process through the client (cached
# on it, as tools.py's _spec does).
#
# Reads and writes are separate tools, as the connector directory's review
# asks (a tool that takes both safe and unsafe methods is rejected): api_get
# sends GET only, post_operation POST, update_operation PUT or PATCH and
# delete_operation DELETE. find_operations and describe_operation only read
# the document.
#
# The write tools refuse nothing the API would decide: the token's role, the
# closing date and validation are the API's, admin areas included (tokens,
# users, backups, the company file, system), and the API's answer is what
# the person hears. It refuses only what it cannot send (an unknown
# operation, a missing path value, a stray parameter, a key that is not a
# key, a file upload), what is not data (a PDF, a CSV or IIF file, a print
# page), and the two things client.refusal() keeps to SlowBooks Pro.
# ============================================================================

import difflib
import re
from urllib.parse import quote

from app.switchboard import tools
from app.switchboard.client import (
    ApiClient,
    reachable,
    refusal,
    unreachable_sentence,
)

METHODS = ("get", "post", "put", "patch", "delete")
# The tool that sends each method.
TOOL_FOR = {
    "get": "api_get",
    "post": "post_operation",
    "put": "update_operation",
    "patch": "update_operation",
    "delete": "delete_operation",
}

# The routes that honour an Idempotency-Key header (the product's
# request_keys service). The OpenAPI document does not declare the header, so
# it is named here; a route whose document does name it is keyed too.
KEYED = {
    ("post", "/api/payments"),
    ("post", "/api/bill-payments"),
    ("post", "/api/payments/{payment_id}/discount"),
    ("post", "/api/bill-payments/{bill_payment_id}/discount"),
}
_KEY_HEADER = "idempotency-key"
# Headers the client sets itself; a parameter never overrides them.
_OWN_HEADERS = {"authorization", "cookie", "host", "content-type", "accept"}
_DESCRIPTION_CHARS = 1500
_MAX_SCHEMAS = 60


def _spec(client: ApiClient) -> dict:
    return tools._spec(client)


def _operations(client: ApiClient):
    """(method, path, operation) for every operation the program reaches."""
    for path, item in _spec(client).get("paths", {}).items():
        if not reachable(path) or not isinstance(item, dict):
            continue
        for method in METHODS:
            op = item.get(method)
            if isinstance(op, dict):
                yield method, path, op


def _first_sentence(text: str, limit: int = 200) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    end = re.search(r"(?<=[.!?])\s", text)
    if end:
        text = text[: end.start()]
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _summary(op: dict) -> str:
    summary = op.get("summary") or ""
    first = _first_sentence(op.get("description") or "")
    if first and first.lower().rstrip(".") != summary.lower():
        return f"{summary}: {first}" if summary else first
    return summary


def is_keyed(method: str, path: str, op: dict) -> bool:
    if (method, path) in KEYED:
        return True
    for p in op.get("parameters") or []:
        if p.get("in") == "header" and str(p.get("name", "")).lower() == _KEY_HEADER:
            return True
    return False


def file_kind(method: str, path: str, op: dict) -> str | None:
    """'a PDF', 'a CSV file', 'a print page' when the answer is not data.

    The document declares most file routes as JSON (FastAPI's default), so
    the path decides first: a pdf, csv, iif, print or download segment."""
    if method != "get":
        return None
    segments = [x.lower() for x in path.strip("/").split("/")]
    last = segments[-1] if segments else ""
    if last == "pdf" or last.endswith(".pdf"):
        return "a PDF"
    if last == "csv" or last.endswith(".csv") or "csv" in segments[:-1]:
        return "a CSV file"
    if "iif" in segments:
        return "an IIF file"
    if last in ("print", "print-preview"):
        return "a print page"
    if "download" in segments:
        return "a file"
    types = set()
    for code, resp in (op.get("responses") or {}).items():
        if str(code).startswith("2"):
            types |= set((resp.get("content") or {}).keys())
    if types and "application/json" not in types:
        return "a print page" if "text/html" in types else "a file"
    return None


# ---------------------------------------------------------------------------
# Finding an operation by method and path (a template or a concrete path).
# ---------------------------------------------------------------------------


def _template_regex(template: str):
    parts = re.split(r"(\{[^}]+\})", template)
    pattern = "".join(
        (
            "(?P<{}>[^/]+)".format(re.sub(r"\W", "_", p[1:-1]))
            if p.startswith("{")
            else re.escape(p)
        )
        for p in parts
    )
    return re.compile(pattern + r"\Z")


def _norm_method(method) -> str:
    m = str(method or "").strip().lower()
    if m not in METHODS:
        raise ValueError(
            f"method must be one of {', '.join(x.upper() for x in METHODS)}, "
            f"not {method!r}."
        )
    return m


def lookup(client: ApiClient, method, path) -> tuple:
    """(method, template, operation, values found in a concrete path)."""
    m = _norm_method(method)
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(
            "path must be an API path that starts with /, such as /api/invoices."
        )
    if "?" in path or "#" in path:
        raise ValueError(
            "Put query values in params, not in the path: "
            f"{path.split('?')[0]} with params {{...}}."
        )
    if not reachable(path):
        raise ValueError(unreachable_sentence(path))
    paths = _spec(client).get("paths", {})
    if path in paths:
        op = paths[path].get(m)
        if not isinstance(op, dict):
            raise ValueError(_wrong_method(m, path, methods_at(client, path)))
        return m, path, op, {}
    # A concrete path such as /api/invoices/12: match it to its template,
    # preferring the template with the fewest placeholders.
    matches = []
    for template, item in paths.items():
        if "{" not in template or not isinstance(item.get(m), dict):
            continue
        found = _template_regex(template).match(path)
        if found:
            matches.append((template.count("{"), template, found))
    if matches:
        _, template, found = min(matches)
        names = re.findall(r"\{([^}]+)\}", template)
        values = dict(zip(names, found.groups()))
        return m, template, paths[template][m], values
    others = methods_at(client, path)
    if others:
        raise ValueError(_wrong_method(m, path, others))
    close = difflib.get_close_matches(path, list(paths), n=3, cutoff=0.6)
    hint = f" Closest: {', '.join(close)}." if close else ""
    raise ValueError(
        f"SlowBooks Pro has no {m.upper()} {path}.{hint} "
        "Use find_operations to look it up."
    )


def methods_at(client: ApiClient, path: str) -> list:
    """The methods a path (a template or a concrete path) takes, in order."""
    paths = _spec(client).get("paths", {})
    items = [paths[path]] if path in paths else []
    if not items:
        items = [
            item
            for template, item in paths.items()
            if "{" in template and _template_regex(template).match(path)
        ]
    return [m for m in METHODS if any(isinstance(i.get(m), dict) for i in items)]


def _wrong_method(m: str, path: str, have: list) -> str:
    """The sentence for a method the path does not take, naming the tools."""
    ways = ", ".join(f"{x.upper()} ({TOOL_FOR[x]})" for x in have)
    return f"{path} does not take {m.upper()}; it takes {ways}."


# ---------------------------------------------------------------------------
# find_operations
# ---------------------------------------------------------------------------


def _words(text: str) -> list:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def find_operations(client: ApiClient, query: str, limit: int = 20):
    """Operations whose path, summary or tags match the words."""
    words = [_stem(w) for w in _words(query)]
    if not words:
        raise ValueError(
            "query needs at least one word, such as 'invoice' or 'payment'."
        )
    limit = max(1, min(int(limit), 50))
    scored = []
    for method, path, op in _operations(client):
        main = " ".join([path, op.get("summary") or "", " ".join(op.get("tags") or [])])
        main_words = {_stem(w) for w in _words(main)}
        desc = {_stem(w) for w in _words(op.get("description") or "")}
        main_hits = sum(1 for w in words if w in main_words)
        desc_hits = sum(1 for w in words if w in desc and w not in main_words)
        prefix_hits = sum(
            1
            for w in words
            if w not in main_words and any(m.startswith(w) for m in main_words)
        )
        hits = main_hits + prefix_hits + desc_hits
        if not hits:
            continue
        everything = (main_hits + prefix_hits + desc_hits) == len(words)
        score = (everything, main_hits * 3 + prefix_hits * 2 + desc_hits)
        scored.append((score, -len(path), method, path, op))
    if not scored:
        return (
            f"No operation matches {query!r}. Try fewer words or other ones, "
            "such as 'invoice', 'bill payment' or 'profit loss'."
        )
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
    out = []
    for _, _, method, path, op in scored[:limit]:
        row = {
            "method": method.upper(),
            "path": path,
            "does": _summary(op),
            "tool": TOOL_FOR[method],
        }
        kind = file_kind(method, path, op)
        if kind:
            row["note"] = f"returns {kind} for a person to open, not data"
        if is_keyed(method, path, op):
            row["idempotency_key"] = "required"
        out.append(row)
    if len(scored) > limit:
        out.append(
            {
                "more": f"{len(scored) - limit} more match. Add a word to narrow "
                "the search, or raise limit (up to 50)."
            }
        )
    return out


# ---------------------------------------------------------------------------
# describe_operation
# ---------------------------------------------------------------------------

_DROP_KEYS = {"title"}  # pydantic's titles repeat the field name


def _ref_name(ref: str) -> str | None:
    prefix = "#/components/schemas/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None


def _clean(node, refs: list):
    """The schema without titles; component refs noted and kept short."""
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str):
            name = _ref_name(node["$ref"])
            if name:
                if name not in refs:
                    refs.append(name)
                return {"$ref": name}
        return {
            k: _clean(v, refs)
            for k, v in node.items()
            if not (k in _DROP_KEYS and isinstance(v, str))
        }
    if isinstance(node, list):
        return [_clean(v, refs) for v in node]
    return node


def _resolve_param(client: ApiClient, p: dict) -> dict:
    if "$ref" in p:
        target = _spec(client)
        for part in p["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return target
    return p


def describe_operation(client: ApiClient, method: str, path: str) -> dict:
    """Parameters and request body of one operation, references resolved."""
    m, template, op, _ = lookup(client, method, path)
    refs: list = []
    out: dict = {"method": m.upper(), "path": template}
    if op.get("summary"):
        out["summary"] = op["summary"]
    desc = (op.get("description") or "").strip()
    if desc:
        if len(desc) > _DESCRIPTION_CHARS:
            desc = desc[:_DESCRIPTION_CHARS].rstrip() + " … (cut)"
        out["description"] = desc
    params = []
    for raw in op.get("parameters") or []:
        p = _resolve_param(client, raw)
        if p.get("in") == "header" and str(p.get("name", "")).lower() in (
            _OWN_HEADERS | {_KEY_HEADER}
        ):
            continue
        row = {
            "name": p.get("name"),
            "in": p.get("in"),
            "required": bool(p.get("required")),
        }
        if p.get("description"):
            row["description"] = p["description"]
        if p.get("schema"):
            row["schema"] = _clean(p["schema"], refs)
        params.append(row)
    if params:
        out["parameters"] = params
    body = op.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content") or {}
        media = content.get("application/json") or next(iter(content.values()), {})
        out["request_body"] = {
            "required": bool(body.get("required")),
            "schema": _clean((media or {}).get("schema") or {}, refs),
        }
        if "application/json" not in content and content:
            out["request_body"]["note"] = (
                f"This operation takes {', '.join(content)}, which "
                "cannot be sent from here; do it in SlowBooks Pro."
            )
    schemas = _spec(client).get("components", {}).get("schemas", {})
    resolved: dict = {}
    i = 0
    while i < len(refs) and len(resolved) < _MAX_SCHEMAS:
        name = refs[i]
        i += 1
        if name in schemas:
            resolved[name] = _clean(schemas[name], refs)
    if resolved:
        out["schemas"] = resolved
    if len(refs) > len(resolved):
        out["schemas_cut"] = (
            f"{len(refs) - len(resolved)} more nested schemas were left out; "
            "describe the operation that creates that record to see them."
        )
    kind = file_kind(m, template, op)
    if kind:
        out["returns"] = (
            f"{kind}, which is not returned here: the person opens it in "
            "SlowBooks Pro."
        )
    out["idempotency_key"] = (
        "required: pass a stable idempotency_key so a retry books it once"
        if is_keyed(m, template, op)
        else "not taken by this operation"
    )
    out["how_to_call"] = _how_to_call(client, m, template)
    return out


def _how_to_call(client: ApiClient, m: str, template: str) -> str:
    how = f"{TOOL_FOR[m]} with this path"
    if m in ("put", "patch") and {"put", "patch"} <= set(methods_at(client, template)):
        how += f" and method {m.upper()}"
    how += "; put path and query parameters in params by name"
    if m in ("get", "delete"):
        return how + "."
    return how + ", and the request body in body."


# ---------------------------------------------------------------------------
# api_get and the write tools
# ---------------------------------------------------------------------------


def _refuse_files(client: ApiClient, m: str, template: str, op: dict) -> None:
    """A route whose answer is a PDF, a file or a print page is not sent."""
    kind = file_kind(m, template, op)
    if not kind:
        return
    parent = template.rstrip("/").rsplit("/", 1)[0]
    paths = _spec(client).get("paths", {})
    hint = (
        f" The same data comes as JSON from GET {parent}."
        if isinstance(paths.get(parent, {}).get("get"), dict)
        and not file_kind("get", parent, paths[parent]["get"])
        else ""
    )
    raise ValueError(
        f"{m.upper()} {template} returns {kind} for a person to open, which "
        f"is not returned here; they can open it in SlowBooks Pro.{hint}"
    )


def _query_value(name: str, value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, list) and all(
        isinstance(v, (int, float, str, bool)) for v in value
    ):
        return [_query_value(name, v) for v in value]
    raise ValueError(
        f"The query parameter {name} must be text, a number, true or false, "
        "or a list of those."
    )


def build_request(
    client: ApiClient,
    method: str,
    path: str,
    params: dict | None = None,
    body=None,
    idempotency_key: str | None = None,
) -> dict:
    """The request a write tool would send, as a dict; nothing is sent."""
    m, template, op, from_path = lookup(client, method, path)
    _refuse_files(client, m, template, op)
    kept = refusal(m, template)
    if kept:
        raise ValueError(kept)
    content = (op.get("requestBody") or {}).get("content") or {}
    if content and "application/json" not in content:
        raise ValueError(
            f"{m.upper()} {template} takes a file upload ({', '.join(content)}), "
            "which cannot be sent from here: the person does it in SlowBooks Pro."
        )
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(  # noqa: TRY004 - the tools report ValueError as a sentence
            "params must be an object of parameter names to values."
        )
    declared = {}
    for raw in op.get("parameters") or []:
        p = _resolve_param(client, raw)
        if p.get("in") in ("path", "query", "header") and p.get("name"):
            declared[p["name"]] = p
    names_in_path = re.findall(r"\{([^}]+)\}", template)
    values = dict(from_path)
    query: dict = {}
    headers: dict = {}
    stray = []
    for name, value in params.items():
        p = declared.get(name)
        where = p.get("in") if p else ("path" if name in names_in_path else None)
        if where == "path":
            values[name] = value
        elif where == "query":
            if value is not None:
                query[name] = _query_value(name, value)
        elif where == "header" and name.lower() not in _OWN_HEADERS | {_KEY_HEADER}:
            if value is not None:
                headers[name] = str(value)
        else:
            stray.append(name)
    if stray:
        known = [n for n in declared if n.lower() not in _OWN_HEADERS | {_KEY_HEADER}]
        known += [n for n in names_in_path if n not in known]
        listed = ", ".join(known) if known else "none"
        raise ValueError(
            f"{m.upper()} {template} has no parameter {', '.join(sorted(stray))}. "
            f"Its parameters are: {listed}. Fields of the record go in body."
        )
    concrete = template
    for name in names_in_path:
        value = values.get(name)
        if value is None or isinstance(value, (bool, dict, list)) or value == "":
            raise ValueError(
                f"{m.upper()} {template} needs {name} in params, as a number "
                "or text."
            )
        concrete = concrete.replace("{" + name + "}", quote(str(value), safe=""))
    if body is not None and not isinstance(op.get("requestBody"), dict):
        raise ValueError(
            f"{m.upper()} {template} takes no request body; leave body out and "
            "put its parameters in params."
        )
    keyed = is_keyed(m, template, op)
    if keyed:
        tools.check_key(idempotency_key, "idempotency_key")
        headers["Idempotency-Key"] = idempotency_key
    return {
        "method": m.upper(),
        "path": concrete,
        "template": template,
        "params": query,
        "body": body,
        "headers": headers,
        "keyed": keyed,
    }


def _send(client: ApiClient, method, path, params, body, idempotency_key):
    """Send one write as the token and return the API's answer."""
    req = build_request(client, method, path, params, body, idempotency_key)
    headers = dict(req["headers"])
    key = headers.pop("Idempotency-Key", None)
    return tools._write(
        client,
        req["path"],
        req["body"],
        key=key,
        params=req["params"] or None,
        method=req["method"],
        headers=headers or None,
    )


def _not_here(tool: str, method: str, path) -> str:
    """The sentence for a method this write tool does not send."""
    other = TOOL_FOR[method]
    if method == "get":
        return (
            f"{tool} only changes the books. A GET is a read: use api_get with "
            f"the same path ({path}) and params."
        )
    return f"{tool} does not send {method.upper()}; use {other}."


def api_get(client: ApiClient, path: str, params: dict | None = None):
    """GET one API route and return the API's answer. Nothing is changed.

    A route the document lists is checked as the write tools check theirs
    (its parameters, path values filled in, no file answers); any other path
    goes to the API, or the client's refusal, as it is. A listed route that
    takes no GET is refused with the tool that sends what it takes."""
    try:
        lookup(client, "get", path)
    except ValueError:
        if isinstance(path, str) and reachable(path) and methods_at(client, path):
            raise  # a route that takes other methods: say which tool sends them
        return client.get(path, params)
    req = build_request(client, "get", path, params)
    return client.request("GET", req["path"], req["params"], None, req["headers"])


def post_operation(
    client: ApiClient,
    path: str,
    params: dict | None = None,
    body=None,
    idempotency_key: str | None = None,
    asked_user: bool = False,
):
    """POST to one API operation: create a record or run an action. A create
    of one of the main records asks first when it is bare (tools.ASK_FIRST)."""
    template = None
    if isinstance(path, str) and path.startswith("/"):
        try:
            template = lookup(client, "post", path)[1]
        except ValueError:
            template = None  # _send says what is wrong
    if template in tools.ASK_FIRST:
        held = tools.ask_first(
            template,
            body,
            asked_user,
            "post_operation",
            None if asked_user else tools.body_fields(client, template),
        )
        if held is not None:
            build_request(client, "post", path, params, body, idempotency_key)
            return held
        answer = _send(client, "post", path, params, body, idempotency_key)
        return tools.with_left_blank(template, body, answer)
    return _send(client, "post", path, params, body, idempotency_key)


def update_operation(
    client: ApiClient,
    path: str,
    params: dict | None = None,
    body=None,
    method: str | None = None,
):
    """PUT or PATCH one API operation: change a record."""
    if method is not None:
        m = _norm_method(method)
        if m not in ("put", "patch"):
            raise ValueError(_not_here("update_operation", m, path))
    else:
        if not isinstance(path, str) or not path.startswith("/"):
            m = "put"  # lookup says what a path must look like
        else:
            takes = [x for x in methods_at(client, path) if x in ("put", "patch")]
            if len(takes) > 1:
                raise ValueError(
                    f"{path} takes both PUT (the whole record) and PATCH (some "
                    "fields): pass method."
                )
            m = takes[0] if takes else "put"
    return _send(client, m, path, params, body, None)


def delete_operation(client: ApiClient, path: str, params: dict | None = None):
    """DELETE through one API operation: remove a record."""
    return _send(client, "delete", path, params, None, None)
