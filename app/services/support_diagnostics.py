# ============================================================================
# Support diagnostics — the environment block, the redacted log tail, and the
# subject/body an in-app "Contact support" email carries.
#
# Deliberately DEPENDENCY-FREE (stdlib only). The desktop launcher imports
# this module from its fatal-error dialog, where the app's third-party
# packages may not be importable, and the redaction has to be reviewable on
# its own. Nothing here touches the network — the no-telemetry promise holds;
# the browser opens the user's own mail client with the text we built.
#
# Redaction is BEST-EFFORT: it catches the common shapes of private data, not
# every possible one. The form tells the user to read the body before sending.
# ============================================================================

import os
import platform
import re
import sys
from urllib.parse import quote

SUPPORT_EMAIL = "support@slowbookspro.com"

# The mailto body is kept under this many characters AFTER URL-encoding:
# encodeURIComponent inflates log text (~1.4x), and ShellExecute / Outlook
# mailto limits sit near 2048. The subject takes the rest of that budget.
MAX_ENCODED_BODY = 1500


# ---------------------------------------------------------------------------
# Redaction — a pure function. Emails, names (passed in from the DB), dollar
# amounts, account numbers, street addresses, phone numbers, tax IDs and bank
# routing numbers become bracketed tokens. Timestamps, log levels and stack
# traces stay readable: the patterns below only match the sensitive shapes.
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.IGNORECASE)
# Social Security number (ddd-dd-dddd) and EIN (dd-ddddddd): both are tax IDs.
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
# North American phone. No leading \b (it can't anchor before "(" or "+"), so
# a not-word lookbehind; separators required so a bare digit run is left to
# the number rules. Starts at "(" (parens area code) and "+1 ..." forms.
_PHONE_RE = re.compile(
    r"(?<!\w)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]\d{4}(?!\d)"
)
# Amounts: $-prefixed, accounting-negative "(1,234.56)", and bare two-decimal
# numbers. The lookarounds keep version strings ("3.45.0") and timestamp
# fractions ("09:15:01.123") out of it — a two-decimal number is only an
# amount when no digit, dot or colon sits on either side.
_AMOUNT_RE = re.compile(
    r"""
      \$\s?\d[\d,]*(?:\.\d+)?                               # $-prefixed
    | \(\s?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\s?\)           # accounting negative
    | (?<![\d.:])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d.:])  # bare 2-decimal
    """,
    re.VERBOSE,
)
# A line carries an account/card/routing number only when it says so — these
# keywords, or a "#"-prefixed number. Keeps PIDs, ports, epoch seconds and row
# ids (bare 6-11 digit runs with no such context) readable.
_ACCOUNT_CONTEXT_RE = re.compile(r"account|acct|routing|aba|iban|card|swift|#\d", re.I)
_LONG_DIGITS_RE = re.compile(r"(?<!\d)\d{12,17}(?!\d)")  # unconditional
_ROUTING_RE = re.compile(r"(?<!\d)\d{9}(?!\d)")  # contextual: exactly nine
_ACCOUNT_RE = re.compile(r"(?<!\d)\d{6,11}(?!\d)")  # contextual: the rest
# Street address: a number then a few words then a street-type word.
_STREET_WORDS = (
    r"Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|"
    r"Way|Circle|Cir|Place|Pl|Terrace|Ter|Highway|Hwy|Parkway|Pkwy|Square|Sq"
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z0-9.]+\s){1,4}(?:" + _STREET_WORDS + r")\b\.?",
    re.IGNORECASE,
)


