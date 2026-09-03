# ============================================================================
# OCR engine seam — platform-native engines behind one interface.
# See docs/design/receipt-intake.md § "OCR engine strategy".
#
# Engines: tesseract (Linux/Docker default; universal detect-if-present
# fallback), Apple Vision (macOS, preinstalled), Windows.Media.Ocr
# (Windows 10/11, preinstalled). Native adapters lazy-import their platform
# bridges (pyobjc / winsdk) which ship only in the frozen desktop builds —
# requirements.txt is untouched. Every adapter degrades to unavailable
# rather than raising at import time.
#
# NOTE: the Vision and WinRT adapters are written to the platform APIs but
# are HARDWARE-VERIFY-PENDING — they cannot execute on this repo's Linux CI.
# The tesseract path is fully covered by tests.
# ============================================================================

import asyncio
import os
import threading
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from app.services import ocr_service


@dataclass
class WordBox:
    """One recognized word (or line, for engines that report lines) with its
    pixel bounding box. The v2 canvas draws these."""

    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float = -1.0
    # dy/dx of the printed line this box sits on, when the engine knows it
    # (Vision: the observation's corner points; WinRT: its line grouping).
    # Positive = right edge lower. None = unknown; see _page_slope.
    slope: Optional[float] = None


@dataclass
class OcrResult:
    text: str
    words: list[WordBox] = field(default_factory=list)
    engine: str = "tesseract"
    language: Optional[str] = None


# ---------------------------------------------------------------------------
# Reading order. Native engines report lines in *region* order, not page
# order — hardware finding on Windows.Media.Ocr (2026-09-02): a receipt's
# header line came back AFTER the amounts column, so "merchant = first
# line" picked the street address and every "TOTAL" anchor sat a dozen
# lines away from its amount. The text parsers were tuned on tesseract's
# top-to-bottom output, so rebuild that ordering from box geometry instead
# of trusting the engine's sequence. Tesseract keeps its own assembly.
# ---------------------------------------------------------------------------

# WinRT tokenizes a decimal point as its own tiny word ("49 . 13", "24 .50").
_SPLIT_DECIMAL_RE = re.compile(r"(\d)\s*\.\s*(\d{2})\b")


# Page skew. A phone photo of a receipt is rarely square: at 2 degrees the
# right-hand amounts on a 2200 px receipt sit ~75 px below the labels on
# the same printed line, and a centre-tolerance row builder puts them on
# the wrong row — subtotal and tax silently take the wrong values while
# total survives (Keith's macOS lap on #73, 2026-09-02; measured wrong from
# 2 degrees, correct at 0 and 1). Pairwise word geometry cannot tell the
# true angle from "one row down" on a receipt with even line pitch, so the
# angle comes from what the engines already know about their own lines
# (WordBox.slope) and the centres are sheared back before grouping.
_MAX_SKEW_SLOPE = 0.27  # ~15 degrees: beyond that the photo is the problem


def _page_slope(words: list[WordBox]) -> float:
    """dy/dx of the printed lines: the width-weighted median of the slopes
    the engine reported per line. 0.0 for a square page or no evidence."""
    samples = sorted(
        (float(w.slope), float(max(w.width, 1)))
        for w in words
        if w.slope is not None and abs(w.slope) <= _MAX_SKEW_SLOPE
    )
    if len(samples) < 2:
        return 0.0
    total = sum(wg for _, wg in samples)
    acc = 0.0
    for sl, wg in samples:
        acc += wg
        if acc >= total / 2.0:
            return sl
    return samples[-1][0]


