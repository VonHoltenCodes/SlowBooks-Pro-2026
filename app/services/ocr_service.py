# ============================================================================
# Receipt / Document Intake — Tier 2 OCR pipeline
# See docs/design/receipt-intake.md + docs/design/receipt-intake-spec.md.
#
# Deterministic regex/anchor extraction over Tesseract output — no AI in v1.
# ZERO Python dependencies by design: we shell out to the user-installed
# `tesseract` binary (never bundled) and, for PDFs, the poppler-utils tools
# (pdftoppm/pdfinfo). Both are detected at runtime and degrade gracefully:
# the route layer turns "binary missing" into a friendly message and the app
# runs exactly as before.
# ============================================================================

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from uuid import uuid4

from app.services import storage

logger = logging.getLogger(__name__)

# Language allowlist for auto-detect (see spec §5.5). Intersected with what
# `tesseract --list-langs` reports; never user input, so no injection surface.
ALLOWED_LANGS = {"eng", "spa", "fra", "deu", "por", "ita", "nld"}

# Intake bucket policy (spec §5.3): files expire after 24h; hard caps protect
# against a forgotten client filling the disk.
INTAKE_TTL_HOURS = 24
INTAKE_MAX_FILES = 500
INTAKE_MAX_BYTES = 1024**3  # 1 GB

INTAKE_DIR = storage.files_root() / "uploads" / "intake"
_INTAKE_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_INTAKE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}

OCR_TIMEOUT_SECONDS = 20
# Rasterization resolution for PDF pages. 200 was too coarse for some
# WeasyPrint-generated PDFs under poppler (amount columns vanished); 300
# renders every ground-truth sample cleanly and is still fast for a
# receipt-sized page.
PDF_DPI = 300


class OCRRuntimeError(Exception):
    """Tesseract ran but failed (bad data, no language data, timeout...)."""


# ---------------------------------------------------------------------------
# Runtime binary detection (all cached — spawning --version per request is
# wasteful; the status endpoint and every modal open would do it)
# ---------------------------------------------------------------------------

_INFO_CACHE_TTL = 60.0
_cache = {"at": 0.0, "info": None}


def tesseract_info() -> dict:
    """{"available", "version", "languages"} — cached, never raises."""
    now = time.monotonic()
    if _cache["info"] is not None and now - _cache["at"] < _INFO_CACHE_TTL:
        return _cache["info"]
    info = _probe_tesseract()
    info["poppler"] = _poppler_available()
    _cache.update(at=now, info=info)
    return info


def _probe_tesseract() -> dict:
    if shutil.which("tesseract") is None:
        return {"available": False, "version": None, "languages": []}
    version = None
    languages: list[str] = []
    try:
        out = subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, timeout=10
        )
        first = (out.stdout or out.stderr or "").strip().splitlines()
        if first:
            m = re.match(r"tesseract\s+v?([\d.]+)", first[0])
            if m:
                version = m.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10
        )
        for line in (out.stdout or "").splitlines():
            line = line.strip()
            # The header ("List of available languages in ...") goes to
            # stdout on some builds; skip it and anything non-lang-like.
            if line and not line.lower().startswith("list of") and " " not in line:
                languages.append(line)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return {"available": True, "version": version, "languages": languages}


def _poppler_available() -> bool:
    """PDF rasterization needs both pdftoppm and pdfinfo (poppler-utils)."""
    return shutil.which("pdftoppm") is not None and shutil.which("pdfinfo") is not None


def tesseract_available() -> bool:
    return tesseract_info()["available"]


def poppler_available() -> bool:
    return tesseract_info()["poppler"]


def ocr_language() -> Optional[str]:
    """Pick the Tesseract language string: eng when available, else any
    allowlisted intersection. None when nothing usable is installed."""
    installed = set(tesseract_info()["languages"])
    usable = installed & ALLOWED_LANGS
    if not usable:
        return None
    return "eng" if "eng" in usable else "+".join(sorted(usable))


# ---------------------------------------------------------------------------
# Tesseract invocation — direct subprocess, no wrapper package
# ---------------------------------------------------------------------------


