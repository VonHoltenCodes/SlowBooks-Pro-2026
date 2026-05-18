# ============================================================================
# Phase 11: Fuzzy duplicate detection for customer/vendor names.
#
# Strategy:
#   1. Normalize candidate names: lowercase, strip business suffixes
#      (Inc, LLC, Corp, Co, Ltd), collapse whitespace, drop punctuation.
#   2. Fast-path: exact match on the normalized form gets a similarity of 1.0.
#   3. Otherwise use difflib.SequenceMatcher.ratio() against every active
#      record. Above DEFAULT_THRESHOLD (0.85) is a "possible duplicate".
#
# Uses stdlib only — difflib ships with Python. No external dep needed.
# For larger datasets we'd build a trigram index; at QuickBooks-sized
# customer lists (hundreds to low thousands) this is fine.
# ============================================================================

import re
from difflib import SequenceMatcher

DEFAULT_THRESHOLD = 0.85

# Common business suffixes that are NOT meaningful for dedup.
# "Acme Inc" and "Acme, Inc." and "ACME LLC" all collapse to "acme".
_SUFFIX_RE = re.compile(
    r"\b("
    r"inc|incorporated|corp|corporation|co|company|"
    r"ltd|limited|llc|l\.l\.c|llp|lp|pllc|"
    r"plc|gmbh|ag|sa|s\.a|bv|pty|"
    r"the"
    r")\b\.?",
    flags=re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")
# Keep letters/digits/whitespace only
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_name(name: str) -> str:
    """Canonicalize a customer/vendor name for similarity comparison.

    Example: 'Acme, Inc.' and 'ACME LLC' both return 'acme'.
    """
    if not name:
        return ""
    s = name.lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _SUFFIX_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def similarity(a: str, b: str) -> float:
    """Return 0.0–1.0 similarity between two names after normalization."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # SequenceMatcher uses a Ratcliff-Obershelp sequence match, which catches
    # typos, transpositions, and partial matches well enough for names.
    return SequenceMatcher(None, na, nb).ratio()


def find_duplicates(candidate_name: str, existing_rows, threshold: float = DEFAULT_THRESHOLD):
    """Return rows whose name similarity to `candidate_name` meets `threshold`.

    `existing_rows` is any iterable of ORM objects with `.id` and `.name`.
    Result is sorted by similarity descending, highest-match first.
    """
    if not candidate_name:
        return []
    matches = []
    for row in existing_rows:
        score = similarity(candidate_name, row.name)
        if score >= threshold:
            matches.append((score, row))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [
        {"id": row.id, "name": row.name, "similarity": round(score, 3)}
        for score, row in matches
    ]