def lines_from_words(words: list[WordBox]) -> str:
    """Rebuild top-to-bottom, left-to-right text from word boxes.

    The page's skew is estimated first (see _page_slope) and every word's
    vertical centre is sheared back to a square page. A word then joins the
    current row when that corrected centre lies within ~60% of a line
    height of the row's centre; rows are read left to right.
    """
    if not words:
        return ""
    slope = _page_slope(words)
    ordered = sorted(
        words,
        key=lambda w: (
            w.top + w.height / 2.0 - slope * (w.left + w.width / 2.0),
            w.left,
        ),
    )
    rows: list[list[WordBox]] = []
    centres: list[float] = []
    heights: list[float] = []
    for w in ordered:
        cy = w.top + w.height / 2.0 - slope * (w.left + w.width / 2.0)
        if rows:
            tol = 0.6 * max(heights[-1], float(w.height), 1.0)
            if abs(cy - centres[-1]) <= tol:
                row = rows[-1]
                row.append(w)
                n = len(row)
                centres[-1] += (cy - centres[-1]) / n
                # Tiny punctuation boxes must not shrink the row height.
                heights[-1] = max(heights[-1], float(w.height))
                continue
        rows.append([w])
        centres.append(cy)
        heights.append(float(w.height))
    out = []
    for row in rows:
        row.sort(key=lambda w: w.left)
        line = " ".join(w.text.strip() for w in row if w.text and w.text.strip())
        line = _SPLIT_DECIMAL_RE.sub(r"\1.\2", line)
        if line:
            out.append(line)
    return "\n".join(out)


class EngineUnavailable(Exception):
    """The selected engine cannot run on this machine right now."""


# ---------------------------------------------------------------------------
# Tesseract — delegates to ocr_service's probe/invoke functions so the
# existing tests (which monkeypatch those) keep controlling behavior.
# ---------------------------------------------------------------------------


TESSERACT_MISSING_MESSAGE = (
    "Tesseract OCR is not installed. Install it to enable scanning "
    "(Ubuntu: sudo apt-get install tesseract-ocr; macOS: brew install "
    "tesseract; Windows: see Settings for an installer link)."
)
LANGUAGE_DATA_MESSAGE = (
    "Tesseract is installed but has no usable language data "
    "(expected at least 'eng'). Install the tesseract-ocr "
    "language packs and try again."
)
NATIVE_MISSING_MESSAGE = (
    "No OCR engine is available. The built-in platform engine could not "
    "start and Tesseract is not installed — install Tesseract to enable "
    "scanning (see Settings)."
)


class TesseractEngine:
    name = "tesseract"

    def unavailable_reason(self) -> Optional[str]:
        if not ocr_service.tesseract_available():
            return TESSERACT_MISSING_MESSAGE
        if ocr_service.ocr_language() is None:
            return LANGUAGE_DATA_MESSAGE
        return None

    def info(self) -> dict:
        raw = ocr_service.tesseract_info()
        return {
            "available": self.unavailable_reason() is None,
            "version": raw["version"],
            "languages": raw["languages"],
        }

    def available(self) -> bool:
        return self.unavailable_reason() is None

    def recognize(self, data: bytes, lang: Optional[str] = None) -> OcrResult:
        lang = lang or ocr_service.ocr_language()
        if lang is None:
            raise EngineUnavailable("tesseract has no usable language data")
        # Page enhancement (10% -> 33% total-field hits on real receipts);
        # boxes come back in the upscaled space and divide back down so the
        # canvas draws in the original image's coordinates.
        prepared, factor = ocr_service.preprocess_page(data)
        text, rows = ocr_service.ocr_image_words(prepared, lang=lang)
        if factor > 1:
            for row in rows:
                for key in ("left", "top", "width", "height"):
                    row[key] = int(round(row[key] / factor))
        words = [WordBox(**row) for row in rows]
        return OcrResult(text=text, words=words, engine=self.name, language=lang)


# ---------------------------------------------------------------------------
# Apple Vision (macOS) — VNRecognizeTextRequest via pyobjc.
# Reports line-level boxes (Vision observations are lines, not words).
# HARDWARE-VERIFY-PENDING.
# ---------------------------------------------------------------------------


