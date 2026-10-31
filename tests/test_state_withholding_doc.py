"""docs/state-withholding.md is written from tables.py by
scripts/gen_state_withholding_doc.py; the committed file must match."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_state_withholding_doc_matches_tables():
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "gen_state_withholding_doc.py"),
            "--check",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