def ocr_image_bytes(data: bytes, lang: Optional[str] = None) -> str:
    """OCR raw image bytes via `tesseract stdin stdout`.

    Tesseract reads the image from stdin and writes the extracted text to
    stdout; leptonica decodes PNG/JPEG/WebP/TIFF natively, so no image
    library is needed on the Python side. Raises OCRRuntimeError on any
    failure (nonzero exit, timeout, missing binary).
    """
    cmd = ["tesseract", "stdin", "stdout", "--psm", "3"]
    if lang:
        cmd += ["-l", lang]
    try:
        proc = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRRuntimeError(
            f"Tesseract timed out after {OCR_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise OCRRuntimeError(f"Could not run tesseract: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise OCRRuntimeError(
            f"Tesseract failed (exit {proc.returncode})"
            + (f": {stderr[:300]}" if stderr else "")
        )
    return (proc.stdout or b"").decode("utf-8", errors="replace")


def ocr_image_words(data: bytes, lang: Optional[str] = None):
    """OCR raw image bytes via `tesseract stdin stdout tsv`.

    Returns (text, words): `text` reconstructed from the TSV word rows
    (line-faithful — the deterministic parsers are line-based), `words` a
    list of {text, left, top, width, height, conf} dicts for the v2
    canvas. One subprocess call serves both. Raises OCRRuntimeError like
    ocr_image_bytes.
    """
    cmd = ["tesseract", "stdin", "stdout", "--psm", "3"]
    if lang:
        cmd += ["-l", lang]
    cmd += ["tsv"]
    try:
        proc = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            timeout=OCR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRRuntimeError(
            f"Tesseract timed out after {OCR_TIMEOUT_SECONDS}s"
        ) from exc
    except OSError as exc:
        raise OCRRuntimeError(f"Could not run tesseract: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise OCRRuntimeError(
            f"Tesseract failed (exit {proc.returncode})"
            + (f": {stderr[:300]}" if stderr else "")
        )
    tsv = (proc.stdout or b"").decode("utf-8", errors="replace")
    return _parse_tesseract_tsv(tsv)


def _parse_tesseract_tsv(tsv: str):
    """TSV rows -> (reconstructed_text, word dicts).

    Columns: level page block par line word left top width height conf text.
    Word rows are level 5; lines break on (block, par, line) changes; a
    paragraph change inserts a blank line so multi-block receipts keep
    their visual grouping for the line-based parsers.
    """
    words: list[dict] = []
    lines: list[str] = []
    current_key = None
    current_words: list[str] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        try:
            level = int(cols[0])
        except ValueError:
            continue
        if level != 5:
            continue
        text = cols[11]
        if not text.strip():
            continue
        try:
            left, top, width, height = (int(c) for c in cols[6:10])
            conf = float(cols[10])
        except ValueError:
            left = top = width = height = 0
            conf = -1.0
        key = (cols[2], cols[3], cols[4])  # block, par, line
        if key != current_key:
            if current_words:
                lines.append(" ".join(current_words))
            if current_key is not None and cols[2:4] != list(current_key[:2]):
                lines.append("")  # block/par boundary -> blank line
            current_key = key
            current_words = []
        current_words.append(text)
        words.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "conf": conf,
            }
        )
    if current_words:
        lines.append(" ".join(current_words))
    return "\n".join(lines), words


def preprocess_page(data: bytes):
    """Full-page enhancement before tesseract: grayscale -> upscale toward
    ~1800px height -> autocontrast -> binarize. Returns (png_bytes, factor)
    where factor is the integer upscale (word boxes divide by it to return
    to the original image's coordinate space).

    Measured on 39 real-world SROIE receipts: total-field extraction went
    10% -> 33% with this pass. Applied on the tesseract path only — the
    platform-native engines handle photographic input themselves. Falls
    back to the raw bytes on any decode problem (tesseract then reports
    its own error).
    """
    try:
        import io as _io

        from PIL import Image, ImageOps

        img = Image.open(_io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img).convert("L")
        factor = 1
        if img.height < 1800:
            factor = max(1, round(1800 / img.height))
            img = img.resize((img.width * factor, img.height * factor), Image.LANCZOS)
        img = ImageOps.autocontrast(img, cutoff=1)
        img = img.point(lambda p: 255 if p > 140 else 0)
        out = _io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue(), factor
    except Exception:
        return data, 1


# ---------------------------------------------------------------------------
# PDF handling — poppler-utils (pdftoppm/pdfinfo), page 1 only (spec §3)
# ---------------------------------------------------------------------------


