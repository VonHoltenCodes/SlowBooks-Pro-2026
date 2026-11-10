"""The check face's per-field layout (#749): Settings -> Payroll and checks
-> Check Printing -> Field layout moves, sizes and bolds each value the face
prints (payee, payee address, date, amount in figures, amount in words,
memo) on its own, on top of the whole-face nudge, for pre-printed stock
whose boxes do not all sit where the face prints them. It is one setting,
check_field_layout, JSON of what differs from today's face; empty (the
default) is today's face, byte for byte.

The PDF tests read the real WeasyPrint output with pypdf (they skip
without it), matching words by place as tests/test_check_blank_stock.py
does: a word's text is whatever pypdf makes of it on the font set at hand.
"""

import datetime
import io
import json
import re
from decimal import Decimal

import pytest

import app.services.pdf_service as pdf_service
from app.services.pdf_service import render_checks_html

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
# each field's words on the face, apart from every other word on the page
# (the stubs and vouchers print the payee, date and amount too, at their
# own places)
_FIELD_WORDS = {
    "payee": ["Gulf Supply"],
    "payee_address": ["Attn Accounts Payable", "200 Harbor Rd"],
    "date": ["Oct 24, 2026"],
    "amount": ["$1,234.56"],
    "amount_words": ["One Thousand Two Hundred Thirty-Four and 56/100 ******"],
    "memo": ["Invoice 55"],
}
_BOLD_TODAY = {"payee": True, "amount": True}


def _check(**overrides):
    data = {
        "payee": "Gulf Supply",
        "date": datetime.date(2026, 10, 24),
        "amount": Decimal("1234.56"),
        "check_number": "1072",
        "memo": "Invoice 55",
        "details": [{"description": "Bill #B-1", "amount": Decimal("1234.56")}],
        "bank_account": "Operating Checking",
        "payee_address": ["Attn Accounts Payable", "200 Harbor Rd"],
        "bank_details": dict(_BANK),
    }
    data.update(overrides)
    return data


def _settings(layout=None, **settings):
    out = {**_COMPANY, **settings}
    if layout is not None:
        out["check_field_layout"] = (
            layout if isinstance(layout, str) else json.dumps(layout)
        )
    return out


def _html(layout=None, checks=None, **settings):
    return render_checks_html(checks or [_check()], _settings(layout, **settings))


# ----- the setting -----------------------------------------------------


def _saved(client):
    return client.get("/api/settings").json()["check_field_layout"]


def test_the_layout_is_empty_by_default(client):
    assert _saved(client) == ""


def test_a_layout_saves_as_what_differs_from_today(client):
    sent = {
        "payee": {"x": "0.25", "y": -0.1, "size": "", "bold": False},
        "date": {"x": 0, "y": "0", "size": 12.5, "bold": True},
        "memo": {},
        "amount_words": {"x": "", "y": None},
    }
    r = client.put("/api/settings", json={"check_field_layout": json.dumps(sent)})
    assert r.status_code == 200, r.text
    assert json.loads(_saved(client)) == {
        "date": {"bold": True, "size": 12.5},
        "payee": {"bold": False, "x": 0.25, "y": -0.1},
    }
    # the object itself, as the API may send it
    r = client.put("/api/settings", json={"check_field_layout": {"memo": {"size": 10}}})
    assert r.status_code == 200, r.text
    assert _saved(client) == '{"memo":{"size":10}}'


@pytest.mark.parametrize("empty", ["", "{}", '{"payee": {"x": 0, "y": "0"}}'])
def test_a_layout_with_nothing_set_saves_as_empty(client, empty):
    client.put("/api/settings", json={"check_field_layout": '{"memo":{"x":0.1}}'})
    r = client.put("/api/settings", json={"check_field_layout": empty})
    assert r.status_code == 200, r.text
    assert _saved(client) == ""


