"""The Customer Statement dialog keeps "As of" in view (macbase1, F22).

The customer select sized itself to its longest option, so a
120-character customer name pushed the As of date past the dialog's right
edge behind a horizontal scrollbar. The dialog's grid now lets the select
shrink (minmax(0, ...)) and the select fills its column (width:100%).
"""

from pathlib import Path

REPORTS_JS = (
    Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "reports.js"
)


def _statement_dialog() -> str:
    js = REPORTS_JS.read_text(encoding="utf-8")
    start = js.index("async customerStatementPicker()")
    return js[start : js.index("openStatement(e)", start)]


def test_the_customer_select_cannot_push_the_date_out_of_the_dialog():
    dialog = _statement_dialog()
    assert "grid-template-columns:minmax(0, 2fr) minmax(0, 1fr);" in dialog
    select = dialog[dialog.index('<select name="customer_id"') :].split(">", 1)[0]
    assert "width:100%" in select and "min-width:0" in select
    assert 'name="as_of_date"' in dialog
