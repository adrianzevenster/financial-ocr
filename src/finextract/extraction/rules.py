from __future__ import annotations

import re
from typing import Any

import structlog

from finextract.domain import (
    DocumentType,
    Evidence,
    ExtractionMethod,
    ExtractionResult,
    FieldResult,
    FieldSchema,
    FieldType,
    PolicyConfig,
)

from .base import DocumentExtractor, register_extractor
from .classifier import KeywordClassifier

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Pattern tables: (compiled_pattern, confidence)
# ---------------------------------------------------------------------------

_INVOICE_NUMBER_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    # Must be labelled — "invoice no", "invoice #", etc. (not bare "invoice" → avoids capturing "oice")
    (re.compile(r"invoice\s*(?:#|no\.?|number|num\.?)\s*[:\-]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.92),
    # \binv\b — word boundary prevents matching inside "invoice" and capturing "oice"
    (re.compile(r"\binv\b\.?\s*(?:no\.?|#)?\s*[:\-]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.85),
    (re.compile(r"bill\s*(?:no\.?|number)\s*[:\-]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.80),
    (re.compile(r"pro[-\s]?forma\s+invoice\s*[:\-#]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.85),
]

# Receipt / booking reference numbers (for receipts that have no invoice number)
_RECEIPT_NUMBER_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"sale\s*[:#]?\s*([A-Z0-9][\w\-./]{2,30})", re.IGNORECASE), 0.85),
    (re.compile(r"receipt\s*(?:no\.?|#|number)?\s*[:\-]?\s*([A-Z0-9][\w\-./]{2,30})", re.IGNORECASE), 0.85),
    # "booking" alone is too broad — require an explicit ref keyword OR a colon/# separator
    (re.compile(r"booking\s*(?:(?:no\.?|number|#|ref(?:erence)?)\s*[:\-]?\s*|[:\-#]\s*)([A-Z0-9][\w\-./]{2,30})", re.IGNORECASE), 0.82),
    (re.compile(r"order\s*(?:no\.?|#|number)?\s*[:\-]?\s*(\d{2,20})", re.IGNORECASE), 0.80),
    (re.compile(r"transaction\s*(?:no\.?|#|ref(?:erence)?)?\s*[:\-]?\s*([A-Z0-9][\w\-./]{4,30})", re.IGNORECASE), 0.78),
    (re.compile(r"tran\s*#\s*(\d{3,20})", re.IGNORECASE), 0.75),
    (re.compile(r"trace\s*no\.?\s*[-:\s]*([A-Z0-9]{3,20})", re.IGNORECASE), 0.75),
    (re.compile(r"ref(?:erence)?\s*[:\-]?\s*([A-Z0-9][\w\-./]{3,30})", re.IGNORECASE), 0.70),
]

_ORGANIZATION_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"(?:from|issued\s+by|supplier|vendor|billed?\s+by)\s*[:\-]?\s*\n?\s*([A-Z][^\n]{3,60})", re.IGNORECASE), 0.85),
    (re.compile(r"([A-Z][a-zA-Z &,.'()-]{5,60})\s*(?:Ltd\.?|Limited|Inc\.?|LLC|GmbH|B\.?V\.?|SAS|S\.?A\.?|AG|PLC|Corp\.?|CC|Pty\.?\s*Ltd\.?)", re.IGNORECASE), 0.88),
    # South African: (Pty) Ltd / CC / NPC
    (re.compile(r"([A-Z][a-zA-Z &,.'()-]{3,60})\s*\(\s*Pty\s*\)\s*Ltd", re.IGNORECASE), 0.90),
    (re.compile(r"([A-Z][a-zA-Z &,.'()-]{3,60})\s+CC\b", re.IGNORECASE), 0.88),
    (re.compile(r"([A-Z][a-zA-Z &,.'()-]{3,60})\s+Inc\b", re.IGNORECASE), 0.85),
    # T/A (trading as) — picks the trading name
    (re.compile(r"T/A\s+([A-Z][^\n]{3,60})", re.IGNORECASE), 0.85),
]

_INVOICE_DATE_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"(?:invoice\s+)?date\s*[:\-]?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", re.IGNORECASE), 0.92),
    (re.compile(r"date\s*[:\-]?\s*(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})", re.IGNORECASE), 0.90),
    (re.compile(r"(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})", re.IGNORECASE), 0.88),
    (re.compile(r"(\d{4}-\d{2}-\d{2})"), 0.80),
    # e.g. "23-Jan-19", "07-Jan-2019"
    (re.compile(r"(\d{1,2}[-/](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[-/]\d{2,4})", re.IGNORECASE), 0.85),
    # e.g. "Mon, Nov 26, 2018" — Uber-style
    (re.compile(r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})", re.IGNORECASE), 0.82),
    # "25 January 2019" format appearing inline ("on 25 January 2019")
    (re.compile(r"\bon\s+(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})", re.IGNORECASE), 0.86),
    # Date formats like "12/14/2018" (US-style in email headers, Kiwi.com)
    (re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})", re.MULTILINE), 0.70),
]

