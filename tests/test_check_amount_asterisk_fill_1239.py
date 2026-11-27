"""Asterisks before a check's amount in figures (#1239).

Settings -> Check Printing -> Fill the amount with asterisks (off by
default) prints the amount box as "$**1,234.56", or "**1,234.56" with
Leave the dollar sign off the amount (#799) on, as QuickBooks Desktop does,
so nothing can be written in front of the figures. The amount in words, the
stubs, the vouchers and the MICR line are unchanged, on plain, pre-printed
and blank stock, in both positions and on the test check. Off, every check
prints exactly as it did before the setting existed.

The HTML tests run everywhere; the PDF tests read the real WeasyPrint output
with pypdf (they skip without it), as tests/test_check_field_layout.py does.
"""

import datetime
import io
import re
from decimal import Decimal

import pytest

import app.services.pdf_service as pdf_service
from app.services.pdf_service import check_amount_figures, render_checks_html

_COMPANY = {
    "company_name": "Acme Builders",
    "company_address1": "1 Main St",
    "company_city": "Tampa",
    "company_state": "FL",
    "company_zip": "33601",
}
_BANK = {
    "bank_name": "First Prairie Bank",
    "bank_city_state": "Joliet, IL",
    "fractional_routing": "70-1234/719",
    "routing_number": "123456780",
    "account_number": "4455667788",
}
_WORDS = "One Thousand Two Hundred Thirty-Four and 56/100 ******"
# what the amount box holds for each (sign left off, asterisks) pair
_BOXES = {
    (False, False): "$1,234.56",
    (True, False): "1,234.56",
    (False, True): "$**1,234.56",
    (True, True): "**1,234.56",
}
_STOCKS = {
    "plain": {},
    "preprinted": {"check_stock": "preprinted"},
    "blank": {"check_blank_stock": "true"},
}
_POSITIONS = ["bottom", "top"]


def _box(text):
    return f'<div class="amount-box">{text}</div>'


def _check(**overrides):
    data = {
        "payee": "Gulf Supply",
        "date": datetime.date(2026, 10, 24),
        "amount": Decimal("1234.56"),
        "check_number": "1072",
        "memo": "Invoice 55",
        "details": [{"description": "Bill #B-1", "amount": Decimal("1234.56")}],
        "bank_account": "Operating Checking",
        "payee_address": ["Gulf Supply", "200 Harbor Rd", "Gulfport, MS 39501"],
        "bank_details": dict(_BANK),
    }
    data.update(overrides)
    return data


def _settings(no_sign=None, fill=None, **settings):
    out = {**_COMPANY, **settings}
    if no_sign is not None:
        out["check_amount_no_dollar_sign"] = "true" if no_sign else "false"
    if fill is not None:
        out["check_amount_asterisk_fill"] = "true" if fill else "false"
    return out


def _html(no_sign=None, fill=None, checks=None, **settings):
    return render_checks_html(
        checks or [_check()], _settings(no_sign, fill, **settings)
    )


# ----- the setting -------------------------------------------------------


def test_the_setting_is_off_by_default_and_round_trips(client):
    assert client.get("/api/settings").json()["check_amount_asterisk_fill"] == "false"
    for value in ("true", "false"):
        r = client.put("/api/settings", json={"check_amount_asterisk_fill": value})
        assert r.status_code == 200, r.text
        assert client.get("/api/settings").json()["check_amount_asterisk_fill"] == value


@pytest.mark.parametrize("value", ["yes", "", "TRUE", True, 1, None])
def test_the_setting_refuses_anything_but_true_or_false(client, value):
    r = client.put("/api/settings", json={"check_amount_asterisk_fill": value})
    assert r.status_code == 422, r.text
    assert "check_amount_asterisk_fill must be one of: false, true" in r.text
    assert client.get("/api/settings").json()["check_amount_asterisk_fill"] == "false"


def test_the_settings_page_has_the_box_and_the_test_print_sends_it():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    assert 'name="check_amount_asterisk_fill" value="true" data-bool' in js
    assert "${s.check_amount_asterisk_fill === 'true' ? 'checked' : ''}" in js
    assert (
        '<label for="settings-check-amount-asterisk-fill" '
        'style="font-weight:normal; margin:0;">'
        "Fill the amount with asterisks</label>"
    ) in js
    assert 'aria-describedby="settings-check-amount-asterisk-fill-help"' in js
    assert "id: 'settings-check-amount-asterisk-fill-help'" in js
    assert (
        "'check_amount_no_dollar_sign', 'check_amount_asterisk_fill']" in js
    ), "the test print sends the box with the other check settings"


# ----- the amount in figures ---------------------------------------------


def test_the_figures_for_each_pair_of_choices():
    for (no_sign, fill), text in _BOXES.items():
        assert check_amount_figures(Decimal("1234.56"), no_sign, fill) == text
    assert check_amount_figures(Decimal("0.5"), False, True) == "$**0.50"
    assert check_amount_figures(Decimal("957.68"), True, True) == "**957.68"
    assert check_amount_figures(Decimal("1000000"), False, True) == "$**1,000,000.00"
    # the defaults are today's figures
    assert check_amount_figures(Decimal("1234.56")) == "$1,234.56"


