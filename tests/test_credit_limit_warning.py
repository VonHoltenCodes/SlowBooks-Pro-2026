"""A warning before an invoice takes a customer past their credit limit
(2.17.3 exploratory, macbase1 suggestion S-b).

Salt & Pine (limit $500) was given a $512.13 invoice, converted from an
estimate, without a word. Saving an invoice, or converting an estimate,
that would take the customer's open balance past their limit now asks
first; the user can still go ahead. The balance is read from the
customer's open invoices, so it does not depend on the stored customer
balance (which nothing kept up to date, W-H3).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_warning_counts_what_the_customer_already_owes():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "credit_limit_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["atLimit"] is True and got["askedAtLimit"] == 0
    assert got["pastNo"] is False
    assert got["message"] == (
        "Salt & Pine Catering Co.'s credit limit is $500.00. With this invoice "
        "they would owe $512.13. Save it anyway?"
    )
    assert got["pastYes"] is True
    assert got["editing"] is True and got["askedEditing"] == 0
    assert got["noLimit"] is True


def test_saving_and_converting_ask_before_they_post():
    inv = (JS / "invoices.js").read_text(encoding="utf-8")
    save = inv[inv.index("    async save(e, id) {") :]
    save = save[: save.index("\n    },")]
    check = save.index("await InvoicesPage.creditLimitOk(customer, owed, id)")
    # sent through SalesLines.sendAllowingZero (a $0.00 invoice asks too)
    assert check < save.index("API.post('/invoices', body)")
    assert check < save.index("API.put(`/invoices/${id}`, body)")

    est = (JS / "estimates.js").read_text(encoding="utf-8")
    convert = est[est.index("    async convert(id) {") :]
    convert = convert[: convert.index("\n    },")]
    assert convert.index("InvoicesPage.creditLimitOk(customer") < convert.index(
        "API.post(`/estimates/${id}/convert`"
    )
