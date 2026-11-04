"""The Customer Statement dialog keeps "As of" in view (macbase1, F22).

The customer select sized itself to its longest option, so a
120-character customer name pushed the As of date past the dialog's right
edge behind a horizontal scrollbar. The dialog's grid now lets the select
shrink (minmax(0, ...)) and the select fills its column (width:100%).

The same name still pushed Date, Due Date, Class and Exchange Rate out of the
Edit Invoice dialog at the 2.18.0 gate: every form's grid had the same
columns. The form grid itself now lets its columns shrink, so the invoice,
estimate and sales receipt forms (and every other form dialog) keep their
second column in view. tests/test_browser_ui.py measures those three
in a real browser at 1280 x 800.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS_JS = ROOT / "app" / "static" / "js" / "reports.js"
STYLE_CSS = ROOT / "app" / "static" / "css" / "style.css"


def _statement_dialog() -> str:
    js = REPORTS_JS.read_text(encoding="utf-8")
    start = js.index("async customerStatementPicker()")
    return js[start : js.index("openStatement(e, kind", start)]


def test_the_customer_select_cannot_push_the_date_out_of_the_dialog():
    dialog = _statement_dialog()
    assert "grid-template-columns:minmax(0, 2fr) minmax(0, 1fr);" in dialog
    select = dialog[dialog.index('<select name="customer_id"') :].split(">", 1)[0]
    assert "width:100%" in select and "min-width:0" in select
    assert 'name="as_of_date"' in dialog


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def test_no_form_grid_column_is_sized_by_its_longest_option():
    css = STYLE_CSS.read_text(encoding="utf-8")
    # 1fr is minmax(auto, 1fr): the column grew to the select's longest option
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in _rule(
        css, ".form-grid"
    )
    assert "min-width: 0;" in _rule(css, ".form-group")
