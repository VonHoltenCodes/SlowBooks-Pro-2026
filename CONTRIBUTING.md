# Contributing to SlowBooks Pro 2026

Thanks for considering a contribution. This is a small project — the
process is light, but a few conventions keep the codebase coherent.

## Before you start

For anything bigger than a typo or a one-line bug fix, open an issue
first so we can sanity-check direction before you write the code. Quick
fixes can just be a PR.

## Branch naming

- `claude/<short-topic>` — branches authored by Claude Code or via the
  Claude Code on the web integration
- `fix/<short-topic>` — bug fixes
- `feat/<short-topic>` — new features
- Use kebab-case for the topic (`feat/portal-cookie-session`, not
  `feat/portal_cookie_session`)

## Commit messages

We don't enforce Conventional Commits, but commit messages should:

- Start with a short imperative subject line (≤ 70 chars), no period:
  - ✅ `Fix portal token expiry comparison on SQLite`
  - ❌ `fixed the portal thing`
- Explain *why* in the body if the change isn't obvious from the diff —
  what was broken, what the user-visible effect is, why this approach
  over alternatives. Wrap body lines around 72 chars.
- Reference related issues with `Fixes #123` or `Refs #45` in the body

Commits authored via Claude Code on the web carry a session URL at the
bottom; leave that in.

## Code style

- **Python**: `black --check app/ tests/` and `ruff check app/ tests/`
  must pass. CI gates on both. Use `black app/ tests/` to auto-format.
- **JavaScript**: vanilla JS (no build step). Match the surrounding
  style; no semicolons-vs-not crusade.
- **Tests**: every behavior change comes with a test. Tests live under
  `tests/` and are run with `pytest tests/ -q`. The full suite runs in
  under 60 seconds with no network dependencies. Common fixtures
  (defined in [tests/conftest.py](tests/conftest.py)):
  - `client` — authenticated `TestClient`. Use for most tests.
  - `unauthed_client` — `TestClient` with no session. Use only for
    auth-flow tests (setup, login, logout).
  - `db_session` — isolated SQLAlchemy session backed by an in-memory
    SQLite DB; cleared between tests.
  - `seed_accounts` — chart-of-accounts pre-loaded.
  - `seed_customer` — a single active customer pre-loaded.

  Picking the wrong client fixture is the most common newbie miss:
  using `unauthed_client` against a protected route silently 401s.

## Adding a feature

A full feature touches all five layers — but **you don't always need
all five**. For a one-endpoint addition (e.g. `GET /api/hello`
returning a dict), skip straight to steps 4 + 6.

1. **Model** in `app/models/` — SQLAlchemy class plus any enum types
2. **Schema** in `app/schemas/` — Pydantic request/response shapes
3. **Service** in `app/services/` — business logic, when there is any
4. **Route** in `app/routes/` — `APIRouter` with `@router.get/post/...`
   decorators. Register it in `app/main.py`. Group routes by domain;
   add to the smallest related file, or create a new file for a new
   feature area. Don't pile unrelated endpoints into `main.py`.
5. **Frontend** in `app/static/js/` — vanilla JS module, registered as a
   hash route in `app/static/js/app.js`. Use the `API` helper from
   `app/static/js/api.js` — note `API.del` (not `API.delete`)
6. **Tests** in `tests/test_<area>.py` covering at least the happy path
   and one failure mode

### Tiniest possible example

A minimal route + test, end to end:

```python
# app/routes/hello.py
from fastapi import APIRouter
router = APIRouter(prefix="/api", tags=["hello"])

@router.get("/hello")
def hello():
    return {"message": "hi"}
```

```python
# app/main.py — add the import + include
from app.routes import hello
app.include_router(hello.router)
```

```python
# tests/test_hello.py
def test_hello(client):
    r = client.get("/api/hello")
    assert r.status_code == 200
    assert r.json() == {"message": "hi"}
```

That's the whole flow — no model, no schema, no service, no frontend
needed for an endpoint that doesn't touch the DB.

### Backend-only endpoints

The wiring audit (`tests/test_wiring.py`) catches JS callers that
point at non-existent routes (forward direction) AND backend routes
that have no SPA caller (reverse direction). If your endpoint is
deliberately not surfaced in the SPA — a webhook, an admin-only
utility, a cron-job target — add it to `_INTENTIONAL_BACKEND_ONLY`
in `tests/test_wiring.py` with a one-line comment explaining why.
Otherwise the reverse test fails and the CI build is red.

## Schema conventions

### ⚠ The `date: date` field-name-shadows-the-type collision

This one has bitten us **eleven times** across the codebase. Pydantic
v2 still has it as of 2.13. If a model has a field named `date` AND
imports `date` from `datetime` without an alias, **`Optional[date]`
on the corresponding Update model silently breaks** — every value
validates as "Input should be None":

