"""Native PDF text extraction using PyMuPDF (fitz)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from finextract.domain import CorruptFileError, EmptyFileError, EncryptedFileError


@dataclass
class PageText:
    page_number: int  # 0-based
    text: str
    char_count: int
    block_count: int


@dataclass
class DocumentText:
    pages: list[PageText]
    total_chars: int
    is_native: bool  # True = digitally generated text; False = OCR or sparse/empty

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)


# Minimum characters per page to consider native text reliable
_MIN_CHARS_PER_PAGE = 20
# Minimum total characters across all pages before flagging for OCR
_MIN_TOTAL_CHARS = 50


def extract_native_text(path: Path) -> DocumentText:
    """Extract text from a PDF using PyMuPDF.

    Returns a :class:`DocumentText` with ``is_native=False`` if the document
    appears to be a scanned image (total text below threshold), signalling that
    the caller should fall back to OCR.

    Raises:
        EncryptedFileError: if the PDF is password-protected.
        CorruptFileError: if PyMuPDF cannot open the file.
        EmptyFileError: if the file is zero bytes.
    """
    if path.stat().st_size == 0:
        raise EmptyFileError(str(path))

    try:
        import fitz  # type: ignore[import-untyped]  # PyMuPDF
    except ImportError as exc:
        raise ImportError("pymupdf is required: pip install pymupdf") from exc

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise CorruptFileError(str(path), str(exc)) from exc

    if doc.is_encrypted:
        doc.close()
        raise EncryptedFileError(str(path))

    pages: list[PageText] = []
    total_chars = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        text: str = page.get_text("text")  # type: ignore[attr-defined]
        blocks = page.get_text("blocks")  # type: ignore[attr-defined]
        char_count = len(text.strip())
        pages.append(
            PageText(
                page_number=page_num,
                text=text,
                char_count=char_count,
                block_count=len(blocks),
            )
        )
        total_chars += char_count

    doc.close()

    is_native = total_chars >= _MIN_TOTAL_CHARS
    return DocumentText(pages=pages, total_chars=total_chars, is_native=is_native)


def _word_ratio(text: str) -> float:
    """Fraction of whitespace-separated tokens that are purely alphabetic (length >= 2)."""
    import re as _re
    tokens = text.split()
    if not tokens:
        return 0.0
    word_like = sum(1 for t in tokens if _re.match(r'^[a-zA-Z]{2,}$', t))
    return word_like / len(tokens)


def is_garbled(text: str, min_word_ratio: float = 0.30) -> bool:
    """Return True if extracted native text looks like garbled font encoding or corrupt OCR.

    PDFs that were scanned, OCR'd by the printer firmware, and then saved as
    PDF-with-embedded-text often contain character-soup that fools PyMuPDF into
    reporting high char counts while producing unreadable extraction.  The
    word-ratio heuristic catches this: fewer than ~30 % purely-alphabetic tokens
    strongly indicates corruption.
    """
    if not text or len(text) < 50:
        return False
    return _word_ratio(text) < min_word_ratio


def needs_ocr(doc_text: DocumentText, min_coverage_ratio: float = 0.1) -> bool:
    """Return ``True`` if *doc_text* is too sparse or garbled for reliable extraction."""
    if not doc_text.pages:
        return True
    if not doc_text.is_native:
        return True
    avg_chars = doc_text.total_chars / len(doc_text.pages)
    if avg_chars < (min_coverage_ratio * 500):
        return True
    # Garbled embedded text (corrupted font encoding) — fall back to OCR
    return is_garbled(doc_text.full_text)


def extract_images_for_ocr(path: Path) -> list[tuple[int, bytes]]:
    """Render each page of *path* to a PNG at 200 DPI for OCR processing.

    Works for both PDF and image files (fitz handles JPEG, PNG, TIFF, BMP, WEBP).

    Returns:
        List of ``(page_number, png_bytes)`` tuples (0-based page numbers).
    """
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError("pymupdf is required: pip install pymupdf") from exc

    if path.stat().st_size == 0:
        raise EmptyFileError(str(path))

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise CorruptFileError(str(path), str(exc)) from exc

    results: list[tuple[int, bytes]] = []
    matrix = fitz.Matrix(200 / 72, 200 / 72)  # 200 DPI

    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=matrix, alpha=False)  # type: ignore[attr-defined]
        png_bytes: bytes = pix.tobytes("png")
        results.append((page_num, png_bytes))

    doc.close()
    return results
