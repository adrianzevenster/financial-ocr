from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser as dateutil_parser

from finextract.domain import FieldType, ValidationError

KNOWN_CURRENCIES: frozenset[str] = frozenset(
    {
        "AED", "AUD", "BGN", "BRL", "CAD", "CHF", "CNY", "CZK", "DKK",
        "EUR", "GBP", "HKD", "HRK", "HUF", "IDR", "ILS", "INR", "ISK",
        "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON",
        "RUB", "SEK", "SGD", "THB", "TRY", "TWD", "USD", "VND", "ZAR",
    }
)

_SYMBOL_TO_ISO: dict[str, str] = {
    "£": "GBP",
    "€": "EUR",
    "$": "USD",
    "¥": "JPY",
    "₹": "INR",
    "R": "ZAR",  # South African Rand prefix
}

_CURRENCY_SYMBOL_RE = re.compile(r"[£€$¥₹]")
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[.,;:!?]+$")

# Company suffix abbreviations to preserve in title case
_COMPANY_SUFFIXES = {
    "ltd", "llc", "inc", "plc", "ag", "bv", "sa", "sas", "gmbh",
    "nv", "oy", "ab", "aps", "as", "kk", "pte",
}


def normalize_string(raw: str) -> str:
    """Strip and collapse internal whitespace."""
    return _WHITESPACE_RE.sub(" ", raw.strip())


def normalize_date(raw: str) -> date:
    """Parse a date string into a date object.

    Detects year-first formats (YYYY-MM-DD, YYYY/MM/DD) and uses yearfirst=True
    to avoid dateutil misinterpreting e.g. "2024-01-10" as October 1, 2024
    when dayfirst=True is applied.

    Raises ValidationError if unparseable.
    """
    cleaned = normalize_string(raw)
    if not cleaned:
        raise ValidationError(f"Cannot parse date {raw!r}: empty string")
    yearfirst = bool(re.match(r"^\d{4}[\-/.]", cleaned))
    dayfirst = not yearfirst
    try:
        dt = dateutil_parser.parse(cleaned, dayfirst=dayfirst, yearfirst=yearfirst)
        return dt.date()
    except (ValueError, OverflowError) as exc:
        raise ValidationError(f"Cannot parse date {raw!r}: {exc}") from exc


def normalize_decimal(raw: str) -> Decimal:
    """Parse a decimal string into an exact Decimal.

    Handles:
    - Currency symbol prefixes/suffixes
    - Thousands separators (comma or period)
    - Ambiguous comma/period: if both present, the last separator is decimal

    Raises ValidationError if unparseable.
    """
    cleaned = normalize_string(raw)
    # Strip currency symbols
    cleaned = _CURRENCY_SYMBOL_RE.sub("", cleaned).strip()
    # Strip alphabetic prefix/suffix (e.g. "R", " EUR", "ZAR")
    cleaned = re.sub(r"^[A-Za-z]+\s*", "", cleaned).strip()
    cleaned = re.sub(r"\s*[A-Za-z]+$", "", cleaned).strip()
    # Collapse internal spaces (SA thousands separator: "204 412,50" → "204412,50")
    cleaned = re.sub(r"(?<=\d)\s+(?=\d)", "", cleaned)

    # Determine decimal/thousands separator
    has_comma = "," in cleaned
    has_period = "." in cleaned

    if has_comma and has_period:
        # Both present: whichever comes last is the decimal separator
        last_comma = cleaned.rfind(",")
        last_period = cleaned.rfind(".")
        if last_period > last_comma:
            # period is decimal → remove commas (thousands)
            cleaned = cleaned.replace(",", "")
        else:
            # comma is decimal → remove periods (thousands), replace comma with period
            cleaned = cleaned.replace(".", "").replace(",", ".")
    elif has_comma and not has_period:
        # Could be thousands (1,000) or decimal (1,50)
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            # e.g. "1,50" → decimal
            cleaned = cleaned.replace(",", ".")
        else:
            # e.g. "1,000" → thousands separator
            cleaned = cleaned.replace(",", "")
    # else: period only or no separator → fine as-is

    cleaned = cleaned.strip()
    if not cleaned:
        raise ValidationError(f"Empty decimal after stripping: {raw!r}")

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValidationError(f"Cannot parse decimal {raw!r} (cleaned: {cleaned!r}): {exc}") from exc


def normalize_currency(raw: str) -> str:
    """Normalize a currency string to an ISO 4217 code.

    Accepts ISO codes and common symbols.
    Raises ValidationError if unrecognized.
    """
    cleaned = normalize_string(raw).upper()

    # Direct ISO match
    if cleaned in KNOWN_CURRENCIES:
        return cleaned

    # Symbol match (single character, case-sensitive for "R")
    code = _SYMBOL_TO_ISO.get(raw.strip())
    if code:
        return code

    # Symbol in raw (e.g. "€", "R")
    for sym, iso in _SYMBOL_TO_ISO.items():
        if sym in raw:
            return iso

    # "ZAR" or "RAND" context
    if "ZAR" in cleaned or "RAND" in cleaned:
        return "ZAR"

    raise ValidationError(
        f"Unrecognized currency {raw!r}. Expected ISO 4217 code or symbol."
    )


def normalize_organization(raw: str) -> str:
    """Clean and title-case an organization name, preserving known abbreviations."""
    cleaned = normalize_string(raw)
    cleaned = _TRAILING_PUNCT_RE.sub("", cleaned)

    words = cleaned.split()
    result: list[str] = []
    for word in words:
        lower = word.lower().rstrip(".")
        if lower in _COMPANY_SUFFIXES:
            result.append(word.upper() if len(lower) <= 3 else word.capitalize())
        else:
            result.append(word.capitalize() if word.islower() else word)

    return " ".join(result)


def normalize_field(field_type: FieldType, raw: str) -> tuple[Any, float]:
    """Normalize a raw string value according to its field type.

    Returns (normalized_value, confidence_adjustment).
    A clean parse gives +0.05; ambiguous gives -0.10.
    Raises ValidationError on failure.
    """
    if field_type == FieldType.STRING:
        return normalize_string(raw), 0.05

    if field_type == FieldType.ORGANIZATION:
        return normalize_organization(raw), 0.05

    if field_type == FieldType.DATE:
        normalized = normalize_date(raw)
        adj = 0.05
        cleaned = normalize_string(raw)
        yearfirst = bool(re.match(r"^\d{4}[\-/.]", cleaned))
        if not yearfirst:
            # For non-ISO formats check dayfirst ambiguity: "01/10/2024" is ambiguous
            try:
                alt = dateutil_parser.parse(cleaned, dayfirst=False, yearfirst=False).date()
                if alt != normalized:
                    adj = -0.10
            except Exception:
                pass
        return normalized, adj

    if field_type == FieldType.DECIMAL:
        normalized = normalize_decimal(raw)
        # Check for comma/period ambiguity
        has_comma = "," in raw
        has_period = "." in raw
        adj = -0.10 if (has_comma and has_period) else 0.05
        return normalized, adj

    if field_type == FieldType.CURRENCY:
        normalized = normalize_currency(raw)
        adj = 0.05
        # Ambiguous: $ without clear country context
        if raw.strip() == "$" or raw.strip().upper() == "USD":
            adj = 0.0
        return normalized, adj

    return normalize_string(raw), 0.0
