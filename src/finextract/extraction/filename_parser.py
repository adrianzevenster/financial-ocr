"""Parse structured fields from the SA financial document naming convention.

Convention:  YYYYMMDD - VendorName - DocType [Ref] - RAmount - Entity.ext[.ext]

Examples:
    20190107 - Esri - Inv MNT016528 - R10,005.00 - Enterprise.pdf.pdf
    20181126 - Uber - Rec - R127.00 - Enterprise.pdf
    20171002 - Auberge Theresa Mischa Guesthouse - POP - R6,265.00 - Enterprise.pdf
    20190212 - domains.co.za - Statement 20190101 to 20190212 - R0.00 - Enterprise.pdf.pdf
    20190125 -Tremor Coffee - Rec 2019.01.023782 - R200.00 - Enterprise.pdf
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from finextract.domain import (
    DocumentType,
    Evidence,
    ExtractionMethod,
    ExtractionResult,
    FieldResult,
    FieldType,
)

log = structlog.get_logger(__name__)

# Strips one or more extensions: ".pdf.pdf", ".jpg.jpg", ".pdf", ".jpg"
_STRIP_EXT_RE = re.compile(r"(?:\.\w{2,5})+$")

# Core pattern — tolerates missing space after the first dash (Tremor Coffee edge case)
_SA_FILENAME_RE = re.compile(
    r"^(\d{8})"              # YYYYMMDD date
    r"\s*-\s*"               # separator (flexible spacing)
    r"(.+?)"                 # vendor name (lazy)
    r"\s+-\s+"               # separator
    r"(.+?)"                 # doc-type segment (lazy)
    r"\s+-\s+"               # separator
    r"(R\s*[\d\s,]+(?:[.,]\d{1,2})?)"  # amount: R prefix + number
    r"(?:\s+-\s+(.+?))?$",  # optional entity
    re.IGNORECASE,
)

# Patterns for doc-type segment → (DocumentType, optional ref)
_DOCTYPE_RULES: list[tuple[re.Pattern[str], DocumentType]] = [
    # Receipt indicators take priority when both Inv and Rec appear
    (re.compile(r"\bRec\b", re.IGNORECASE), DocumentType.RECEIPT),
    (re.compile(r"^Rec\b", re.IGNORECASE), DocumentType.RECEIPT),
    (re.compile(r"^POP\b", re.IGNORECASE), DocumentType.PROOF_OF_PAYMENT),
    (re.compile(r"^Statement\b", re.IGNORECASE), DocumentType.STATEMENT),
    (re.compile(r"^Inv\b", re.IGNORECASE), DocumentType.INVOICE),
    (re.compile(r"^Ref\b", re.IGNORECASE), DocumentType.INVOICE),
    (re.compile(r"^Booking\b", re.IGNORECASE), DocumentType.RECEIPT),
    (re.compile(r"^Order\b", re.IGNORECASE), DocumentType.RECEIPT),
]

# Extracts the primary reference number from the doc-type segment
_REF_PATTERNS: list[re.Pattern[str]] = [
    # "Inv MNT016528", "Inv 40705", "Inv 2557.201901.1"
    re.compile(r"^Inv\s+([A-Z0-9][\w.\-]+)", re.IGNORECASE),
    # "Rec 2019.01.023782"
    re.compile(r"^Rec\s+([A-Z0-9][\w.\-]+)", re.IGNORECASE),
    # "Booking 19925213", "Booking Ref UZMYBZ"
    re.compile(r"Booking\s+(?:Ref\s+)?([A-Z0-9][\w.\-]+)", re.IGNORECASE),
    # "Order 58"
    re.compile(r"Order\s+(\d+)", re.IGNORECASE),
    # "Ref NL0002.ENERTRAG"
    re.compile(r"^Ref\s+([A-Z0-9][\w.\-/]+)", re.IGNORECASE),
]


def _make_field(
    field_id: str,
    field_type: FieldType,
    raw: str,
    confidence: float = 0.97,
) -> FieldResult:
    evidence = Evidence(
        page=0,
        method=ExtractionMethod.RULE,
        confidence=confidence,
        text=f"[filename] {raw}",
    )
    return FieldResult(
        field_id=field_id,
        field_type=field_type,
        raw_value=raw,
        normalized_value=None,
        evidence=[evidence],
        confidence=confidence,
        is_required=False,
    )


def _parse_amount(raw_amount: str) -> str:
    """Strip R prefix and normalize spaces for the normalizer."""
    return raw_amount.strip().lstrip("Rr").strip()


def _classify_doctype_segment(segment: str) -> tuple[DocumentType, str | None]:
    """Return (DocumentType, reference_string | None) from the doc-type segment."""
    doc_type = DocumentType.UNKNOWN

    # Check rules in order — first match wins
    for pat, dtype in _DOCTYPE_RULES:
        if pat.search(segment):
            doc_type = dtype
            break

    # Extract reference number
    ref: str | None = None
    for pat in _REF_PATTERNS:
        m = pat.search(segment)
        if m:
            ref = m.group(1).strip()
            break

    return doc_type, ref


def parse_filename(filename: str) -> ExtractionResult | None:
    """Attempt to parse an SA-convention filename into an ExtractionResult.

    Returns None if the filename does not match the expected pattern.
    Confidence on filename-derived fields is 0.97 (human-authored, very reliable).
    """
    # Strip known double/single extensions
    stem = _STRIP_EXT_RE.sub("", Path(filename).name)

    m = _SA_FILENAME_RE.match(stem)
    if not m:
        log.debug("filename_parse_miss", filename=filename)
        return None

    date_str, vendor, doctype_seg, amount_raw, entity = (
        m.group(1),
        m.group(2).strip(),
        m.group(3).strip(),
        m.group(4).strip(),
        (m.group(5) or "").strip(),
    )

    doc_type, ref = _classify_doctype_segment(doctype_seg)

    log.debug(
        "filename_parsed",
        date=date_str,
        vendor=vendor,
        doctype=doc_type.value,
        ref=ref,
        amount=amount_raw,
        entity=entity,
    )

    CONF = 0.97
    fields: dict[str, FieldResult] = {}

    # Date: reformat YYYYMMDD → YYYY-MM-DD for the normalizer
    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    fields["invoice_date"] = _make_field("invoice_date", FieldType.DATE, iso_date, CONF)

    # Vendor / organization name
    if vendor:
        fields["organization_name"] = _make_field("organization_name", FieldType.ORGANIZATION, vendor, CONF)

    # Reference number (invoice / receipt / booking)
    if ref:
        fields["invoice_number"] = _make_field("invoice_number", FieldType.STRING, ref, CONF)

    # Amount: strip R prefix, pass raw SA decimal to normalizer
    amount_clean = _parse_amount(amount_raw)
    if amount_clean and re.search(r"\d", amount_clean):
        fields["total_amount"] = _make_field("total_amount", FieldType.DECIMAL, amount_clean, CONF)

    # Currency is always ZAR for this naming convention
    fields["currency"] = _make_field("currency", FieldType.CURRENCY, "ZAR", CONF)

    return ExtractionResult(
        document_type=doc_type,
        classification_confidence=CONF,
        fields=fields,
        extractor_version="filename-v1",
        schema_version="invoice-v1",
        page_count=0,
        raw_text_length=0,
    )