class VisionEngine:
    name = "vision"

    def _bridge(self):
        import Quartz  # pyobjc-framework-Quartz (frozen mac build only)
        import Vision  # pyobjc-framework-Vision

        return Quartz, Vision

    def info(self) -> dict:
        if sys.platform != "darwin":
            return {"available": False, "version": None, "languages": None}
        try:
            _, Vision = self._bridge()
        except Exception:
            return {"available": False, "version": None, "languages": None}
        languages = None
        try:
            req = Vision.VNRecognizeTextRequest.alloc().init()
            langs, _err = req.supportedRecognitionLanguagesAndReturnError_(None)
            if langs:
                languages = [str(code) for code in langs]
        except Exception:
            languages = None  # the row says "—"; recognition still works
        return {"available": True, "version": "macOS Vision", "languages": languages}

    def available(self) -> bool:
        return self.info()["available"]

    def unavailable_reason(self) -> Optional[str]:
        return None if self.available() else NATIVE_MISSING_MESSAGE

    def recognize(self, data: bytes, lang: Optional[str] = None) -> OcrResult:
        Quartz, Vision = self._bridge()
        src = Quartz.CGImageSourceCreateWithData(data, None)
        if src is None:
            raise ocr_service.OCRRuntimeError("Could not decode the image")
        img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
        if img is None:
            raise ocr_service.OCRRuntimeError("Could not decode the image")
        width = Quartz.CGImageGetWidth(img)
        height = Quartz.CGImageGetHeight(img)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            img, None
        )
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(0)  # VNRequestTextRecognitionLevelAccurate
        request.setUsesLanguageCorrection_(False)  # receipts aren't prose
        ok, _err = handler.performRequests_error_([request], None)
        if not ok:
            raise ocr_service.OCRRuntimeError("Vision text recognition failed")

        lines: list[tuple[float, str, WordBox]] = []
        for obs in request.results() or []:
            cands = obs.topCandidates_(1)
            if not cands or not len(cands):
                continue
            text = str(cands[0].string())
            bb = obs.boundingBox()  # normalized, origin bottom-left
            x = bb.origin.x * width
            w = bb.size.width * width
            h = bb.size.height * height
            top = (1.0 - bb.origin.y - bb.size.height) * height
            slope = None
            try:
                # VNRecognizedTextObservation is a VNRectangleObservation:
                # its corners give the line's tilt for free (y is up there,
                # down in pixel space — hence the sign flip).
                tl, tr = obs.topLeft(), obs.topRight()
                run = (tr.x - tl.x) * width
                if run > 0:
                    slope = -((tr.y - tl.y) * height) / run
            except Exception:
                slope = None
            box = WordBox(
                text=text,
                left=int(x),
                top=int(top),
                width=int(w),
                height=int(h),
                conf=float(cands[0].confidence()) * 100.0,
                slope=slope,
            )
            lines.append((top, text, box))
        lines.sort(key=lambda item: item[0])
        boxes = [item[2] for item in lines]
        return OcrResult(
            text=lines_from_words(boxes),
            words=boxes,
            engine=self.name,
            language=lang,
        )


# ---------------------------------------------------------------------------
# One WinRT thread for the life of the process.
#
# Hardware finding (SkyTech, build 44, 2026-09-02): the server child died
# with an access violation inside _winrt_windows_media_ocr.pyd on the
# SECOND scan of a session — every time. cppwinrt caches the OcrEngine
# activation factory in a static; when the throwaway thread that made the
# first call exits, the COM apartment it implicitly joined is torn down,
# the in-proc factory goes with it, and the next call jumps through a
# dangling pointer (no Python traceback — the GUI just reports "network
# error"). So: every WinRT call runs on ONE persistent worker that joins
# the multithreaded apartment explicitly and never exits, and nothing
# WinRT-owned leaves that thread — results come back as plain WordBoxes.
# ---------------------------------------------------------------------------

