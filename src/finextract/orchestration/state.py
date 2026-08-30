from __future__ import annotations

from finextract.domain import ProcessingStatus, ReasonCode

VALID_TRANSITIONS: dict[ProcessingStatus, set[ProcessingStatus]] = {
    ProcessingStatus.PENDING: {
        ProcessingStatus.PROCESSING,
        ProcessingStatus.SKIPPED,
        ProcessingStatus.FAILED,
    },
    ProcessingStatus.PROCESSING: {
        ProcessingStatus.PLANNED,
        ProcessingStatus.QUARANTINED,
        ProcessingStatus.FAILED,
    },
    ProcessingStatus.PLANNED: {
        ProcessingStatus.APPLIED,
        ProcessingStatus.QUARANTINED,
        ProcessingStatus.FAILED,
    },
    ProcessingStatus.APPLIED: set(),
    ProcessingStatus.QUARANTINED: set(),
    ProcessingStatus.FAILED: set(),
    ProcessingStatus.SKIPPED: set(),
}

_REASON_MESSAGES: dict[ReasonCode, str] = {
    ReasonCode.MISSING_REQUIRED_FIELD: "one or more required fields could not be extracted",
    ReasonCode.LOW_CONFIDENCE: "extraction confidence is below the review threshold",
    ReasonCode.INVALID_DATE: "extracted date value is invalid",
    ReasonCode.INVALID_AMOUNT: "extracted amount value is invalid",
    ReasonCode.INVALID_CURRENCY: "extracted currency code is not a known ISO 4217 code",
    ReasonCode.AMBIGUOUS_CURRENCY: "currency is ambiguous and cannot be inferred safely",
    ReasonCode.CROSS_FIELD_INCONSISTENCY: "extracted fields are internally inconsistent",
    ReasonCode.UNSUPPORTED_MIME: "file type is not supported",
    ReasonCode.CORRUPT_FILE: "file is corrupt or cannot be read",
    ReasonCode.ENCRYPTED_FILE: "file is password-protected or encrypted",
    ReasonCode.EMPTY_FILE: "file is empty",
    ReasonCode.OCR_QUALITY_TOO_LOW: "OCR text quality is too low for reliable extraction",
    ReasonCode.ETAG_MISMATCH: "file was modified since it was downloaded",
    ReasonCode.COLLISION: "proposed filename already exists at the destination",
    ReasonCode.ALREADY_PROCESSED: "document has already been processed with this policy",
    ReasonCode.POLICY_MISMATCH: "document does not match the configured policy",
}


def assert_transition(from_status: ProcessingStatus, to_status: ProcessingStatus) -> None:
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Invalid state transition: {from_status.value!r} -> {to_status.value!r}. "
            f"Allowed from {from_status.value!r}: "
            f"{[s.value for s in allowed] or 'none (terminal state)'}"
        )


def quarantine_reason(reason_codes: list[ReasonCode]) -> str:
    if not reason_codes:
        return "quarantined: no specific reason recorded"
    parts = [_REASON_MESSAGES.get(rc, rc.value) for rc in reason_codes]
    return "quarantined: " + "; ".join(parts)
