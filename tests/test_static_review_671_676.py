"""#671 and #676, pinned on the source in the repo's style (the API tests
are tests/test_bank_feed_review.py and tests/test_transfers.py).

#671 — the To review row set only the category; the add call already took a
class, a job and a vendor or customer, and Add all categorised posted none
of them. The row now has a Class box (once the company has a class of its
own), a Job box (once there are jobs) and a Name box, each named, and both
Add and Add all send what each row chose. The register's entry row has the
Class box + Entry has.
#676 — the Transfer dialog had no class, and + Entry to a bank or card
account showed one and dropped it.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

JS = (
    Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "banking.js"
).read_text(encoding="utf-8")


def _body(start, end):
    i = JS.index(start)
    return JS[i : JS.index(end, i + len(start))]


def test_the_review_row_draws_class_job_and_name_under_the_forms_rules():
    cells = _body("    _reviewDimCells(t) {", "    _reviewPicks(lineId) {")
    # a class only once the company has one of its own; a job once jobs exist
    assert "if (BankingPage._lineClassShown()) {" in cells
    assert "if (jobs.length) {" in cells
    # every box is named, and takes part in the read-only role's lock
    for box in ("rv-class-", "rv-job-", "rv-customer-", "rv-vendor-"):
        tag = cells[cells.index(f'id="{box}') :]
        tag = tag[: tag.index(">")]
        assert "aria-label=" in tag and "data-write" in tag, box
    # money in names a customer, whose jobs the Job box narrows to
    assert 'data-customer-select="rv-customer-${t.id}"' in cells
    # money out names a vendor from the vendor list, as the register's payee box
    assert 'list="review-vendors"' in cells
    panel = _body(
        "    _reviewPanel(review, feedId, accountId, kind) {",
        "    async _fillCategorySelects",
    )
    assert "${BankingPage._reviewDimCells(t)}" in panel
    assert "${dimHeads}" in panel
    assert "BankingPage._vendorDatalist('review-vendors')" in panel


def test_add_and_add_all_send_each_rows_picks():
    picks = _body("    _reviewPicks(lineId) {", "\n    },")
    for key in ("'class_id'", "'job_id'", "'customer_id'"):
        assert key in picks
    assert "picks.vendor_id = vendorId; else picks.payee = name;" in picks
    add = _body("    async addLine(lineId, accountId) {", "\n    },")
    assert "const body = { ...picks };" in add
    assert "API.post(`/banking/transactions/${lineId}/add`, body)" in add
    add_all = _body("    async addAll(feedId, accountId) {", "\n    },")
    assert "BankingPage._reviewPicks(t.id)" in add_all
    assert "{ lines: picked }" in add_all


def test_the_entry_row_has_the_class_box_and_posts_it():
    row = _body(
        "    _quickEntryRow(accountId, kind, chart, outLabel, inLabel) {", "\n    },"
    )
    assert "${BankingPage._quickClassHtml(key)}" in row
    box = _body("    _quickClassHtml(key) {", "\n    },")
    assert "if (!BankingPage._lineClassShown()) return '';" in box
    assert 'id="qe-class" aria-label="New entry ${T(\'class\').toLowerCase()}"' in box
    post = _body("    async postQuickEntry(accountId) {", "\n    },")
    assert (
        "data.class_id = classBox.value ? parseInt(classBox.value, 10) : null;" in post
    )
    assert "ClassWarn.okValue(data.class_id)" in post


def test_the_transfer_dialog_has_a_class_and_sends_it():
    form = _body("    async showTransferForm(fromId = null) {", "\n    },")
    assert "const classGroup = await classFormGroupHtml();" in form
    assert "${classGroup}" in form
    save = _body("    async saveTransfer(e) {", "\n    },")
    assert "class_id: classIdFromForm(form)," in save
    assert "ClassWarn.ok(form)" in save


def test_the_job_list_disables_what_it_hides_and_the_view_loads_cost_codes():
    # NEW-198: a native select on macOS shows hidden options; disabled ones
    # cannot be picked. NEW-196: View decides its Cost Code column by
    # CostCodes.any(), so it loads the list itself.
    utils = (ROOT / "app/static/js/utils.js").read_text(encoding="utf-8")
    sync = utils[utils.index("sync(select) {") :][:900]
    assert "opt.hidden = !mine;" in sync and "opt.disabled = !mine;" in sync
    journal = (ROOT / "app/static/js/journal.js").read_text(encoding="utf-8")
    view = journal[journal.index("async view(id) {") :][:600]
    assert "CostCodes.load()" in view
