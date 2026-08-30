from __future__ import annotations

import pytest
from datetime import date, datetime
from decimal import Decimal

from finextract.domain import (
    CategoryRule,
    DocumentType,
    Evidence,
    ExtractionMethod,
    ExtractionResult,
    FieldResult,
    FieldSchema,
    FieldType,
    NamingConfig,
    PolicyConfig,
    ReasonCode,
    Thresholds,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def policy() -> PolicyConfig:
    return PolicyConfig(
        policy_version="test-v1",
        schema_version="invoice-v1",
        document_type=DocumentType.INVOICE,
        fields=[
            FieldSchema("invoice_number", FieldType.STRING, required=True),
            FieldSchema("organization_name", FieldType.ORGANIZATION, required=True),
            FieldSchema("invoice_date", FieldType.DATE, required=True),
            FieldSchema("total_amount", FieldType.DECIMAL, required=True),
            FieldSchema("currency", FieldType.CURRENCY, required=True),
            FieldSchema("due_date", FieldType.DATE, required=False),
        ],
        naming=NamingConfig(
            template="{invoice_date}_{organization_slug}_{document_type}_{invoice_number}.{extension}",
            max_length=180,
            collision_strategy="content_hash_suffix",
            unsafe_chars=r'[<>:"/\\|?*\x00-\x1f]',
            reserved_names=[],
            component_max_length=60,
        ),
        categories=[],
        thresholds=Thresholds(
            auto_apply=0.94,
            manual_review=0.75,
            ocr_min_coverage=0.30,
            ocr_min_confidence=60,
        ),
    )


def _evidence(confidence: float = 0.95) -> list[Evidence]:
    return [Evidence(page=1, method=ExtractionMethod.RULE, confidence=confidence, text="test")]


def _make_extraction(
    fields: dict[str, tuple[str, float]],  # field_id -> (raw_value, confidence)
    doc_type: DocumentType = DocumentType.INVOICE,
) -> ExtractionResult:
    field_results = {}
    for fid, (raw, conf) in fields.items():
        field_results[fid] = FieldResult(
            field_id=fid,
            field_type=FieldType.STRING,
            raw_value=raw,
            normalized_value=None,
            evidence=_evidence(conf),
            confidence=conf,
            is_required=True,
        )
    return ExtractionResult(
        document_type=doc_type,
        classification_confidence=0.95,
        fields=field_results,
        extractor_version="rules-v1",
        schema_version="invoice-v1",
    )


_ALL_FIELDS: dict[str, tuple[str, float]] = {
    "invoice_number": ("INV-001", 0.97),
    "organization_name": ("Acme Ltd", 0.96),
    "invoice_date": ("2024-01-15", 0.97),
    "total_amount": ("1234.56", 0.96),
    "currency": ("GBP", 0.97),
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_accepted(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    extraction = _make_extraction(_ALL_FIELDS)
    validator = Validator(policy)
    result, normalized = validator.validate(extraction)
    assert result.status == ValidationStatus.ACCEPTED
    assert result.overall_confidence >= policy.thresholds.auto_apply


def test_review(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    # Confidence below auto_apply but above manual_review
    fields = {k: (v, 0.80) for k, (v, _) in _ALL_FIELDS.items()}
    extraction = _make_extraction(fields)
    validator = Validator(policy)
    result, _ = validator.validate(extraction)
    assert result.status == ValidationStatus.REVIEW


def test_quarantine_missing_required_field(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    fields = {k: v for k, v in _ALL_FIELDS.items() if k != "invoice_number"}
    extraction = _make_extraction(fields)
    validator = Validator(policy)
    result, _ = validator.validate(extraction)
    assert result.status == ValidationStatus.REJECTED
    assert ReasonCode.MISSING_REQUIRED_FIELD in result.reason_codes


def test_quarantine_invalid_date(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    fields = {**_ALL_FIELDS, "invoice_date": ("not-a-date", 0.95)}
    extraction = _make_extraction(fields)
    validator = Validator(policy)
    result, _ = validator.validate(extraction)
    assert result.status == ValidationStatus.REJECTED
    assert any(
        rc in result.reason_codes
        for rc in [ReasonCode.INVALID_DATE, ReasonCode.MISSING_REQUIRED_FIELD]
    )


def test_low_confidence_currency_reason_code(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    # Currency below review threshold
    fields = {**_ALL_FIELDS, "currency": ("USD", 0.50)}
    extraction = _make_extraction(fields)
    validator = Validator(policy)
    result, normalized = validator.validate(extraction)
    currency_validation = result.field_validations.get("currency")
    assert currency_validation is not None
    assert ReasonCode.LOW_CONFIDENCE in currency_validation.reason_codes


def test_cross_field_inconsistency_due_date_before_invoice(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    fields = {
        **_ALL_FIELDS,
        "due_date": ("2024-01-10", 0.95),  # before invoice_date 2024-01-15
    }
    extraction = _make_extraction(fields)
    validator = Validator(policy)
    result, _ = validator.validate(extraction)
    assert ReasonCode.CROSS_FIELD_INCONSISTENCY in result.reason_codes


def test_all_required_fields_normalized(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    extraction = _make_extraction(_ALL_FIELDS)
    validator = Validator(policy)
    _, normalized = validator.validate(extraction)

    assert "invoice_number" in normalized
    assert "invoice_date" in normalized
    assert "total_amount" in normalized
    assert "currency" in normalized

    # invoice_date should be a date object
    date_field = normalized["invoice_date"]
    if date_field and date_field.normalized_value is not None:
        assert isinstance(date_field.normalized_value, date)

    # total_amount should be Decimal
    amount_field = normalized["total_amount"]
    if amount_field and amount_field.normalized_value is not None:
        assert isinstance(amount_field.normalized_value, Decimal)


def test_optional_field_missing_is_not_rejected(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    # due_date is optional — omitting it should not cause rejection
    extraction = _make_extraction(_ALL_FIELDS)  # no due_date
    validator = Validator(policy)
    result, _ = validator.validate(extraction)
    assert result.status in (ValidationStatus.ACCEPTED, ValidationStatus.REVIEW)


def test_normalized_fields_dict_returned(policy: PolicyConfig):
    from finextract.validation.validator import Validator

    extraction = _make_extraction(_ALL_FIELDS)
    validator = Validator(policy)
    result, normalized = validator.validate(extraction)
    assert isinstance(normalized, dict)
    assert len(normalized) > 0
