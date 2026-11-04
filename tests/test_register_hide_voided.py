"""The register's Hide voided box (#694).

A register with a long history of voids is hard to read: each void is two
struck-through rows, the entry and the reversal that voided it. Hide voided
(off unless chosen, kept in this browser by sign-in) leaves both out of the
rows drawn and says how many it left out. The listing is the service's as
before, every line in it, so the ledger, reconciliation and the reports are
untouched; the rows shown keep a running balance that adds up.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_listing_marks_a_void_and_its_reversal_with_their_amounts(
    client, seed_accounts
):
    # what the box reads: `voided` on the entry and on its reversal, and
    # each line's signed `amount` (the balance shown is taken back by it)
    checking = seed_accounts["1000"]
    body = {
        "account_id": checking.id,
        "date": "2026-09-14",
        "amount": "-250.00",
        "category_account_id": seed_accounts["6500"].id,
        "payee": "Main Street Properties",
        "check_number": "1072",
    }
    r = client.post("/api/banking/transactions", json=body)
    assert r.status_code == 201, r.text
    voided_id = r.json()["id"]
    r = client.post(
        "/api/banking/transactions",
        json={**body, "amount": "-40.00", "check_number": "1073"},
    )
    assert r.status_code == 201, r.text
    kept_id = r.json()["id"]
    r = client.post(f"/api/banking/entries/{voided_id}/void")
    assert r.status_code == 200, r.text

    reg = client.get(f"/api/banking/check-register?account_id={checking.id}").json()
    rows = reg["entries"]
    assert len(rows) == 3
    void_rows = [e for e in rows if e["voided"]]
    assert {e["source_type"] for e in void_rows} == {"bank_entry", "bank_entry_void"}
    assert sum(e["amount"] for e in void_rows) == 0
    kept = next(e for e in rows if e["transaction_id"] == kept_id)
    assert kept["voided"] is False and kept["amount"] == -40
    # the account's balance and the last row's are the ledger's, every line in
    assert reg["balance"] == -40 and rows[-1]["balance"] == -40


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_register_hides_voided_rows_only_when_chosen():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "register_hide_voided_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    every = [
        [71, "$1000.00"],
        [72, "$750.00"],
        [73, "$650.00"],
        [74, "$900.00"],
        [75, "$860.00"],
    ]
    # off unless chosen: loaded alone (no Store), with a null localStorage,
    # and for another sign-in on the same computer, every row as before
    for case in ("no_store", "null_storage", "other_user"):
        assert got[case]["rows"] == every, case
        assert got[case]["checked"] is False, case
        assert got[case]["label"] == "Hide voided", case
        assert got[case]["note"] is None, case
    # chosen: the void and its reversal are left out and counted; the row
    # between them reads as the rows shown add up, the rows after it keep
    # the ledger's balance
    hidden = got["hidden"]
    assert hidden["checked"] is True
    assert hidden["rows"] == [[71, "$1000.00"], [73, "$900.00"], [75, "$860.00"]]
    assert hidden["note"] == "2 voided entries hidden"
