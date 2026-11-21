"""A refusal in app/routes never puts what the request carried into an
f-string (gate round 30, W-246).

Every HTTP error crosses one vocabulary swap (app/main.py). An f-string
detail is plain text, so the swap rewords all of it, what was typed with
it: a nonprofit's duplicate benefit code "CUSTOMER" read "Code DONOR
already exists". said() keeps the typed pieces as typed (terminology.py).

This test reads each module under app/routes and fails when an
HTTPException detail is an f-string (or a name assigned one in the same
function) that fills in text the request carried:

- a str field of an endpoint's own parameters (query, path, form), or of
  a pydantic body, read as ``data.field`` or out of ``data.model_dump()``
  by its key;
- an uploaded file's ``.filename`` or ``.content_type``;
- ``request.query_params``;
- anything made from those by simple steps (strip, upper, a slice,
  ``str()``, a join, assignment, a loop over them), and a helper's
  parameter in the same module that an endpoint, or another such helper,
  passes one of them to.

What it cannot see: a value handed across modules (a route calling a
service), a refusal raised outside app/routes (a service's ValueError read
back with str(exc)), a sentence another function builds and returns into
a detail (migration's _names_in_a_sentence is converted by hand and
tested in test_typed_text_as_typed_r31), and stored records' names, which
are not the request's. Those are not covered by this guard.

A detail that fills in a request value checked against a fixed set of our
own words first (a kind like "invoice") is prose, rightly reworded: those
are listed in OURS with the reason.
"""

import ast
import importlib
import pathlib
import types
import typing

from fastapi import UploadFile
from pydantic import BaseModel

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROUTES = ROOT / "app" / "routes"

# (module path, function, the filled-in expression): our own words, so the
# company's vocabulary rightly applies to them.
OURS = {
    # entity_type is refused unless it is "invoice", "bill" or "expense"
    ("app/routes/ocr.py", "attach_intake", "body.entity_type"),
}

_HTTP_VERBS = {"get", "post", "put", "patch", "delete", "api_route"}
_TEXT_METHODS = {
    "strip",
    "lstrip",
    "rstrip",
    "lower",
    "upper",
    "title",
    "casefold",
    "capitalize",
    "replace",
    "removeprefix",
    "removesuffix",
}


def _unwrap(tp):
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _unwrap(typing.get_args(tp)[0])
    if origin in (typing.Union, types.UnionType):
        return [a for a in typing.get_args(tp) if a is not type(None)]
    return [tp]


def _is_text(tp) -> bool:
    for t in _unwrap(tp):
        if t is str:
            return True
        if typing.get_origin(t) in (list, tuple, set, frozenset):
            if any(_is_text(a) for a in typing.get_args(t) if a is not Ellipsis):
                return True
    return False


def _model(tp):
    for t in _unwrap(tp):
        if isinstance(t, type) and issubclass(t, BaseModel):
            return t
    return None


def _is_upload(tp) -> bool:
    for t in _unwrap(tp):
        if (
            t is UploadFile
            or typing.get_origin(t) is list
            and any(a is UploadFile for a in typing.get_args(t))
        ):
            return True
    return False


def _is_endpoint(node) -> bool:
    for dec in node.decorator_list:
        if (
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in _HTTP_VERBS
        ):
            return True
    return False


def _model_field_is_text(model, key) -> bool:
    field = model.model_fields.get(key) if isinstance(key, str) else None
    return bool(field and _is_text(field.annotation))