def _name_regexes(names):
    """Two compiled alternations for the name list, longest first:

    - multi-word names match case-insensitively ("acme supply co" == DB value);
    - single-word names must be >= 4 chars and match case-SENSITIVELY, so a
      company called "Log", "App" or "Main" can't blank a log line, and
      "Summit" is redacted while the English word "summit" is not.

    Interior whitespace in a name becomes ``\\s+`` (a DB "Jane  Doe" matches
    "Jane Doe"), and the whole name is wrapped in not-word lookarounds so a
    name that starts or ends in punctuation ("#1 Fence Co", "Smith & Sons,
    Inc.") is still matched.
    """
    multi, single = [], []
    for raw in names or []:
        if not raw:
            continue
        name = " ".join(str(raw).split())  # trim + collapse interior whitespace
        if not name:
            continue
        tokens = name.split(" ")
        if len(tokens) >= 2:
            multi.append(name)
        elif len(name) >= 4:
            single.append(name)

    def build(group, flags):
        if not group:
            return None
        group = sorted(set(group), key=len, reverse=True)
        alt = "|".join(r"\s+".join(re.escape(t) for t in n.split(" ")) for n in group)
        return re.compile(r"(?<!\w)(?:" + alt + r")(?!\w)", flags)

    return build(multi, re.IGNORECASE), build(single, 0)


def _redact_numbers(text):
    """Account / routing numbers, line by line. 12-17 digit runs are always
    redacted; 6-11 digit runs (routing is nine) only on a line whose words
    show it is an account number."""
    out = []
    for line in text.split("\n"):
        line = _LONG_DIGITS_RE.sub("[account]", line)
        if _ACCOUNT_CONTEXT_RE.search(line):
            line = _ROUTING_RE.sub("[routing]", line)
            line = _ACCOUNT_RE.sub("[account]", line)
        out.append(line)
    return "\n".join(out)


def redact(text, names=None):
    """Replace sensitive values in ``text`` with bracketed tokens. Pure — reads
    nothing and calls nothing external. Best-effort by design (see header)."""
    if not text:
        return text or ""
    out = text
    out = _EMAIL_RE.sub("[email]", out)
    out = _SSN_RE.sub("[tax-id]", out)
    out = _EIN_RE.sub("[tax-id]", out)
    out = _PHONE_RE.sub("[phone]", out)
    out = _ADDRESS_RE.sub("[address]", out)
    out = _AMOUNT_RE.sub("[amount]", out)
    out = _redact_numbers(out)
    # Names last, so a name that looks like nothing else is still caught.
    multi_re, single_re = _name_regexes(names)
    if multi_re:
        out = multi_re.sub("[name]", out)
    if single_re:
        out = single_re.sub("[name]", out)
    return out


# ---------------------------------------------------------------------------
# What the app is running. Reused from the same flags system.py reads, so the
# support block and the update check never disagree about the install kind.
# ---------------------------------------------------------------------------


def detect_platform():
    """macOS / Windows / Linux — the human name, for the subject line."""
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform == "win32":
        return "Windows"
    if sys.platform.startswith("linux"):
        return "Linux"
    return sys.platform or "unknown"


def detect_deployment():
    """native / Server Edition / self-managed.

    The server process sets SLOWBOOKS_DESKTOP=1 for every server the launcher
    starts and SLOWBOOKS_SERVER_MODE=1 only when it serves beyond loopback
    (--serve-lan). A browser install with neither flag is self-managed: Docker,
    or a plain source run (nothing distinguishes the two from inside).
    NOTE: these flags live in the SERVER process; the launcher process itself
    passes its deployment explicitly (see desktop_launcher._support_mailto).
    """
    desktop = os.environ.get("SLOWBOOKS_DESKTOP") == "1"
    server_mode = os.environ.get("SLOWBOOKS_SERVER_MODE") == "1"
    if desktop and server_mode:
        return "Server Edition"
    if desktop:
        return "native"
    return "self-managed"


def is_native():
    """The native desktop window — the only place Reveal log file makes sense
    (a local folder to open; a browser, Server Edition or self-managed client
    has none)."""
    return detect_deployment() == "native"


def app_commit():
    """The 12-character commit this build was made from, or None (#816).
    One source of truth with About and the smoke test: app.build_info reads
    SLOWBOOKS_COMMIT, then the BUILD_SHA file the packaged builds bake. Never
    shells out — a frozen app has no git, and this must not fail."""
    try:
        from app.build_info import build_sha

        return build_sha()
    except Exception:
        return None


