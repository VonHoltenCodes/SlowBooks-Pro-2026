"""Copy SlowBooks Switchboard's line client into app/switchboard (#1087).

The client is written and tested in SlowBooks Switchboard's private
source repository (package ``sbplive``); SlowBooks Pro carries a copy of
the modules the line needs, so Settings can connect Claude with no second
program. A copy is the source file with three mechanical changes and
nothing else:

- a two-line header naming where it came from;
- the blocks between "# >>> private" and "# <<< private" left out, as
  Switchboard's own public export leaves them out;
- its imports of ``sbplive`` read ``app.switchboard`` (the import lines
  only: the logger's name, the user agent and every sentence keep
  ``sbplive``).

    python scripts/sync_switchboard.py <source checkout>
    python scripts/sync_switchboard.py <source checkout> --check

Then update the commit in app/switchboard/SOURCE.md. tests/
test_switchboard_vendored_1087.py checks the copies against a checkout when
one is present (CI has none and skips it).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "switchboard"

# The modules sbplive/line.py imports, directly or through server.py (and
# remote.py, which server.Mcp imports for its sessions). Not serve-http's
# runner (serve_http.py), the OAuth door, the companion's window, its CLI or
# the relay.
FILES = (
    "__init__.py",
    "account.py",
    "client.py",
    "config.py",
    "line.py",
    "logs.py",
    "operations.py",
    "protocol.py",
    "registry.py",
    "remote.py",
    "server.py",
    "store.py",
    "tools.py",
    "ws.py",
)

_IMPORT = re.compile(r"^(\s*)(from|import) sbplive\b", re.M)
_HEADER_FIRST = "# Vendored from SlowBooks Switchboard: sbplive/{name}"
_HEADER_SECOND = (
    "# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run "
    "scripts/sync_switchboard.py.\n"
)


def header(name: str) -> str:
    return _HEADER_FIRST.format(name=name) + "\n" + _HEADER_SECOND


PRIVATE_BEGIN, PRIVATE_END = "# >>> private", "# <<< private"


def strip_private(text: str, name: str) -> str:
    """The source without its private blocks (the public export's rule)."""
    out, inside = [], False
    for line in text.splitlines(keepends=True):
        mark = line.strip()
        if mark in (PRIVATE_BEGIN, PRIVATE_END):
            if (mark == PRIVATE_BEGIN) == inside:
                raise ValueError(f"{name}: private blocks do not pair up")
            inside = mark == PRIVATE_BEGIN
            continue
        if not inside:
            out.append(line)
    if inside:
        raise ValueError(f"{name}: a private block is not closed")
    return "".join(out)


def vendored(name: str, source: str) -> str:
    """The copy of sbplive/<name> as it sits in app/switchboard."""
    body = strip_private(source, name)
    return header(name) + _IMPORT.sub(r"\1\2 app.switchboard", body)


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    checkout = Path(argv[0]).expanduser()
    check = "--check" in argv[1:]
    package = checkout / "sbplive"
    bad = []
    for name in FILES:
        source = (package / name).read_text(encoding="utf-8")
        want = vendored(name, source)
        path = TARGET / name
        have = path.read_text(encoding="utf-8") if path.exists() else None
        if have != want:
            bad.append(name)
            if not check:
                path.write_text(want, encoding="utf-8", newline="\n")
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if check:
        print("differ: " + ", ".join(bad) if bad else "all copies match")
        return 1 if bad else 0
    print(f"copied {len(bad)} changed file(s) from {commit or 'the checkout'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
