# Vendored from SlowBooks Switchboard: sbplive/__init__.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
"""SlowBooks Switchboard: an MCP host works a SlowBooks Pro company's books.

Run with ``python -m sbplive``. The host starts the program and
speaks the Model Context Protocol on its stdin and stdout; the program
answers through the running app's REST API with an API token read from a
private file. Nothing here touches the database or the models: every tool
is a request the browser could make, under the token's role, audited as
``token:<label>``.

Standard library only: the package imports nothing outside it, at run time
or in its tests' fast path, so it runs on any Python 3.10 or newer.
"""

__version__ = "0.1.0"
