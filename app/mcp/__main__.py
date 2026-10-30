"""``python -m app.mcp`` from the checkout, or ``python /path/to/app/mcp/__main__.py``
from anywhere: start the stdio MCP server (standard library only)."""

import os
import sys

if __package__ in (None, ""):
    # Run by path: put the checkout on sys.path so `app` imports.
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )

from app.mcp.server import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
