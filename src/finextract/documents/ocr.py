"""OCR processing via pytesseract + Pillow."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from .extraction import DocumentText, PageText


@dataclass
class OcrResult:
    page_number: int
    text: str
    confidence: float  # 0–100 Tesseract scale
    word_count: int


@dataclass
class OcrDocumentResult:
    pages: list[OcrResult]
    total_text: str = field(default="", init=False)
    average_confidence: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.total_text = "\n\n".join(p.text for p in self.pages)
        if self.pages:
            self.average_confidence = sum(p.confidence for p in self.pages) / len(self.pages)


def run_ocr(image_bytes: bytes, page_number: int, lang: str = "eng") -> OcrResult:
    """OCR a single image and return structured result with confidence.

    Uses ``pytesseract.image_to_data`` for per-word confidence, then
    ``image_to_string`` for the clean text.
    """
    try:
        import pytesseract  # type: ignore[import-untyped]
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "pytesseract and Pillow are required: pip install pytesseract Pillow"
        ) from exc

    img = Image.open(io.BytesIO(image_bytes))

    # Get per-word confidence data
    data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)

    confidences: list[float] = []
    word_count = 0
    for i, word_text in enumerate(data["text"]):
        word = str(word_text).strip()
        if not word:
            continue
        conf = data["conf"][i]
        try:
            conf_float = float(conf)
        except (ValueError, TypeError):
            continue
        if conf_float < 0:
            continue  # tesseract uses -1 for non-word blocks
        confidences.append(conf_float)
        word_count += 1

    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Clean text output
    text = pytesseract.image_to_string(img, lang=lang)

    return OcrResult(
        page_number=page_number,
        text=text.strip(),
        confidence=avg_confidence,
        word_count=word_count,
    )


def ocr_document(
    image_pages: list[tuple[int, bytes]], lang: str = "eng"
) -> OcrDocumentResult:
    """Run OCR on a list of ``(page_number, png_bytes)`` pairs.

    Pages are processed in order.  The ``total_text`` and ``average_confidence``
    are computed automatically in ``__post_init__``.
    """
    results: list[OcrResult] = []
    for page_number, png_bytes in image_pages:
        result = run_ocr(png_bytes, page_number=page_number, lang=lang)
        results.append(result)
    return OcrDocumentResult(pages=results)


def merge_ocr_into_document_text(
    native: DocumentText,
    ocr: OcrDocumentResult,
    min_native_chars: int = 20,
) -> DocumentText:
    """Merge OCR results into *native*, replacing sparse or garbled pages with OCR text.

    A native page is replaced when it has fewer than *min_native_chars* characters
    OR when its text appears garbled (corrupted font encoding).
    """
    from finextract.documents.extraction import is_garbled

    ocr_by_page: dict[int, OcrResult] = {r.page_number: r for r in ocr.pages}

    merged_pages: list[PageText] = []
    total_chars = 0

    for native_page in native.pages:
        if native_page.char_count >= min_native_chars and not is_garbled(native_page.text):
            merged_pages.append(native_page)
            total_chars += native_page.char_count
        else:
            ocr_page = ocr_by_page.get(native_page.page_number)
            if ocr_page:
                text = ocr_page.text
                char_count = len(text.strip())
                merged_pages.append(
                    PageText(
                        page_number=native_page.page_number,
                        text=text,
                        char_count=char_count,
                        block_count=ocr_page.word_count,
                    )
                )
                total_chars += char_count
            else:
                merged_pages.append(native_page)
                total_chars += native_page.char_count

    return DocumentText(pages=merged_pages, total_chars=total_chars, is_native=False)
