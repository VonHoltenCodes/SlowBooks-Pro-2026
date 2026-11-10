"""The macOS build's retries (#634), tested where every CI job runs.

tests/test_macos_release.py is skipped on Windows as a whole (its tests lean
on macOS packaging tools, #121). These two need no tool: one drives
release.py's hdiutil retry through a stubbed runner, the other reads the
workflow file. Kept apart so Windows runs them too, and so they do not
count against the Windows job's skip budget."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MACOS_DIR = Path(__file__).resolve().parent.parent / "packaging" / "macos"
sys.path.insert(0, str(MACOS_DIR))
SPEC = importlib.util.spec_from_file_location(
    "slowbooks_macos_release_retries", MACOS_DIR / "release.py"
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
sys.path.pop(0)


def test_hdiutil_create_retries_only_on_resource_busy(monkeypatch):
    """GitHub's macOS runners fail hdiutil create with "Resource busy" now and
    then right after a large copy (run 40800371868). The candidate DMG gets
    a few tries on that error alone; any other failure raises at once."""
    calls = []
    slept = []
    outcomes = ["busy", "busy", "ok"]

    def fake_run(*args, check=True):
        calls.append(args)
        if outcomes.pop(0) == "busy":
            raise subprocess.CalledProcessError(
                1, args, output="", stderr="hdiutil: create failed - Resource busy\n"
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "_sleep", slept.append)
    release._run_hdiutil_create("-volname", "SlowBooks Pro", "-ov", "x.dmg")
    assert len(calls) == 3 and all(c[:2] == ("hdiutil", "create") for c in calls)
    assert slept == [5, 10]

    outcomes[:] = ["other"]
    calls.clear()
    slept.clear()

    def fake_run_other(*args, check=True):
        calls.append(args)
        raise subprocess.CalledProcessError(
            1, args, output="", stderr="no space left\n"
        )

    monkeypatch.setattr(release, "_run", fake_run_other)
    with pytest.raises(subprocess.CalledProcessError):
        release._run_hdiutil_create("-volname", "SlowBooks Pro", "-ov", "x.dmg")
    assert len(calls) == 1 and slept == []

    outcomes[:] = ["busy"] * release.HDIUTIL_ATTEMPTS
    calls.clear()
    slept.clear()
    monkeypatch.setattr(release, "_run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        release._run_hdiutil_create("-volname", "SlowBooks Pro", "-ov", "x.dmg")
    assert len(calls) == release.HDIUTIL_ATTEMPTS
    assert len(slept) == release.HDIUTIL_ATTEMPTS - 1


def test_hdiutil_verify_is_retried_and_every_try_is_recorded(monkeypatch, tmp_path):
    """hdiutil verify failed once on the runner right after codesign wrote the
    candidate DMG, "Resource temporarily unavailable" (run 42697156611; #758).
    It gets the same tries as create, each one kept in the evidence file;
    any other failure raises at once, recorded once."""
    calls = []
    slept = []
    outcomes = ["unavailable", "busy", "ok"]
    unavailable = (
        'hdiutil: verify: unable to recognize "x.dmg" as a disk image. '
        "(Resource temporarily unavailable)\n"
    )

    def fake_run(*args, check=True):
        calls.append(args)
        outcome = outcomes.pop(0)
        if outcome == "ok":
            return subprocess.CompletedProcess(args, 0, stdout="verified\n", stderr="")
        said = unavailable if outcome == "unavailable" else "Resource busy\n"
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=said)

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "_sleep", slept.append)
    report = tmp_path / "candidate-dmg-verification.txt"
    result = release._record_hdiutil(report, "verify", "x.dmg")
    assert result.returncode == 0
    assert calls == [("hdiutil", "verify", "x.dmg")] * 3
    assert slept == [5, 10]
    evidence = report.read_text(encoding="utf-8")
    assert evidence.count("$ hdiutil verify x.dmg\n") == 3
    assert evidence.count("exit_code=1\n") == 2 and evidence.count("exit_code=0\n") == 1
    assert "Resource temporarily unavailable" in evidence

    calls.clear()
    slept.clear()

    def fake_run_other(*args, check=True):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="hdiutil: verify: checksum mismatch\n"
        )

    monkeypatch.setattr(release, "_run", fake_run_other)
    other = tmp_path / "final-dmg-verification.txt"
    with pytest.raises(subprocess.CalledProcessError):
        release._record_hdiutil(other, "verify", "y.dmg")
    assert len(calls) == 1 and slept == []
    assert other.read_text(encoding="utf-8").count("$ hdiutil verify y.dmg\n") == 1

    # the last try's failure is the one raised, after every attempt was kept
    outcomes[:] = ["unavailable"] * release.HDIUTIL_ATTEMPTS
    calls.clear()
    slept.clear()
    monkeypatch.setattr(release, "_run", fake_run)
    spent = tmp_path / "spent.txt"
    with pytest.raises(subprocess.CalledProcessError):
        release._record_hdiutil(spent, "verify", "z.dmg")
    assert len(calls) == release.HDIUTIL_ATTEMPTS
    assert len(slept) == release.HDIUTIL_ATTEMPTS - 1
    assert (
        spent.read_text(encoding="utf-8").count("$ hdiutil verify z.dmg\n")
        == release.HDIUTIL_ATTEMPTS
    )


def test_hdiutil_create_also_retries_on_temporarily_unavailable(monkeypatch):
    """The create retry reads both runner messages (#758)."""
    calls = []
    slept = []
    outcomes = ["unavailable", "ok"]

    def fake_run(*args, check=True):
        calls.append(args)
        if outcomes.pop(0) == "unavailable":
            raise subprocess.CalledProcessError(
                1,
                args,
                output="",
                stderr="hdiutil: create failed - Resource temporarily unavailable\n",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(release, "_run", fake_run)
    monkeypatch.setattr(release, "_sleep", slept.append)
    release._run_hdiutil_create("-volname", "SlowBooks Pro", "-ov", "x.dmg")
    assert len(calls) == 2 and slept == [5]


def test_the_build_job_retries_the_unsigned_dmg():
    """The same flake in the build job's packaging step: sync first, then up
    to five tries of hdiutil create before the step fails."""
    import yaml

    workflow = yaml.safe_load(
        (MACOS_DIR.parent.parent / ".github/workflows/macos.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["build"]["steps"]
    step = next(s for s in steps if "unsigned transport" in s.get("name", ""))
    run = step["run"]
    create = run.index("hdiutil create -volname")  # the command, not the comment
    assert run.index("sync\n") < create
    assert "for attempt in 1 2 3 4 5; do" in run
    assert run.index("for attempt") < create
    assert "hdiutil create failed 5 times" in run
    # and the verify of that image gets the same tries (#758)
    verify = run.index("if hdiutil verify")
    assert run.index("hdiutil create failed 5 times") < verify
    assert "hdiutil verify failed 5 times" in run
