"""Every report PDF's footer names the company as it is written, in the
report's own typeface (2.17.3 exploratory test, W-M17).

The footer printed "Explore Signs &amp; Co — page 1 of 1": the name was
written into a <style> block, which is raw text, so autoescaping reached
the page as a literal "&amp;". The margin box also fell back to a serif or
monospace face, because a page margin does not inherit the body's font.
Invoices and statements were fine; they have no such footer.
"""

import re
import shutil
import subprocess

import pytest

NAME = "Explore Signs & Co"
SECTIONS = [
    {
        "title": "Profit & Loss",
        "period": "2026-01-01 — 2026-12-31",
        "columns": ["", "Amount"],
        "rows": [{"cells": ["Service Income", "$612.30"]}],
    }
]


def _style(html: str) -> str:
    return "".join(re.findall(r"<style>(.*?)</style>", html, flags=re.S))


def test_the_footer_rule_carries_no_escaped_text_and_names_a_font():
    from app.services.pdf_service import _render

    html = _render(
        "report_pdf.html",
        {"company_name": NAME},
        sections=SECTIONS,
        paper_size="letter",
        generated_on="2026-09-26",
    )
    style = _style(html)
    footer = re.search(r"@bottom-center\s*{(.*?)}", style, flags=re.S).group(1)
    assert "&amp;" not in style and "&#" not in style
    assert "string(company)" in footer
    assert "font-family" in footer and "sans-serif" in footer
    # the name the footer repeats is the header's, escaped as HTML text is
    assert 'class="report-company"' in html and "Explore Signs &amp; Co</div>" in html


def _pdf_text(pdf: bytes, tmp_path, tool: str) -> str:
    if not shutil.which(tool):
        pytest.skip(f"{tool} (poppler-utils) not available")
    path = tmp_path / "report.pdf"
    path.write_bytes(pdf)
    args = [tool, str(path)] + (["-"] if tool == "pdftotext" else [])
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def test_the_printed_footer_reads_the_company_name(client, seed_accounts, tmp_path):
    pytest.importorskip("weasyprint")
    assert client.put("/api/settings", json={"company_name": NAME}).status_code == 200
    r = client.get(
        "/api/reports/profit-loss/pdf?start_date=2026-01-01&end_date=2026-12-31"
    )
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    text = _pdf_text(r.content, tmp_path, "pdftotext")
    assert f"{NAME} — page 1 of 1" in text
    assert "&amp;" not in text
    fonts = _pdf_text(r.content, tmp_path, "pdffonts")
    faces = [ln.split()[0] for ln in fonts.splitlines()[2:] if ln.strip()]
    assert faces and not [
        f for f in faces if re.search(r"Serif|Mono|Courier|Times", f)
    ], faces
