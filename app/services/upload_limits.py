# ============================================================================
# Upload size limits — shared guard for every file-upload endpoint.
#
# Reading `await file.read()` unbounded lets one authenticated request
# buffer an arbitrarily large body into memory. Every upload route reads
# through read_limited() instead, with a cap matched to what the endpoint
# actually ingests. Logo uploads (app/routes/uploads.py) and attachments
# (app/routes/attachments.py) had their own caps first; the constants
# here centralize the policy for the import endpoints.
#
# Ported concept from the joelmacklow fork's upload_limits service.
# ============================================================================

from fastapi import HTTPException, UploadFile

# Text imports (CSV/IIF/OFX bank files) are far smaller than binary media;
# 20 MB is orders of magnitude above any real statement or ledger export.
MAX_IMPORT_BYTES = 20 * 1024 * 1024


async def read_limited(
    file: UploadFile, max_bytes: int = MAX_IMPORT_BYTES, label: str = "File"
) -> bytes:
    """Read an UploadFile, rejecting bodies over max_bytes with a 413.

    Reads max_bytes + 1 so oversize detection doesn't require buffering
    the whole oversized body.
    """
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            # label + ...: a label built with said() keeps the file's name
            detail=label + f" is too large (limit {max_bytes // (1024 * 1024)} MB).",
        )
    return content
