"""Checks on blank stock (#739): with Settings -> Check Printing -> Print on
blank check stock on, the face carries what pre-printed stock would — the
logo, the company's name and address, the bank's name and city, the
fractional routing number, the check number and the MICR line from the bank
account's check details — and every field the face already prints stays
where it is. Off (the default), nothing about any check changes.

The PDF tests read the real WeasyPrint output with pypdf (they skip
without it): word positions from the text, the MICR glyphs from the
rectangles drawn on the page.
"""

import datetime
import io
from decimal import Decimal

import pytest

import app.services.pdf_service as pdf_service
from app.routes import checks as checks_route
from app.services.micr import ON_US, TRANSIT
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
_ON = {"check_blank_stock": "true"}
# a 1x1 PNG
_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlE"
    "QVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_NEW_WORDS = {"First Prairie Bank", "Joliet, IL", "70-1234/719"}


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


def _html(checks=None, **settings):
    return render_checks_html(checks or [_check()], {**_COMPANY, **settings})


# ----- the settings ----------------------------------------------------


def test_both_options_are_off_by_default_and_round_trip(client):
    s = client.get("/api/settings").json()
    assert s["check_blank_stock"] == "false"
    assert s["check_micr_amount"] == "false"
    r = client.put(
        "/api/settings", json={"check_blank_stock": "true", "check_micr_amount": "true"}
    )
    assert r.status_code == 200, r.text
    s = client.get("/api/settings").json()
    assert (s["check_blank_stock"], s["check_micr_amount"]) == ("true", "true")
    r = client.put("/api/settings", json={"check_blank_stock": "false"})
    assert r.status_code == 200, r.text
    assert client.get("/api/settings").json()["check_blank_stock"] == "false"


@pytest.mark.parametrize("key", ["check_blank_stock", "check_micr_amount"])
def test_the_options_refuse_anything_but_true_or_false(client, key):
    r = client.put("/api/settings", json={key: "yes"})
    assert r.status_code == 422, r.text
    assert client.get("/api/settings").json()[key] == "false"


def test_the_settings_page_has_both_boxes_and_the_test_print_sends_them():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app/static/js/settings.js").read_text(
        encoding="utf-8"
    )
    assert 'name="check_blank_stock" value="true" data-bool' in js
    assert 'name="check_micr_amount" value="true" data-bool' in js
    assert (
        '<label for="settings-check-blank-stock" style="font-weight:normal; margin:0;">'
        "Print on blank check stock</label>"
    ) in js
    assert "'check_nudge_y_in', 'check_blank_stock', 'check_micr_amount'" in js


# ----- a bank account's details ----------------------------------------


def _url(seed_accounts, number="1000"):
    return f"/api/settings/check-bank-details/{seed_accounts[number].id}"


def test_a_bank_accounts_details_start_empty_and_round_trip(client, seed_accounts):
    r = client.get(_url(seed_accounts))
    assert r.status_code == 200, r.text
    assert r.json()["routing_number"] == "" and not r.json()["has_account_number"]
    r = client.put(_url(seed_accounts), json=dict(_BANK))
    assert r.status_code == 200, r.text
    got = client.get(_url(seed_accounts)).json()
    assert got == {
        "account_id": seed_accounts["1000"].id,
        "bank_name": "First Prairie Bank",
        "bank_city_state": "Joliet, IL",
        "fractional_routing": "70-1234/719",
        "routing_number": "123456780",
        "account_number_last_four": "7788",
        "has_account_number": True,
    }
    # the full account number is never read back
    assert "4455667788" not in client.get(_url(seed_accounts)).text


def test_the_account_number_is_encrypted_at_rest(client, db_session, seed_accounts):
    from app.models.bank_check_details import BankCheckDetails

    client.put(_url(seed_accounts), json=dict(_BANK))
    row = db_session.query(BankCheckDetails).one()
    assert row.account_number_enc.startswith("fernet:")
    assert "4455667788" not in row.account_number_enc


def test_leaving_the_account_number_out_keeps_it_and_empty_clears_it(
    client, seed_accounts
):
    client.put(_url(seed_accounts), json=dict(_BANK))
    r = client.put(_url(seed_accounts), json={"bank_name": "Prairie Bank"})
    assert r.status_code == 200, r.text
    assert r.json()["has_account_number"] and r.json()["bank_name"] == "Prairie Bank"
    assert r.json()["routing_number"] == "123456780"
    r = client.put(_url(seed_accounts), json={"account_number": ""})
    assert not r.json()["has_account_number"]