class _Function:
    """One function's view: which names hold the request's text."""

    def __init__(self, node, hints, endpoint):
        self.node = node
        self.params = [
            a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        ]
        self.text: set[str] = set()
        self.models: dict[str, type] = {}
        self.dicts: dict[str, type] = {}
        for name, tp in hints.items():
            if name == "return":
                continue
            if endpoint and _is_text(tp):
                self.text.add(name)
            model = _model(tp)
            if model is not None:
                self.models[name] = model
            if _is_upload(tp):
                self.text.add(name)  # its .filename, through _attr

    def settle(self) -> bool:
        """Spread the text through assignments; True when anything was added."""
        grew = False
        for n in ast.walk(self.node):
            targets, value = [], None
            if isinstance(n, ast.Assign):
                targets, value = n.targets, n.value
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign)) and n.value is not None:
                targets, value = [n.target], n.value
            elif isinstance(n, ast.NamedExpr):
                targets, value = [n.target], n.value
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                targets, value = [n.target], n.iter
            if value is None:
                continue
            text = self.is_text(value) or (
                # a loop over what is read out of the request's text: an
                # upload's zip members, say
                isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension))
                and isinstance(value, ast.Call)
                and any(self.is_text(a) for a in value.args)
            )
            for target in targets:
                for t in _bound_names(target):
                    if t.id not in self.text and text:
                        self.text.add(t.id)
                        grew = True
                    model = self._dumped(value)
                    if model is not None and t.id not in self.dicts:
                        self.dicts[t.id] = model
                        grew = True
        return grew

    def _dumped(self, e):
        for c in ast.walk(e):
            if (
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Attribute)
                and c.func.attr == "model_dump"
                and isinstance(c.func.value, ast.Name)
                and c.func.value.id in self.models
            ):
                return self.models[c.func.value.id]
        return None

    @staticmethod
    def _query_params(e) -> bool:
        return isinstance(e, ast.Attribute) and e.attr == "query_params"

    def is_text(self, e) -> bool:
        if isinstance(e, ast.Name):
            return e.id in self.text
        if isinstance(e, ast.Attribute):
            if e.attr in ("filename", "content_type") or self._query_params(e):
                return True
            if self._query_params(e.value):
                return True
            if isinstance(e.value, ast.Name) and e.value.id in self.models:
                return _model_field_is_text(self.models[e.value.id], e.attr)
            return False
        if isinstance(e, ast.Subscript):
            if self._query_params(e.value):
                return True
            if isinstance(e.value, ast.Name) and e.value.id in self.dicts:
                key = e.slice.value if isinstance(e.slice, ast.Constant) else None
                return _model_field_is_text(self.dicts[e.value.id], key)
            return self.is_text(e.value)
        if isinstance(e, ast.Call):
            f = e.func
            if isinstance(f, ast.Attribute):
                if f.attr in _TEXT_METHODS:
                    return self.is_text(f.value)
                if f.attr == "get":
                    if self._query_params(f.value):
                        return True
                    if isinstance(f.value, ast.Name) and f.value.id in self.dicts:
                        key = (
                            e.args[0].value
                            if e.args and isinstance(e.args[0], ast.Constant)
                            else None
                        )
                        return _model_field_is_text(self.dicts[f.value.id], key)
                if f.attr == "join":
                    return any(self.is_text(a) for a in e.args)
            if isinstance(f, ast.Name) and f.id in ("str", "sorted", "list", "set"):
                return any(self.is_text(a) for a in e.args)
            return False
        if isinstance(e, ast.BinOp):
            return self.is_text(e.left) or self.is_text(e.right)
        if isinstance(e, ast.IfExp):
            return self.is_text(e.body) or self.is_text(e.orelse)
        if isinstance(e, ast.BoolOp):
            return any(self.is_text(v) for v in e.values)
        if isinstance(e, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            return self.is_text(e.elt)
        if isinstance(e, ast.JoinedStr):
            return any(
                isinstance(v, ast.FormattedValue) and self.is_text(v.value)
                for v in e.values
            )
        return False


def _bound_names(target):
    """The names an assignment binds: ``a``, ``a, b``, ``*rest``; not
    ``d[key]`` or ``obj.attr``, which bind nothing new."""
    if isinstance(target, ast.Name):
        yield target
    elif isinstance(target, (ast.Tuple, ast.List)):
        for t in target.elts:
            yield from _bound_names(t)
    elif isinstance(target, ast.Starred):
        yield from _bound_names(target.value)


def _functions(tree, module):
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        obj = getattr(module, node.name, None)
        try:
            hints = typing.get_type_hints(obj) if callable(obj) else {}
        except Exception:
            hints = {}
        out.setdefault(node.name, _Function(node, hints, _is_endpoint(node)))
    return out


def _spread_to_helpers(functions) -> bool:
    """A helper's parameter holds the request's text when a function that
    holds it passes it there."""
    grew = False
    for caller in functions.values():
        for call in ast.walk(caller.node):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)):
                continue
            helper = functions.get(call.func.id)
            if helper is None:
                continue
            given = list(zip(helper.params, call.args))
            given += [(k.arg, k.value) for k in call.keywords if k.arg]
            for param, value in given:
                if param not in helper.text and caller.is_text(value):
                    helper.text.add(param)
                    grew = True
    return grew