def python_version():
    return platform.python_version()


def environment_block(version, commit, platform_name, python, deployment, browser=None):
    """The fixed facts a support email needs, one per line. Carries no PII, so
    it is placed as-is; the log tail below it is what gets redacted. The browser
    / web-view UA is included whenever known — it matters most on the native
    window, where the WebView2 / WKWebView version is the common culprit."""
    lines = [
        # <version>+<sha>, the build identity About and the smoke test show
        # (#816); the bare version when the commit is not known.
        f"Version: v{version}" + (f"+{commit}" if commit else ""),
        f"Platform: {platform_name}",
        f"Python: {python}",
        f"Deployment: {deployment}",
    ]
    if browser:
        lines.append(f"Browser: {browser}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The log tail. Never raises if the file is missing or unreadable — a support
# email is still useful without it, and the form says the log was not found.
# ---------------------------------------------------------------------------


def read_log_tail(path, lines=50):
    """Return ``(text, available)`` for the last ``lines`` of ``path``.

    ``available`` is False (and ``text`` empty) when the file is missing or
    cannot be read. The caller redacts ``text`` before placing it.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
        return "".join(tail).rstrip("\n"), True
    except (OSError, ValueError):
        return "", False


# ---------------------------------------------------------------------------
# The email. The JS form builds its own mailto (it has the user's typed text
# live), but the subject/body shape lives here so the desktop launcher's
# error dialog sends exactly the same thing.
# ---------------------------------------------------------------------------


def short_title(what_happened, limit=60):
    """The first ~``limit`` characters of what happened, one line, for the
    subject's tail. Empty text gives a neutral placeholder."""
    text = " ".join((what_happened or "").split())
    if not text:
        return "support request"
    return text[:limit].rstrip()


def build_subject(version, platform_name, deployment, what_happened):
    """[SBP] v<version> · <platform> <deployment> · <short title>"""
    return (
        f"[SBP] v{version} · {platform_name} {deployment} "
        f"· {short_title(what_happened)}"
    )


def _encoded_len(text):
    """How long ``text`` is once mailto-encoded. quote with no safe set
    over-counts slightly vs. encodeURIComponent, which keeps the budget on the
    safe side."""
    return len(quote(text, safe=""))


_TRIM_FOOTER = "\n(log trimmed — use Copy diagnostics for the full block)"


def build_body(
    what_happened,
    what_expected,
    reply_to,
    env_block,
    log_tail,
    log_available=True,
    log_note="",
    max_encoded=MAX_ENCODED_BODY,
):
    """Assemble the email body, trimming the LOG TAIL (never the user's own
    text) until the URL-encoded body is within ``max_encoded``. When even the
    header alone is over budget, the tail is dropped entirely; the user's text
    is left intact and the subject carries the gist."""
    header_parts = [
        "What happened:",
        (what_happened or "").strip() or "(not described)",
        "",
        "What you expected:",
        (what_expected or "").strip() or "(not described)",
        "",
    ]
    if reply_to:
        header_parts += [f"Reply to: {reply_to}", ""]
    header_parts += ["--- Environment ---", env_block, "", "--- Recent log ---"]
    header = "\n".join(header_parts) + "\n"

    if not log_available:
        return header + (log_note or "(log file not found)")

    tail = log_tail or "(log is empty)"
    full = header + tail
    if _encoded_len(full) <= max_encoded:
        return full

    # Need to trim. Reserve the footer, then binary-search the largest number
    # of trailing log characters (the newest lines, where the error is) that
    # still fits. If nothing fits, drop the tail and keep the user's text.
    base = header + _TRIM_FOOTER + "\n"
    if _encoded_len(base) > max_encoded:
        return header.rstrip()
    lo, hi = 0, len(tail)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _encoded_len(base + tail[-mid:]) <= max_encoded:
            lo = mid
        else:
            hi = mid - 1
    return base + tail[-lo:] if lo else header.rstrip()
