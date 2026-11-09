"""The MICR line of a check on blank stock (#739): the E-13B glyphs drawn
on their grid, the line's fields at their ANSI X9.100-160 positions, and the
SVG that places each glyph in inches from the check's right and bottom
edges."""

import re
from decimal import Decimal

import pytest

from app.services.micr import (
    AMOUNT,
    DASH,
    GLYPHS,
    ON_US,
    TRANSIT,
    glyph_rects,
    micr_line,
    micr_svg,
)


def test_there_are_the_fourteen_e13b_glyphs():
    assert set(GLYPHS) == set("0123456789") | {TRANSIT, AMOUNT, ON_US, DASH}


@pytest.mark.parametrize("ch", sorted(GLYPHS))
def test_each_glyph_is_seven_cells_wide_and_nine_tall_of_filled_or_empty(ch):
    rows = GLYPHS[ch]
    assert len(rows) == 9
    assert all(len(row) == 7 and set(row) <= {"#", "."} for row in rows)
    assert any("#" in row for row in rows)


def test_no_two_glyphs_are_the_same():
    shapes = list(GLYPHS.values())
    assert len(set(shapes)) == len(shapes)


def test_a_glyphs_runs_cover_exactly_its_filled_cells():
    for ch, rows in GLYPHS.items():
        filled = {
            (c, r) for r, row in enumerate(rows) for c, x in enumerate(row) if x == "#"
        }
        covered = {(c + i, r) for c, r, w in glyph_rects(ch) for i in range(w)}
        assert covered == filled, ch


def test_the_line_puts_each_field_at_its_positions():
    line = micr_line("123456780", "000123456789", "1072", Decimal("1234.56"))
    expected = (
        f"{ON_US}1072{ON_US}"  # auxiliary on-us, ending at position 45
        " "  # 44
        f"{TRANSIT}123456780{TRANSIT}"  # transit, 43-33
        + " " * 6  # 32-27
        + f"000123456789{ON_US}"  # on-us, ending at position 14
        " "  # 13
        f"{AMOUNT}0000123456{AMOUNT}"  # amount, 12-1
    )
    assert line == expected
    # position p is line[-p]
    assert line[-1] == AMOUNT and line[-12] == AMOUNT
    assert line[-14] == ON_US
    assert line[-33] == TRANSIT and line[-43] == TRANSIT
    assert line[-45] == ON_US


def test_without_the_amount_the_amount_field_is_left_blank():
    line = micr_line("123456780", "000123456789", "1072")
    assert line.endswith(ON_US + " " * 13)
    assert AMOUNT not in line
    assert line[-33] == TRANSIT and line[-45] == ON_US


def test_the_amount_is_ten_digits_of_cents_rounded_to_the_cent():
    assert micr_line("123456780", "1", amount=Decimal("5"))[-12:] == (
        f"{AMOUNT}0000000500{AMOUNT}"
    )
    assert micr_line("123456780", "1", amount=Decimal("0.005"))[-12:] == (
        f"{AMOUNT}0000000001{AMOUNT}"
    )
    with pytest.raises(ValueError):
        micr_line("123456780", "1", amount=Decimal("100000000.00"))


def test_a_dash_in_the_account_number_prints_as_the_dash_symbol():
    line = micr_line("123456780", "12-345")
    assert f"12{DASH}345{ON_US}" in line


def test_a_check_number_that_is_not_digits_leaves_the_auxiliary_field_out():
    line = micr_line("123456780", "12345", "EFT")
    assert len(line) == 43 and line[0] == TRANSIT
    assert len(micr_line("123456780", "12345", "")) == 43


@pytest.mark.parametrize("routing", ["123456789", "12345678", "abcdefghi", ""])
def test_a_routing_number_that_fails_the_aba_check_digit_is_refused(routing):
    with pytest.raises(ValueError):
        micr_line(routing, "12345")


@pytest.mark.parametrize("account", ["", "12 34", "12a4", "1" * 18])
def test_an_account_number_that_does_not_fit_the_on_us_field_is_refused(account):
    with pytest.raises(ValueError):
        micr_line("123456780", account)


def _rects(svg):
    return [
        tuple(Decimal(v) for v in m)
        for m in re.findall(
            r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"/>',
            svg,
        )
    ]


def test_the_svg_places_position_one_from_the_checks_right_and_bottom_edges():
    # in thousandths of an inch: the right edge of position 1 at 0.3125 in
    # from the right edge, the 0.091 in glyph centered in its 0.125 in pitch,
    # its bottom on the 0.1875 in baseline
    svg = str(micr_svg("0", Decimal("8.5"), Decimal("0.625")))
    assert 'width="8.5in" height="0.625in" viewBox="0 0 8500 625"' in svg
    rects = _rects(svg)
    assert max(x + w for x, y, w, h in rects) == Decimal("8170.5")
    assert min(x for x, y, w, h in rects) == Decimal("8079.5")
    assert max(y + h for x, y, w, h in rects) == Decimal("437.5")
    assert min(y for x, y, w, h in rects) == Decimal("320.5")
    assert all(h == 13 for x, y, w, h in rects)


def test_each_position_is_one_eighth_inch_to_the_left_of_the_last():
    one = _rects(str(micr_svg("0", Decimal("8.5"), Decimal("0.625"))))
    two = _rects(str(micr_svg("0 ", Decimal("8.5"), Decimal("0.625"))))
    assert [(x - 125, y, w, h) for x, y, w, h in one] == two
