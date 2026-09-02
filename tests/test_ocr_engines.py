"""Engine seam (delivery-plan step 1.5): selection, TSV word extraction,
and the review fixes from #71 (tesseract '+' language join, 400 on
unreadable input).

The Vision/WinRT adapters are hardware-verify-pending and only their
selection/degrade logic is testable here; the tesseract path is fully
exercised (CI installs tesseract)."""

import shutil
from pathlib import Path

import pytest

from app.services import ocr_engines, ocr_service

_FIX_DIR = Path(__file__).parent / "fixtures" / "ocr"
FIXTURES = sorted(
    p for p in _FIX_DIR.glob("*") if p.suffix.lower() in (".png", ".jpg", ".pdf")
)
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".pdf": "application/pdf"}

SAMPLE_TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
1\t1\t0\t0\t0\t0\t0\t0\t400\t600\t-1\t
5\t1\t1\t1\t1\t1\t10\t12\t80\t20\t96.5\tACME
5\t1\t1\t1\t1\t2\t95\t12\t90\t20\t95.0\tSUPPLY
5\t1\t1\t1\t2\t1\t10\t40\t60\t18\t91.2\tTOTAL
5\t1\t1\t1\t2\t2\t80\t40\t70\t18\t93.0\t$49.13
5\t1\t2\t1\t1\t1\t10\t80\t95\t18\t88.8\tTHANKS
"""


def test_tsv_parser_reconstructs_lines_and_words():
    text, words = ocr_service._parse_tesseract_tsv(SAMPLE_TSV)
    lines = [ln for ln in text.splitlines() if ln]
    assert lines[0] == "ACME SUPPLY"
    assert lines[1] == "TOTAL $49.13"
    assert lines[2] == "THANKS"
    assert len(words) == 5
    total_word = next(w for w in words if w["text"] == "$49.13")
    assert (total_word["left"], total_word["top"]) == (80, 40)
    assert total_word["conf"] == 93.0


def test_reconstructed_text_still_parses():
    text, _ = ocr_service._parse_tesseract_tsv(SAMPLE_TSV)
    total, conf = ocr_service.parse_total(text)
    assert total == "49.13" and conf == "high"


def test_language_join_uses_plus(monkeypatch):
    """#71 review fix: tesseract -l is '+'-separated; ',' errors out."""
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5", "languages": ["fra", "spa"]},
    )
    assert ocr_service.ocr_language() == "fra+spa"


def test_engine_selection_env_override(monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "tesseract")
    assert ocr_engines.get_engine().name == "tesseract"
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "vision")
    engine = ocr_engines.get_engine()
    assert engine.name == "vision"
    # On Linux CI the Vision bridge can't import: it must degrade, not raise
    assert engine.available() is False
    assert engine.unavailable_reason()


def test_engine_selection_auto_falls_back_to_tesseract(monkeypatch):
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "auto")
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5", "languages": ["eng"]},
    )
    engine = ocr_engines.get_engine()
    assert engine.name == "tesseract"
    assert engine.available() is True


def test_status_endpoint_reports_engine(client, monkeypatch):
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5.5.0", "languages": ["eng"]},
    )
    body = client.get("/api/ocr/status").json()
    assert body["engine"] == "tesseract"
    assert body["available"] is True


needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract not installed"
)


@needs_tesseract
def test_unreadable_image_is_400_not_500(client, monkeypatch, tmp_path):
    """#71 review fix: junk bytes with a valid image MIME must 400."""
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    r = client.post(
        "/api/ocr/receipt",
        files={"file": ("junk.png", b"this is not a png", "image/png")},
    )
    assert r.status_code == 400
    assert "read the image" in r.json()["detail"]