@pytest.mark.parametrize(
    "layout, says",
    [
        (
            {"payee": {"x": 1.01}},
            "Payee left/right offset must be a number of inches from -1 to 1.",
        ),
        (
            {"date": {"y": "-1.5"}},
            "Date up/down offset must be a number of inches from -1 to 1.",
        ),
        ({"memo": {"x": "abc"}}, "Memo left/right offset must be a number"),
        ({"memo": {"y": "NaN"}}, "Memo up/down offset must be a number"),
        (
            {"amount": {"x": 0.005}},
            "Amount in figures left/right offset can have up to two decimal places",
        ),
        (
            {"amount": {"size": 5.9}},
            "Amount in figures text size must be a number of points from 6 to 18",
        ),
        (
            {"amount_words": {"size": "19"}},
            "Amount in words text size must be a number of points from 6 to 18",
        ),
        (
            {"payee_address": {"size": 10.25}},
            "Payee address text size can have one decimal place",
        ),
        ({"payee": {"bold": "yes"}}, "Payee bold must be true or false."),
        ({"payee": {"bold": 1}}, "Payee bold must be true or false."),
        ({"signature": {"x": 0.1}}, "signature is not a field"),
        ({"payee": {"left": 0.1}}, "left is not part of a field's layout"),
        ({"payee": 0.1}, "Payee: the layout must be an object."),
        ([1, 2], "Check field layout must be an object of fields"),
        ("not json", "Check field layout must be an object of fields"),
    ],
)
def test_a_layout_out_of_bounds_is_refused_and_nothing_is_saved(client, layout, says):
    client.put("/api/settings", json={"check_field_layout": '{"memo":{"x":0.1}}'})
    text = layout if isinstance(layout, str) else json.dumps(layout)
    r = client.put(
        "/api/settings",
        json={"check_field_layout": text, "check_nudge_x_in": "0.2"},
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert says in detail, detail
    assert detail.endswith("Nothing was saved.")
    s = client.get("/api/settings").json()
    assert s["check_field_layout"] == '{"memo":{"x":0.1}}'
    assert s["check_nudge_x_in"] == "0"


def test_every_problem_of_a_layout_is_named_at_once(client):
    r = client.put(
        "/api/settings",
        json={"check_field_layout": {"payee": {"x": 2}, "memo": {"size": 40}}},
    )
    assert r.status_code == 422, r.text
    assert "Payee left/right offset" in r.json()["detail"]
    assert "Memo text size" in r.json()["detail"]


@pytest.mark.parametrize("bound", ["-1", "1", "-1.00", "0.99"])
def test_an_offset_of_up_to_an_inch_each_way_saves(client, bound):
    r = client.put(
        "/api/settings",
        json={"check_field_layout": {"payee": {"x": bound, "y": bound}}},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("size", ["6", "18", "10.5"])
def test_a_size_from_6_to_18_points_saves(client, size):
    r = client.put(
        "/api/settings", json={"check_field_layout": {"memo": {"size": size}}}
    )
    assert r.status_code == 200, r.text


def test_a_damaged_stored_layout_prints_the_fields_that_check_out():
    from app.services.settings_service import check_field_layout

    assert check_field_layout("") == {}
    assert check_field_layout("not json") == {}
    assert check_field_layout("[1]") == {}
    assert check_field_layout(
        '{"payee": {"x": 9}, "memo": {"size": 12}, "nope": {}}'
    ) == {"memo": {"size": 12}}


# ----- today's face, unchanged -----------------------------------------


def _feature_off_template():
    """check_pdf.html with the field layout taken out: no layout style
    block, no field() macro, each value printed as it was before #749.
    Rendering it is the feature's code path disabled."""
    src = (pdf_service.TEMPLATE_DIR / "check_pdf.html").read_text(encoding="utf-8")
    src, n = re.subn(r"\{%- if field_css %\}.*?\{%- endif %\}\n", "", src, flags=re.S)
    assert n == 1
    src, n = re.subn(
        r"\{%- macro field\(name, value\) -%\}.*?\{%- endmacro %\}\n",
        "",
        src,
        flags=re.S,
    )
    assert n == 1
    src, n = re.subn(r"\{\{ field\('\w+', (.+?)\) \}\}", r"{{ \1 }}", src)
    assert n == 10
    return pdf_service._jinja_env.from_string(src)


_FACES = [
    pytest.param({}, id="bottom"),
    pytest.param({"check_position": "top"}, id="top"),
    pytest.param({"check_stock": "preprinted"}, id="preprinted"),
    pytest.param(
        {"check_stock": "preprinted", "check_position": "top"}, id="preprinted-top"
    ),
    pytest.param({"check_blank_stock": "true"}, id="blank"),
    pytest.param(
        {"check_blank_stock": "true", "check_position": "top"}, id="blank-top"
    ),
    pytest.param({"check_nudge_x_in": "0.1", "check_nudge_y_in": "-0.05"}, id="nudged"),
]


@pytest.mark.parametrize("face", _FACES)
@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize(
    "layout", [None, "", "{}", '{"payee": {"x": 0, "size": ""}}', "garbage"]
)
def test_an_empty_layout_prints_exactly_the_face_without_the_feature(
    monkeypatch, face, count, layout
):
    off = _feature_off_template()
    real = pdf_service._jinja_env.get_template("check_pdf.html")

    def batch():
        return [_check(check_number=str(1072 + n)) for n in range(count)]

    on_html = _html(layout, batch(), **face)

    class _Env:
        def get_template(self, _name):
            return off

    monkeypatch.setattr(pdf_service, "_jinja_env", _Env())
    off_html = _html(layout, batch(), **face)
    assert on_html == off_html
    assert "check-field-" not in on_html and "Field layout" not in on_html
    assert real is not off


def test_a_layout_names_only_the_fields_it_sets():
    html = _html({"memo": {"x": 0.1}, "date": {"bold": True}})
    assert ".check-area .memo-text { transform: translate(0.1in, 0in); }" in html
    assert '<span class="check-field-date">' in html
    for field in ("payee", "amount", "amount_words", "memo"):
        assert f'class="check-field-{field}"' not in html
    assert ".pay-to-line { transform" not in html


# ----- the PDF ---------------------------------------------------------


def _words(settings, checks=None):
    """Every word on every page: (text, x, y, size, bold)."""
    pypdf = pytest.importorskip("pypdf")
    pdf = pdf_service.render_pdf(render_checks_html(checks or [_check()], settings))
    pages = []
    for page in pypdf.PdfReader(io.BytesIO(pdf)).pages:
        words = []

        def visit(text, cm, tm, font, size):
            if text.strip():
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                bold = "Bold" in str((font or {}).get("/BaseFont", ""))
                points = size * tm[0] * cm[0]
                # where the first letter is: a run may start with the space
                # before it (the date after DATE), as one run or as two,
                # and the face's fonts are monospaced (0.6 em a letter)
                x += (len(text) - len(text.lstrip())) * 0.6 * points
                words.append(
                    (text.strip(), round(x, 2), round(y, 2), round(points, 2), bold)
                )

        page.extract_text(visitor_text=visit)
        pages.append(words)
    return pages


def _lines(words):
    rows = {}
    for w in words:
        rows.setdefault(w[2], []).append(w)
    return [
        " ".join(w[0] for w in sorted(row, key=lambda w: w[1])) for row in rows.values()
    ]


def _changed(before, after):
    gone = [w for w in before if w not in after]
    new = [w for w in after if w not in before]
    return gone, new


_POSITIONS = ["bottom", "top"]
_FIELDS = list(_FIELD_WORDS)


@pytest.mark.parametrize("face", _FACES)
def test_restating_todays_weights_prints_every_word_where_it_was(face):
    today = _words(_settings(None, **face))
    same = _words(
        _settings({f: {"bold": _BOLD_TODAY.get(f, False)} for f in _FIELDS}, **face)
    )
    assert today == same


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("field", _FIELDS)
def test_an_offset_moves_that_field_alone(position, field):
    before = _words(_settings(None, check_position=position))[0]
    after = _words(_settings({field: {"x": 0.5, "y": 0.25}}, check_position=position))[
        0
    ]
    gone, new = _changed(before, after)
    assert gone, "nothing moved"
    # the date's label moves with it (on plain stock)
    moved = [w for w in gone if w[0] != "DATE"]
    assert all(any(w[0] in p for p in _FIELD_WORDS[field]) for w in moved), gone
    # 0.5 in right and 0.25 in down: +36 pt across, -18 pt in PDF space
    shifted = sorted(
        (w[0], round(w[1] + 36, 2), round(w[2] - 18, 2)) + w[3:] for w in gone
    )
    assert sorted(new) == shifted


@pytest.mark.parametrize("position", _POSITIONS)
def test_offsets_apply_on_top_of_the_nudge(position):
    nudged = _words(_settings(None, check_position=position, check_nudge_x_in="0.1"))[0]
    both = _words(
        _settings(
            {"memo": {"x": -0.25}}, check_position=position, check_nudge_x_in="0.1"
        )
    )[0]
    gone, new = _changed(nudged, both)
    assert [w[0] for w in gone] == ["Invoice 55"]
    assert new == [(gone[0][0], round(gone[0][1] - 18, 2)) + gone[0][2:]]


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("field", _FIELDS)
def test_a_size_changes_that_field_alone(position, field):
    before = _words(_settings(None, check_position=position))[0]
    after = _words(_settings({field: {"size": 16}}, check_position=position))[0]
    gone, new = _changed(before, after)
    # the date's label stays its size (a right-aligned date grows to the
    # left, and its label with it)
    assert gone and all(w[3] == 16 for w in new if w[0] != "DATE"), new
    texts = [w[0] for w in gone if w[0] != "DATE"]
    assert all(any(t in p for p in _FIELD_WORDS[field]) for t in texts), gone
    assert sorted(w[0] for w in new if w[0] != "DATE") == sorted(texts)
    if field != "payee_address":
        # one line on its own baseline: it does not move up or down
        assert {w[2] for w in new if w[0] != "DATE"} == {
            w[2] for w in gone if w[0] != "DATE"
        }


@pytest.mark.parametrize("position", _POSITIONS)
@pytest.mark.parametrize("field", _FIELDS)
def test_bold_turns_that_field_alone_on_or_off(position, field):
    flip = not _BOLD_TODAY.get(field, False)
    before = _words(_settings(None, check_position=position))[0]
    after = _words(_settings({field: {"bold": flip}}, check_position=position))[0]
    gone, new = _changed(before, after)
    assert gone, "nothing changed"
    assert all(any(w[0] in p for p in _FIELD_WORDS[field]) for w in gone), gone
    assert all(w[4] is flip for w in new)
    # the weight only: every word keeps its place and size
    assert sorted(w[:4] for w in gone) == sorted(w[:4] for w in new)


@pytest.mark.parametrize(
    "face",
    [
        pytest.param({"check_stock": "preprinted"}, id="preprinted"),
        pytest.param({"check_blank_stock": "true"}, id="blank"),
        pytest.param(
            {"check_blank_stock": "true", "check_position": "top"}, id="blank-top"
        ),
    ],
)
def test_the_layout_applies_to_every_stock_and_every_page_of_a_batch(face):
    batch = [_check(check_number=str(1072 + n)) for n in range(2)]
    layout = {"date": {"x": -0.5, "size": 12, "bold": True}}
    before = _words(_settings(None, **face), batch)
    after = _words(_settings(layout, **face), [dict(c) for c in batch])
    assert len(after) == 2
    for page_before, page_after in zip(before, after):
        gone, new = _changed(page_before, page_after)
        assert {w[0] for w in gone} - {"DATE"} == {"Oct 24, 2026"}, gone
        (date,) = [w for w in new if w[0] == "Oct 24, 2026"]
        assert date[3] == 12 and date[4] is True
    # both pages alike
    assert [w[1:] for w in after[0]] == [w[1:] for w in after[1]]


# ----- the test print --------------------------------------------------


def test_the_test_print_uses_the_unsaved_layout(client, monkeypatch):
    import app.routes.checks as checks

    seen = {}

    def fake_pdf(check_data, company):
        seen["html"] = render_checks_html([check_data], company)
        return b"%PDF-fake"

    monkeypatch.setattr(checks, "generate_check_pdf", fake_pdf)
    layout = json.dumps({"payee": {"x": 0.3, "size": 14, "bold": False}})
    r = client.get("/api/checks/test-print", params={"check_field_layout": layout})
    assert r.status_code == 200, r.text
    assert (
        ".check-area .pay-to-line { transform: translate(0.3in, 0in); }" in seen["html"]
    )
    assert '<span class="check-field-payee">Sample Payee</span>' in seen["html"]
    assert (
        "font-size: 14pt; line-height: 0; white-space: nowrap; font-weight: normal;"
        in seen["html"]
    )
    assert _saved(client) == ""
    # left out, it reads the saved layout; sent empty, today's face
    client.put("/api/settings", json={"check_field_layout": '{"memo":{"y":0.2}}'})
    client.get("/api/checks/test-print")
    assert "translate(0in, 0.2in)" in seen["html"]
    client.get("/api/checks/test-print", params={"check_field_layout": ""})
    assert "check-field-" not in seen["html"] and "translate(0in" not in seen["html"]


def test_the_test_print_refuses_a_bad_unsaved_layout(client):
    r = client.get(
        "/api/checks/test-print",
        params={"check_field_layout": '{"date": {"size": 30}}'},
    )
    assert r.status_code == 422, r.text
    assert (
        "Date text size must be a number of points from 6 to 18" in r.json()["detail"]
    )


def test_the_test_print_pdf_carries_the_unsaved_layout(client):
    pypdf = pytest.importorskip("pypdf")
    r = client.get(
        "/api/checks/test-print",
        params={"check_field_layout": '{"amount": {"size": 18, "bold": false}}'},
    )
    assert r.status_code == 200, r.text[:300]
    sizes = []

    def visit(text, cm, tm, font, size):
        if text.strip() == "$1,234.56":
            sizes.append(
                (round(size * tm[0] * cm[0], 2), "Bold" in str(font.get("/BaseFont")))
            )

    pypdf.PdfReader(io.BytesIO(r.content)).pages[0].extract_text(visitor_text=visit)
    assert (18, False) in sizes


# ----- the settings page -----------------------------------------------


def _js():
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1] / "app/static/js/settings.js"
    ).read_text(encoding="utf-8")