def _f_strings_in_details(fn):
    assigned: dict[str, list] = {}
    for n in ast.walk(fn.node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    assigned.setdefault(t.id, []).extend(
                        j for j in _text_of(n.value) if isinstance(j, ast.JoinedStr)
                    )
    for n in ast.walk(fn.node):
        if not isinstance(n, ast.Call):
            continue
        name = getattr(n.func, "id", getattr(n.func, "attr", None))
        if name != "HTTPException":
            continue
        for kw in n.keywords:
            if kw.arg != "detail":
                continue
            for part in _text_of(kw.value):
                if isinstance(part, ast.JoinedStr):
                    yield part
                else:
                    yield from assigned.get(part.id, [])


def _text_of(e):
    """The f-strings and names the detail itself is made of: joined with
    ``+``, chosen with ``if``, not passed into a call (whose own text is
    another thing)."""
    if isinstance(e, (ast.JoinedStr, ast.Name)):
        yield e
    elif isinstance(e, ast.BinOp):
        yield from _text_of(e.left)
        yield from _text_of(e.right)
    elif isinstance(e, ast.IfExp):
        yield from _text_of(e.body)
        yield from _text_of(e.orelse)
    elif isinstance(e, ast.BoolOp):
        for v in e.values:
            yield from _text_of(v)


def typed_text_in_f_string_details(path: pathlib.Path, dotted=None) -> list[str]:
    rel = path.relative_to(ROOT).as_posix() if dotted is None else path.name
    if dotted is None:
        dotted = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        if dotted.endswith(".__init__"):
            dotted = dotted[: -len(".__init__")]
    module = importlib.import_module(dotted)
    functions = _functions(ast.parse(path.read_text(encoding="utf-8")), module)
    grew = True
    while grew:
        grew = any([fn.settle() for fn in functions.values()])
        grew = _spread_to_helpers(functions) or grew
    found = []
    for fn in functions.values():
        for js in _f_strings_in_details(fn):
            for v in js.values:
                if not (isinstance(v, ast.FormattedValue) and fn.is_text(v.value)):
                    continue
                expr = ast.unparse(v.value)
                if (rel, fn.node.name, expr) in OURS:
                    continue
                found.append(f"{rel}:{js.lineno} {fn.node.name}: {{{expr}}}")
    return sorted(set(found))


def _route_files():
    return sorted(ROUTES.rglob("*.py"))


def test_no_refusal_puts_the_requests_text_in_an_f_string():
    found = []
    for path in _route_files():
        found += typed_text_in_f_string_details(path)
    assert not found, (
        "These HTTPException details fill in what the request carried with "
        "an f-string, so the vocabulary swap rewords it; build them with "
        "said() (app/services/terminology.py):\n" + "\n".join(found)
    )


def test_the_guard_sees_the_shapes_it_names(tmp_path, monkeypatch):
    """The guard itself catches each shape it says it catches, so a green
    run above means something."""
    (tmp_path / "guard_probe_r31.py").write_text("""
from fastapi import APIRouter, HTTPException, Request, UploadFile
from pydantic import BaseModel

router = APIRouter()


class Body(BaseModel):
    code: str
    count: int = 0


def _check(number):
    raise HTTPException(status_code=400, detail=f"{number[:40]} is not a number")


@router.post("/a")
def a(data: Body, q: str = ""):
    code = data.code.strip().upper()
    _check(data.code)
    changes = data.model_dump()
    if changes["code"]:
        raise HTTPException(status_code=409, detail=f"{changes['code']} again")
    if data.count:
        raise HTTPException(status_code=409, detail=f"{data.count} is fine")
    msg = f"Code {code} already exists"
    raise HTTPException(status_code=409, detail=msg + f" ({q})")


@router.post("/b")
async def b(request: Request, file: UploadFile):
    for name in sorted(request.query_params):
        raise HTTPException(status_code=422, detail=f"{name} is not a parameter")
    for member, _ in _members(file.filename):
        _bundle(member)
    raise HTTPException(status_code=400, detail=f"{file.filename} is not a zip")


def _members(filename):
    return [(filename, b"")]


def _bundle(name):
    raise HTTPException(status_code=400, detail=f"{name} has other columns")


def _ours(kind):
    raise HTTPException(status_code=502, detail=helper(f"sent_{kind}"))


def helper(text):
    return text
""")
    monkeypatch.syspath_prepend(str(tmp_path))
    found = typed_text_in_f_string_details(
        tmp_path / "guard_probe_r31.py", dotted="guard_probe_r31"
    )
    assert sorted(f.split(" ", 1)[1] for f in found) == [
        "_bundle: {name}",
        "_check: {number[:40]}",
        "a: {changes['code']}",
        "a: {code}",
        "a: {q}",
        "b: {file.filename}",
        "b: {name}",
    ]
