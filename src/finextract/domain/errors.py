from __future__ import annotations

from .enums import ReasonCode


class FinextractError(Exception):
    """Base for all domain errors."""

    def __init__(self, message: str, reason_code: ReasonCode | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class DocumentError(FinextractError):
    """Raised when a document cannot be processed."""


class UnsupportedMimeError(DocumentError):
    def __init__(self, mime: str) -> None:
        super().__init__(f"Unsupported MIME type: {mime}", ReasonCode.UNSUPPORTED_MIME)
        self.mime = mime


class CorruptFileError(DocumentError):
    def __init__(self, path: str, detail: str = "") -> None:
        msg = f"Corrupt or unreadable file: {path}"
        if detail:
            msg += f" ({detail})"
        super().__init__(msg, ReasonCode.CORRUPT_FILE)


class EncryptedFileError(DocumentError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Encrypted/password-protected file: {path}", ReasonCode.ENCRYPTED_FILE)


class EmptyFileError(DocumentError):
    def __init__(self, path: str) -> None:
        super().__init__(f"File is empty: {path}", ReasonCode.EMPTY_FILE)


class OcrQualityError(DocumentError):
    def __init__(self, coverage: float, min_coverage: float) -> None:
        super().__init__(
            f"OCR text coverage {coverage:.1%} below minimum {min_coverage:.1%}",
            ReasonCode.OCR_QUALITY_TOO_LOW,
        )
        self.coverage = coverage


class ExtractionError(FinextractError):
    """Raised when extraction cannot produce a result."""


class ValidationError(FinextractError):
    """Raised when a value fails validation at parse time (not field-level quarantine)."""


class PolicyError(FinextractError):
    """Raised when a policy or schema config is invalid."""


class SourceError(FinextractError):
    """Raised when a source adapter encounters an error."""


class ETagMismatchError(SourceError):
    def __init__(self, item_id: str, expected: str, actual: str) -> None:
        super().__init__(
            f"eTag mismatch for {item_id}: expected {expected!r}, got {actual!r}",
            ReasonCode.ETAG_MISMATCH,
        )
        self.item_id = item_id
        self.expected = expected
        self.actual = actual


class CollisionError(FinextractError):
    def __init__(self, proposed_path: str) -> None:
        super().__init__(
            f"Filename collision at proposed path: {proposed_path}",
            ReasonCode.COLLISION,
        )
        self.proposed_path = proposed_path


class AuditError(FinextractError):
    """Raised when the audit store cannot persist state."""


class ConfigError(FinextractError):
    """Raised when configuration is missing or malformed."""
