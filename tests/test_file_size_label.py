"""An attachment's size reads the way a person says it (2.17.3 exploratory
test, W-L7): every size was shown in KB to one decimal, so an 18-byte
attachment read "0.0 KB"."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _format_file_size(values):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    utils = (JS / "utils.js").read_text(encoding="utf-8")
    fn = re.search(r"function formatFileSize\(bytes\) \{.*?\n\}", utils, re.S).group(0)
    script = (
        f"{fn}\nconsole.log(JSON.stringify({json.dumps(values)}.map(formatFileSize)));"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True
    ).stdout
    return json.loads(out)


def test_small_files_read_in_bytes():
    assert _format_file_size([18, 1, 0, 1023, 1024, 1536, 5 * 1024 * 1024 + 1]) == [
        "18 bytes",
        "1 byte",
        "0 bytes",
        "1023 bytes",
        "1.0 KB",
        "1.5 KB",
        "5.0 MB",
    ]


@pytest.mark.parametrize(
    "page", ["bills.js", "expenses.js", "invoices.js", "employees.js", "iif.js"]
)
def test_every_file_list_uses_the_one_label(page):
    text = (JS / page).read_text(encoding="utf-8")
    assert "formatFileSize(" in text, page
    assert (
        "/1024).toFixed(1)} KB" not in text and "/ 1024).toFixed(1) + ' KB'" not in text
    )
