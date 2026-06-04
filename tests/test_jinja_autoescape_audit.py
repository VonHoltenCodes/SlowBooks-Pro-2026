"""
Jinja2 autoescape audit as a unit test.

Catches the XSS-via-template-rendering pattern that WC3D fixed in
commit ca6182f (`app/routes/public.py` + `app/services/pdf_service.py`)
and that this branch's red-team pass found in one more spot
(`app/services/email_service.py` SandboxedEnvironment + a fallback
f-string in the same module).

The pattern:
    env = Environment(loader=FileSystemLoader(...))         # autoescape OFF
    env = SandboxedEnvironment()                            # autoescape OFF
    Template(unsafe_string).render(user_input=...)          # autoescape OFF

The fix:
    env = Environment(loader=FileSystemLoader(...), autoescape=True)
    env = SandboxedEnvironment(autoescape=True)

Jinja2 defaults autoescape to False. Any Environment created without
explicitly setting autoescape=True will render `<script>...</script>`
in user-controlled data verbatim. For HTML/email contexts this is XSS.

This test scans every Environment / SandboxedEnvironment construction
under `app/` and fails if any one is missing the autoescape argument.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

# Locate the start of each `Environment(` or `SandboxedEnvironment(` call.
# The paren walker below finds the matching `)` — which a single `[^)]*`
# regex can't do because Environment(loader=FileSystemLoader(...)) has
# nested parens.
_ENV_CALL_START = re.compile(r"\b(?:Sandboxed)?Environment\s*\(")


def _args_of_call(text: str, open_paren_idx: int) -> str:
    """Return the text between matching parens starting at open_paren_idx.

    Walks forward counting paren depth. Handles nested calls
    (`Environment(loader=FileSystemLoader(...), ...)`) which a simple
    [^)]* regex can't. Ignores parens inside string literals — handled
    by tracking a "we're inside a string" flag.
    """
    depth = 0
    in_str = None  # None, '"', "'", or '"""' / "'''"
    i = open_paren_idx
    n = len(text)
    start = None
    while i < n:
        ch = text[i]
        # Handle triple-quoted strings before single
        if not in_str and i + 2 < n and text[i : i + 3] in ('"""', "'''"):
            in_str = text[i : i + 3]
            i += 3
            continue
        if in_str in ('"""', "'''") and text[i : i + 3] == in_str:
            in_str = None
            i += 3
            continue
        if not in_str and ch in ('"', "'"):
            in_str = ch
            i += 1
            continue
        if in_str in ('"', "'") and ch == in_str and text[i - 1] != "\\":
            in_str = None
            i += 1
            continue
        if in_str:
            i += 1
            continue
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return ""  # unterminated; treat as empty args


def test_every_jinja2_environment_has_autoescape():
    """Every Jinja2 Environment / SandboxedEnvironment must set autoescape.

    Jinja2 defaults autoescape to False — silently. Any HTML or email
    template rendered from such an environment is XSS-vulnerable when
    the context contains user-controlled strings (customer names, memo
    fields, anything from a public-facing form).
    """
    offenders = []
    for py in APP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in _ENV_CALL_START.finditer(text):
            args = _args_of_call(text, m.end() - 1)
            if "autoescape" not in args:
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(
                    f"  {py.relative_to(APP_DIR.parent)}:{line}  "
                    f"Environment(...) without `autoescape=`"
                )
    assert not offenders, (
        "Jinja2 Environments missing autoescape=True (XSS risk):\n"
        + "\n".join(offenders)
        + "\n\nAdd `autoescape=True` to the constructor. See "
        "tests/test_jinja_autoescape_audit.py for the rule."
    )
