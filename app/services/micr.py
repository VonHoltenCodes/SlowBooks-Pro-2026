"""The MICR line of a check printed on blank stock (#739).

The line carries the E-13B character set: the digits 0-9 and four symbols,
Transit (U+2446), Amount (U+2447), On-Us (U+2448) and Dash (U+2449). Each
glyph here is drawn on E-13B's grid of 0.013 in square cells, 7 cells wide
and 9 tall (0.091 x 0.117 in) inside the standard 0.125 in character pitch,
after the E-13B standard shapes, and printed as filled rectangles, so no
font is needed. Whether a bank's reader-sorter can read the line
magnetically depends on printing it with MICR toner, which is the
printer's job, not this module's.

Field positions follow ANSI X9.100-160, counted from the right edge of the
check in 0.125 in steps (position 1 is the rightmost): the amount in 1-12,
the on-us field (account number) ending at 14, the transit field (routing
number) in 33-43 and the auxiliary on-us field (check number) ending at 45.
"""

from decimal import Decimal

from markupsafe import Markup

TRANSIT = "⑆"
AMOUNT = "⑇"
ON_US = "⑈"
DASH = "⑉"

# '#' is a filled 0.013 in cell. Rows run top to bottom.
GLYPHS = {
    "0": (
        "#######",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "#######",
    ),
    "1": (
        "..##...",
        "..##...",
        "..##...",
        "..##...",
        "..##...",
        "..####.",
        "..####.",
        "..####.",
        "..####.",
    ),
    "2": (
        "######.",
        ".....#.",
        ".....#.",
        "######.",
        "##.....",
        "##.....",
        "##.....",
        "#######",
        "#######",
    ),
    "3": (
        "#####..",
        "....#..",
        "....#..",
        ".######",
        "....###",
        "....###",
        "....###",
        "#######",
        "#######",
    ),
    "4": (
        "##.....",
        "##.....",
        "##..#..",
        "##..#..",
        "#######",
        "#######",
        "....##.",
        "....##.",
        "....##.",
    ),
    "5": (
        "######.",
        "#......",
        "#......",
        "######.",
        ".....##",
        ".....##",
        ".....##",
        "#######",
        "#######",
    ),
    "6": (
        "#####..",
        "#......",
        "#......",
        "#......",
        "#######",
        "##...##",
        "##...##",
        "##...##",
        "#######",
    ),
    "7": (
        "#######",
        ".....#.",
        ".....#.",
        "....#..",
        "...##..",
        "...##..",
        "...##..",
        "...##..",
        "...##..",
    ),
    "8": (
        ".#####.",
        ".#...#.",
        ".#...#.",
        ".#####.",
        "##...##",
        "##...##",
        "##...##",
        "##...##",
        "#######",
    ),
    "9": (
        "#######",
        "#....##",
        "#....##",
        "#....##",
        "#######",
        ".....##",
        ".....##",
        ".....##",
        ".....##",
    ),
    TRANSIT: (
        "##..###",
        "##..###",
        "##.....",
        "##.....",
        "##.....",
        "##.....",
        "##.....",
        "##..###",
        "##..###",
    ),
    AMOUNT: (
        "##...##",
        "##...##",
        "##...##",
        "##.#.##",
        "##.#.##",
        "##.#.##",
        "##...##",
        "##...##",
        "##...##",
    ),
    ON_US: (
        "##.##..",
        "##.##..",
        "##.##..",
        "##.##..",
        "##.##..",
        "##.##..",
        ".......",
        "#######",
        "#######",
    ),
    DASH: (
        ".......",
        ".......",
        ".......",
        "###.###",
        "###.###",
        ".......",
        ".......",
        ".......",
        ".......",
    ),
}

CELL_IN = Decimal("0.013")
PITCH_IN = Decimal("0.125")
# the right edge of position 1, from the right edge of the check
FIRST_POSITION_IN = Decimal("0.3125")
# the characters' baseline, from the bottom edge of the check
BASELINE_IN = Decimal("0.1875")

