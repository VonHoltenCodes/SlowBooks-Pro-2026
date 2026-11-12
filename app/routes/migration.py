# ============================================================================
# Migration — one route pair for every "migrate from X" source.
#
# GET  /api/migration/sources               → picker metadata
# POST /api/migration/{source}/dry-run      → validate, write nothing
# POST /api/migration/{source}/import       → dry-run-gated import
#
# The Xero and MYOB-specific routes (/api/xero-import, /api/myob-import)
# remain as compatibility aliases; new sources exist only here.
# ============================================================================

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from sqlalchemy.orm import Session

from app.routes._roles import may_run_payroll
from app.database import get_db
from app.services import (
    gnucash_import,
    myob_import,
    qbo_export_import,
    sage_import,
    wave_import,
    xero_import,
    zoho_import,
)
from app.services.safe_errors import DataProblem
from app.services.settings_service import set_setting
from app.services.upload_limits import read_limited

router = APIRouter(prefix="/api/migration", tags=["migration"])

# source key → (label, dialect module, chart_setup_source value)
SOURCES = {
    "xero": ("Xero", xero_import, "xero_import"),
    "myob": ("MYOB", myob_import, "myob_import"),
    "sage": ("Sage 50", sage_import, "sage_import"),
    "wave": ("Wave", wave_import, "wave_import"),
    "zoho": ("Zoho Books", zoho_import, "zoho_import"),
    "gnucash": ("GnuCash", gnucash_import, "gnucash_import"),
    # QuickBooks Online's own Export data bundle, no Intuit developer app
    # needed: the zip as downloaded, or its workbooks, or those saved as CSV.
    "qbo_export": ("QuickBooks Online export", qbo_export_import, "qbo_export_import"),
}


def _source(key: str):
    entry = SOURCES.get(key)
    if not entry:
        raise HTTPException(status_code=400, detail=f"Unknown migration source: {key}")
    return entry


def _sheet_as_csv(content: bytes, computed: bool = False) -> str:
    """An Excel workbook's first sheet as CSV text, cells as they read:
    QuickBooks Online exports its reports and lists as .xlsx, with every
    figure as formula text ("=23683.16") and labels indented by spaces, and
    both survive this pass for the dialect to read. With `computed`, a
    formula cell reads as the value Excel last computed for it, when the
    file carries one (QuickBooks Desktop's timesheet report writes its
    Total rows as =ROUND(SUM(...),5); 2.23.4 gate, NEW-132); a formula
    without a computed value stays as its text."""
    import csv
    import io
    from datetime import date, datetime, time, timedelta

    try:
        import openpyxl
    except ImportError:  # pragma: no cover - a build without the reader
        raise HTTPException(
            status_code=400,
            detail="This build cannot read .xlsx files; save the sheet as CSV and upload that.",
        )
    try:
        book = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=False
        )
    except Exception:
        raise HTTPException(
            status_code=400, detail="The .xlsx file could not be read as a workbook"
        )
    sheet = book.worksheets[0]
    computed_rows = None
    if computed:
        try:
            values = openpyxl.load_workbook(
                io.BytesIO(content), read_only=True, data_only=True
            )
            computed_rows = values.worksheets[0].iter_rows(values_only=True)
        except Exception:
            computed_rows = None
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in sheet.iter_rows(values_only=True):
        cached = next(computed_rows, None) if computed_rows is not None else None
        if cached is not None:
            row = tuple(
                (
                    cached[i]
                    if isinstance(cell, str)
                    and cell.startswith("=")
                    and i < len(cached)
                    and cached[i] is not None
                    else cell
                )
                for i, cell in enumerate(row)
            )
        cells = []
        for cell in row:
            if cell is None:
                cells.append("")
            elif isinstance(cell, datetime):
                # ISO: every importer's dialect reads it, and the day-first
                # ones (MYOB) misread a month-first text (2.31.3)
                cells.append(cell.strftime("%Y-%m-%d"))
            elif isinstance(cell, date):
                cells.append(cell.strftime("%Y-%m-%d"))
            elif isinstance(cell, time):
                # a duration Excel took for a time of day ("0:20" in a
                # Minutes-format timesheet): hours and minutes (NEW-137)
                cells.append(f"{cell.hour}:{cell.minute:02d}")
            elif isinstance(cell, timedelta):
                # a duration past a day, as Excel keeps [h]:mm
                minutes = int(round(cell.total_seconds() / 60))
                cells.append(f"{minutes // 60}:{minutes % 60:02d}")
            elif isinstance(cell, float) and cell.is_integer():
                cells.append(str(int(cell)))
            else:
                cells.append(str(cell))
        writer.writerow(cells)
    book.close()
    return out.getvalue()


