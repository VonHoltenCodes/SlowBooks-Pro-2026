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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import (
    gnucash_import,
    myob_import,
    sage_import,
    wave_import,
    xero_import,
    zoho_import,
)
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
}


def _source(key: str):
    entry = SOURCES.get(key)
    if not entry:
        raise HTTPException(status_code=400, detail=f"Unknown migration source: {key}")
    return entry


async def bundle_from_uploads(files: list[UploadFile], classify) -> dict:
    bundle: dict[str, str] = {}
    unrecognized = []
    for file in files:
        kind = classify(file.filename)
        if not kind:
            unrecognized.append(file.filename)
            continue
        content = await read_limited(file, label=f"Migration file {file.filename}")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
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
                        f"{file.filename} has different columns than the other "
                        f"{kind} file(s); export them with the same settings"
                    ),
                )
            bundle[kind] = bundle[kind].rstrip("\n") + "\n" + body
        else:
            bundle[kind] = text
    if unrecognized:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Could not classify files by name: {unrecognized}. Expected "
                f"filenames containing 'chart'/'accounts', "
                f"'ledger'/'journal'/'transactions', or 'trial'."
            ),
        )
    return bundle


@router.get("/sources")
def list_sources():
    return [{"key": key, "label": label} for key, (label, _, _) in SOURCES.items()]


@router.post("/{source}/dry-run")
async def migration_dry_run(
    source: str, files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    _, module, _ = _source(source)
    bundle = await bundle_from_uploads(files, module.classify_filename)
    return module.dry_run(db, bundle)


@router.post("/{source}/import")
async def migration_import(
    source: str, files: list[UploadFile] = File(...), db: Session = Depends(get_db)
):
    _, module, chart_source = _source(source)
    bundle = await bundle_from_uploads(files, module.classify_filename)
    result = module.run_import(db, bundle)
    if result["ok"]:
        from datetime import date

        set_setting(db, "chart_setup_source", chart_source)
        set_setting(db, "chart_setup_ready_at", date.today().isoformat())
        db.commit()
    return result
