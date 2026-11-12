"""Which build this is: the release version and, when the build knows it, the
commit it was built from (#816).

Two builds of one release are told apart by the commit, so every place that
names the version to a person or a support email names it the same way:
``<version>+<sha>``, the 12-character short SHA the macOS bundle's
CFBundleVersion carries. Where the commit is not known (a plain source run),
the version stands alone.

The commit comes from, in order:

1. ``SLOWBOOKS_COMMIT`` in the environment (a Docker image or a source run can
   set it);
2. the ``BUILD_SHA`` file the packaged builds bake next to this module
   (``packaging/windows/SlowBooksPro.spec`` and
   ``packaging/macos/SlowBooksPro-mac.spec`` write it from ``APP_BUILD_SHA``,
   or the checkout's ``git rev-parse HEAD`` on a local build).

Stdlib only and never shells out: the desktop launcher's fatal-error dialog
reads it before anything else can be trusted to import, and a frozen app has
no git.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from app import __version__

BUILD_FILE_NAME = "BUILD_SHA"
SHORT_SHA_LEN = 12

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def normalize_sha(value) -> str | None:
    """A commit as the build shows it: lower-case hex, cut to 12 characters.
    Anything that is not a commit (blank, ``unknown``, a stray word) is None."""
    sha = str(value or "").strip().lower()
    if not _SHA_RE.match(sha):
        return None
    return sha[:SHORT_SHA_LEN]


def _build_file_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent / BUILD_FILE_NAME
    out = [here]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        out.append(Path(bundle) / "app" / BUILD_FILE_NAME)
    return out


def _baked_sha() -> str | None:
    for path in _build_file_candidates():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        sha = normalize_sha(text)
        if sha:
            return sha
    return None


def build_sha() -> str | None:
    """The 12-character commit this build was made from, or None."""
    env = normalize_sha(os.environ.get("SLOWBOOKS_COMMIT"))
    if env:
        return env
    return _baked_sha()


def version_string(version: str | None = None, sha: str | None = None) -> str:
    """``2.32.0+0123456789ab`` when the commit is known, else ``2.32.0``.
    With no arguments, this build's own version and commit."""
    version = version or __version__
    if sha is None:
        sha = build_sha()
    sha = normalize_sha(sha)
    return f"{version}+{sha}" if sha else version
