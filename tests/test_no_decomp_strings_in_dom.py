"""S1 audit follow-up: pin that decompilation-heritage debug strings
stay confined to comments and never reach the rendered DOM.

The codebase historically carried QuickBooks-2003 "decompilation flair"
in comments (removed in the 2026-08 tone-down sweep). These tests remain
as a guard against REINTRODUCTION: such strings must never appear inside
template literals or other executable code, because they then ship to the browser
and give an attacker a free fingerprinting surface (and look unprofessional
to legitimate users).

The May-2026 audit caught two such leaks:
- app/static/js/settings.js — page-header subtitle exposing
  "CPreferencesDialog — IDD_PREFERENCES @ 0x0023F800"
- app/static/js/app.js — canned error block exposing
  "Error 0x8004: ..." + "CQBView::OnActivate() failed at offset 0x00042A10"

This test scans every JS file under app/static/js/, strips comments,
and fails if any of the well-known decomp patterns survive. Future
flair belongs in comments — keep this test green and the audit gap
stays closed.
"""

import re
from pathlib import Path

import pytest

# Patterns that look like decomp-heritage debug info. Tuned to be
# specific enough that legitimate code (CSS hex colors, etc.) doesn't
# false-positive — we don't match # colors, we don't match short
# 0xNN constants, we don't match arbitrary capitalised words.
_DECOMP_PATTERNS = [
    # Hex memory offsets / Win32 constants: 0x followed by 4+ hex digits.
    # 4+ filters out short hex literals like 0xFF or 0x100; real
    # decomp offsets are always at least 4 nibbles.
    (re.compile(r"\b0x[0-9A-Fa-f]{4,}\b"), "hex memory offset / Win32 constant"),
    # MFC / decomp class names — CQB followed by capitalised name.
    (re.compile(r"\bCQB[A-Z]\w*"), "decompiled MFC/QB class name"),
    # Win32 dialog / control resource IDs.
    (re.compile(r"\bID[DC]_[A-Z][A-Z0-9_]*"), "Win32 resource ID"),
    # Specific decomp markers explicitly called out by the S1 audit.
    (re.compile(r"\bCPreferences\w*"), "decompiled preferences class"),
    (re.compile(r"\bqbw32\b", re.IGNORECASE), "QuickBooks binary name"),
]


_JS_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "js"


def _strip_comments(src: str) -> str:
    """Remove /* ... */ block comments AND // line comments.

    Naive but adequate: the codebase doesn't have // sequences inside
    string literals (URLs use `'/'` or backticks without `//`), and no
    template literal contains `*/`. If those assumptions ever break,
    upgrade this to a real tokenizer. Until then the simple regex
    pair is good enough and keeps the test self-contained.
    """
    no_block = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    no_line = re.sub(r"//[^\n]*", "", no_block)
    return no_line


@pytest.mark.parametrize(
    "js_path",
    sorted(_JS_DIR.glob("*.js")),
    ids=lambda p: p.name,
)
def test_no_decomp_strings_outside_comments(js_path):
    src = js_path.read_text(encoding="utf-8")
    code_only = _strip_comments(src)

    for pattern, label in _DECOMP_PATTERNS:
        match = pattern.search(code_only)
        if match is None:
            continue
        # Report with line number from the COMMENT-STRIPPED source so
        # the developer can locate the offending template literal.
        preceding = code_only[: match.start()]
        line_no = preceding.count("\n") + 1
        # Show ~80 chars of context around the match for quick triage.
        ctx_start = max(0, match.start() - 40)
        ctx_end = min(len(code_only), match.end() + 40)
        snippet = code_only[ctx_start:ctx_end].replace("\n", " ")
        pytest.fail(
            f"\nS1 regression: {label} found in executable code of "
            f"{js_path.name}\n"
            f"  Pattern:    {pattern.pattern!r}\n"
            f"  Match:      {match.group(0)!r}\n"
            f"  Line (in comment-stripped source): {line_no}\n"
            f"  Context:    ...{snippet}...\n"
            f"  Fix:        if it's documentation, move it into a "
            f"/* */ or // comment.\n"
            f"              if it's debug info actually leaking to the "
            f"DOM, strip it.\n"
        )
