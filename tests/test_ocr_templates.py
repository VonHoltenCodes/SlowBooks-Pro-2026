"""Merchant template memory (v3): key normalization and anchor-relative
box encoding/resolution — the pure logic under the persistence layer."""

from app.services import ocr_templates as tpl


def test_merchant_key_strips_store_numbers_and_noise():
    assert tpl.merchant_key("HOME DEPOT #4521 SHOREWOOD") == "HOME DEPOT SHOREWOOD"
    assert tpl.merchant_key("Home  Depot, #0187") == "HOME DEPOT"
    assert tpl.merchant_key("NEON PULSE TECHSHOP") == "NEON PULSE TECHSHOP"
    assert tpl.merchant_key("  ") is None
    assert tpl.merchant_key("#1234") is None


def test_keys_match_prefix_rule():
    assert tpl.keys_match("HOME DEPOT", "HOME DEPOT")
    assert tpl.keys_match("HOME DEPOT", "HOME DEPOT PRO DESK")
    assert tpl.keys_match("HOME DEPOT PRO DESK", "HOME DEPOT")
    assert not tpl.keys_match("HOME DEPOT", "OFFICE DEPOT")
    assert not tpl.keys_match("DEPOT", "HOME DEPOT")  # 1-word prefix too weak
    assert not tpl.keys_match(None, "HOME DEPOT")


WORDS = [
    {"text": "NEON", "left": 40, "top": 10, "width": 60, "height": 16},
    {"text": "PULSE", "left": 110, "top": 10, "width": 70, "height": 16},
    {"text": "TOTAL", "left": 30, "top": 200, "width": 60, "height": 20},
    {"text": "$49.13", "left": 150, "top": 200, "width": 70, "height": 20},
    {"text": "TAX", "left": 30, "top": 170, "width": 40, "height": 20},
    {"text": "2.89", "left": 150, "top": 170, "width": 50, "height": 20},
]


def test_encode_prefers_same_line_label_anchor():
    box = {"left": 145, "top": 196, "width": 80, "height": 28}  # around $49.13
    enc = tpl.encode_field(box, WORDS)
    assert enc is not None
    assert enc["anchor_text"] == "TOTAL"
    assert enc["dx"] > 0  # box sits right of the label


def test_roundtrip_resolution_same_scale():
    box = {"left": 145, "top": 196, "width": 80, "height": 28}
    enc = tpl.encode_field(box, WORDS)
    out = tpl.resolve_field(enc, WORDS)
    assert out is not None
    assert abs(out["left"] - box["left"]) <= 1
    assert abs(out["top"] - box["top"]) <= 1


def test_resolution_survives_scale_and_shift():
    """A 2x-DPI, shifted rescan: offsets are anchor-height-scaled, so the
    resolved box lands on the amount in the new coordinate space."""
    box = {"left": 145, "top": 196, "width": 80, "height": 28}
    enc = tpl.encode_field(box, WORDS)
    rescanned = [
        {**w, "left": w["left"] * 2 + 33, "top": w["top"] * 2 + 90,
         "width": w["width"] * 2, "height": w["height"] * 2}
        for w in WORDS
    ]
    out = tpl.resolve_field(enc, rescanned)
    assert out is not None
    expected_left = 145 * 2 + 33
    expected_top = 196 * 2 + 90
    assert abs(out["left"] - expected_left) <= 2
    assert abs(out["top"] - expected_top) <= 2
    assert abs(out["width"] - 160) <= 2


def test_resolution_fails_closed_when_anchor_missing():
    enc = {"anchor_text": "GESAMTSUMME", "dx": 1, "dy": 0, "w": 3, "h": 1}
    assert tpl.resolve_field(enc, WORDS) is None


def test_encode_falls_back_to_top_block_anchor():
    """A date box with no same-line label anchors to the merchant block."""
    words = [w for w in WORDS if w["text"] not in ("TOTAL", "TAX")]
    box = {"left": 200, "top": 60, "width": 90, "height": 18}
    enc = tpl.encode_field(box, words)
    assert enc is not None
    assert enc["anchor_text"] == "NEON"