def _feature_off_template():
    """check_pdf.html as it was before #1239: the amount filter without the
    asterisks and no asterisk style block. Rendering it is the feature's
    code path taken out."""
    src = (pdf_service.TEMPLATE_DIR / "check_pdf.html").read_text(encoding="utf-8")
    src, n = re.subn(
        r"\{%- if asterisk_fill and position == 'top' %\}.*?\{%- endif %\}\n",
        "",
        src,
        flags=re.S,
    )
    assert n == 1
    src, n = re.subn(
        r"check_amount\(no_dollar_sign, asterisk_fill\)",
        "check_amount(no_dollar_sign)",
        src,
    )
    assert n == 2
    return pdf_service._jinja_env.from_string(src)


@pytest.mark.parametrize("no_sign", [None, False, True])
@pytest.mark.parametrize("fill", [None, False])
@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("stock", list(_STOCKS))
def test_off_every_check_prints_exactly_as_before(
    monkeypatch, stock, position, count, fill, no_sign
):
    off = _feature_off_template()

    def batch():
        return [_check(check_number=str(1072 + n)) for n in range(count)]

    settings = dict(check_position=position, **_STOCKS[stock])
    now = _html(no_sign, fill, batch(), **settings)

    class _Env:
        def get_template(self, _name):
            return off

    monkeypatch.setattr(pdf_service, "_jinja_env", _Env())
    before = _html(no_sign, None, batch(), **settings)
    assert now == before
    assert "**" not in now.replace("******", "")


@pytest.mark.parametrize("no_sign", [False, True])
@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("stock", list(_STOCKS))
def test_on_the_amount_box_alone_takes_the_asterisks(stock, position, no_sign):
    settings = dict(check_position=position, **_STOCKS[stock])
    off = _html(no_sign, False, **settings)
    on = _html(no_sign, True, **settings)
    plain, filled = _BOXES[(no_sign, False)], _BOXES[(no_sign, True)]
    assert _box(filled) in on and _box(plain) not in on
    assert _WORDS in on
    # the one change on the face is the amount box (and, with the check on
    # top, the payee's longest length); the stubs and vouchers keep "$"
    expected = off.replace(_box(plain), _box(filled))
    if position == "top":
        assert on.count(".check-top .pay-to-line { max-width: 5in; }") == 1
        on = re.sub(
            r"\n<style>\n    /\* The amount's asterisks.*?</style>", "", on, flags=re.S
        )
    else:
        assert "max-width: 5in" not in on
    assert on == expected
    assert on.count("$1,234.56") == off.count("$1,234.56") - (0 if no_sign else 1)


def test_on_a_moved_and_sized_amount_keeps_its_asterisks():
    layout = '{"amount": {"size": 16}}'
    on = _html(True, True, check_field_layout=layout)
    assert '<span class="check-field-amount">**1,234.56</span>' in on
    on = _html(False, True, check_field_layout=layout)
    assert '<span class="check-field-amount">$**1,234.56</span>' in on


def test_on_every_check_of_a_batch_takes_the_asterisks():
    checks = [
        _check(),
        _check(check_number="1073", amount=Decimal("88.10"), details=[]),
    ]
    on = render_checks_html(checks, _settings(False, True))
    assert _box("$**1,234.56") in on
    assert _box("$**88.10") in on
    # the second check's stubs print "Payment $88.10", unfilled
    assert "$88.10" in on and on.count("**88.10") == 1


def test_on_the_micr_amount_is_unchanged():
    settings = {"check_blank_stock": "true", "check_micr_amount": "true"}
    off = _html(None, None, **settings)
    on = _html(None, True, **settings)
    assert on == off.replace(_box("$1,234.56"), _box("$**1,234.56"))


# ----- the test check ----------------------------------------------------


@pytest.fixture
def seen_html(monkeypatch):
    import app.routes.checks as checks
    from app.services.pdf_service import render_check_html

    seen = {}

    def fake_pdf(check_data, company):
        seen["html"] = render_check_html(check_data, company)
        return b"%PDF-fake"

    monkeypatch.setattr(checks, "generate_check_pdf", fake_pdf)
    return seen


def test_the_test_check_follows_the_saved_setting(client, seen_html):
    assert client.get("/api/checks/test-print").status_code == 200
    assert _box("$1,234.56") in seen_html["html"]
    client.put("/api/settings", json={"check_amount_asterisk_fill": "true"})
    assert client.get("/api/checks/test-print").status_code == 200
    assert _box("$**1,234.56") in seen_html["html"]


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("stock", list(_STOCKS))
def test_the_test_check_uses_the_unsaved_boxes(client, seen_html, stock, position):
    base = {
        "check_stock": "preprinted" if stock == "preprinted" else "plain",
        "check_blank_stock": "true" if stock == "blank" else "false",
        "check_position": position,
    }
    for (no_sign, fill), text in _BOXES.items():
        params = {
            **base,
            "check_amount_no_dollar_sign": "true" if no_sign else "false",
            "check_amount_asterisk_fill": "true" if fill else "false",
        }
        r = client.get("/api/checks/test-print", params=params)
        assert r.status_code == 200, r.text
        assert _box(text) in seen_html["html"], (no_sign, fill)
        assert _WORDS in seen_html["html"]
    saved = client.get("/api/settings").json()
    assert saved["check_amount_asterisk_fill"] == "false"
    assert saved["check_amount_no_dollar_sign"] == "false"


