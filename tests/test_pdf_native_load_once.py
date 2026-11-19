"""WeasyPrint installed without its native libraries: the import is tried
once per process, never again.

A failed import has already dlopen()ed gobject; retrying re-runs gobject's
constructor against a glib that kept the first attempt's interned strings,
and the process segfaults (macOS with Homebrew glib but no pango). The
server crashed on every second PDF, and pytest-xdist workers died the same
way mid-suite.
"""

import builtins
import sys

import pytest

from app.services import pdf_service
from app.services.pdf_service import PdfEngineMissing


@pytest.fixture
def import_attempts(monkeypatch):
    """Make `import weasyprint` fail the way a missing libpango does, and
    count how many times anything tries it."""
    attempts = []
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "weasyprint" or name.startswith("weasyprint."):
            attempts.append(name)
            raise OSError("cannot load library 'libpango-1.0-0'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(pdf_service, "_NATIVE_LOAD_FAILURE", None, raising=False)
    # conftest counts every PdfEngineMissing so a failing test on a machine
    # without the stack becomes a skip. These tests are about that very
    # failure, so they must fail loudly there too: stop the counting.
    monkeypatch.setattr(PdfEngineMissing, "__init__", RuntimeError.__init__)
    monkeypatch.delitem(sys.modules, "weasyprint", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    return attempts


def test_a_native_library_failure_is_never_retried(import_attempts):
    for _ in range(3):
        with pytest.raises(PdfEngineMissing):
            pdf_service.render_pdf("<p>hello</p>")

    assert import_attempts == ["weasyprint"]


def test_the_retried_failure_still_names_the_missing_library(import_attempts):
    with pytest.raises(PdfEngineMissing):
        pdf_service.render_pdf("<p>first</p>")
    with pytest.raises(PdfEngineMissing) as second:
        pdf_service.render_pdf("<p>second</p>")

    assert isinstance(second.value.__cause__, OSError)
    assert "libpango" in str(second.value.__cause__)
