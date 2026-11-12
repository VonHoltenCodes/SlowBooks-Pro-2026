"""The recurring schedule form offers every frequency the API takes (#813):
it offered weekly, monthly, quarterly and yearly where the API takes six,
so daily and twice a month were the API's alone."""

import re
from pathlib import Path

from app.services.recurring_service import FREQUENCIES

JS = (Path(__file__).resolve().parents[1] / "app/static/js/recurring.js").read_text(
    encoding="utf-8"
)


def test_the_form_offers_the_six_the_api_takes():
    start = JS.index("FREQUENCIES: [")
    block = JS[start : JS.index("    ],\n", start)]
    offered = re.findall(r"\['(\w+)', '[^']+'\]", block)
    assert tuple(offered) == FREQUENCIES
    assert "RecurringPage.FREQUENCIES.map(" in JS
    assert '<option value="${f}"' in JS