def _members(filename: str, content: bytes) -> list[tuple[str, bytes]]:
    """An upload as the files it holds: a zip's members (a vendor's bundle,
    whose unknown members are left alone), or the file itself."""
    import io
    import posixpath
    import zipfile

    if not (filename or "").lower().endswith(".zip"):
        return [(filename, content)]
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail=f"{filename} is not a zip file")
    members = []
    with archive:
        for info in archive.infolist():
            name = posixpath.basename(info.filename)
            if (
                info.is_dir()
                or not name
                or name.startswith(".")
                or "__MACOSX" in info.filename
            ):
                continue
            members.append((name, archive.read(info)))
    return members


def _looks_like(names: list[str], chosen) -> str | None:
    """The one other source whose file names these are, when exactly one
    reads every one of them (an A/R Aging Detail report is QuickBooks
    Online's; #706), else None."""
    matches = [
        label
        for label, module, _ in SOURCES.values()
        if module.classify_filename is not chosen
        and all(module.classify_filename(name) for name in names)
    ]
    return matches[0] if len(matches) == 1 else None


def _export_name(label: str) -> str:
    return label if label.endswith(" export") else f"{label} export"


def _another_source_sentence(label: str) -> str:
    """What the dry run says when the whole upload is another source's
    export and the chosen source is missing a file it needs (#706)."""
    return (
        f"These look like a {_export_name(label)}: choose {label} under "
        f"Coming from and run the dry run again."
    )


def _names_in_a_sentence(names: list[str]) -> str:
    if len(names) == 1:
        return f"Could not tell what {names[0]} is from its name"
    listed = ", ".join(names[:-1]) + f" and {names[-1]}"
    return f"Could not tell what {listed} are from their names"


def _unclassified_text(
    names: list[str], classify, every_name: list[str] | None = None
) -> str:
    """Files the chosen source cannot place by name, said in a sentence
    (#706: it printed a Python list). `every_name` is the whole upload, a
    zip's members included: when it is all another source's, that is said
    too."""
    head = _names_in_a_sentence(names)
    other = _looks_like(names, classify)
    if other:
        these = "it looks" if len(names) == 1 else "these look"
        return (
            f"{head}; {these} like a {_export_name(other)}. Choose {other} under "
            f"Coming from, or leave {'it' if len(names) == 1 else 'them'} out."
        )
    whole = _looks_like(every_name, classify) if every_name else None
    if whole:
        return f"{head}. {_another_source_sentence(whole)}"
    return (
        f"{head}. This source reads files whose names say what they hold: "
        f"the chart of accounts ('chart' or 'accounts'), the ledger "
        f"('ledger', 'journal' or 'transactions'), or the trial balance "
        f"('trial')."
    )


class Upload:
    """What the files came to: the bundle the source reads, every file's
    name (a zip's members included), and the zip members the source could
    not place, which are left out rather than refused (a vendor's bundle
    may carry files it has no use for)."""

    def __init__(
        self,
        bundle: dict,
        names: list[str],
        left_out: list[str],
        notes: list[str] | None = None,
    ):
        self.bundle = bundle
        self.names = names
        self.left_out = left_out
        # what the source said while the bundle was built (two Trial
        # Balances, one kept): the dry run's first warnings
        self.notes = notes or []

    def missing_required(self) -> bool:
        return "coa" not in self.bundle or "gl" not in self.bundle


