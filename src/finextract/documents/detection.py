"""MIME type detection for incoming document files."""

from __future__ import annotations

from pathlib import Path

from finextract.domain import UnsupportedMimeError

SUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "image/webp",
    }
)

_SUFFIX_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def detect_mime(path: Path) -> str:
    """Detect the MIME type of *path*.

    Uses python-magic if available (inspects file bytes), then falls back to
    suffix mapping.  Never raises; returns ``application/octet-stream`` for
    completely unknown types.
    """
    try:
        import magic  # type: ignore[import-untyped]

        mime = magic.from_file(str(path), mime=True)
        if mime:
            return mime
    except Exception:
        pass

    return _SUFFIX_MIME.get(path.suffix.lower(), "application/octet-stream")


def assert_supported(path: Path, mime: str) -> None:
    """Raise :class:`UnsupportedMimeError` if *mime* is not in ``SUPPORTED_MIMES``."""
    if mime not in SUPPORTED_MIMES:
        raise UnsupportedMimeError(mime)