def rasterize_pdf(data: bytes, dpi: int = PDF_DPI):
    """Rasterize PDF page 1 to PNG bytes. Returns (png_bytes, page_count).

    Raises ValueError on missing poppler-utils or a corrupt PDF. Uses temp
    files (poppler reads files, not stdin); cleaned up in all paths.
    """
    if not poppler_available():
        raise ValueError(
            "PDF scanning requires poppler-utils (pdftoppm/pdfinfo). "
            "Install it to scan PDFs — images still work without it."
        )
    with tempfile.TemporaryDirectory(prefix="slowbooks-ocr-") as tmp:
        pdf_path = Path(tmp) / "input.pdf"
        pdf_path.write_bytes(data)

        page_count = 1
        try:
            info = subprocess.run(
                ["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=15
            )
            if info.returncode == 0:
                m = re.search(r"^Pages:\s*(\d+)", info.stdout, re.MULTILINE)
                if m:
                    page_count = int(m.group(1))
        except (OSError, subprocess.TimeoutExpired):
            pass  # page count is informational only

        prefix = Path(tmp) / "page"
        try:
            proc = subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    str(dpi),
                    "-f",
                    "1",
                    "-l",
                    "1",
                    "-singlefile",
                    str(pdf_path),
                    str(prefix),
                ],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"pdftoppm could not run: {exc}") from exc
        if proc.returncode != 0:
            raise ValueError(
                "Could not read the PDF"
                + (
                    ": " + (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
                    if proc.stderr
                    else ""
                )
            )
        png_path = Path(f"{prefix}.png")
        if not png_path.is_file():
            raise ValueError("Could not rasterize the PDF (no page output)")
        return png_path.read_bytes(), page_count


# ---------------------------------------------------------------------------
# Deterministic parsers — raw OCR text → structured fields
# ---------------------------------------------------------------------------

_MONTH_NAMES: dict[str, int] = {}
for _num, _names in enumerate(
    [
        ["Jan", "January"],
        ["Feb", "February"],
        ["Mar", "March"],
        ["Apr", "April"],
        ["May"],
        ["Jun", "June"],
        ["Jul", "July"],
        ["Aug", "August"],
        ["Sep", "Sept", "September"],
        ["Oct", "October"],
        ["Nov", "November"],
        ["Dec", "December"],
    ],
    start=1,
):
    for _n in _names:
        _MONTH_NAMES[_n.lower()] = _num

# A currency amount: optional $, thousands separators, exactly two decimals.
# The (?!\s*%) guard stops a percentage like "(7.25%)" being read as an
# amount on a tax line.
_AMOUNT_RE = re.compile(r"\$?\s?\d{1,3}(?:,\d{3})*\.\d{2}(?!\s*%)")

# Anchor words whose adjacent amount is (almost always) the grand total.
# \b around "total" keeps "Subtotal" from matching.
_TOTAL_ANCHOR_RE = re.compile(
    r"\b(?:grand\s+)?total\b|\bamount\s+due\b|\bbalance\s+due\b", re.IGNORECASE
)

_TAX_EXCLUDE_RE = re.compile(
    r"tax\s+(?:included|free|exempt)|no\s+tax|tax\s*(?:-|–)?\s*exempt",
    re.IGNORECASE,
)


def _valid_date(y: int, m: int, d: int) -> bool:
    if m < 1 or m > 12 or d < 1 or y < 1900 or y > 2100:
        return False
    return d <= monthrange(y, m)[1]


def _amounts_in_line(line: str) -> list[str]:
    """Currency amounts on one line, normalized to plain decimal strings."""
    return [_normalize_amount(m.group()) for m in _AMOUNT_RE.finditer(line)]


def _normalize_amount(raw: str) -> str:
    """'$1,234.56' → '1234.56'. Keeps raw on parse failure (parser marks
    confidence low and the operator reviews anyway)."""
    cleaned = raw.replace("$", "").replace(",", "").strip()
    try:
        return f"{Decimal(cleaned):.2f}"
    except InvalidOperation:
        return cleaned


def _largest_amount(text: str) -> Optional[str]:
    best: Optional[tuple[Decimal, str]] = None
    for line in text.splitlines():
        for m in _AMOUNT_RE.finditer(line):
            cleaned = m.group().replace("$", "").replace(",", "").strip()
            try:
                val = Decimal(cleaned)
            except InvalidOperation:
                continue
            if best is None or val > best[0]:
                best = (val, f"{val:.2f}")
    return best[1] if best else None


def parse_date(text: str) -> Optional[str]:
    """First parseable date in the text (receipts print the purchase date
    near the top), US-first ordering, ISO YYYY-MM-DD out."""
    for line in text.splitlines():
        line = line.strip()
        # YYYY-MM-DD
        m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", line)
        if m:
            y, mo, d = (int(g) for g in m.groups())
            if _valid_date(y, mo, d):
                return f"{y:04d}-{mo:02d}-{d:02d}"
        # MM/DD/YYYY or MM/DD/YY (US-first)
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", line)
        if m:
            mo, d, y = (int(g) for g in m.groups())
            if y < 100:
                y += 2000
            if _valid_date(y, mo, d):
                return f"{y:04d}-{mo:02d}-{d:02d}"
        # "Aug 14, 2026" / "Aug 14 2026"
        m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b", line)
        if m and m.group(1).lower() in _MONTH_NAMES:
            mo = _MONTH_NAMES[m.group(1).lower()]
            d, y = int(m.group(2)), int(m.group(3))
            if _valid_date(y, mo, d):
                return f"{y:04d}-{mo:02d}-{d:02d}"
        # "14 Aug 2026"
        m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", line)
        if m and m.group(2).lower() in _MONTH_NAMES:
            d, mo, y = (
                int(m.group(1)),
                _MONTH_NAMES[m.group(2).lower()],
                int(m.group(3)),
            )
            if _valid_date(y, mo, d):
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def parse_total(text: str) -> tuple[Optional[str], str]:
    """(total, confidence) — anchor-first per spec §5.4: an amount on a
    TOTAL/AMOUNT DUE/BALANCE DUE line is high-confidence; otherwise the
    largest currency amount is a low-confidence fallback."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _TOTAL_ANCHOR_RE.search(line):
            amounts = _amounts_in_line(line)
            # The amount may sit on the next non-empty line
            # ("AMOUNT DUE\n$123.45"); peek up to two lines ahead —
            # never past the last line (an anchor as the final OCR line
            # with nothing after it crashed here; found by corpus eval).
            j = i
            while not amounts and j < min(i + 2, len(lines) - 1):
                j += 1
                amounts = _amounts_in_line(lines[j])
            if amounts:
                return amounts[0], "high"
    fallback = _largest_amount(text)
    return (fallback, "low") if fallback else (None, "missing")


def parse_tax(text: str) -> tuple[Optional[str], Optional[str]]:
    """(tax, subtotal) — anchored lines, tax-included/exempt excluded.

    Like parse_total, an anchor's amount may sit on the next line — the
    WinRT engine in particular splits "TAX 6.25%" and "2.89" into separate
    lines (seen on the VH308 hardware lap) — so peek up to two lines
    ahead, never past the last line."""
    lines = text.splitlines()

    def _anchored_amount(i: int) -> Optional[str]:
        amounts = _amounts_in_line(lines[i])
        j = i
        while not amounts and j < min(i + 2, len(lines) - 1):
            j += 1
            amounts = _amounts_in_line(lines[j])
        return amounts[0] if amounts else None

    tax: Optional[str] = None
    subtotal: Optional[str] = None
    for i, line in enumerate(lines):
        if subtotal is None and re.search(r"\bsubtotal\b", line, re.IGNORECASE):
            subtotal = _anchored_amount(i)
        if tax is None and re.search(r"\btax\b", line, re.IGNORECASE):
            if not _TAX_EXCLUDE_RE.search(line):
                tax = _anchored_amount(i)
    return tax, subtotal


def parse_merchant(text: str) -> tuple[Optional[str], str]:
    """(merchant, confidence) — the first non-empty line that looks like a
    name (2+ words, no currency amount, no date/phone/pure-number line).
    First-line-of-receipt hits are high-confidence; later hits low."""
    for idx, line in enumerate(ln.strip() for ln in text.splitlines()):
        if not line or len(line.split()) < 2:
            continue
        if _AMOUNT_RE.search(line):
            continue  # a totals/price line, not a merchant name
        if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", line):
            continue
        if re.search(r"\(\d{3}\)\s?\d{3}[-.\s]\d{4}", line):
            continue  # phone number
        if re.match(r"^[\d\s.,#-]+$", line):
            continue  # pure number / address number
        return line[:60], ("high" if idx == 0 else "low")
    return None, "missing"


def extract_receipt(text: str) -> dict:
    """Run every parser and assemble the extraction result + partial flags."""
    merchant_value, merchant_conf = parse_merchant(text)
    total, total_conf = parse_total(text)
    tax, subtotal = parse_tax(text)

    reasons: list[str] = []
    if total is None:
        reasons.append("total not detected")
    elif total_conf == "low":
        reasons.append("total is low-confidence — verify the amount")
    if merchant_value is None:
        reasons.append("merchant not detected")
    # Date-missing is reported by the route (it applies the today default).

    return {
        "merchant": {"value": merchant_value, "confidence": merchant_conf},
        "date": parse_date(text),
        "total": total,
        "total_confidence": total_conf,
        "subtotal": subtotal,
        "tax": tax,
        "tax_detected": tax is not None,
        "partial_reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Intake bucket — pending scans awaiting attachment to a saved document.
# Files live under uploads/intake (never served), expire after 24h, and are
# evicted oldest-first past 500 files / 1 GB.
# ---------------------------------------------------------------------------


def _intake_dir() -> Path:
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    return INTAKE_DIR


def save_intake(data: bytes, original_filename: str, mime_type: str) -> str:
    """Store a scan; returns the intake id. Sweeps expired entries first."""
    sweep_intake()
    intake_id = uuid4().hex
    base = _intake_dir()
    ext = Path(original_filename or "").suffix.lower()
    if ext not in _INTAKE_EXTS:
        ext = ".png"  # validated by the route; never a traversal vector
    stored_name = f"{intake_id}{ext}"
    stored_path = (base / stored_name).resolve()
    if not stored_path.is_relative_to(INTAKE_DIR.resolve()):
        raise ValueError("path escapes intake directory")
    stored_path.write_bytes(data)
    meta = {
        "intake_id": intake_id,
        "original_filename": Path(original_filename or "receipt").name,
        "stored_name": stored_name,
        "mime_type": mime_type,
        "size": len(data),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta_path = (base / f"{intake_id}.json").resolve()
    if not meta_path.is_relative_to(INTAKE_DIR.resolve()):
        raise ValueError("path escapes intake directory")
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return intake_id


def get_intake(intake_id: str) -> Optional[dict]:
    """Load an unexpired intake with its file bytes, or None."""
    if not _INTAKE_ID_RE.fullmatch(intake_id or ""):
        return None
    base = INTAKE_DIR
    meta_path = (base / f"{intake_id}.json").resolve()
    if not meta_path.is_relative_to(INTAKE_DIR.resolve()):
        return None
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        created = datetime.fromisoformat(meta["created_at"])
    except (KeyError, ValueError):
        delete_intake(intake_id)
        return None
    if created < datetime.now() - timedelta(hours=INTAKE_TTL_HOURS):
        delete_intake(intake_id)
        return None
    stored = Path(meta.get("stored_name") or f"{intake_id}.png").name
    file_path = (base / stored).resolve()
    if not file_path.is_relative_to(INTAKE_DIR.resolve()):
        return None
    if not file_path.is_file():
        return None
    try:
        data = file_path.read_bytes()
    except OSError:
        return None
    return {**meta, "data": data}


def delete_intake(intake_id: str) -> None:
    """Remove the stored file and sidecar (best-effort)."""
    if not _INTAKE_ID_RE.fullmatch(intake_id or ""):
        return
    for p in INTAKE_DIR.glob(f"{intake_id}.*"):
        if not p.resolve().is_relative_to(INTAKE_DIR.resolve()):
            continue
        try:
            p.unlink()
        except OSError:
            pass


def sweep_intake() -> int:
    """Expire >24h-old intakes, then enforce the file-count / byte caps.
    Returns how many were removed. Opportunistic: called on each save."""
    if not INTAKE_DIR.exists():
        return 0
    now = datetime.now()
    removed = 0

    entries: list[tuple[datetime, dict]] = []
    for meta_path in INTAKE_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            created = datetime.fromisoformat(meta["created_at"])
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            try:
                meta_path.unlink()
            except OSError:
                pass
            removed += 1
            continue
        entries.append((created, meta))

    # TTL
    for created, meta in list(entries):
        if created < now - timedelta(hours=INTAKE_TTL_HOURS):
            delete_intake(meta["intake_id"])
            removed += 1
            entries.remove((created, meta))

    # Caps (evict oldest first)
    entries.sort(key=lambda e: e[0])
    while entries and len(entries) > INTAKE_MAX_FILES:
        _, meta = entries.pop(0)
        delete_intake(meta["intake_id"])
        removed += 1
    if entries:
        total = sum(
            (INTAKE_DIR / Path(m.get("stored_name") or "x.png").name).stat().st_size
            for _, m in entries
            if (INTAKE_DIR / Path(m.get("stored_name") or "x.png").name).is_file()
        )
        while entries and total > INTAKE_MAX_BYTES:
            _, meta = entries.pop(0)
            size = _intake_size(meta)
            delete_intake(meta["intake_id"])
            total -= size
            removed += 1
    return removed


def _intake_size(meta: dict) -> int:
    name = Path(meta.get("stored_name") or "x.png").name
    try:
        return (INTAKE_DIR / name).stat().st_size
    except OSError:
        return 0