async def bundle_from_uploads(
    files: list[UploadFile], classify, combine=None, note_for=None
) -> Upload:
    bundle: dict[str, str] = {}
    unrecognized = []
    left_out = []
    names = []
    kept_names: dict[str, str] = {}
    set_aside: dict[str, list] = {}
    for file in files:
        content = await read_limited(file, label=f"Migration file {file.filename}")
        zipped = (file.filename or "").lower().endswith(".zip")
        for name, data in _members(file.filename, content):
            names.append(name)
            kind = classify(name)
            if kind == "skip":
                continue  # a file the source says to leave alone
            if not kind:
                # a zip's stray is left out and named; a loose file refused
                (left_out if zipped else unrecognized).append(name)
                continue
            if name.lower().endswith(".xlsx"):
                text = _sheet_as_csv(data)
            else:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = data.decode("latin-1")
            _add_to_bundle(bundle, kind, name, text, combine, set_aside, kept_names)
    if unrecognized:
        raise HTTPException(
            status_code=400,
            detail=_unclassified_text(left_out + unrecognized, classify, names),
        )
    # one sentence per kind that kept one file, however many came (W-135)
    notes: list[str] = []
    for kind, others in set_aside.items():
        note = (
            note_for(kind, kept_names.get(kind), bundle[kind], others)
            if note_for
            else None
        )
        if note:
            notes.append(note)
    return Upload(bundle, names, left_out, notes)


def _say_what_was_left(result: dict, upload: Upload, classify) -> dict:
    """The zip members left out, named in the result; and when the import
    cannot go ahead for want of a file while every file is another
    source's, the result says which source (#706, W-114)."""
    if upload.left_out:
        result["warnings"] = [
            f"{_names_in_a_sentence(upload.left_out)}, so "
            f"{'it was' if len(upload.left_out) == 1 else 'they were'} left out."
        ] + list(result.get("warnings") or [])
    if upload.notes:
        result["warnings"] = list(upload.notes) + list(result.get("warnings") or [])
    if not result.get("ok") and upload.missing_required():
        other = _looks_like(upload.names, classify)
        if other:
            result["errors"] = [_another_source_sentence(other)] + list(
                result.get("errors") or []
            )
    return result


def _add_to_bundle(
    bundle: dict,
    kind: str,
    name: str,
    text: str,
    combine=None,
    set_aside: dict[str, list] | None = None,
    kept_names: dict[str, str] | None = None,
) -> None:
    if True:
        if kind in bundle and combine is not None:
            # the source may know what two files of this kind mean (one
            # Trial Balance is read, W-133); None leaves the joining below
            kept_name = (kept_names or {}).get(kind, "the first file")
            chosen = combine(kind, bundle[kind], kept_name, text, name)
            if chosen is not None:
                chosen_text, chosen_name = chosen
                other = (
                    (kept_name, bundle[kind]) if chosen_text is text else (name, text)
                )
                if set_aside is not None:
                    set_aside.setdefault(kind, []).append(other)
                if kept_names is not None:
                    kept_names[kind] = chosen_name
                bundle[kind] = chosen_text
                return
        if kind in bundle:
            # Multiple files of one kind (MYOB exports journals per type):
            # concatenate, dropping the duplicate header row — which must
            # match the first file's, or the files aren't the same table.
            first_header = bundle[kind].split("\n", 1)[0].strip()
            header, _, body = text.partition("\n")
            if header.strip() != first_header:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{name} has different columns than the other "
                        f"{kind} file(s); export them with the same settings"
                    ),
                )
            bundle[kind] = bundle[kind].rstrip("\n") + "\n" + body
        else:
            bundle[kind] = text
            if kept_names is not None:
                kept_names[kind] = name


def _refused(
    db: Session, exc: DataProblem, upload: Upload | None = None, classify=None
) -> HTTPException:
    """A problem with the books the import found on its way in (an entry out
    of balance, an account it cannot place) is the person's to read: a 422
    with its words, nothing kept, for every source (#690). It used to
    surface as a bare HTTP 500."""
    db.rollback()
    detail = exc.user_text
    if upload is not None and upload.missing_required():
        other = _looks_like(upload.names, classify)
        if other:
            detail = f"{detail} {_another_source_sentence(other)}"
    return HTTPException(status_code=422, detail=detail)


@router.get("/sources")
def list_sources():
    return [{"key": key, "label": label} for key, (label, _, _) in SOURCES.items()]


# Adding employees is HR (GHSA-rh75-6834-f66j, #755): the Employee Contact
# List comes in only by a person's choice, ``include_employees``, which the
# dry run and the import both take and answer alike (NEW-222). Left out of
# the request, it comes in for a caller who may add employees (an
# administrator, or a token let run payroll) and is left out, said so, for
# anyone else; ``false`` leaves it out for anyone; ``true`` from a caller
# who may not add employees is refused.
_EMPLOYEES_NEED = (
    "adding employees needs an administrator, or a token that may also run payroll"
)