# ZAR/SA amounts — handles R-prefix, space-thousands, comma-decimal formats
_ZAR_TOTAL_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    # "Total: R127.00" / "Total: 204 412,50" / "Amount Due R2,440.00"
    # Negative lookbehind for "sub" (4 chars fixed-width) prevents matching "Sub Total"
    (re.compile(
        r"(?:total\s+(?:amount\s+)?(?:due|payable|incl\.?\s*(?:vat|tax)?)|amount\s+(?:due|charged|paid)|grand\s+total|(?<!sub\s)(?<!SUB\s)total)\s*[:\-]?\s*R?\s*([\d]{1,3}(?:[\s,]\d{3})*(?:[.,]\d{1,2})?|\d+[.,]\d{1,2}|\d+)",
        re.IGNORECASE,
    ), 0.93),
    # "R127.00" / "R 8 000,00" appearing on same line as "total", "subtotal", or "amount"
    (re.compile(
        r"(?:total|subtotal|amount)\b[^\n]*?R\s*([\d\s,]+\.\d{2})\b",
        re.IGNORECASE,
    ), 0.88),
    # Bare "R <amount>" with word boundary — lower confidence (avoid balance-sheet noise)
    (re.compile(r"\bR\s*([\d]{1,3}(?:[\s,]\d{3})*(?:[.,]\d{1,2})?)\b"), 0.60),
]

# SA amount group: handles space-thousands ("204 412,50"), comma-thousands ("204,412.50"),
# and period-decimal ("204412.50"), with optional R/currency prefix.
_SA_AMOUNT_GROUP = r"R?\s*([\d]{1,3}(?:[\s,]\d{3})*(?:[.,]\d{1,2})?|\d+[.,]\d{1,2}|\d+)"

_TOTAL_AMOUNT_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(
        r"(?:total\s+(?:amount\s+)?due|amount\s+due|total\s+payable|grand\s+total|total)\s*[:\-]?\s*[£€$¥₹]?\s*" + _SA_AMOUNT_GROUP,
        re.IGNORECASE,
    ), 0.90),
    (re.compile(
        r"(?:balance\s+due|net\s+total)\s*[:\-]?\s*[£€$¥₹]?\s*" + _SA_AMOUNT_GROUP,
        re.IGNORECASE,
    ), 0.85),
]

# Fallback: find the largest monetary amount in the document (ZAR R prefix or ISO-tagged)
_MONEY_AMOUNT_RE = re.compile(
    r"[£€$¥₹]\s*([\d,\s]+\.?\d*)"
    r"|(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*(?:EUR|USD|GBP|JPY|CHF|AUD|CAD|ZAR)\b"
    r"|\bR\s*([\d]{1,3}(?:[\s,]\d{3})*(?:[.,]\d{1,2})?)\b",
    re.IGNORECASE,
)

