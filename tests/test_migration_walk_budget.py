"""A test that runs alembic's command line in a subprocess has a time budget
of its own, and its subprocesses run out of time before the test does.

The Windows job gives every test 120 s, and on Windows pytest-timeout can
only end the whole worker: under xdist a slow walk of the migration chain was
reported as "worker crashed", with no word about time, and the alembic child
was left running with the SQLite file open. The rule lives in
tests/conftest.py (`alembic_cli`); these tests hold it in place.
"""

import re
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import (
    ALEMBIC_WALK_SUBPROCESS_BUDGET,
    ALEMBIC_WALK_TEST_BUDGET,
)

TESTS = Path(__file__).resolve().parent

# `[sys.executable, "-m", "alembic", ...]` however it is quoted or wrapped
ALEMBIC_COMMAND_LINE = re.compile(r"""["']-m["']\s*,\s*["']alembic["']""")


def test_the_subprocesses_run_out_of_time_before_the_test_does():
    # the margin is what lets subprocess.run kill the child, collect its
    # output and raise while the test is still inside its own budget
    assert ALEMBIC_WALK_SUBPROCESS_BUDGET + 30 <= ALEMBIC_WALK_TEST_BUDGET


def test_only_the_fixture_runs_alembics_command_line():
    """A test that starts `python -m alembic` by hand has neither budget."""
    by_hand = [
        path.name
        for path in sorted(TESTS.glob("test_*.py"))
        if path.name != Path(__file__).name
        and ALEMBIC_COMMAND_LINE.search(path.read_text(encoding="utf-8"))
    ]
    assert by_hand == [], (
        "run alembic through the alembic_cli fixture (tests/conftest.py), "
        f"which carries the time budget: {by_hand}"
    )


def test_a_test_that_uses_the_fixture_has_the_budget(request, alembic_cli):
    marker = request.node.get_closest_marker("timeout")
    assert marker is not None and marker.args == (ALEMBIC_WALK_TEST_BUDGET,)


@pytest.mark.timeout(45)
def test_a_budget_of_its_own_is_left_as_written(request, alembic_cli):
    assert request.node.get_closest_marker("timeout").args == (45,)


def test_a_walk_that_runs_out_of_time_fails_as_a_test_and_leaves_no_child(
    tmp_path, alembic_cli
):
    db = tmp_path / "mig.db"
    run = alembic_cli(db, budget=0.3)  # no walk of the chain fits in this
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run("upgrade", "head")
    # the next call finds the budget spent and starts nothing
    with pytest.raises(subprocess.TimeoutExpired):
        run("downgrade", "-1")
    assert time.monotonic() - started < 60
    # the child was killed and waited for: nothing holds the file open, which
    # on Windows is what lets it be removed
    for leftover in tmp_path.glob("mig.db*"):
        leftover.unlink()
    assert list(tmp_path.glob("mig.db*")) == []