```python
# ❌ DON'T — the field name `date` shadows the type `date`,
#          so Optional[date] becomes Optional[<the field itself>]
from datetime import date
from pydantic import BaseModel

class InvoiceUpdate(BaseModel):
    date: Optional[date] = None    # Pydantic reads this as
                                    # Optional[FieldInfo] → must be None
```

```python
# ✅ DO — alias the import so the type name and field name
#         can never collide
from datetime import date as dt_date
from pydantic import BaseModel

class InvoiceUpdate(BaseModel):
    date: Optional[dt_date] = None
```

`tests/test_schemas_audit.py` enforces this — CI fails if any schema
imports `date` without `as dt_date` AND has a field literally named
`date`. Same rule applies for any other type whose name might collide
with a sensible field name (`time`, `datetime`, `id` — though only
`date` has bitten us in practice).

## Template rendering conventions

### ⚠ Jinja2 `Environment(...)` without `autoescape=True`

Jinja2 defaults `autoescape` to **False** — silently. Every HTML or
email template rendered through such an environment is XSS-vulnerable
when the context contains user-controlled strings (customer names,
memo fields, anything from a public-facing form). This has bitten us
twice — once in WC3D's commit `ca6182f` (`app/routes/public.py` +
`app/services/pdf_service.py`) and once in this branch's red-team
sweep (`app/services/email_service.py` SandboxedEnvironment + an
f-string fallback in the same module).

```python
# ❌ DON'T — autoescape defaults to False
from jinja2 import Environment, FileSystemLoader
from jinja2.sandbox import SandboxedEnvironment

env = Environment(loader=FileSystemLoader(template_dir))
env = SandboxedEnvironment()
```

```python
# ✅ DO — pass autoescape=True explicitly to every Environment
env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
env = SandboxedEnvironment(autoescape=True)
```

And if you fall back to raw HTML interpolation (an f-string with
user-string fields), route each field through `html.escape()`:

```python
import html as _html
return f"<p>Dear {_html.escape(customer.name)},</p>"
```

`tests/test_jinja_autoescape_audit.py` enforces this — CI fails if
any `Environment(...)` or `SandboxedEnvironment(...)` call site in
`app/` is missing the `autoescape=` argument. The walker handles
nested calls like `Environment(loader=FileSystemLoader(...))` so
the rule can't be defeated by indentation.

## Frontend ↔ backend wiring

Every `API.get/post/put/del` call must hit a real handler with a
matching method. After adding endpoints, sanity-check with:

```bash
grep -rEn "API\.(get|post|put|del)\s*\(" app/static/js/*.js
grep -rn "@router\." app/routes/*.py
```

See [docs/wiring-audit.md](docs/wiring-audit.md) for the methodology.

## Security-sensitive changes

If your change touches:

- Authentication or session handling
- Cryptography (anything reading `PAYROLL_ENCRYPTION_SECRET`)
- Field-level encryption (`app/services/encryption.py`)
- The portal token flow (`app/routes/portal.py`)
- File upload handling
- Subprocess invocations (currently only `pg_dump` / `pg_restore`)
- Any startup check in `app/main.py:startup_security_checks()`

…flag it in the PR description and read [docs/security-hardening.md](docs/security-hardening.md)
first. CI runs CodeQL on every PR but that's not a substitute for a
human eye on the diff.

## Documentation

User-facing changes need:

- **CHANGELOG.md** entry under `[Unreleased]` — what the user sees
  changing, not a re-tell of the diff
- Updates to relevant sections of **README.md** if the feature surface
  changed
- Updates to **docs/** for non-trivial behavior (portal flow, security
  posture, etc.)
- Internal-only TODO items go in **docs/todo.md** (not linked from
  README)

## Pull requests

- One feature or fix per PR. Refactors that touch many files are OK if
  they're mechanical and the PR description says so.
- Run the full test suite locally before pushing:
  `python -m pytest tests/ -q`
- Run formatters before pushing: `black app/ tests/`
- Fill in the PR template — it's there to remind you to mention test
  coverage, security implications, and migrations.

## Database migrations

SQLAlchemy's `Base.metadata.create_all()` runs at startup so a fresh
install always gets the latest schema. For changes to *existing* tables
that need to ship to a deployed instance, add an Alembic migration
under `migrations/versions/`. Run the migration locally against a snapshot
of a real DB before shipping.

## Reporting security issues

**Don't open a public issue for vulnerabilities.** See
[SECURITY.md](SECURITY.md) for the responsible disclosure path.

## License

By contributing, you agree your contributions are licensed under the
same terms as the rest of the repo (see LICENSE).