_winrt_pool: Optional[ThreadPoolExecutor] = None
_winrt_pool_lock = threading.Lock()
_winrt_thread_ident: Optional[int] = None


def _winrt_thread_init():
    """Runs once on the worker before its first task. Joining the MTA from
    a thread that lives as long as the server keeps the apartment (and the
    cached factories) alive; the thread's ident lets re-entrant calls run
    inline instead of deadlocking on their own executor."""
    global _winrt_thread_ident
    _winrt_thread_ident = threading.get_ident()
    try:
        from winrt.runtime import ApartmentType, init_apartment
    except ImportError:
        # winsdk (older projection) initializes the apartment on import.
        return
    try:
        init_apartment(ApartmentType.MULTI_THREADED)
    except Exception:
        # Already initialized (or a different model on this thread) —
        # either way the apartment exists and this thread pins it.
        pass


def _winrt_executor() -> ThreadPoolExecutor:
    global _winrt_pool
    with _winrt_pool_lock:
        if _winrt_pool is None:
            _winrt_pool = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="winrt-ocr",
                initializer=_winrt_thread_init,
            )
        return _winrt_pool


def _on_winrt_thread(fn):
    """Run `fn()` on the persistent WinRT thread and return its result."""
    if threading.get_ident() == _winrt_thread_ident:
        return fn()
    return _winrt_executor().submit(fn).result()


def _run_winrt_coroutine(factory):
    """Run a WinRT async operation to completion from ANY calling context.

    asyncio.run() works only when no event loop is running — the scan
    route is `async def` and lives ON the loop, where it raises (the VH308
    500-on-every-scan regression). The coroutine is therefore created AND
    run on the WinRT worker, whose own short-lived loop is fine because the
    thread underneath it — the one that owns the apartment — persists.
    """
    return _on_winrt_thread(lambda: asyncio.run(factory()))


# ---------------------------------------------------------------------------
# Windows.Media.Ocr (Windows 10/11) via the winrt-* projection. Word-level boxes.
# Hardware-validated on Windows 11 (2026-09-02).
# ---------------------------------------------------------------------------


def _winrt_words(result) -> list[WordBox]:
    """OcrResult lines/words → plain WordBoxes (engine order preserved)."""
    words: list[WordBox] = []
    for line in result.lines:
        line_words = []
        for word in line.words:
            r = word.bounding_rect
            line_words.append(
                WordBox(
                    text=str(word.text),
                    left=int(r.x),
                    top=int(r.y),
                    width=int(r.width),
                    height=int(r.height),
                )
            )
        # The engine's own line grouping is the skew evidence: a line whose
        # words span a few line heights gives a usable tilt (see _page_slope).
        if len(line_words) >= 2:
            first = min(line_words, key=lambda b: b.left)
            last = max(line_words, key=lambda b: b.left + b.width)
            run = (last.left + last.width / 2.0) - (first.left + first.width / 2.0)
            tall = max(first.height, last.height, 1)
            if run >= 4.0 * tall:
                rise = (last.top + last.height / 2.0) - (first.top + first.height / 2.0)
                for b in line_words:
                    b.slope = rise / run
        words.extend(line_words)
    return words


