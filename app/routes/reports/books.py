# ============================================================================
# Books Check (#893): every subledger beside its control account, as of a
# date, on screen (JSON), as a PDF on the report theme and as a spreadsheet.
# The figures are app/services/books_check.py's.
# ============================================================================

import io
from datetime import date

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.routes._helpers import refuse_unknown_filters
from app.routes.reports._router import router
from app.services.books_check import books_check

_AS_OF = Query(default=None, description="The date the books are checked as of")

RESULT_WORDS = {
    "pass": "PASS",
    "difference": "Difference",
    "not_checked": "Not checked",
}


def _figure(value) -> str:
    from app.routes.reports.financial import _money

    return "" if value is None else _money(value)


def _document_words(doc: dict) -> str:
    kind = doc["type"].replace("_", " ")
    number = f" {doc['number']}" if doc.get("number") else ""
    return f"{kind[:1].upper()}{kind[1:]}{number}: {doc['why']}"


@router.get("/books-check", dependencies=[Depends(refuse_unknown_filters)])
def books_check_report(as_of: date = _AS_OF, db: Session = Depends(get_db)):
    """Books Check (#893): a row per subledger and its control account as of
    `as_of` (today when omitted) — the subledger's figure, the ledger's, the
    difference, PASS or a difference, a sentence, and the documents behind a
    difference — then the journal's own checks. Accrual basis."""
    return books_check(db, as_of)


def _sections(data: dict, company_type_terms) -> list[dict]:
    t = company_type_terms
    rows = []
    for r in data["rows"]:
        rows.append(
            {
                "cells": [
                    r["name"],
                    RESULT_WORDS[r["status"]],
                    _figure(r["subledger"]),
                    _figure(r["control"]),
                    "" if r["status"] != "difference" else _figure(r["difference"]),
                ],
                "style": "subtotal",
            }
        )
        rows.append(
            {"cells": [r["sentence"], "", "", "", ""], "indent": 1, "style": "detail"}
        )
        for doc in r["documents"]:
            rows.append(
                {
                    "cells": [
                        t.text(_document_words(doc)),
                        "",
                        "",
                        "",
                        _figure(doc["amount"]),
                    ],
                    "indent": 2,
                }
            )
        if r["more_documents"]:
            rows.append(
                {
                    "cells": [f"… and {r['more_documents']} more", "", "", "", ""],
                    "indent": 2,
                }
            )
    return [
        {
            "title": "Books Check",
            "period": f"As of {data['as_of']} (accrual basis)",
            "columns": [
                "Check",
                "Result",
                "Subledger",
                "Control account",
                "Difference",
            ],
            "text_columns": 2,
            "rows": rows,
            "note": data["summary"],
        }
    ]


@router.get("/books-check/pdf", dependencies=[Depends(refuse_unknown_filters)])
def books_check_pdf(as_of: date = _AS_OF, db: Session = Depends(get_db)):
    """Books Check as a PDF on the report theme: every row, its sentence and
    the documents behind a difference."""
    from app.routes.reports.financial import _pdf_response
    from app.services.terminology import terms_from_db

    data = books_check(db, as_of)
    return _pdf_response(
        _sections(data, terms_from_db(db)), db, f"books-check_{data['as_of']}.pdf"
    )


def books_check_csv(data: dict, company: str) -> str:
    """Check | Result | Subledger | Control account | Difference | Sentence,
    a row per check, then a row per document behind a difference (Document
    | Number | Amount | Why)."""
    from app.services.csv_export import _SafeWriter
    from app.services.ledger_exports import _money, _preamble

    out = io.StringIO()
    w = _SafeWriter(out)
    _preamble(w, company, "Books Check", f"As of {data['as_of']} (accrual basis)")
    w.writerow(
        ["Check", "Result", "Subledger", "Control account", "Difference", "Sentence"]
    )
    for r in data["rows"]:
        w.writerow(
            [
                r["name"],
                RESULT_WORDS[r["status"]],
                "" if r["subledger"] is None else _money(r["subledger"]),
                "" if r["control"] is None else _money(r["control"]),
                "" if r["difference"] is None else _money(r["difference"]),
                r["sentence"],
            ]
        )
    documents = [(r, d) for r in data["rows"] for d in r["documents"]]
    if documents:
        w.writerow([])
        w.writerow(["Check", "Document", "Number", "Amount", "Why"])
        for r, d in documents:
            w.writerow(
                [
                    r["name"],
                    d["type"].replace("_", " "),
                    d["number"],
                    "" if d["amount"] is None else _money(d["amount"]),
                    d["why"],
                ]
            )
    w.writerow([])
    w.writerow([data["summary"]])
    return out.getvalue()


@router.get("/books-check/csv", dependencies=[Depends(refuse_unknown_filters)])
def books_check_csv_route(
    request: Request, as_of: date = _AS_OF, db: Session = Depends(get_db)
):
    """Books Check as a spreadsheet: a row per check, then the documents."""
    from app.routes.reports.financial import _company_name, _csv_download

    data = books_check(db, as_of)
    return _csv_download(
        books_check_csv(data, _company_name(db)),
        f"books-check_{data['as_of']}.csv",
        request,
    )
