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
import sys
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


@dataclass
class OcrResult:
    text: str
    words: list[WordBox] = field(default_factory=list)
    engine: str = "tesseract"
    language: Optional[str] = None


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
            self._bridge()
        except Exception:
            return {"available": False, "version": None, "languages": None}
        return {"available": True, "version": "macOS Vision", "languages": None}

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
            box = WordBox(
                text=text,
                left=int(x),
                top=int(top),
                width=int(w),
                height=int(h),
                conf=float(cands[0].confidence()) * 100.0,
            )
            lines.append((top, text, box))
        lines.sort(key=lambda item: item[0])
        return OcrResult(
            text="\n".join(item[1] for item in lines),
            words=[item[2] for item in lines],
            engine=self.name,
            language=lang,
        )


# ---------------------------------------------------------------------------
# Windows.Media.Ocr (Windows 10/11) via winsdk. Word-level boxes.
# HARDWARE-VERIFY-PENDING.
# ---------------------------------------------------------------------------


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
            engine = WinOcr.try_create_from_user_profile_languages()
            if engine is None:
                return {"available": False, "version": None, "languages": None}
            return {
                "available": True,
                "version": "Windows.Media.Ocr",
                "languages": [str(engine.recognizer_language.language_tag)],
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
            return await engine.recognize_async(bitmap)

        result = asyncio.run(_run())
        text_lines = []
        words: list[WordBox] = []
        for line in result.lines:
            text_lines.append(str(line.text))
            for word in line.words:
                r = word.bounding_rect
                words.append(
                    WordBox(
                        text=str(word.text),
                        left=int(r.x),
                        top=int(r.y),
                        width=int(r.width),
                        height=int(r.height),
                    )
                )
        return OcrResult(
            text="\n".join(text_lines),
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


def get_engine():
    """The engine to use right now. Never raises; the returned engine may
    report unavailable (routes turn that into the guidance envelope)."""
    override = os.environ.get("SLOWBOOKS_OCR_ENGINE", "auto").strip().lower()
    if override in _ENGINES:
        return _ENGINES[override]()
    native = _native_engine_for_platform()
    if native is not None and native.available():
        return native
    tess = TesseractEngine()
    if tess.available():
        return tess
    # Nothing works. Tesseract's reason is the actionable one everywhere
    # (the native engine ships with the OS or not at all).
    return tess


def engine_status() -> dict:
    """For /api/ocr/status: the active engine's info + which engine it is."""
    engine = get_engine()
    info = engine.info()
    info["engine"] = engine.name
    return info
