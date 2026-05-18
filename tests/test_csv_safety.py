"""
CSV formula-injection protection: any user-controlled cell that starts
with =, +, -, @, \\t, or \\r must be prefixed with a single quote before
being written to the CSV stream.
"""

import pytest

from app.routes.analytics import _csv_safe


@pytest.mark.parametrize(
    "dangerous",
    [
        "=SUM(A1:A2)",
        "+1+1",
        "-2+3",
        "@cmd",
        "\tHELLO",
        "\rEVIL",
        '=HYPERLINK("http://evil.com")',
    ],
)
def test_csv_safe_neutralizes_formula_prefix(dangerous):
    safe = _csv_safe(dangerous)
    assert safe.startswith("'")
    assert safe[1:] == dangerous


@pytest.mark.parametrize(
    "benign",
    [
        "ABC Corp",
        "January 2026",
        "1234.56",
        "hello world",
        "",
    ],
)
def test_csv_safe_passes_benign_unchanged(benign):
    assert _csv_safe(benign) == benign


def test_csv_safe_handles_none():
    # None gets stringified ("None") — not dangerous, not formula-prefix,
    # passes through unchanged. The important invariant is "no formula
    # injection", not "None becomes empty string".
    assert _csv_safe(None) == "None"
