from enum import Enum


class DocumentType(str, Enum):
    INVOICE = "invoice"
    RECEIPT = "receipt"
    CREDIT_NOTE = "credit_note"
    PURCHASE_ORDER = "purchase_order"
    STATEMENT = "statement"
    PROOF_OF_PAYMENT = "proof_of_payment"
    UNKNOWN = "unknown"


class ExtractionMethod(str, Enum):
    NATIVE_PDF = "native_pdf"
    OCR = "ocr"
    RULE = "rule"
    MODEL = "model"


class ValidationStatus(str, Enum):
    ACCEPTED = "accepted"
    REVIEW = "review"
    REJECTED = "rejected"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PLANNED = "planned"
    APPLIED = "applied"
    QUARANTINED = "quarantined"
    FAILED = "failed"
    SKIPPED = "skipped"


class FieldType(str, Enum):
    STRING = "string"
    DATE = "date"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    ORGANIZATION = "organization"


class SourceType(str, Enum):
    LOCAL = "local"
    SHAREPOINT = "sharepoint"


class MutationOp(str, Enum):
    RENAME = "rename"
    MOVE = "move"
    METADATA_UPDATE = "metadata_update"


class ReasonCode(str, Enum):
    # Validation reason codes
    MISSING_REQUIRED_FIELD = "missing_required_field"
    LOW_CONFIDENCE = "low_confidence"
    INVALID_DATE = "invalid_date"
    INVALID_AMOUNT = "invalid_amount"
    INVALID_CURRENCY = "invalid_currency"
    AMBIGUOUS_CURRENCY = "ambiguous_currency"
    CROSS_FIELD_INCONSISTENCY = "cross_field_inconsistency"
    # Document reason codes
    UNSUPPORTED_MIME = "unsupported_mime"
    CORRUPT_FILE = "corrupt_file"
    ENCRYPTED_FILE = "encrypted_file"
    EMPTY_FILE = "empty_file"
    OCR_QUALITY_TOO_LOW = "ocr_quality_too_low"
    # Pipeline reason codes
    ETAG_MISMATCH = "etag_mismatch"
    COLLISION = "collision"
    ALREADY_PROCESSED = "already_processed"
    POLICY_MISMATCH = "policy_mismatch"
