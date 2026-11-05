"""What only the desktop app's own window can do (#719).

The launcher (desktop_launcher.py, its windowed run alone) makes a random
secret at each start and hands it to the server child through the child's
environment, the way it hands on a company file's key. The window's page
never sees it: a request that needs it is made by the launcher itself, on
the page's behalf, through the window's bridge (window.pywebview.api), which
a browser does not have. So a request carrying it came from the launcher of
this run, on this computer, for its window:

- a browser on the network, or on this computer, has no bridge and no
  secret;
- an API token is a program's, never the window's, and is refused here
  whatever else it carries;
- Server Edition (--serve-lan) and a windowless run (--no-window) are never
  given a secret, and Docker or a source run has no launcher at all.

The loopback address and the desktop flag are still checked too (auth's
_desktop_window): the secret adds to them, it replaces nothing.
"""

from __future__ import annotations

import hmac
import os

# The child's environment: the secret, and whether the picker opened this
# company with its recovery key (a hint for the sign-in page; the reset
# itself is proven by the recovery key the launcher sends with it).
SECRET_ENV = "SLOWBOOKS_WINDOW_SECRET"
RECOVERY_OPENED_ENV = "SLOWBOOKS_OPENED_WITH_RECOVERY_KEY"
# The header the launcher sends it in.
SECRET_HEADER = "X-SlowBooks-Window"


def secret() -> str:
    return os.environ.get(SECRET_ENV) or ""


def has_secret() -> bool:
    """True when this server was started by the launcher for its window."""
    return len(secret()) >= 32


def secret_matches(headers) -> bool:
    """The request carries this run's window secret. Constant-time; an
    unset secret never matches, not even an empty header."""
    expected = secret()
    if len(expected) < 32:
        return False
    given = headers.get(SECRET_HEADER) or ""
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def opened_with_recovery_key() -> bool:
    return has_secret() and os.environ.get(RECOVERY_OPENED_ENV) == "1"