# Field ends, as positions from the right edge (X9.100-160).
AMOUNT_END = 1
ON_US_END = 14
ON_US_START = 31
TRANSIT_END = 33
AUX_ON_US_END = 45


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def micr_line(routing: str, account: str, check_number="", amount=None) -> str:
    """The MICR line as a string whose last character is position 1, a
    space for each empty position. `account` may carry dashes (printed as
    the Dash symbol). The check number goes in the auxiliary on-us field
    when it is digits; `amount` (a Decimal, or None to leave the field for
    the bank of first deposit) is encoded in cents, ten digits."""
    from app.services.nacha_export import validate_routing_number

    if not validate_routing_number(routing):
        raise ValueError("The routing number is not a valid ABA routing number.")
    on_us = str(account or "").strip().replace("-", DASH)
    if not on_us or any(ch not in "0123456789" + DASH for ch in on_us):
        raise ValueError("The account number is digits and dashes.")
    if len(on_us) + 1 > ON_US_START - ON_US_END + 1:
        raise ValueError("The account number is at most 17 characters.")
    cells = {}

    def place(field: str, end: int):
        for i, ch in enumerate(reversed(field)):
            cells[end + i] = ch

    if amount is not None:
        from app.services.accounting import _q

        cents = int(_q(amount) * 100)
        if not 0 <= cents < 10**10:
            raise ValueError("The amount does not fit the MICR amount field.")
        place(f"{AMOUNT}{cents:010d}{AMOUNT}", AMOUNT_END)
    place(on_us + ON_US, ON_US_END)
    place(f"{TRANSIT}{routing}{TRANSIT}", TRANSIT_END)
    number = _digits(check_number)
    if number and number == str(check_number).strip():
        place(f"{ON_US}{number}{ON_US}", AUX_ON_US_END)
    return "".join(cells.get(p, " ") for p in range(max(cells), 0, -1))


def glyph_rects(ch: str):
    """The filled cells of one glyph as (column, row, width) runs, row 0 at
    the top; a run joins the filled cells next to each other in a row."""
    runs = []
    for row, cells in enumerate(GLYPHS[ch]):
        col = 0
        while col < len(cells):
            if cells[col] == "#":
                start = col
                while col < len(cells) and cells[col] == "#":
                    col += 1
                runs.append((start, row, col - start))
            else:
                col += 1
    return runs


def micr_svg(line: str, width_in: Decimal, height_in: Decimal) -> Markup:
    """The line drawn as an SVG `width_in` wide (the check's width; the
    SVG's right edge is the check's) and `height_in` tall (its bottom edge
    is the check's), in thousandths of an inch. Each glyph is centered in
    its 0.125 in position."""
    mil = Decimal(1000)
    w, h = width_in * mil, height_in * mil
    cell = CELL_IN * mil
    glyph_w = cell * 7
    inset = (PITCH_IN * mil - glyph_w) / 2
    top = h - BASELINE_IN * mil - cell * 9
    rects = []
    for i, ch in enumerate(reversed(line)):
        if ch == " ":
            continue
        right = w - FIRST_POSITION_IN * mil - PITCH_IN * mil * i
        x0 = right - inset - glyph_w
        for col, row, run in glyph_rects(ch):
            rects.append(
                f'<rect x="{_n(x0 + cell * col)}" y="{_n(top + cell * row)}" '
                f'width="{_n(cell * run)}" height="{_n(cell)}"/>'
            )
    return Markup(
        f'<svg class="micr" xmlns="http://www.w3.org/2000/svg" '
        f'width="{_n(width_in)}in" height="{_n(height_in)}in" '
        f'viewBox="0 0 {_n(w)} {_n(h)}" role="img" aria-label="MICR line">'
        f'<g fill="#000">{"".join(rects)}</g></svg>'
    )


def _n(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text
