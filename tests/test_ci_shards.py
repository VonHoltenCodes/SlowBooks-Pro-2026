"""CI's shards run every test file exactly once (#1095).

The workflow splits the suite by file across parallel jobs
(scripts/ci_shards.py); a split that dropped a file would leave its tests
unrun with every check green. The final job of each platform reads back the
lists the shards really ran; these tests hold the split itself to the same
rule for any shard count, and the workflow to reading its count from the
matrix.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_shards  # noqa: E402

WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_every_test_file_is_in_exactly_one_group():
    every = ci_shards.group_files("all")
    core = ci_shards.group_files("core")
    browser = ci_shards.group_files("browser")
    assert sorted(core + browser) == every
    assert not set(core) & set(browser)
    assert "tests/test_ci_shards.py" in core


def test_the_browser_group_is_every_module_that_imports_playwright():
    importers = sorted(
        f
        for f in ci_shards.test_files()
        if re.search(
            r"importorskip\(\s*[\"']playwright",
            (ROOT / f).read_text(encoding="utf-8"),
        )
    )
    assert ci_shards.group_files("browser") == importers
    assert len(importers) > 50
    # a module that only mentions playwright in prose is not one
    assert "tests/test_keyboard_paths.py" in ci_shards.group_files("core")


@pytest.mark.parametrize("group", ci_shards.GROUPS)
@pytest.mark.parametrize("platform", ci_shards.PLATFORMS)
@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7])
def test_a_split_places_every_file_once(group, platform, n):
    shards, _ = ci_shards.plan(group, platform, n)
    placed = [f for s in shards for f in s]
    assert sorted(placed) == ci_shards.group_files(group)
    assert len(placed) == len(set(placed))
    assert len(shards) == n


def test_new_files_are_dealt_round_robin():
    files = [f"tests/test_new_{i}.py" for i in range(7)]
    shards, _ = ci_shards.split(files, 3, {})
    assert sorted(len(s) for s in shards) == [2, 2, 3]
    # measured files balance on their seconds, the new ones still spread
    durations = {"tests/test_big.py": 100.0, "tests/test_small.py": 1.0}
    shards, loads = ci_shards.split(files + list(durations), 3, durations)
    assert sorted(loads) == [0.0, 1.0, 100.0]
    assert sorted(f for s in shards for f in s) == sorted(files + list(durations))


def test_both_platforms_have_measurements():
    # a file deleted since the last refresh is ignored by the split, so a
    # stale entry is harmless; an empty table would deal every file blind
    for platform in ci_shards.PLATFORMS:
        assert len(ci_shards.load_durations(platform)) > 100, platform


def test_the_workflow_reads_the_shard_count_from_its_matrix():
    # each list step asks for its shard of strategy.job-total, so a matrix
    # made longer or shorter can never leave a slice of the files unrun
    lists = re.findall(
        r"ci_shards\.py list --group (\w+)[^\n]*\n?[^\n]*?--of (\S+ \S+ \S+)", WORKFLOW
    )
    assert sorted(lists) == [
        ("all", "${{ strategy.job-total }}"),
        ("browser", "${{ strategy.job-total }}"),
        ("core", "${{ strategy.job-total }}"),
    ]
    for group in ("core", "browser", "all"):
        assert f"ci_shards.py verify --group {group} " in WORKFLOW, group