@needs_tesseract
@pytest.mark.skipif(not FIXTURES, reason="no OCR fixtures")
def test_real_scan_returns_words_and_engine(client, monkeypatch, tmp_path):
    fixture = FIXTURES[0]
    if fixture.suffix.lower() == ".pdf" and shutil.which("pdftoppm") is None:
        pytest.skip("pdf fixture but poppler not installed")
    monkeypatch.setattr(ocr_service, "INTAKE_DIR", tmp_path)
    r = client.post(
        "/api/ocr/receipt",
        files={
            "file": (
                fixture.name,
                fixture.read_bytes(),
                _MIME[fixture.suffix.lower()],
            )
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "tesseract"
    assert body["words"], "expected word boxes from the tsv path"
    first = body["words"][0]
    assert {"text", "left", "top", "width", "height", "conf"} <= set(first)


def test_total_anchor_on_last_line_does_not_crash():
    """Corpus-found crash: a TOTAL anchor as the final OCR line with no
    amount after it indexed past the end of the line list."""
    total, conf = ocr_service.parse_total("SOME SHOP\nitems here\nTOTAL")
    assert total is None and conf == "missing"
    # Anchor on last line WITH an amount on it still works
    total, conf = ocr_service.parse_total("SOME SHOP\nTOTAL 12.34")
    assert total == "12.34" and conf == "high"
    # And the legitimate two-line-lookahead case is preserved
    total, conf = ocr_service.parse_total("AMOUNT DUE\n\n$123.45")
    assert total == "123.45" and conf == "high"


def test_recognize_rescales_boxes_to_original_space(monkeypatch):
    """preprocess_page upscales; recognize must divide word boxes back so
    the canvas draws in the original image's coordinates."""
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5", "languages": ["eng"]},
    )
    monkeypatch.setattr(ocr_service, "preprocess_page", lambda data: (data, 3))
    monkeypatch.setattr(
        ocr_service,
        "ocr_image_words",
        lambda data, lang=None: (
            "TOTAL 49.13",
            [
                {
                    "text": "49.13",
                    "left": 300,
                    "top": 90,
                    "width": 60,
                    "height": 30,
                    "conf": 90.0,
                }
            ],
        ),
    )
    result = ocr_engines.TesseractEngine().recognize(b"png")
    box = result.words[0]
    assert (box.left, box.top, box.width, box.height) == (100, 30, 20, 10)


def test_preprocess_page_survives_junk_bytes():
    data, factor = ocr_service.preprocess_page(b"not an image at all")
    assert data == b"not an image at all" and factor == 1


def test_engine_preference_from_settings(monkeypatch):
    """The stored ocr_engine setting steers selection; the env override
    (support tool) still outranks it; junk values fall through to auto."""
    from app.services import ocr_engines

    monkeypatch.delenv("SLOWBOOKS_OCR_ENGINE", raising=False)
    assert ocr_engines.get_engine("tesseract").name == "tesseract"
    # Unrecognized preference -> auto path (never raises)
    assert ocr_engines.get_engine("copperplate").name in (
        "tesseract",
        "vision",
        "winrt",
    )
    # Env override wins over the stored preference
    monkeypatch.setenv("SLOWBOOKS_OCR_ENGINE", "tesseract")
    assert ocr_engines.get_engine("winrt").name == "tesseract"


def test_status_endpoint_honors_setting(client, db_session, monkeypatch):
    """Setting ocr_engine=tesseract makes /api/ocr/status report tesseract
    even where a native engine would win auto-selection."""
    from app.services import ocr_service
    from app.services.settings_service import set_setting

    monkeypatch.setattr(ocr_service, "tesseract_available", lambda: True)
    monkeypatch.setattr(
        ocr_service,
        "tesseract_info",
        lambda: {"available": True, "version": "5.0-test", "languages": ["eng"]},
    )

    set_setting(db_session, "ocr_engine", "tesseract")
    db_session.commit()

    r = client.get("/api/ocr/status")
    assert r.status_code == 200
    assert r.json()["engine"] == "tesseract"


def test_run_winrt_coroutine_inside_and_outside_event_loop():
    """The WinRT bridge must complete its coroutine whether or not the
    caller already sits on a running event loop (async scan route vs sync
    region route) — the VH308 500-on-every-scan regression."""
    import asyncio

    from app.services.ocr_engines import _run_winrt_coroutine

    async def op():
        await asyncio.sleep(0)
        return "ok"

    # No running loop (sync route / CLI context)
    assert _run_winrt_coroutine(op) == "ok"

    # From inside a running loop (async def route)
    async def caller():
        return _run_winrt_coroutine(op)

    assert asyncio.run(caller()) == "ok"


# ---------------------------------------------------------------------------
# Reading order rebuilt from geometry (hardware finding, WinRT 2026-09-02)
# ---------------------------------------------------------------------------


def _wb(text, left, top, width=60, height=18):
    return ocr_engines.WordBox(
        text=text, left=left, top=top, width=width, height=height
    )


def test_lines_from_words_restores_page_order_from_region_order():
    # Exactly the shape Windows.Media.Ocr returned on hardware: left column,
    # then the amounts column, then the header line LAST, with the decimal
    # points tokenized as their own 5x4 boxes.
    words = [
        _wb("SUBTOTAL", 40, 432, 134),
        _wb("TAX", 41, 476, 50),
        _wb("6.25%", 108, 476, 83),
        _wb("TOTAL", 41, 520, 82),
        _wb("NEON", 42, 36, 63),
        _wb("PULSE", 126, 36, 81),
        _wb("TECHSHOP", 226, 36, 132),
        _wb("46.24", 343, 432, 83),
        _wb("2.89", 360, 476, 65),
        _wb("49", 343, 520, 31),
        _wb(".", 382, 534, 5, 4),
        _wb("13", 395, 520, 31),
    ]
    text = ocr_engines.lines_from_words(words)
    assert text.splitlines() == [
        "NEON PULSE TECHSHOP",
        "SUBTOTAL 46.24",
        "TAX 6.25% 2.89",
        "TOTAL 49.13",
    ]
    # ...and the downstream parsers see what they were tuned on.
    assert ocr_service.parse_total(text)[0] == "49.13"
    assert ocr_service.parse_tax(text) == ("2.89", "46.24")
    assert ocr_service.parse_merchant(text)[0] == "NEON PULSE TECHSHOP"


def test_lines_from_words_tolerates_skew_and_empty_input():
    assert ocr_engines.lines_from_words([]) == ""
    # A phone photo tilts the row: the right-hand amount sits 8px lower than
    # the anchor but still belongs to the same line.
    words = [_wb("TOTAL", 40, 100), _wb("12.34", 300, 108), _wb("THANKS", 40, 150)]
    assert ocr_engines.lines_from_words(words) == "TOTAL 12.34\nTHANKS"


def test_winrt_adapter_text_is_rebuilt_from_words(monkeypatch):
    class _Rect:
        def __init__(self, x, y, w, h):
            self.x, self.y, self.width, self.height = x, y, w, h

    class _Word:
        def __init__(self, text, x, y, w=60, h=18):
            self.text, self.bounding_rect = text, _Rect(x, y, w, h)

    class _Line:
        def __init__(self, *words):
            self.words = list(words)
            self.text = " ".join(w.text for w in words)

    class _Result:
        # Engine order: amounts column first, header last.
        lines = [
            _Line(_Word("TOTAL", 40, 100)),
            _Line(_Word("9.99", 300, 100)),
            _Line(_Word("SHOP", 40, 20)),
        ]

    engine = ocr_engines.WinRTEngine()
    monkeypatch.setattr(engine, "_bridge", lambda: (None, None, None, None))
    monkeypatch.setattr(
        ocr_engines,
        "_run_winrt_coroutine",
        lambda factory: ocr_engines._winrt_words(_Result()),
    )
    result = engine.recognize(b"not-a-real-png")
    assert result.text == "SHOP\nTOTAL 9.99"
    assert [w.text for w in result.words] == ["TOTAL", "9.99", "SHOP"]


# ---------------------------------------------------------------------------
# One apartment thread for the life of the process (SkyTech crash, build 44)
# ---------------------------------------------------------------------------


def test_winrt_calls_all_run_on_one_persistent_thread(monkeypatch):
    """Every WinRT touch — the status probe, the scan, the flattening of
    the result — must happen on the same long-lived thread. Per-call
    threads let the COM apartment die between scans, and the cached
    OcrEngine factory took the server child down with it."""
    import threading

    seen: list[int] = []

    class _Rect:
        x, y, width, height = 1, 2, 30, 10

    class _Word:
        text = "TOTAL"
        bounding_rect = _Rect()

    class _Line:
        words = [_Word()]

    class _Result:
        @property
        def lines(self):
            seen.append(threading.get_ident())
            return [_Line()]

    class _Lang:
        language_tag = "en-US"

    class _Engine:
        recognizer_language = _Lang()

        async def recognize_async(self, bitmap):
            seen.append(threading.get_ident())
            return _Result()

    class _WinOcr:
        @staticmethod
        def try_create_from_user_profile_languages():
            seen.append(threading.get_ident())
            return _Engine()

    class _Stream:
        def get_output_stream_at(self, _pos):
            return None

    class _Writer:
        def __init__(self, _out):
            pass

        def write_bytes(self, _data):
            pass

        async def store_async(self):
            return None

    class _Decoder:
        @staticmethod
        async def create_async(_stream):
            return _Decoder()

        async def get_software_bitmap_async(self):
            return object()

    inits: list[int] = []
    real_init = ocr_engines._winrt_thread_init

    def _init():
        inits.append(threading.get_ident())
        real_init()

    monkeypatch.setattr(ocr_engines, "_winrt_pool", None)
    monkeypatch.setattr(ocr_engines, "_winrt_thread_init", _init)
    monkeypatch.setattr(ocr_engines.sys, "platform", "win32")

    engine = ocr_engines.WinRTEngine()
    monkeypatch.setattr(
        engine, "_bridge", lambda: (_Decoder, _WinOcr, _Writer, _Stream)
    )

    assert engine.info()["languages"] == ["en-US"]
    first = engine.recognize(b"png")
    second = engine.recognize(b"png")
    assert first.words[0].text == "TOTAL" and second.text == "TOTAL"

    # probe + 2 x (create, recognize, flatten) = 7 touches, one thread,
    # not the caller's, initialized exactly once and still alive after.
    assert len(seen) == 7
    assert len(set(seen)) == 1
    assert seen[0] != threading.get_ident()
    assert inits == [seen[0]]
    assert any(t.ident == seen[0] and t.is_alive() for t in threading.enumerate())
