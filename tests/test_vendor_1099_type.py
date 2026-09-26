"""The vendor form's 1099 Type is off until "1099 Vendor" is Yes
(explore 2.17.3 integration, I13).

The server clears a 1099 type on a vendor that is not a 1099 vendor, but
the form kept the select live under "1099 Vendor: No", so a type could be
picked and saved, then silently dropped. It renders disabled for a vendor
that is not a 1099 vendor (and for a new one), turns on with Yes, and turns
off and empties with No; a disabled select is not sent, so the save sends
no type.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_1099_type_follows_the_1099_vendor_answer():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "vendor_1099_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert " disabled " in got["new"]
    assert " disabled " in got["not1099"]
    assert "disabled" not in got["is1099"]
    assert 'onchange="VendorsPage.toggle1099Type(this)"' in got["answer"]
    assert got["toggle"] == [
        {"answer": "Yes", "disabled": False, "value": ""},
        {"answer": "No", "disabled": True, "value": ""},
    ]


def test_a_vendor_saved_as_not_1099_keeps_no_type(client):
    v = client.post(
        "/api/vendors",
        json={"name": "Paper Co", "is_1099_vendor": False, "vendor_1099_type": None},
    )
    assert v.status_code == 201, v.text
    assert v.json()["vendor_1099_type"] is None