def _employee_refusal(imported: bool) -> HTTPException:
    detail = (
        f"The Employee Contact List cannot come in: {_EMPLOYEES_NEED}. Leave "
        "include_employees out, or send it false, to import the books without it."
    )
    if imported:
        detail += " Nothing was imported."
    return HTTPException(status_code=403, detail=detail)


def _choose_employees(
    request: Request,
    db: Session,
    module,
    upload: Upload,
    include: bool | None,
    imported: bool,
) -> dict | None:
    """Apply the caller's choice to the bundle before the dry run or the
    import reads it: the Employee Contact List stays in it only when it
    comes in. Answers what the result says about the list, or None when
    the bundle has none."""
    may_add = may_run_payroll(request)
    if include and not may_add:
        raise _employee_refusal(imported)
    if "employees" not in upload.bundle:
        return None
    counter = getattr(module, "employee_count", None)
    try:
        found = counter(db, upload.bundle) if counter else 0
    except DataProblem:
        found = 0  # the import's own reading of the list says what is wrong
    included = may_add if include is None else bool(include)
    people = f"{found} employee{'' if found == 1 else 's'}"
    if included:
        note = f"The Employee Contact List has {people} to add; they " + (
            "came in with the import." if imported else "come in with the import."
        )
        warning = None
    else:
        upload.bundle.pop("employees")
        when = "was" if imported else "will be"
        if include is False:
            note = warning = (
                f"The Employee Contact List ({people}) {when} left out, as asked."
            )
        else:
            note = warning = (
                f"The Employee Contact List {when} left out: {_EMPLOYEES_NEED}."
            )
    return {
        "employee_list": {
            "employees": found,
            "included": included,
            "may_add": may_add,
            "note": note,
        },
        "warning": warning,
    }


def _with_employee_choice(result: dict, chosen: dict | None) -> dict:
    if chosen:
        result["employee_list"] = chosen["employee_list"]
        if chosen["warning"]:
            result.setdefault("warnings", []).append(chosen["warning"])
    return result


@router.post("/{source}/dry-run")
async def migration_dry_run(
    source: str,
    request: Request,
    files: list[UploadFile] = File(...),
    include_employees: bool | None = Form(None),
    db: Session = Depends(get_db),
):
    _, module, _ = _source(source)
    classify = module.classify_filename
    upload = await bundle_from_uploads(
        files,
        classify,
        getattr(module, "combine_same_kind", None),
        getattr(module, "same_kind_note", None),
    )
    chosen = _choose_employees(
        request, db, module, upload, include_employees, imported=False
    )
    try:
        result = module.dry_run(db, upload.bundle)
    except DataProblem as e:
        raise _refused(db, e, upload, classify)
    # what the source says of the files that came (a QuickBooks Online
    # export with no customer list names its files and the names it reads)
    name_notes = getattr(module, "dry_run_notes", None)
    if name_notes is not None:
        result["warnings"] = list(result.get("warnings") or []) + name_notes(
            upload.bundle, upload.names
        )
    return _with_employee_choice(_say_what_was_left(result, upload, classify), chosen)


@router.post("/{source}/import")
async def migration_import(
    source: str,
    request: Request,
    files: list[UploadFile] = File(...),
    include_employees: bool | None = Form(None),
    db: Session = Depends(get_db),
):
    _, module, chart_source = _source(source)
    classify = module.classify_filename
    upload = await bundle_from_uploads(
        files,
        classify,
        getattr(module, "combine_same_kind", None),
        getattr(module, "same_kind_note", None),
    )
    chosen = _choose_employees(
        request, db, module, upload, include_employees, imported=True
    )
    try:
        result = module.run_import(db, upload.bundle)
    except DataProblem as e:
        raise _refused(db, e, upload, classify)
    result = _with_employee_choice(_say_what_was_left(result, upload, classify), chosen)
    if result["ok"]:
        from datetime import date

        set_setting(db, "chart_setup_source", chart_source)
        set_setting(db, "chart_setup_ready_at", date.today().isoformat())
        db.commit()
    return result