def test_the_settings_page_has_the_layout_table_and_the_test_print_sends_it():
    js = _js()
    assert "'check_blank_stock', 'check_micr_amount', 'check_field_layout']" in js
    assert '<input type="hidden" name="check_field_layout"' in js
    assert '<h4 id="settings-check-fields-h"' in js and ">Field layout</h4>" in js
    for key, label in (
        ("payee", "Payee"),
        ("payee_address", "Payee address"),
        ("date", "Date"),
        ("amount", "Amount in figures"),
        ("amount_words", "Amount in words"),
        ("memo", "Memo"),
    ):
        assert f"['{key}', '{label}', " in js
    for header in ("Left/right (in)", "Up/down (in)", "Size (pt)", "Bold"):
        assert f'<th scope="col">{header}</th>' in js
    assert "Reset field layout to defaults" in js


# ----- the face itself -------------------------------------------------


def test_the_bottom_layouts_date_box_never_wraps():
    """Pango 1.50 (Ubuntu 22.04) measured the bottom layout's shrink-to-fit
    date box a hair narrower than the line it then laid out, and wrapped the
    date at its space, "Oct" over "24, 2026" (2.31.12 gate, Linux round 1);
    Pango 1.52 did not. The rule says nowrap, so every Pango prints the date
    on one line, and the label's <br> still breaks."""
    import pathlib
    import re

    template = (
        pathlib.Path(pdf_service.__file__).resolve().parents[1]
        / "templates"
        / "check_pdf.html"
    ).read_text(encoding="utf-8")
    rule = re.search(r"\n    \.check-date \{[^}]*\}", template).group(0)
    assert "white-space: nowrap" in rule, rule
    words = _words(_settings())[0]
    dates = [w for w in words if w[0] in ("Oct 24, 2026", "Oct", "24, 2026")]
    assert [w[0] for w in dates] == ["Oct 24, 2026"], dates
    label = [w for w in words if w[0] == "DATE"]
    assert label and label[0][2] > dates[0][2], (label, dates)
