"""Progress of the one-time jobs the server runs at start (#693).

The lifespan in app/main.py catches an older company file up before the
server answers /health: the stored balances (#490), the cash-basis lines of
its payments (#483). On a big file that takes minutes, and the desktop
launcher used to give up after two and stop the server halfway. Each job
now writes what it is doing to a small JSON file in the data folder, next
to launcher.log, and the launcher keeps waiting for as long as that file
keeps changing, showing the text in the company picker.

The file holds the job's label, "done" and "total" where there is a count,
the server's pid and the time of the write. Nothing is written when the
data folder is not set (a plain uvicorn run, the tests).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILENAME = "startup-progress.json"

# Writes at most this often while a count moves; a new label or the last
# item is always written.
MIN_INTERVAL = 1.0

_last = {"label": None, "at": 0.0}


def progress_path() -> Path | None:
    data_dir = os.environ.get("SLOWBOOKS_DATA_DIR")
    return Path(data_dir) / FILENAME if data_dir else None


def describe(state: dict | None) -> str:
    """The line a person reads: 'Catching up cash-basis lines, 1,200 of
    8,580'."""
    if not state or not state.get("label"):
        return ""
    label = str(state["label"])
    done, total = state.get("done"), state.get("total")
    if isinstance(done, int) and isinstance(total, int) and total:
        return f"{label}, {done:,} of {total:,}"
    return label


def report(label: str, done: int | None = None, total: int | None = None) -> None:
    """Record what the start-up is doing. Never raises: a progress note is
    never a reason not to boot."""
    path = progress_path()
    if path is None:
        return
    now = time.monotonic()
    last_item = done is not None and total is not None and done >= total
    if label == _last["label"] and not last_item and now - _last["at"] < MIN_INTERVAL:
        return
    _last.update(label=label, at=now)
    state = {
        "label": label,
        "done": done,
        "total": total,
        "pid": os.getpid(),
        "updated": time.time(),
    }
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def clear(path: Path | None = None) -> None:
    """Remove the file: the jobs are done, or a new server is starting."""
    path = path or progress_path()
    _last.update(label=None, at=0.0)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def read(path: Path) -> dict | None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None