# Currency detection
_ISO_CURRENCY_RE = re.compile(
    r"\b(EUR|USD|GBP|JPY|CHF|AUD|CAD|CNY|INR|SEK|NOK|DKK|PLN|CZK|HUF|RON|MXN|BRL|SGD|HKD|NZD|ZAR|TRY|RUB|TWD|KRW|THB|MYR|IDR|PHP|VND)\b",
    re.IGNORECASE,
)

# "Amounts are in Rand" or "(R)" column header or "ZAR" ISO code
_ZAR_CONTEXT_RE = re.compile(r"\b(?:rand|ZAR|South\s*African\s*Rand)\b|\(R\)", re.IGNORECASE)

# R prefix for ZAR amounts (word boundary to avoid false matches on e.g. "Received")
_ZAR_PREFIX_RE = re.compile(r"\bR\s*\d")

_SYMBOL_TO_CODE: dict[str, str] = {
    "£": "GBP",
    "€": "EUR",
    "$": "USD",
    "¥": "JPY",
    "₹": "INR",
}

_SYMBOL_RE = re.compile(r"[£€$¥₹]")

_DUE_DATE_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"(?:due\s+date|payment\s+due|pay\s+by)\s*[:\-]?\s*(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})", re.IGNORECASE), 0.90),
    (re.compile(r"(?:due\s+date|payment\s+due)\s*[:\-]?\s*(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})", re.IGNORECASE), 0.88),
    (re.compile(r"(?:due\s+date|payment\s+due)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE), 0.85),
]

_PO_NUMBER_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    # Negative lookahead prevents matching "P.O. Box" (street addresses)
    (re.compile(r"(?:purchase\s+order|p\.?o\.?)(?!\s*[Bb]ox)\s*(?:#|no\.?|number)?\s*[:\-]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.88),
    (re.compile(r"(?:order\s+ref(?:erence)?)\s*[:\-]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.80),
    (re.compile(r"(?:client\s+order\s+no\.?|customer\s+order\s+no\.?)\s*[:\-]?\s*([A-Z0-9][\w\-./]{1,30})", re.IGNORECASE), 0.82),
]

_VAT_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    # SA VAT: 10-digit number starting with 4, optionally labelled
    (re.compile(r"(?:vat|tax)\s*(?:reg(?:istration)?\s*)?(?:no\.?|number|#)?\s*[:\-]?\s*(4\d{9})\b", re.IGNORECASE), 0.92),
    # Generic labelled VAT
    (re.compile(r"(?:vat|tax)\s*(?:no\.?|number|reg\.?|registration)\s*[:\-]?\s*([A-Z]{0,2}[\d\s]{5,20})", re.IGNORECASE), 0.85),
    # EU-style VAT with country prefix
    (re.compile(r"\b([A-Z]{2}\d{9,12})\b"), 0.80),
    # Bare 10-digit SA VAT (fallback)
    (re.compile(r"\b(4\d{9})\b"), 0.65),
]

_RECIPIENT_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    # "to" alone is too broad (matches mid-word); require explicit billing/sales context
    # Use \b word boundary and require the capture to start with a true uppercase letter
    (re.compile(r"(?:bill(?:ed)?\s+to|sold\s+to|invoice(?:d)?\s+to|client\s+name|customer\s+name|recipient)\s*[:\-]?\s*\n?\s*([A-Z][^\n]{3,60})", re.IGNORECASE), 0.80),
    # SA: "For Attention:" on invoices addressed to a company
    (re.compile(r"(?:for\s+attention|attn)\s*[:\-]?\s*([A-Z][^\n]{3,60})", re.IGNORECASE), 0.72),
]

# Lines that are email headers — skip these when extracting org names
_EMAIL_HEADER_RE = re.compile(
    r"^(?:from|to|cc|bcc|sent|subject|date|reply-to)\s*:",
    re.IGNORECASE | re.MULTILINE,
)

# Lines that look like addresses / phone numbers — skip for org name
_ADDRESS_LINE_RE = re.compile(r"\d{1,5}\s+\w+\s+(?:street|road|avenue|ave|drive|lane|rd|st|blvd|way|close|circle)", re.IGNORECASE)
_PHONE_LINE_RE = re.compile(r"(?:tel|fax|phone)\s*[:\-]?\s*[\+\d]", re.IGNORECASE)

# Used in the multi-line invoice number fallback to skip date values
_DATE_VALUE_RE = re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$|\d{4}-\d{2}-\d{2}$")
# Code-like value: no spaces, mixed alphanumeric with allowed separators
_CODE_VALUE_RE = re.compile(r"^[A-Z0-9][\w\-./]{2,30}$", re.IGNORECASE)


def _scan_for_invoice_number_after_label(text: str, max_lines: int = 10) -> FieldResult | None:
    """Fallback for documents where the invoice number appears on a separate line from
    the label (e.g., IPIC-style statements with columnar layout).

    Finds the first invoice-label line, then scans the next *max_lines* lines for the
    first code-like value that contains a digit and isn't a date.
    """
    label_re = re.compile(r"(?:invoice|tax\s+invoice)\s*(?:no\.?|number|#)", re.IGNORECASE)
    m = label_re.search(text)
    if not m:
        return None
    remaining_lines = text[m.end():].splitlines()
    for line in remaining_lines[:max_lines]:
        candidate = line.strip()
        if not candidate:
            continue
        if not _CODE_VALUE_RE.match(candidate):
            continue
        if not re.search(r"\d", candidate):
            continue
        if _DATE_VALUE_RE.match(candidate):
            continue
        evidence = Evidence(
            page=1,
            method=ExtractionMethod.RULE,
            confidence=0.70,
            text=candidate,
        )
        return _make_field_result("invoice_number", FieldType.STRING, candidate, evidence, 0.70)
    return None


def _first_match(
    text: str,
    patterns: list[tuple[re.Pattern[str], float]],
    page: int = 1,
) -> FieldResult | None:
    """Return a FieldResult for the first matching pattern, or None."""
    for pat, conf in patterns:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip()
            if raw:
                evidence = Evidence(
                    page=page,
                    method=ExtractionMethod.RULE,
                    confidence=conf,
                    text=m.group(0).strip(),
                    span=(m.start(1), m.end(1)),
                )
                return _make_field_result("", FieldType.STRING, raw, evidence, conf)
    return None


def _make_field_result(
    field_id: str,
    field_type: FieldType,
    raw_value: str,
    evidence: Evidence,
    confidence: float,
    is_required: bool = False,
) -> FieldResult:
    return FieldResult(
        field_id=field_id,
        field_type=field_type,
        raw_value=raw_value,
        normalized_value=None,
        evidence=[evidence],
        confidence=confidence,
        is_required=is_required,
    )


def _extract_currency(text: str) -> FieldResult | None:
    # 1. "Rand" / "ZAR" context in text (highest confidence)
    if _ZAR_CONTEXT_RE.search(text):
        evidence = Evidence(
            page=1,
            method=ExtractionMethod.RULE,
            confidence=0.95,
            text="ZAR (context)",
        )
        return _make_field_result("currency", FieldType.CURRENCY, "ZAR", evidence, 0.95)

    # 2. ISO code in text
    iso_match = _ISO_CURRENCY_RE.search(text)
    if iso_match:
        code = iso_match.group(1).upper()
        evidence = Evidence(
            page=1,
            method=ExtractionMethod.RULE,
            confidence=0.95,
            text=iso_match.group(0),
            span=(iso_match.start(), iso_match.end()),
        )
        return _make_field_result("currency", FieldType.CURRENCY, code, evidence, 0.95)

    # 3. "R <amount>" pattern → ZAR (most SA documents use R prefix)
    if _ZAR_PREFIX_RE.search(text):
        evidence = Evidence(
            page=1,
            method=ExtractionMethod.RULE,
            confidence=0.88,
            text="R prefix",
        )
        return _make_field_result("currency", FieldType.CURRENCY, "ZAR", evidence, 0.88)

    # 4. Currency symbol
    symbols_found: dict[str, int] = {}
    for m in _SYMBOL_RE.finditer(text):
        sym = m.group(0)
        symbols_found[sym] = symbols_found.get(sym, 0) + 1

    if symbols_found:
        dominant = max(symbols_found, key=lambda s: symbols_found[s])
        code = _SYMBOL_TO_CODE.get(dominant, "")
        if not code:
            return None
        conf = 0.95 if dominant != "$" else 0.50
        evidence = Evidence(
            page=1,
            method=ExtractionMethod.RULE,
            confidence=conf,
            text=dominant,
        )
        return _make_field_result("currency", FieldType.CURRENCY, code, evidence, conf)

    return None


def _clean_zar_amount(raw: str) -> str:
    """Strip R prefix and normalize ZAR amount string for further parsing."""
    return raw.lstrip("R").strip()


def _extract_total_amount(text: str) -> FieldResult | None:
    # Try labeled patterns in order: ZAR-specific first (high confidence), then generic.
    # Only the bare-R fallback pattern (index 2) is low-confidence — skip it in the first pass.
    high_conf_zar = _ZAR_TOTAL_PATTERNS[:2]  # labeled patterns only
    for pat, conf in high_conf_zar + _TOTAL_AMOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip()
            if raw and re.search(r"\d", raw):
                evidence = Evidence(
                    page=1,
                    method=ExtractionMethod.RULE,
                    confidence=conf,
                    text=m.group(0).strip(),
                    span=(m.start(1), m.end(1)),
                )
                return _make_field_result("total_amount", FieldType.DECIMAL, raw, evidence, conf)

    # Second pass: bare R-prefix pattern (lower confidence, avoid balance sheet noise)
    bare_r_pat, bare_r_conf = _ZAR_TOTAL_PATTERNS[2]
    m = bare_r_pat.search(text)
    if m:
        raw = m.group(1).strip()
        if raw and re.search(r"\d", raw):
            evidence = Evidence(
                page=1,
                method=ExtractionMethod.RULE,
                confidence=bare_r_conf,
                text=m.group(0).strip(),
                span=(m.start(1), m.end(1)),
            )
            return _make_field_result("total_amount", FieldType.DECIMAL, raw, evidence, bare_r_conf)

    # Fallback: largest monetary amount (ZAR R prefix, symbols, or ISO-tagged)
    amounts: list[tuple[str, float]] = []
    for m in _MONEY_AMOUNT_RE.finditer(text):
        # group(1): symbol-prefixed, group(2): ISO-suffixed, group(3): R-prefixed
        raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if not raw:
            continue
        try:
            numeric = float(re.sub(r"[\s,\.]", "", raw))
            amounts.append((raw, numeric))
        except ValueError:
            continue

    if amounts:
        best_raw, _ = max(amounts, key=lambda x: x[1])
        evidence = Evidence(
            page=1,
            method=ExtractionMethod.RULE,
            confidence=0.55,
            text=best_raw,
        )
        return _make_field_result("total_amount", FieldType.DECIMAL, best_raw, evidence, 0.55)

    return None


# Captures that look like label fragments, table headers, or common words — not org names
_ORG_LABEL_RE = re.compile(
    r"^(?:No\.?|Reg\.?|Order|Vendor|Client|For|The|Visit|Please|Thanks?|We\b|Hi\b|Dear|Your|Our"
    r"|Amount|Total|Sub|Invoice|Receipt|Date|Payment|Balance|Printed|Page"
    r"|Tax|Allocation|Remarks|Exclusive|Property|Unit|Item|Description|Quantity|Deposit"
    r"|Statement|Charge|Fee|Rate|Reference|Period)\b",
    re.IGNORECASE,
)


def _is_bad_org_capture(raw: str) -> bool:
    """Return True if a regex capture looks like a label, address fragment, or email."""
    if "@" in raw or "<" in raw or ">" in raw:
        return True
    if "No.:" in raw or "no.:" in raw:
        return True
    if _ORG_LABEL_RE.match(raw):
        return True
    # Sentence-like: too many lowercase common words
    words = raw.split()
    if len(words) > 6:
        return True
    return False


def _heuristic_org_line(line: str) -> bool:
    """Return True if *line* passes all heuristic checks for a company name."""
    line = line.strip()
    if not line or len(line) < 3 or len(line) > 80:
        return False
    if _EMAIL_HEADER_RE.match(line):
        return False
    if re.match(r"^(?:https?://|www\.|tel|fax|vat|reg|p\.o\.|po box|\d{1,2}[/\-])", line, re.IGNORECASE):
        return False
    if re.match(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
        r"|Mon|Tue|Wed|Thu|Fri|Sat|Sun"
        r"|January|February|March|April|May|June|July|August|September|October|November|December"
        r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        line,
        re.IGNORECASE,
    ):
        return False
    if _ADDRESS_LINE_RE.search(line) or _PHONE_LINE_RE.match(line):
        return False
    if _is_bad_org_capture(line):
        return False
    non_word = sum(1 for c in line if not c.isalnum() and c not in " &,.'()-/")
    if non_word > len(line) * 0.35:
        return False
    words = line.split()
    first_word = words[0].rstrip(".,;:") if words else ""
    return bool(words and line[0].isupper() and first_word.isalpha() and len(words) <= 6)


def _extract_organization_name(text: str) -> FieldResult | None:
    # 1. Top-of-document scan (first 3 lines): the issuer name almost always
    #    appears at the very top of an invoice, before any client/billing sections.
    #    Running this before labeled patterns prevents a company-suffix match deep
    #    in the document from shadowing the issuer at the top.
    for line in text.splitlines()[:3]:
        if _heuristic_org_line(line):
            evidence = Evidence(
                page=1,
                method=ExtractionMethod.RULE,
                confidence=0.72,
                text=line.strip(),
            )
            return _make_field_result("organization_name", FieldType.ORGANIZATION, line.strip(), evidence, 0.72)

    # 2. Labelled patterns (full document)
    for pat, conf in _ORGANIZATION_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip().rstrip(".,;")
            # Require true uppercase start — IGNORECASE on the compile lets [A-Z] match
            # lowercase letters too, so guard here rather than relying on the pattern.
            if raw and len(raw) >= 3 and raw[0].isupper() and not _is_bad_org_capture(raw):
                evidence = Evidence(
                    page=1,
                    method=ExtractionMethod.RULE,
                    confidence=conf,
                    text=m.group(0).strip(),
                    span=(m.start(1), m.end(1)),
                )
                return _make_field_result("organization_name", FieldType.ORGANIZATION, raw, evidence, conf)

    # 3. Full-document heuristic fallback
    for line in text.splitlines():
        if _heuristic_org_line(line):
            evidence = Evidence(
                page=1,
                method=ExtractionMethod.RULE,
                confidence=0.65,
                text=line.strip(),
            )
            return _make_field_result("organization_name", FieldType.ORGANIZATION, line.strip(), evidence, 0.65)

    return None


@register_extractor("rules-v1")
class RulesExtractor(DocumentExtractor):
    """Regex/rule-based baseline extractor for invoice documents."""

    def __init__(
        self,
        schema_version: str = "invoice-v1",
        extractor_version: str = "rules-v1",
    ) -> None:
        self._schema_version = schema_version
        self._extractor_version = extractor_version
        self._classifier = KeywordClassifier()

    @property
    def version(self) -> str:
        return self._extractor_version

    @property
    def schema_version(self) -> str:
        return self._schema_version

    def classify(self, text: str) -> tuple[DocumentType, float]:
        return self._classifier.classify(text)

    def extract(
        self,
        text: str,
        page_texts: list[str],
        policy: PolicyConfig,
    ) -> ExtractionResult:
        doc_type, class_conf = self.classify(text)
        log.debug("classified", document_type=doc_type.value, confidence=class_conf)

        fields: dict[str, FieldResult] = {}

        for field_schema in policy.fields:
            result = self._extract_field(field_schema, text, doc_type)
            if result is not None:
                result.field_id = field_schema.id
                result.field_type = field_schema.type
                result.is_required = field_schema.required
                fields[field_schema.id] = result
                log.debug(
                    "extracted_field",
                    field=field_schema.id,
                    raw=result.raw_value,
                    confidence=result.confidence,
                )
            else:
                log.debug("field_not_found", field=field_schema.id)

        return ExtractionResult(
            document_type=doc_type,
            classification_confidence=class_conf,
            fields=fields,
            extractor_version=self._extractor_version,
            schema_version=self._schema_version,
            page_count=len(page_texts),
            raw_text_length=len(text),
        )

    def _extract_field(
        self,
        field_schema: FieldSchema,
        text: str,
        doc_type: DocumentType = DocumentType.UNKNOWN,
    ) -> FieldResult | None:
        fid = field_schema.id

        if fid == "invoice_number":
            # Statements have no invoice number to extract
            if doc_type == DocumentType.STATEMENT:
                return None
            result = _first_match(text, _INVOICE_NUMBER_PATTERNS)
            # For receipts/POPs, fall back to receipt/booking/order reference numbers
            if result is None and doc_type in (
                DocumentType.RECEIPT,
                DocumentType.PROOF_OF_PAYMENT,
            ):
                result = _first_match(text, _RECEIPT_NUMBER_PATTERNS)
            # Invoice/receipt numbers must contain at least one digit
            if result and not re.search(r"\d", result.raw_value):
                result = None
            # Final fallback: label found but value is on a separate line (columnar layouts)
            if result is None:
                result = _scan_for_invoice_number_after_label(text)
            if result:
                result.field_type = FieldType.STRING
            return result

        if fid == "organization_name":
            return _extract_organization_name(text)

        if fid == "invoice_date":
            result = _first_match(text, _INVOICE_DATE_PATTERNS)
            if result:
                result.field_type = FieldType.DATE
            return result

        if fid == "total_amount":
            return _extract_total_amount(text)

        if fid == "currency":
            return _extract_currency(text)

        if fid == "due_date":
            result = _first_match(text, _DUE_DATE_PATTERNS)
            if result:
                result.field_type = FieldType.DATE
            return result

        if fid == "purchase_order_number":
            result = _first_match(text, _PO_NUMBER_PATTERNS)
            # PO numbers must contain at least one digit (excludes "Box", "n/a", etc.)
            if result and not re.search(r"\d", result.raw_value):
                result = None
            if result:
                result.field_type = FieldType.STRING
            return result

        if fid == "vat_number":
            result = _first_match(text, _VAT_PATTERNS)
            if result:
                result.field_type = FieldType.STRING
            return result

        if fid == "recipient_organization":
            result = _first_match(text, _RECIPIENT_PATTERNS)
            # Discard captures that look like label fragments (Reg No, order no., etc.)
            if result and (
                not result.raw_value[0].isupper()
                or re.match(r"^(?:Reg|Order|No\.?|Vendor|Client\s+order)", result.raw_value, re.IGNORECASE)
            ):
                result = None
            if result:
                result.field_type = FieldType.ORGANIZATION
            return result

        log.debug("no_rule_for_field", field=fid)
        return None
