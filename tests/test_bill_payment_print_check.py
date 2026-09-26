"""A bill payment's check prints from the bill's view (explore 2.17.3
integration, I24).

Print Check sat only on payments RECEIVED from customers, where it printed
a check payable to the customer; putting that right (W-M9) left no Print
Check anywhere, while GET /api/checks/print?bill_payment_id= still worked.
The bill's view lists its payments with Void; a payment made by check now
has Print Check beside it. ACH, cash and card payments have no check, and a
void payment offers nothing.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _print(pid):
    return (
        '<button class="btn btn-sm btn-secondary" '
        f"onclick=\"window.open('/api/checks/print?bill_payment_id={pid}','_blank')\">"
        "Print Check</button>"
    )


def _void(pid):
    return (
        '<button class="btn btn-sm btn-danger" '
        f'onclick="BillsPage.voidBillPayment({pid}, 3)">Void</button>'
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_check_payment_on_the_bills_view_prints_its_check():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "bill_payments_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    rows = json.loads(out.stdout)
    assert rows["check"] == [_print(21), _void(21)]
    assert rows["check_without_number"] == [_print(22), _void(22)]
    # imported with no method but a check number: a check
    assert rows["imported_without_method"] == [_print(23), _void(23)]
    assert rows["ach"] == [_void(24)]
    assert rows["credit_card"] == [_void(25)]
    assert rows["void_check"] == []


def test_the_check_route_is_no_longer_listed_as_having_no_caller():
    from tests.test_wiring import _INTENTIONAL_BACKEND_ONLY

    assert ("GET", "/api/checks/print") not in _INTENTIONAL_BACKEND_ONLY