@pytest.mark.parametrize(
    "body",
    [
        {"routing_number": "123456789"},  # fails the ABA check digit
        {"routing_number": "12345678"},
        {"account_number": "12 34"},
        {"account_number": "1" * 18},
        {"fractional_routing": "seventy"},
        {"bank_name": "x" * 101},
        {"bank_name": None},
        {"bank_colour": "blue"},
    ],
)
def test_bad_details_are_refused_and_nothing_is_saved(client, seed_accounts, body):
    r = client.put(_url(seed_accounts), json=body)
    assert r.status_code == 422, r.text
    assert client.get(_url(seed_accounts)).json()["bank_name"] == ""


def test_details_are_for_a_bank_account_only(client, seed_accounts):
    assert client.get(_url(seed_accounts, "6000")).status_code == 400
    assert client.get("/api/settings/check-bank-details/999999").status_code == 404


# ----- the face, rendered to HTML ----------------------------------------


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_off_the_face_is_byte_for_byte_what_it_was(position):
    without = render_checks_html(
        [_check(bank_details=None)], {**_COMPANY, "check_position": position}
    )
    assert _html(check_position=position) == without
    for mark in ("check-bank", "check-fraction", "check-logo", 'class="micr"'):
        assert mark not in without


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_on_the_face_carries_the_bank_the_fraction_and_the_micr_line(position):
    html = _html(check_position=position, **_ON)
    assert (
        '<div class="check-bank"><span class="check-bank-name">First Prairie Bank'
        "</span><br>Joliet, IL</div>"
    ) in html
    assert '<div class="check-fraction">70-1234/719</div>' in html
    assert html.count('<svg class="micr"') == 1
    assert '<img class="check-logo"' not in html  # no logo saved


def test_on_preprinted_stock_setting_still_prints_the_labels(monkeypatch):
    html = _html(check_stock="preprinted", **_ON)
    assert "{ visibility: hidden; }" not in html


def test_a_saved_logo_prints_at_the_top_left_with_the_name_beside_it(monkeypatch):
    monkeypatch.setattr(pdf_service, "_company_logo_data_uri", lambda s: _PNG)
    for position in ("bottom", "top"):
        html = _html(check_position=position, **_ON)
        assert f'<img class="check-logo" src="{_PNG}"' in html
        assert ' check-has-logo"' in html
    # off, it does not print
    assert "check-logo" not in _html()


def test_the_micr_amount_is_encoded_only_when_asked(monkeypatch):
    seen = []
    real = pdf_service._blank_stock_fields

    def spy(check, micr_amount):
        real(check, micr_amount)
        seen.append(check["micr_line"])

    monkeypatch.setattr(pdf_service, "_blank_stock_fields", spy)
    _html(**_ON)
    _html(check_micr_amount="true", **_ON)
    assert seen[0].endswith(ON_US + " " * 13)
    assert seen[1].endswith("⑇0000123456⑇")
    assert all(TRANSIT + "123456780" + TRANSIT in line for line in seen)


# ----- the face, rendered to PDF -----------------------------------------


def _pdf(checks=None, **settings):
    return pdf_service.render_pdf(_html(checks, **settings))


def _pages(pdf):
    pypdf = pytest.importorskip("pypdf")
    return pypdf.PdfReader(io.BytesIO(pdf))


def _words(reader, page):
    words = []

    def visit(text, cm, tm, _font, _size):
        if text.strip():
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            words.append((text.strip(), round(x, 2), round(y, 2)))

    reader.pages[page].extract_text(visitor_text=visit)
    return words


