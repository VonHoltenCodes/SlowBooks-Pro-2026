# ============================================================================
# Region OCR — the v2 "box-to-fix" backend.
# See docs/design/receipt-intake.md § delivery plan step 2.
#
# A region is a user-drawn (or template-proposed) rectangle over the scanned
# image with a declared field type. Knowing the type unlocks OCR settings
# impossible on a whole receipt: crop to the box, upscale, boost contrast,
# run single-line PSM with a per-field character whitelist. This is the
# low-contrast answer — a known-type crop reads far better than a full page.
#
# Pillow is already in the dependency tree (WeasyPrint); no new pins.
# Region OCR is tesseract-config-driven today; when the native engines get
# region support they slot in behind the same service function.
# ============================================================================

import io
import subprocess
from typing import Optional

from app.services import ocr_service

# Per-field tesseract configuration: page-segmentation mode + charset.
# PSM 7 = single text line; PSM 6 = uniform block (merchant names can wrap).
FIELD_CONFIGS: dict[str, dict] = {
    "amount": {"psm": "7", "whitelist": "0123456789.,$"},
    "date": {"psm": "7", "whitelist": "0123456789/-.,: APMapmJanFebMrouyglSctNvDei"},
    "merchant": {"psm": "6", "whitelist": None},
    "text": {"psm": "6", "whitelist": None},
}

UPSCALE = 3  # 3x nearest-edge upscale rescues thermal-print strokes
MIN_REGION_PX = 4
MAX_REGION_RATIO = 1.0  # a region may cover up to the whole image


class RegionError(ValueError):
    """Bad region geometry or unreadable source image."""


def _load_image(data: bytes):
    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise RegionError(f"Could not read the stored scan image: {exc}") from exc
    img = ImageOps.exif_transpose(img)  # phone photos carry rotation in EXIF
    return img


def _prepare_region(img, left: int, top: int, width: int, height: int) -> bytes:
    """Crop -> grayscale -> upscale -> autocontrast -> binarize -> PNG bytes."""
    from PIL import Image, ImageOps

    iw, ih = img.size
    left = max(0, min(left, iw - 1))
    top = max(0, min(top, ih - 1))
    right = max(left + 1, min(left + width, iw))
    bottom = max(top + 1, min(top + height, ih))
    if right - left < MIN_REGION_PX or bottom - top < MIN_REGION_PX:
        raise RegionError("Region is too small to read")

    region = img.crop((left, top, right, bottom)).convert("L")
    region = region.resize(
        (region.width * UPSCALE, region.height * UPSCALE), Image.LANCZOS
    )
    # Autocontrast stretches faded thermal print to full range; the fixed
    # midpoint threshold after that is a serviceable Otsu stand-in without
    # adding a CV dependency.
    region = ImageOps.autocontrast(region, cutoff=1)
    region = region.point(lambda p: 255 if p > 140 else 0)
    out = io.BytesIO()
    region.save(out, format="PNG")
    return out.getvalue()


def ocr_region(
    image_data: bytes,
    left: int,
    top: int,
    width: int,
    height: int,
    field_type: str = "text",
    lang: Optional[str] = None,
) -> dict:
    """OCR one typed region of a scanned image.

    Returns {"text", "value", "field_type", "confidence"} where `value` is
    the field-normalized reading (amounts -> '1234.56', dates -> ISO) and
    `text` is the raw region text. Raises RegionError for geometry/image
    problems and ocr_service.OCRRuntimeError when tesseract fails.
    """
    config = FIELD_CONFIGS.get(field_type)
    if config is None:
        raise RegionError(
            f"Unknown field type '{field_type}' "
            f"(expected one of {sorted(FIELD_CONFIGS)})"
        )
    img = _load_image(image_data)
    png = _prepare_region(img, left, top, width, height)

    lang = lang or ocr_service.ocr_language()
    cmd = ["tesseract", "stdin", "stdout", "--psm", config["psm"]]
    if lang:
        cmd += ["-l", lang]
    if config["whitelist"]:
        cmd += ["-c", f"tessedit_char_whitelist={config['whitelist']}"]
    try:
        proc = subprocess.run(
            cmd,
            input=png,
            capture_output=True,
            timeout=ocr_service.OCR_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ocr_service.OCRRuntimeError("Region OCR timed out") from exc
    except OSError as exc:
        raise ocr_service.OCRRuntimeError(f"Could not run tesseract: {exc}") from exc
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
        raise ocr_service.OCRRuntimeError(f"Region OCR failed: {stderr}")
    text = (proc.stdout or b"").decode("utf-8", errors="replace").strip()

    value, confidence = _normalize(text, field_type)
    return {
        "text": text,
        "value": value,
        "field_type": field_type,
        "confidence": confidence,
    }


def _normalize(text: str, field_type: str):
    """Field-aware normalization of the raw region text."""
    line = " ".join(text.split())
    if not line:
        return None, "missing"
    if field_type == "amount":
        amounts = ocr_service._amounts_in_line(line)
        if amounts:
            return amounts[0], "high"
        # Whitelisted OCR can drop separators; a bare digit run like
        # "4913" is ambiguous — hand it back low-confidence.
        digits = line.replace("$", "").replace(",", "").strip()
        return (digits, "low") if digits else (None, "missing")
    if field_type == "date":
        iso = ocr_service.parse_date(line)
        return (iso, "high") if iso else (line, "low")
    # merchant / free text
    return line[:80], "high"
