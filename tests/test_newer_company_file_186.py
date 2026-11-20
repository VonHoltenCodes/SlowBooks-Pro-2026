# ruff: noqa: F811  (pytest fixtures imported from another module)
"""A company file last opened by a newer SlowBooks Pro is refused in a
sentence, and left as it is (W-186).

Opened by an older build, such a file answered with Alembic's own "Can't
locate revision identified by '…'" in the company list (Windows), or a
generic "hit an unexpected error and could not start" (Mac). Its revision
is now read as the file lies, before anything migrates it: the picker's
list notes it, opening it says the sentence, the launcher records it for
its wait, the migrations refuse it before Alembic looks for the revision,
and the server's start-up guard says it rather than calling the file
"behind" this build. The revision here is fabricated: one no build has."""

import sqlalchemy as sa
import pytest
from alembic import command

import desktop_launcher as dl
from app.services import migration_floor, startup_progress
from app.services.migration_floor import (
    NEWER_VERSION_MESSAGE,
    CompanyFileRefused,
    FromNewerVersion,
)
from tests.test_migration_floor_769 import (  # noqa: F401  (the fixture)
    _cfg,
    _current,
    _manifest,
    _stamped,
    _url,
    data_dir,
)

FUTURE = "fffff0000001"
SENTENCE = (
    "This company file was last opened by a newer version of SlowBooks Pro, "
    "and this version cannot open it. Update SlowBooks Pro to open it; the "
    "file was left as it is."
)


def test_the_sentence():
    assert NEWER_VERSION_MESSAGE == SENTENCE
    assert migration_floor.is_from_newer([FUTURE])
    assert not migration_floor.is_from_newer([])
    assert migration_floor.refusal([FUTURE]) == SENTENCE
    assert issubclass(FromNewerVersion, CompanyFileRefused)


def test_the_migrations_refuse_it_before_alembic_and_leave_it_byte_identical(
    tmp_path,
):
    db = _stamped(tmp_path / "newer.db", FUTURE)
    before = db.read_bytes()
    with pytest.raises(FromNewerVersion) as refused:
        command.upgrade(_cfg(_url(db)), "head")
    assert str(refused.value) == SENTENCE
    assert "Can't locate revision" not in str(refused.value)
    assert db.read_bytes() == before
    assert _current(db) == FUTURE


def test_the_start_up_guard_says_the_sentence_and_records_it(tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setenv("SLOWBOOKS_DATA_DIR", str(tmp_path))
    db = _stamped(tmp_path / "newer.db", FUTURE)
    before = db.read_bytes()
    engine = sa.create_engine(_url(db))
    monkeypatch.setattr(main, "engine", engine)
    try:
        with pytest.raises(FromNewerVersion) as refused:
            main._refuse_a_database_behind_head()
    finally:
        engine.dispose()
    assert str(refused.value) == SENTENCE
    note = startup_progress.read(tmp_path / startup_progress.FILENAME)
    assert startup_progress.reason(note) == SENTENCE
    assert db.read_bytes() == before


def test_the_launcher_refuses_it_before_migrating_it(data_dir, monkeypatch):
    newer = _stamped(data_dir / "companies" / "newer.db", FUTURE)
    before = newer.read_bytes()

    def must_not(*a, **k):
        raise AssertionError("a newer build's file reached the migrations")

    monkeypatch.setattr(dl, "migrate", must_not)
    monkeypatch.setattr(dl, "start_server", must_not)
    monkeypatch.setattr(dl, "set_env_value", must_not)
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    with pytest.raises(FromNewerVersion) as refused:
        dl.launch_company("newer.db", 3999)
    assert str(refused.value) == SENTENCE
    assert newer.read_bytes() == before
    note = startup_progress.read(dl.startup_progress_path())
    assert startup_progress.reason(note) == SENTENCE


def test_the_picker_lists_it_with_the_sentence_and_opening_it_says_it(
    data_dir, monkeypatch
):
    from app.services import company_service

    _stamped(data_dir / "companies" / "newer.db", FUTURE)
    _manifest(data_dir, "newer.db")
    listed = {c["file"]: c for c in company_service.manifest_list_companies()}
    assert listed["newer.db"]["note"] == SENTENCE
    monkeypatch.setattr(dl, "_server_already_running", lambda port: False)
    api = dl.PickerApi(3999)
    monkeypatch.setattr(api, "_prepare_server", lambda *a, **k: None)
    assert api.open_company("newer.db") == {"success": False, "error": SENTENCE}


def test_server_edition_leaves_it_unserved(data_dir, monkeypatch, capsys):
    newer = _stamped(data_dir / "companies" / "newer.db", FUTURE)
    before = newer.read_bytes()
    _manifest(data_dir, "started.db", "newer.db")

    def must_not(*a, **k):
        raise AssertionError("a newer build's file reached the migrations")

    monkeypatch.setattr(dl, "migrate", must_not)
    skipped = dl._migrate_other_companies("started.db")
    assert skipped == ["newer.db"]
    assert SENTENCE in capsys.readouterr().out
    assert newer.read_bytes() == before
