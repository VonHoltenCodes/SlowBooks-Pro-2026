"""startup_progress.clear() on Windows (#813): deleting a file another
process has open fails there, and the OSError was swallowed, so the note
could be left behind for the launcher to read. The delete is tried again;
if it is still refused, the note is emptied in place, so no reader shows a
job that is over."""

import json
from pathlib import Path

from app.services import startup_progress as sp


def _note(tmp_path) -> Path:
    path = tmp_path / sp.FILENAME
    path.write_text(json.dumps({"label": "Catching up", "done": 1, "total": 9}))
    return path


def test_a_delete_refused_a_while_is_tried_again(tmp_path, monkeypatch):
    path = _note(tmp_path)
    real = Path.unlink
    refusals = iter([PermissionError(13, "in use"), PermissionError(13, "in use")])
    pauses = []

    def unlink(self, *a, **k):
        err = next(refusals, None)
        if err:
            raise err
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    sp.clear(path, sleep=pauses.append)
    assert not path.exists()
    assert pauses == [sp.CLEAR_PAUSE, sp.CLEAR_PAUSE]


def test_a_delete_refused_every_time_empties_the_note(tmp_path, monkeypatch):
    path = _note(tmp_path)

    def unlink(self, *a, **k):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(Path, "unlink", unlink)
    pauses = []
    sp.clear(path, sleep=pauses.append)
    assert len(pauses) == sp.CLEAR_TRIES - 1
    state = sp.read(path)
    assert sp.describe(state) == "" and sp.reason(state) == ""


def test_no_file_is_nothing_to_do(tmp_path):
    sp.clear(
        tmp_path / sp.FILENAME, sleep=lambda s: (_ for _ in ()).throw(AssertionError)
    )