class WinRTEngine:
    name = "winrt"

    def _bridge(self):
        # The maintained WinRT projection is the `winrt-*` namespace
        # packages (cp313+ wheels); `winsdk` (same API, older) is the
        # fallback for existing installs. Identical class surface.
        # Frozen-build pins (hardware-validated on real Windows 11,
        # 2026-09-02): winrt-runtime + winrt-Windows.{Media.Ocr,
        # Graphics.Imaging, Storage.Streams, Foundation,
        # Foundation.Collections, Globalization} — Collections is split
        # into its own wheel and iterating OcrResult.lines needs it.
        try:
            from winrt.windows.graphics.imaging import BitmapDecoder
            from winrt.windows.media.ocr import OcrEngine as WinOcr
            from winrt.windows.storage.streams import (
                DataWriter,
                InMemoryRandomAccessStream,
            )
        except ImportError:
            from winsdk.windows.graphics.imaging import BitmapDecoder
            from winsdk.windows.media.ocr import OcrEngine as WinOcr
            from winsdk.windows.storage.streams import (
                DataWriter,
                InMemoryRandomAccessStream,
            )

        return BitmapDecoder, WinOcr, DataWriter, InMemoryRandomAccessStream

    def info(self) -> dict:
        if sys.platform != "win32":
            return {"available": False, "version": None, "languages": None}
        try:
            _, WinOcr, _, _ = self._bridge()

            def _probe():
                engine = WinOcr.try_create_from_user_profile_languages()
                if engine is None:
                    return None
                return str(engine.recognizer_language.language_tag)

            tag = _on_winrt_thread(_probe)
            if tag is None:
                return {"available": False, "version": None, "languages": None}
            return {
                "available": True,
                "version": "Windows.Media.Ocr",
                "languages": [tag],
            }
        except Exception:
            return {"available": False, "version": None, "languages": None}

    def available(self) -> bool:
        return self.info()["available"]

    def unavailable_reason(self) -> Optional[str]:
        return None if self.available() else NATIVE_MISSING_MESSAGE

    def recognize(self, data: bytes, lang: Optional[str] = None) -> OcrResult:
        BitmapDecoder, WinOcr, DataWriter, Stream = self._bridge()

        async def _run():
            stream = Stream()
            writer = DataWriter(stream.get_output_stream_at(0))
            writer.write_bytes(data)
            await writer.store_async()
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            engine = WinOcr.try_create_from_user_profile_languages()
            if engine is None:
                raise EngineUnavailable("No OCR language available in Windows")
            # Flatten on the WinRT thread: no OcrResult/OcrWord proxies
            # cross back to the caller, so their releases happen here too.
            return _winrt_words(await engine.recognize_async(bitmap))

        words = _run_winrt_coroutine(_run)
        return OcrResult(
            text=lines_from_words(words),
            words=words,
            engine=self.name,
            language=lang,
        )


# ---------------------------------------------------------------------------
# Selection — platform-native first, tesseract as the universal fallback.
# SLOWBOOKS_OCR_ENGINE=tesseract|vision|winrt|auto overrides (support tool
# and test hook, not a documented user setting).
# ---------------------------------------------------------------------------

_ENGINES = {
    "tesseract": TesseractEngine,
    "vision": VisionEngine,
    "winrt": WinRTEngine,
}


def _native_engine_for_platform():
    if sys.platform == "darwin":
        return VisionEngine()
    if sys.platform == "win32":
        return WinRTEngine()
    return None


def get_engine(preference: str | None = None):
    """The engine to use right now. Never raises; the returned engine may
    report unavailable (routes turn that into the guidance envelope).

    `preference` is the stored Settings choice ("auto"/"tesseract"/...);
    the SLOWBOOKS_OCR_ENGINE env var (support tool) outranks it, and
    anything unrecognized falls through to auto-selection.
    """
    override = os.environ.get("SLOWBOOKS_OCR_ENGINE", "auto").strip().lower()
    if override in _ENGINES:
        return _ENGINES[override]()
    pref = (preference or "auto").strip().lower()
    if pref in _ENGINES:
        return _ENGINES[pref]()
    native = _native_engine_for_platform()
    if native is not None and native.available():
        return native
    tess = TesseractEngine()
    if tess.available():
        return tess
    # Nothing works. Tesseract's reason is the actionable one everywhere
    # (the native engine ships with the OS or not at all).
    return tess


def engine_status(preference: str | None = None) -> dict:
    """For /api/ocr/status: the active engine's info + which engine it is."""
    engine = get_engine(preference)
    info = engine.info()
    info["engine"] = engine.name
    return info
