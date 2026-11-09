"""#174: the Job Cost Entry dialog's cost lines were cut off.

Eleven columns in a 700px dialog whose table container hides overflow: the
Bill? column and the remove button were behind the edge with no scrollbar,
and the selects had shrunk to a few characters. The dialog opens wide now
and the table scrolls sideways when the window is narrower still.
"""

from pathlib import Path

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
CSS = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "style.css"


def test_open_modal_takes_a_wide_option_and_close_clears_it():
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    assert "function openModal(title, html, opts)" in utils
    assert "modal.classList.toggle('modal--wide', !!(opts && opts.wide))" in utils
    assert "$('#modal').classList.remove('modal--wide')" in utils


def test_the_job_cost_entry_opens_wide_with_a_scrolling_table():
    js = (JS / "job_costs.js").read_text(encoding="utf-8")
    assert "{ wide: true });" in js
    assert 'class="table-container table-container--scroll"' in js
    # the controls keep a readable width instead of shrinking to fit
    for cls in ("jc-code", "jc-who", "jc-debit", "jc-credit"):
        assert f'class="{cls}" style="min-width:' in js, cls


def test_the_stylesheet_carries_both_rules():
    css = CSS.read_text(encoding="utf-8")
    # as wide as the window less a margin, up to 1300px (NEW-187: the 90%,
    # then 96%, left a construction bill's lines scrolling; 1280 left its
    # move column no slack on a wider font set, 2.31.11 gate L-1)
    assert ".modal.modal--wide {" in css
    assert "width: calc(100% - 12px);\n    max-width: 1300px;" in css
    assert ".table-container--scroll {" in css and "overflow-x: auto" in css
