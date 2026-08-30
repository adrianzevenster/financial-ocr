from __future__ import annotations

import pytest
from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

# These imports will work once extraction/validation modules are implemented
from finextract.validation.normalizers import (
    normalize_date,
    normalize_decimal,
    normalize_currency,
    normalize_string,
    normalize_organization,
    KNOWN_CURRENCIES,
)
from finextract.domain import ValidationError


# ---------------------------------------------------------------------------
# normalize_date
# ---------------------------------------------------------------------------


def test_normalize_date_iso():
    assert normalize_date("2024-01-15") == date(2024, 1, 15)


def test_normalize_date_dmy_slash():
    assert normalize_date("15/01/2024") == date(2024, 1, 15)


def test_normalize_date_dmy_dot():
    assert normalize_date("15.01.2024") == date(2024, 1, 15)


def test_normalize_date_natural():
    assert normalize_date("15 January 2024") == date(2024, 1, 15)


def test_normalize_date_natural_short():
    assert normalize_date("15 Jan 2024") == date(2024, 1, 15)


def test_normalize_date_mdy():
    assert normalize_date("January 15, 2024") == date(2024, 1, 15)


def test_normalize_date_invalid():
    with pytest.raises(ValidationError):
        normalize_date("not-a-date")


def test_normalize_date_empty():
    with pytest.raises(ValidationError):
        normalize_date("")


def test_normalize_date_returns_date_not_datetime():
    result = normalize_date("2024-06-01")
    assert isinstance(result, date)


# ---------------------------------------------------------------------------
# normalize_decimal
# ---------------------------------------------------------------------------


def test_normalize_decimal_plain():
    assert normalize_decimal("1234.56") == Decimal("1234.56")


def test_normalize_decimal_comma_thousands():
    assert normalize_decimal("1,234.56") == Decimal("1234.56")


def test_normalize_decimal_comma_decimal_sep():
    assert normalize_decimal("1.234,56") == Decimal("1234.56")


def test_normalize_decimal_symbol_pound():
    assert normalize_decimal("£1,234.56") == Decimal("1234.56")


def test_normalize_decimal_symbol_euro():
    assert normalize_decimal("€ 1.234,56") == Decimal("1234.56")


def test_normalize_decimal_symbol_dollar():
    assert normalize_decimal("$1234.56") == Decimal("1234.56")


def test_normalize_decimal_integer():
    assert normalize_decimal("1000") == Decimal("1000")


def test_normalize_decimal_invalid():
    with pytest.raises(ValidationError):
        normalize_decimal("not-a-number")


def test_normalize_decimal_empty():
    with pytest.raises(ValidationError):
        normalize_decimal("")


def test_normalize_decimal_never_float():
    result = normalize_decimal("1234.56")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


# ---------------------------------------------------------------------------
# normalize_currency
# ---------------------------------------------------------------------------


def test_normalize_currency_symbol_pound():
    assert normalize_currency("£") == "GBP"


def test_normalize_currency_symbol_euro():
    assert normalize_currency("€") == "EUR"


def test_normalize_currency_symbol_dollar():
    assert normalize_currency("$") == "USD"


def test_normalize_currency_code_lower():
    assert normalize_currency("eur") == "EUR"


def test_normalize_currency_code_upper():
    assert normalize_currency("USD") == "USD"


def test_normalize_currency_code_gbp():
    assert normalize_currency("GBP") == "GBP"


def test_normalize_currency_unknown():
    with pytest.raises(ValidationError):
        normalize_currency("XYZ")


def test_normalize_currency_empty():
    with pytest.raises(ValidationError):
        normalize_currency("")


def test_normalize_currency_all_known_currencies_valid():
    for code in KNOWN_CURRENCIES:
        assert normalize_currency(code) == code


# ---------------------------------------------------------------------------
# normalize_string
# ---------------------------------------------------------------------------


def test_normalize_string_whitespace():
    assert normalize_string("  hello   world  ") == "hello world"


def test_normalize_string_tabs_and_newlines():
    assert normalize_string("hello\t\nworld") == "hello world"


def test_normalize_string_empty():
    assert normalize_string("") == ""


def test_normalize_string_already_clean():
    assert normalize_string("hello world") == "hello world"


# ---------------------------------------------------------------------------
# normalize_organization
# ---------------------------------------------------------------------------


def test_normalize_organization_strips_whitespace():
    result = normalize_organization("  Acme Ltd  ")
    assert "Acme" in result


def test_normalize_organization_no_trailing_punctuation():
    result = normalize_organization("Acme Corp.")
    assert not result.endswith(".")


def test_normalize_organization_preserves_meaningful_content():
    result = normalize_organization("Acme Corporation Ltd")
    assert "Acme" in result


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@given(st.text())
@settings(max_examples=200)
def test_normalize_string_never_raises(s: str):
    # normalize_string should never raise — it's a pure cleanup function
    result = normalize_string(s)
    assert isinstance(result, str)


@given(st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=1, max_size=5))
@settings(max_examples=100)
def test_normalize_currency_either_raises_or_returns_uppercase(s: str):
    try:
        result = normalize_currency(s)
        assert result == result.upper()
        assert result in KNOWN_CURRENCIES
    except ValidationError:
        pass  # expected for unknown codes