def _mul(a, b):
    return [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def _micr_cells(reader, page):
    """The rectangles one 0.013 in cell tall, in inches from the sheet's
    bottom left: the MICR glyphs' runs."""
    from pypdf.generic import ContentStream

    p = reader.pages[page]
    ctm, stack, cells = [1, 0, 0, 1, 0, 0], [], []
    for args, op in ContentStream(p.get_contents(), reader).operations:
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            ctm = stack.pop()
        elif op == b"cm":
            ctm = _mul([float(a) for a in args], ctm)
        elif op == b"re":
            x, y, w, h = (float(a) for a in args)
            xs = (
                x * ctm[0] + y * ctm[2] + ctm[4],
                (x + w) * ctm[0] + (y + h) * ctm[2] + ctm[4],
            )
            ys = (
                x * ctm[1] + y * ctm[3] + ctm[5],
                (x + w) * ctm[1] + (y + h) * ctm[3] + ctm[5],
            )
            if abs(abs(ys[1] - ys[0]) - 0.013 * 72) < 0.02:
                cells.append(
                    tuple(v / 72 for v in (min(xs), min(ys), max(xs), max(ys)))
                )
    return cells


# where the bottom edge of the check is, from the foot of the sheet
_CHECK_FOOT = {"bottom": 0.0, "top": 11 - 11 / 3}


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_the_pdf_prints_every_new_element_on_and_none_off(position):
    on = _pages(_pdf(check_position=position, check_micr_amount="true", **_ON))
    text = on.pages[0].extract_text()
    for piece in ("Acme Builders", "1 Main St", "#1072", *sorted(_NEW_WORDS)):
        assert piece in text
    cells = _micr_cells(on, 0)
    assert len(cells) > 100
    # (the amount field on, so position 1 prints) the right edge of
    # position 1 at 5/16 in from the sheet's right edge,
    # the glyph centered in its 1/8 in pitch; the baseline 3/16 in above the
    # check's bottom edge
    assert max(c[2] for c in cells) == pytest.approx(8.5 - 0.3125 - 0.017, abs=0.002)
    assert min(c[1] for c in cells) == pytest.approx(
        _CHECK_FOOT[position] + 0.1875, abs=0.002
    )
    off = _pages(_pdf(check_position=position))
    assert _micr_cells(off, 0) == []
    assert not any(_is_new(w) for w, _x, _y in _words(off, 0))


def _is_new(text):
    """A word of the blank-stock face's new elements: whole, or the piece
    pypdf hands back where a font set splits one at a space ("First" /
    "Prairie Bank")."""
    return len(text) > 1 and any(text in phrase for phrase in _NEW_WORDS)


def _lines(words):
    """Each line's words joined in reading order, so a field one font set
    splits ("Oct" / "24, 2026") reads whole again."""
    rows = {}
    for text, x, y in words:
        rows.setdefault(y, []).append((x, text))
    return [" ".join(t for _x, t in sorted(row)) for row in rows.values()]


def _only_the_new_words_differ(off, on):
    """Every word the plain face prints is on blank stock at the same place,
    and what blank stock adds is the new elements and nothing else. Matched
    by place: a word's text is whatever pypdf makes of it on the font set at
    hand. Returns the added words."""
    assert [w for w in off if w not in on] == []
    added = [w for w in on if w not in off]
    assert all(_is_new(w[0]) for w in added), added
    return added


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_every_field_prints_at_the_same_place_on_and_off(position):
    off = _words(_pages(_pdf(check_position=position)), 0)
    on = _words(_pages(_pdf(check_position=position, **_ON)), 0)
    added = _only_the_new_words_differ(off, on)
    for phrase in _NEW_WORDS:
        assert any(phrase in line for line in _lines(added)), (phrase, added)
    # the fields the face carries are among the plain face's words
    lines = _lines(off)
    for field in (
        "#1072",
        "DATE",
        "Oct 24, 2026",
        "Gulf Supply",
        "$1,234.56",
        "MEMO",
        "Invoice 55",
    ):
        assert any(field in line for line in lines), (field, lines)


@pytest.mark.parametrize("position", ["bottom", "top"])
def test_with_a_logo_only_the_company_block_moves(monkeypatch, position):
    monkeypatch.setattr(pdf_service, "_company_logo_data_uri", lambda s: _PNG)
    off = _words(_pages(_pdf(check_position=position)), 0)
    reader = _pages(_pdf(check_position=position, **_ON))
    on = _words(reader, 0)
    assert len(reader.pages[0].images) == 1

    def face_block(words):
        # the company block: the top inch of the face's left side, the face
        # being the sheet's bottom third or its top third. The stubs' name
        # sits outside the face, the date and the number to the right. Picked
        # by place, not by text: one font set hands pypdf "Tampa," and
        # "FL 33601" as two words, and the block's words are what moved.
        top = (11 / 3 if position == "bottom" else 11) * 72
        return sorted(
            (w for w in words if w[1] < 200 and top - 72 < w[2] < top),
            key=lambda w: (-w[2], w[1]),
        )

    block_off, block_on = face_block(off), face_block(on)
    kept_on = [w for w in on if w not in block_on]
    kept_off = [w for w in off if w not in block_off]
    _only_the_new_words_differ(kept_off, kept_on)
    # the face's company block, name, street and city line, sits 1.25 in
    # right of where it was, each word at its height
    assert " ".join(_lines(block_off)) == "Acme Builders 1 Main St Tampa, FL 33601"
    assert len(block_on) == len(block_off) >= 3
    for (t0, x0, y0), (t1, x1, y1) in zip(block_off, block_on):
        assert t0 == t1 and y0 == y1
        assert x1 - x0 == pytest.approx(1.25 * 72, abs=0.5)


# ----- the print paths ---------------------------------------------------


def _bank_details(client, seed_accounts):
    r = client.put(
        f"/api/settings/check-bank-details/{seed_accounts['1000'].id}", json=dict(_BANK)
    )
    assert r.status_code == 200, r.text


def _unprinted(client, seed_accounts, amount):
    name = {10: "Harbor Supply", 11: "Bay Rentals", 12: "Gulf Lumber"}[amount]
    v = client.post("/api/vendors", json={"name": name})
    assert v.status_code in (200, 201), v.text
    bill = client.post(
        "/api/bills",
        json={
            "vendor_id": v.json()["id"],
            "bill_number": f"B-{amount}",
            "date": "2026-09-01",
            "lines": [
                {
                    "description": "Supplies",
                    "amount": amount,
                    "account_id": seed_accounts["6000"].id,
                }
            ],
        },
    )
    assert bill.status_code == 201, bill.text
    r = client.post(
        "/api/bill-payments",
        json={
            "vendor_id": v.json()["id"],
            "date": "2026-09-10",
            "amount": amount,
            "method": "check",
            "pay_from_account_id": seed_accounts["1000"].id,
            "allocations": [{"bill_id": bill.json()["id"], "amount": amount}],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _batch(client, seed_accounts, ids):
    return client.post(
        "/api/checks/print-batch",
        json={
            "bank_account_id": seed_accounts["1000"].id,
            "checks": [
                {"bill_payment_id": i, "check_number": str(3001 + n)}
                for n, i in enumerate(ids)
            ],
        },
    )


def test_the_batch_prints_the_micr_line_on_every_page(client, seed_accounts):
    _bank_details(client, seed_accounts)
    client.put("/api/settings", json=_ON)
    ids = [_unprinted(client, seed_accounts, a) for a in (10, 11, 12)]
    r = _batch(client, seed_accounts, ids)
    assert r.status_code == 200, r.text[:300]
    reader = _pages(r.content)
    assert len(reader.pages) == 3
    for page in range(3):
        assert len(_micr_cells(reader, page)) > 100
        assert "First Prairie Bank" in reader.pages[page].extract_text()


def test_the_batch_puts_each_checks_number_in_its_micr_line(
    client, seed_accounts, monkeypatch
):
    seen = []

    def fake(checks, company):
        render_checks_html(checks, company)
        seen.extend(c["micr_line"] for c in checks)
        return b"%PDF-stub"

    monkeypatch.setattr(checks_route, "generate_checks_pdf", fake)
    _bank_details(client, seed_accounts)
    client.put("/api/settings", json=_ON)
    ids = [_unprinted(client, seed_accounts, a) for a in (10, 11)]
    assert _batch(client, seed_accounts, ids).status_code == 200
    assert [line.split(" ")[0] for line in seen] == [
        f"{ON_US}3001{ON_US}",
        f"{ON_US}3002{ON_US}",
    ]


def test_a_batch_on_an_account_without_details_is_refused_and_not_numbered(
    client, seed_accounts
):
    client.put("/api/settings", json=_ON)
    ids = [_unprinted(client, seed_accounts, 10)]
    r = _batch(client, seed_accounts, ids)
    assert r.status_code == 422, r.text
    assert "routing and account number" in r.json()["detail"]
    assert client.get(f"/api/bill-payments/{ids[0]}").json()["check_number"] is None


def test_a_single_check_prints_the_micr_line_and_is_refused_without_details(
    client, seed_accounts, monkeypatch
):
    seen = []

    def fake(check, company):
        render_checks_html([check], company)
        seen.append(check["micr_line"])
        return b"%PDF-stub"

    monkeypatch.setattr(checks_route, "generate_check_pdf", fake)
    client.put("/api/settings", json=_ON)
    pid = _unprinted(client, seed_accounts, 10)
    r = client.get(f"/api/checks/print?bill_payment_id={pid}")
    assert r.status_code == 422, r.text
    _bank_details(client, seed_accounts)
    r = client.get(f"/api/checks/print?bill_payment_id={pid}")
    assert r.status_code == 200, r.text
    assert TRANSIT + "123456780" + TRANSIT in seen[0]


def test_off_a_check_prints_without_any_bank_details(client, seed_accounts):
    pid = _unprinted(client, seed_accounts, 10)
    r = client.get(f"/api/checks/print?bill_payment_id={pid}")
    assert r.status_code == 200, r.text


def test_the_test_print_uses_sample_bank_details_never_a_saved_accounts(
    client, seed_accounts, monkeypatch
):
    seen = {}

    def fake(check, company):
        seen["html"] = render_checks_html([check], company)
        seen["line"] = check.get("micr_line")
        return b"%PDF-stub"

    monkeypatch.setattr(checks_route, "generate_check_pdf", fake)
    _bank_details(client, seed_accounts)
    r = client.get("/api/checks/test-print?check_blank_stock=true")
    assert r.status_code == 200, r.text
    assert "Sample Bank" in seen["html"]
    assert "4455667788" not in seen["line"] and "000123456789" in seen["line"]
    r = client.get("/api/checks/test-print?check_blank_stock=maybe")
    assert r.status_code == 422
    # left out, the saved setting decides: off
    client.get("/api/checks/test-print")
    assert 'class="micr"' not in seen["html"]


# ----- the account number's key --------------------------------------------

_PAYROLL_PW = "a-real-payroll-secret-for-this-test"
_NEXT_PAYROLL_PW = "the-next-payroll-secret-for-this-test"


def test_rotating_the_payroll_secret_rewraps_the_account_number(
    client, seed_accounts, db_session, monkeypatch, tmp_path
):
    """With the settings key derived from the payroll secret (a Docker
    install), the rotation's rewrap re-encrypts the check account number
    with the saved passwords, so it still reads after the old secret goes."""
    from app.models.bank_check_details import BankCheckDetails
    from app.services import bank_check_details as details
    from app.services import crypto
    from app.services.encryption import rewrap_all

    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("PAYROLL_ENCRYPTION_SECRET_PREV", raising=False)
    monkeypatch.setenv("PAYROLL_ENCRYPTION_SECRET", _PAYROLL_PW)
    monkeypatch.setattr(crypto, "_KEY_FILE", tmp_path / ".slowbooks-master.key")
    crypto.reset_cache_for_tests()
    try:
        assert crypto.key_source() == "derived"
        _bank_details(client, seed_accounts)
        monkeypatch.setenv("PAYROLL_ENCRYPTION_SECRET", _NEXT_PAYROLL_PW)
        monkeypatch.setenv("PAYROLL_ENCRYPTION_SECRET_PREV", _PAYROLL_PW)
        crypto.reset_cache_for_tests()
        summary = rewrap_all(db_session)
        assert summary["rewrapped"] == 1 and summary["failed"] == 0, summary
        monkeypatch.delenv("PAYROLL_ENCRYPTION_SECRET_PREV")
        crypto.reset_cache_for_tests()
        row = db_session.query(BankCheckDetails).one()
        db_session.refresh(row)
        got = details.for_check(db_session, seed_accounts["1000"].id)
        assert got["account_number"] == "4455667788"
    finally:
        crypto.reset_cache_for_tests()


def test_the_accountants_copy_carries_no_check_account_number(tmp_path):
    """The copy does not carry the key the number is encrypted under, as it
    does not carry the encrypted settings."""
    from app.services import company_file
    from app.services.accountants_copy import _strip_owner

    plain = tmp_path / "copy.db"
    conn = company_file.connect_raw(plain, None)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE bank_check_details (id INTEGER PRIMARY KEY, account_id "
        "INTEGER, bank_name TEXT, routing_number TEXT, account_number_enc TEXT, "
        "account_last_four TEXT)"
    )
    conn.execute(
        "INSERT INTO bank_check_details VALUES "
        "(1, 7, 'First Prairie Bank', '123456780', 'fernet:v1:abc', '7788')"
    )
    conn.commit()
    conn.close()
    _strip_owner(
        plain,
        {
            "copy_id": "c" * 36,
            "token": "t" * 64,
            "dividing_date": "2026-03-31",
            "made_at": "2026-10-20T00:00:00+00:00",
            "company_name": "Harbor Light",
            "last_txn_id": 0,
        },
    )
    conn = company_file.connect_raw(plain, None)
    try:
        row = conn.execute(
            "SELECT bank_name, routing_number, account_number_enc, "
            "account_last_four FROM bank_check_details"
        ).fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("First Prairie Bank", "123456780", None, None)
