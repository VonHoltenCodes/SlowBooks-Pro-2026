"""The written restore steps (#1219). Going back to an older version must not
reuse a restore block that ends with `docker compose ... start slowbooks`:
`git checkout` leaves the newer image in place, so `start` boots the newer
version, which upgrades the restored data again. Every restore checks the
file with `gunzip -t` before anything is dropped, because a cut-short file
piped into `psql --single-transaction` can still commit a partial restore.
The bash-only `SLOWBOOKS_COMMIT=$(...)` line also says what to run in
PowerShell."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _bash_blocks(text):
    return re.findall(r"```bash\n(.*?)```", text, flags=re.S)


def _restore_blocks(text):
    return [b for b in _bash_blocks(text) if "--single-transaction" in b]


def test_every_restore_checks_the_file_before_the_drop():
    for rel in ("docs/cloud-hosting.md", "docs/operations.md"):
        blocks = _restore_blocks(_read(rel))
        assert blocks, rel
        for block in blocks:
            assert "gunzip -t " in block, (rel, block)
            assert block.index("gunzip -t ") < block.index("dropdb"), (rel, block)
            assert "-v ON_ERROR_STOP=1" in block, (rel, block)


def test_going_back_block_does_not_start_the_newer_image():
    text = _read("docs/cloud-hosting.md")
    section = text[text.index("**Going back to an older version.**") :]
    block = _bash_blocks(section)[0]
    assert "git checkout v2.31.14" in block
    assert block.index("git checkout") < block.index("dropdb")
    assert "createdb" in block and "--single-transaction" in block
    assert " start slowbooks" not in block
    assert "up -d --build" in block
    assert "There is no `start` line here" in section


def test_operations_going_back_says_to_leave_out_the_start_line():
    text = _read("docs/operations.md")
    section = text[text.index("**Going back to an older version.**") :]
    section = section[: section.index("`pg_dump` not found?")]
    assert "leave out the last line of the restore block" in section
    assert "upgrades the restored data" in section


def test_going_back_does_not_say_an_older_version_never_starts():
    for rel in ("docs/cloud-hosting.md", "docs/operations.md"):
        flat = " ".join(_read(rel).split())
        assert "older version cannot start on a database" not in flat, rel
        assert "older version usually cannot start on a database" in flat, rel


def test_commit_build_line_says_what_to_run_in_powershell():
    for rel in ("INSTALL.md", "docs/features.md"):
        flat = " ".join(_read(rel).replace("> ", "").split())
        assert (
            "SLOWBOOKS_COMMIT=$(git rev-parse HEAD) docker compose up --build" in flat
        )
        assert "$env:SLOWBOOKS_COMMIT = git rev-parse HEAD" in flat, rel