def test_the_test_check_refuses_a_bad_unsaved_value(client):
    r = client.get("/api/checks/test-print?check_amount_asterisk_fill=maybe")
    assert r.status_code == 422, r.text
    assert (
        r.json()["detail"] == "check_amount_asterisk_fill must be one of: false, true"
    )


# ----- the PDF: the amount stays in its box ------------------------------


def _face_words(settings, checks=None):
    """The words of the first page: (text, x, y, size)."""
    pypdf = pytest.importorskip("pypdf")
    pdf = pdf_service.render_pdf(render_checks_html(checks or [_check()], settings))
    words = []

    def visit(text, cm, tm, font, size):
        if text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            points = size * tm[0] * cm[0]
            # the face's fonts are monospaced (0.6 em a letter)
            x += (len(text) - len(text.lstrip())) * 0.6 * points
            words.append((text.strip(), round(x, 2), round(y, 2), round(points, 2)))

    pypdf.PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=visit)
    return words


def _amount(words, text):
    """The face's amount box: the stubs and vouchers print the amount too,
    smaller (9 pt against 14, or 12 with the check on top)."""
    found = [w for w in words if w[0] == text and w[3] >= 12]
    assert len(found) == 1, (text, words)
    return found[0]


def _right(word):
    return word[1] + len(word[0]) * 0.6 * word[3]


@pytest.mark.parametrize("position", _POSITIONS)
def test_the_off_face_reads_as_it_did(position):
    # the saved "false" prints the very words the absent setting does
    assert _face_words(_settings(None, None, check_position=position)) == _face_words(
        _settings(False, False, check_position=position)
    )


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("stock", list(_STOCKS))
def test_the_figures_keep_their_right_edge_and_baseline(stock, position):
    words = {
        pair: _face_words(_settings(*pair, check_position=position, **_STOCKS[stock]))
        for pair in _BOXES
    }
    today = _amount(words[(False, False)], "$1,234.56")
    for pair, text in _BOXES.items():
        word = _amount(words[pair], text)
        # right-aligned: the last figure ends where it ends today, on the
        # same baseline at the same size; the asterisks grow it leftward
        assert abs(_right(word) - _right(today)) < 0.05, (pair, word, today)
        assert word[2:] == today[2:], (pair, word, today)
        # and nothing else on the face moves
        others = [w for w in words[pair] if w is not word]
        assert others == [w for w in words[(False, False)] if w is not today]


# where the payee's line ends, in points from the sheet's left edge: the
# 0.5 in page margin and the body's 8 px, then at the bottom the face's
# 0.25 in padding and the line's 4 in minimum; on top the line starts at
# 0.75 in, its minimum is 4.6 in, and a long name is cut at 5.2 in (5 in
# with the asterisks)
_FACE_LEFT = 0.5 + 1 / 12
_PAYEE_LINE_END = {
    "bottom": (_FACE_LEFT + 0.25 + 4.0) * 72,
    "top": (_FACE_LEFT + 0.75 + 4.6) * 72,
}
_TOP_PAYEE_LONGEST_END = (_FACE_LEFT + 0.75 + 5.0) * 72
_BOX_LEFT_PAD = 14  # the amount box's 12 px padding and 2 px border, in px


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize(
    "amount", ["0.01", "957.68", "1234.56", "99999.99", "999999.99", "9999999.99"]
)
def test_the_filled_amount_clears_the_payee_line(position, amount):
    checks = [_check(amount=Decimal(amount))]
    words = _face_words(_settings(False, True, check_position=position), checks)
    word = _amount(words, check_amount_figures(Decimal(amount), False, True))
    box_left = word[1] - _BOX_LEFT_PAD * 0.75
    assert box_left > _PAYEE_LINE_END[position], (word, box_left)


def test_on_top_a_long_payee_is_cut_short_of_the_filled_amount():
    # the payee's line on top is cut at its longest; with the asterisks it
    # is cut 0.2 in sooner, so it still ends short of "$**1,234.56"
    payee = "Northern Lakes Regional Mechanical Contracting Services Cooperative"
    checks = [_check(payee=payee)]
    words = _face_words(_settings(False, True, check_position="top"), checks)
    word = _amount(words, "$**1,234.56")
    assert word[1] - _BOX_LEFT_PAD * 0.75 > _TOP_PAYEE_LONGEST_END
    html = render_checks_html(checks, _settings(False, True, check_position="top"))
    assert ".check-top .pay-to-line { max-width: 5in; }" in html
