# ============================================================================
# Merchant template memory — v3 of the receipt intake plan.
# See docs/design/receipt-intake.md § delivery plan step 3.
#
# The idea: when an operator corrects a box on the canvas, remember WHERE
# that field lives on this merchant's receipts — relative to stable anchor
# text, never absolute pixels (receipt length and photo scale vary). Next
# scan from the same merchant, propose (or silently apply) the remembered
# boxes. Template learning, honestly framed: anchor matching plus operator
# feedback, no ML.
#
# This module is the pure logic: merchant-key normalization, anchor
# selection, offset encoding, and resolution against a new scan's word
# boxes. Persistence and endpoints live above it.
# ============================================================================

import re
from typing import Optional

# Store-number / location suffixes that make one chain look like many
# merchants: "HOME DEPOT #4521 SHOREWOOD" -> "HOME DEPOT". Runs AFTER
# whitespace collapse, so a single optional space suffices — an unbounded
# \s* before \d is polynomial on adversarial all-space input (CodeQL
# py/polynomial-redos, and it's right).
_STORE_NUM_RE = re.compile(r"#? ?\d{2,6}\b")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def merchant_key(name: str) -> Optional[str]:
    """Normalize a merchant string into a template key.

    Uppercase, strip punctuation, drop store numbers and trailing
    single-token location words are NOT guessed at (a city name is
    indistinguishable from a brand word); the store-number strip alone
    collapses the common chain case. Returns None when nothing usable
    remains.
    """
    if not name:
        return None
    # Cap the input (merchant strings come from OCR or the client) and
    # collapse whitespace BEFORE the store-number strip — see _STORE_NUM_RE.
    key = _NON_ALNUM_RE.sub(" ", name.strip()[:200].upper())
    key = _WS_RE.sub(" ", key).strip()
    key = _STORE_NUM_RE.sub(" ", key)
    key = _WS_RE.sub(" ", key).strip()
    return key or None


def keys_match(a: Optional[str], b: Optional[str]) -> bool:
    """Two keys refer to the same merchant: exact, or one is a word-prefix
    of the other ("HOME DEPOT" vs "HOME DEPOT PRO DESK")."""
    if not a or not b:
        return False
    if a == b:
        return True
    aw, bw = a.split(), b.split()
    shorter, longer = (aw, bw) if len(aw) <= len(bw) else (bw, aw)
    return len(shorter) >= 2 and longer[: len(shorter)] == shorter


# ---------------------------------------------------------------------------
# Anchor-relative encoding.
#
# A field box is stored as: the anchor word's text + the box's offset from
# that anchor's top-left, all scaled by the anchor's HEIGHT (text height is
# the stable unit across DPI/photo scale; page width is not).
# ---------------------------------------------------------------------------


def _norm_word(text: str) -> str:
    return _NON_ALNUM_RE.sub("", text.upper())


def _text_counts(words: list[dict]) -> dict:
    counts: dict = {}
    for w in words:
        t = _norm_word(w["text"])
        counts[t] = counts.get(t, 0) + 1
    return counts


def pick_anchor(box: dict, words: list[dict]) -> Optional[dict]:
    """The anchor for a field box: prefer a same-line word to its LEFT that
    looks like a label (alphabetic, e.g. TOTAL/TAX/DATE); fall back to the
    topmost alphabetic word on the page (the merchant block).

    Among same-line labels, a word that appears only ONCE on the page wins
    over a nearer repeated one: "Total Sales (Inclusive of GST) : 29.68"
    anchors on INCLUSIVE, not GST — on a GST receipt "GST" shows up in the
    GST ID line, the exclusive/inclusive totals and the tax line, and the
    SkyTech lap (2026-09-02) saw a tax template resolve onto the GST ID
    number.  When every label on the line repeats, the ordinal is stored
    alongside (see encode_field)."""
    cy = box["top"] + box["height"] / 2.0
    same_line = [
        w
        for w in words
        if w["top"] <= cy <= w["top"] + w["height"]
        and w["left"] + w["width"] <= box["left"] + box["width"]
        and _norm_word(w["text"]).isalpha()
        and len(_norm_word(w["text"])) >= 3
    ]
    if same_line:
        counts = _text_counts(words)
        unique = [w for w in same_line if counts[_norm_word(w["text"])] == 1]
        return max(unique or same_line, key=lambda w: w["left"])  # nearest on the left
    page_words = [
        w
        for w in words
        if _norm_word(w["text"]).isalpha() and len(_norm_word(w["text"])) >= 3
    ]
    if page_words:
        return min(page_words, key=lambda w: (w["top"], w["left"]))
    return None


def _same_text(anchor: dict, words: list[dict]) -> list[dict]:
    """Every word with the anchor's normalized text, in reading order."""
    target = _norm_word(anchor["text"])
    return sorted(
        (w for w in words if _norm_word(w["text"]) == target),
        key=lambda w: (w["top"], w["left"]),
    )


def encode_field(box: dict, words: list[dict]) -> Optional[dict]:
    """Encode a corrected field box relative to its anchor. Returns the
    storable dict or None when no usable anchor exists."""
    anchor = pick_anchor(box, words)
    if anchor is None or anchor["height"] <= 0:
        return None
    unit = float(anchor["height"])
    # A repeated anchor word is disambiguated by its ordinal in reading
    # order plus how many there were — resolution insists both still hold.
    peers = _same_text(anchor, words)
    return {
        "anchor_text": _norm_word(anchor["text"]),
        "anchor_index": peers.index(anchor),
        "anchor_count": len(peers),
        "dx": (box["left"] - anchor["left"]) / unit,
        "dy": (box["top"] - anchor["top"]) / unit,
        "w": box["width"] / unit,
        "h": box["height"] / unit,
    }


def resolve_field(encoded: dict, words: list[dict]) -> Optional[dict]:
    """Resolve a stored field against a NEW scan's words: find the anchor
    word by normalized text, apply the height-scaled offsets. None when the
    anchor doesn't appear (layout changed -> canvas takes over).

    A repeated anchor word ("GST" four times on a Malaysian tax invoice) is
    only trusted when the new scan repeats it the same number of times —
    then the stored ordinal picks the right one.  Any other repeat count,
    or a pre-ordinal template meeting a repeated anchor, is ambiguous and
    fails closed rather than guessing the topmost hit."""
    target = encoded.get("anchor_text") or ""
    if not target:
        return None
    candidates = sorted(
        (w for w in words if _norm_word(w["text"]) == target),
        key=lambda w: (w["top"], w["left"]),
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        anchor = candidates[0]
    else:
        index = encoded.get("anchor_index")
        if index is None or encoded.get("anchor_count") != len(candidates):
            return None
        if not 0 <= index < len(candidates):
            return None
        anchor = candidates[index]
    unit = float(anchor["height"]) or 1.0
    return {
        "left": int(round(anchor["left"] + encoded["dx"] * unit)),
        "top": int(round(anchor["top"] + encoded["dy"] * unit)),
        "width": max(4, int(round(encoded["w"] * unit))),
        "height": max(4, int(round(encoded["h"] * unit))),
    }
