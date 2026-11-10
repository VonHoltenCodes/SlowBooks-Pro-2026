"""The Apply Credit dialogs check every row before sending (2.31.11 gate,
NEW-217): a credit memo's Apply sent one request per row and stopped at
the first refusal, so the rows above it were applied while the dialog
still showed the old remaining, and a second Apply applied them again.
Driven through tests/js/credit_apply_dialog_probe.js with a fake DOM."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_apply_credit_dialogs_check_every_row_and_say_what_was_applied():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "credit_apply_dialog_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # a half-cent row: nothing is sent, the row is named, the dialog stays
    assert got["halfCent"]["posts"] == 0 and got["halfCent"]["open"] is True
    assert got["halfCent"]["toast"] == [
        "Invoice 1163: an amount is in whole cents; 0.005 isn't. Nothing was applied.",
        "error",
    ]
    # more than the credit remaining: nothing is sent
    assert got["overRemaining"]["posts"] == 0 and got["overRemaining"]["open"] is True
    assert got["overRemaining"]["toast"] == [
        "$1.10 is more than the $1.00 of credit remaining. Nothing was applied.",
        "error",
    ]
    # the server refuses the second row: the first was applied, the second
    # sent once and refused, the sentence says so, and the dialog was opened
    # again on the memo as it is now
    mid = got["refusedMidway"]
    assert mid["posts"] == [
        {"invoice_id": 1164, "amount": 0.5},
        {"invoice_id": 1163, "amount": 0.01},
    ]
    assert mid["toast"] == [
        "Invoice 1163: Amount exceeds credit balance. $0.50 to invoice 1164 was "
        "applied before it; the dialog shows what remains now.",
        "error",
    ]
    assert mid["reopened"] == 2 and mid["open"] is True and mid["navigated"] == []
    # a clean run posts both rows, closes and goes back to the list
    clean = got["clean"]
    assert clean["posts"] == [
        {"invoice_id": 1164, "amount": 0.5},
        {"invoice_id": 1163, "amount": 0.01},
    ]
    assert clean["toast"] == ["Credit applied", "ok"]
    assert clean["open"] is False and clean["navigated"] == ["#/credit-memos"]
    # the vendor credit's dialog, the same way
    assert got["vcHalfCent"]["posts"] == 0 and got["vcHalfCent"]["open"] is True
    assert got["vcHalfCent"]["toast"][0].startswith(
        "Bill B-52: an amount is in whole cents; 0.005 isn't."
    )
    vc = got["vcRefusedMidway"]
    assert vc["posts"] == [
        {"bill_id": 51, "amount": 0.5},
        {"bill_id": 52, "amount": 0.01},
    ]
    assert vc["toast"][0].startswith(
        "Bill B-52: Amount exceeds credit balance. $0.50 to bill B-51 was applied before it"
    )
    assert vc["reopened"] == 2 and vc["open"] is True
