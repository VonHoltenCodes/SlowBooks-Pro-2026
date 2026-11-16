"""The bank feed's To review Category list shows the bank and card accounts
under a "Transfer to another account" heading (#948, Discussion #947), and the
bank-feeds guide says how a transfer is posted from the feed."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_review_category_list_groups_transfers():
    js = (ROOT / "app/static/js/banking.js").read_text()
    fill = js.split("_fillReviewCats() {")[1].split("async addLine(")[0]
    assert '<optgroup label="Categories">' in fill
    assert '<optgroup label="Transfer to another account">' in fill
    assert "usable.filter(a => a.bank_kind)" in fill


def test_the_guide_says_how_a_transfer_is_posted_from_the_feed():
    doc = (ROOT / "docs/setup-bank-feeds.md").read_text()
    assert "A transfer between two of your own accounts" in doc
    assert (
        "Transfer to another\n  account" in doc or "Transfer to another account" in doc
    )
    assert "offers\n  **Match**" in doc or "offers **Match**" in doc
