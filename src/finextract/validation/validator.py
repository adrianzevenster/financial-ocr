from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import structlog

from finextract.domain import (
    ExtractionResult,
    FieldResult,
    FieldValidation,
    PolicyConfig,
    ReasonCode,
    ValidationError,
    ValidationResult,
    ValidationStatus,
)

from .normalizers import normalize_field

log = structlog.get_logger(__name__)


class Validator:
    """Normalize and validate an ExtractionResult against a PolicyConfig.

    Stateless per call — holds only the policy as read-only config.
    """

    def __init__(self, policy: PolicyConfig) -> None:
        self._policy = policy

    def validate(
        self,
        extraction: ExtractionResult,
    ) -> tuple[ValidationResult, dict[str, FieldResult]]:
        """Validate and normalize extraction results.

        Returns:
            (ValidationResult, dict[field_id → normalized FieldResult])
        """
        policy = self._policy
        field_validations: dict[str, FieldValidation] = {}
        normalized_fields: dict[str, FieldResult] = {}
        document_reason_codes: list[ReasonCode] = []

        # Per-field validation
        for field_schema in policy.fields:
            fid = field_schema.id
            raw_result = extraction.fields.get(fid)

            if raw_result is None or raw_result.raw_value is None:
                if field_schema.required:
                    field_validations[fid] = FieldValidation(
                        field_id=fid,
                        status="missing",
                        reason_codes=[ReasonCode.MISSING_REQUIRED_FIELD],
                    )
                    log.debug("field_missing", field=fid, required=True)
                else:
                    field_validations[fid] = FieldValidation(
                        field_id=fid,
                        status="missing",
                        reason_codes=[],
                    )
                continue

            # Normalize
            try:
                norm_value, conf_adj = normalize_field(field_schema.type, raw_result.raw_value)
            except ValidationError as exc:
                log.debug("field_invalid", field=fid, error=str(exc))
                reason = _invalid_reason(field_schema.type)
                field_validations[fid] = FieldValidation(
                    field_id=fid,
                    status="invalid",
                    reason_codes=[reason],
                )
                continue

            adjusted_confidence = max(0.0, min(1.0, raw_result.confidence + conf_adj))

            # Confidence threshold check
            if adjusted_confidence < policy.thresholds.manual_review:
                field_validations[fid] = FieldValidation(
                    field_id=fid,
                    status="low_confidence",
                    reason_codes=[ReasonCode.LOW_CONFIDENCE],
                )
                log.debug("field_low_confidence", field=fid, confidence=adjusted_confidence)
            else:
                field_validations[fid] = FieldValidation(
                    field_id=fid,
                    status="ok",
                    reason_codes=[],
                )

            # Store normalized field regardless of confidence (for review workflow)
            normalized = FieldResult(
                field_id=raw_result.field_id,
                field_type=raw_result.field_type,
                raw_value=raw_result.raw_value,
                normalized_value=norm_value,
                evidence=raw_result.evidence,
                confidence=adjusted_confidence,
                is_required=raw_result.is_required,
            )
            normalized_fields[fid] = normalized

        # Propagate required-field reason codes to the document level
        for fid, fv in field_validations.items():
            if fv.reason_codes and policy.field_map.get(fid) and policy.field_map[fid].required:
                document_reason_codes.extend(fv.reason_codes)

        # Cross-field consistency checks
        document_reason_codes.extend(
            self._cross_field_checks(normalized_fields, field_validations)
        )

        # Compute overall confidence
        overall_confidence = self._compute_overall_confidence(
            policy, field_validations, normalized_fields
        )

        # Determine status
        has_missing_required = any(
            fv.status in ("missing", "invalid")
            for fid, fv in field_validations.items()
            if policy.field_map.get(fid) and policy.field_map[fid].required
        )

        if not has_missing_required and overall_confidence >= policy.thresholds.auto_apply:
            status = ValidationStatus.ACCEPTED
        elif not has_missing_required and overall_confidence >= policy.thresholds.manual_review:
            status = ValidationStatus.REVIEW
        else:
            status = ValidationStatus.REJECTED

        log.debug(
            "validation_complete",
            status=status.value,
            overall_confidence=overall_confidence,
            has_missing_required=has_missing_required,
        )

        return (
            ValidationResult(
                status=status,
                overall_confidence=overall_confidence,
                field_validations=field_validations,
                reason_codes=document_reason_codes,
            ),
            normalized_fields,
        )

    def _cross_field_checks(
        self,
        normalized: dict[str, FieldResult],
        field_validations: dict[str, FieldValidation],
    ) -> list[ReasonCode]:
        codes: list[ReasonCode] = []

        invoice_date_result = normalized.get("invoice_date")
        due_date_result = normalized.get("due_date")

        if invoice_date_result and due_date_result:
            inv_date = invoice_date_result.normalized_value
            due = due_date_result.normalized_value
            if isinstance(inv_date, date) and isinstance(due, date):
                if due < inv_date:
                    codes.append(ReasonCode.CROSS_FIELD_INCONSISTENCY)
                    log.debug(
                        "cross_field_inconsistency",
                        reason="due_date before invoice_date",
                        invoice_date=str(inv_date),
                        due_date=str(due),
                    )

        total_result = normalized.get("total_amount")
        if total_result and isinstance(total_result.normalized_value, Decimal):
            if total_result.normalized_value == Decimal("0"):
                log.debug("zero_total_amount_warning")

        return codes

    def _compute_overall_confidence(
        self,
        policy: PolicyConfig,
        field_validations: dict[str, FieldValidation],
        normalized: dict[str, FieldResult],
    ) -> float:
        weighted_sum = 0.0
        total_weight = 0.0

        for field_schema in policy.fields:
            fid = field_schema.id
            weight = 2.0 if field_schema.required else 1.0
            fv = field_validations.get(fid)
            result = normalized.get(fid)

            if fv is None or fv.status == "missing":
                if field_schema.required:
                    weighted_sum += 0.0
                    total_weight += weight
                continue

            if fv.status == "invalid":
                weighted_sum += 0.0
                total_weight += weight
                continue

            if result is not None:
                weighted_sum += result.confidence * weight
                total_weight += weight

        if total_weight == 0.0:
            return 0.0

        return weighted_sum / total_weight


def _invalid_reason(field_type: Any) -> ReasonCode:
    from finextract.domain import FieldType

    mapping = {
        FieldType.DATE: ReasonCode.INVALID_DATE,
        FieldType.DECIMAL: ReasonCode.INVALID_AMOUNT,
        FieldType.CURRENCY: ReasonCode.INVALID_CURRENCY,
    }
    return mapping.get(field_type, ReasonCode.MISSING_REQUIRED_FIELD)
