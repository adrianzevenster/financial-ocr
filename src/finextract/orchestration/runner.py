from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from finextract.documents.detection import assert_supported, detect_mime
from finextract.documents.extraction import (
    extract_images_for_ocr,
    extract_native_text,
    needs_ocr,
)
from finextract.documents.ocr import merge_ocr_into_document_text, ocr_document
from finextract.extraction.rules import RulesExtractor
from finextract.policies.naming import render_filename, resolve_category_destination
from finextract.validation.validator import Validator
from finextract.domain import (
    DocumentType,
    FinextractError,
    MutationOp,
    MutationPlan,
    PolicyConfig,
    ProcessingStatus,
    ReasonCode,
    RunRecord,
    RunSummary,
    SourceItem,
    ValidationStatus,
)
from finextract.audit.store import AuditStore
from finextract.sources.base import DocumentSource
from .state import assert_transition, quarantine_reason

log = structlog.get_logger()


@dataclass
class PipelineConfig:
    policy: PolicyConfig
    audit_store: AuditStore
    work_dir: Path
    dry_run: bool = True
    extractor_version: str = "rules-v1"
    batch_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class Runner:
    def __init__(self, config: PipelineConfig) -> None:
        self._cfg = config
        self._cfg.work_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run_batch(self, source: DocumentSource) -> RunSummary:
        batch_id = self._cfg.batch_id
        log.info("runner.batch_start", batch_id=batch_id, dry_run=self._cfg.dry_run)

        for item in source.discover():
            record = self.process_item(source, item)
            log.info(
                "runner.item_done",
                run_id=record.run_id,
                status=record.status.value,
                path=item.original_path,
            )

        summary = self._cfg.audit_store.get_batch_summary(batch_id)
        log.info(
            "runner.batch_done",
            batch_id=batch_id,
            total=summary.total,
            accepted=summary.accepted,
            quarantined=summary.quarantined,
            failed=summary.failed,
        )
        return summary

    def process_item(self, source: DocumentSource, item: SourceItem) -> RunRecord:
        policy = self._cfg.policy
        store = self._cfg.audit_store

        # Idempotency check
        existing = store.find_existing(item.item_id, item.content_hash, policy.policy_version)
        if existing and existing.status == ProcessingStatus.APPLIED:
            log.info("runner.skip_already_applied", item_id=item.item_id)
            existing.transition(ProcessingStatus.SKIPPED, reason="already applied")
            store.save_run(existing)
            return existing

        record = self._make_record(item)
        store.save_run(record)

        try:
            self._process(source, item, record)
        except FinextractError as exc:
            self._fail(record, str(exc), exc.reason_code)
        except Exception as exc:
            self._fail(record, f"Unexpected error: {exc}", None)

        store.save_run(record)
        return record

    # ------------------------------------------------------------------
    # Private pipeline steps
    # ------------------------------------------------------------------

    def _process(
        self, source: DocumentSource, item: SourceItem, record: RunRecord
    ) -> None:
        policy = self._cfg.policy
        store = self._cfg.audit_store

        # PENDING → PROCESSING
        self._transition(record, ProcessingStatus.PROCESSING)
        store.save_run(record)

        # Download
        item = source.download(item, self._cfg.work_dir)
        record.source_item = item

        local_path = Path(item.local_path or item.original_path)

        # MIME check
        mime = detect_mime(local_path)
        try:
            assert_supported(local_path, mime)
        except FinextractError as exc:
            self._quarantine(record, [ReasonCode.UNSUPPORTED_MIME], str(exc))
            store.save_run(record)
            return

        # Text extraction
        doc_text = extract_native_text(local_path)
        if needs_ocr(doc_text, policy.thresholds.ocr_min_coverage):
            image_pages = extract_images_for_ocr(local_path)
            ocr_result = ocr_document(image_pages)
            doc_text = merge_ocr_into_document_text(doc_text, ocr_result)
            record.add_event("ocr_used", {"page_count": len(image_pages)})

        full_text = "\n\n".join(p.text for p in doc_text.pages)
        page_texts = [p.text for p in doc_text.pages]

        # Extract
        extractor = RulesExtractor(
            schema_version=policy.schema_version,
            extractor_version=self._cfg.extractor_version,
        )
        extraction = extractor.extract(full_text, page_texts, policy)
        record.extraction = extraction
        record.document_type = extraction.document_type

        # Validate
        validator = Validator(policy)
        validation, normalized_fields = validator.validate(extraction)
        record.validation = validation

        # Build fields dict for naming
        naming_fields: dict[str, Any] = {"document_type": extraction.document_type.value}
        for fid, fr in normalized_fields.items():
            if fr.normalized_value is not None:
                naming_fields[fid] = fr.normalized_value
            elif fr.raw_value is not None:
                naming_fields[fid] = fr.raw_value

        # Filename
        ext = local_path.suffix
        proposed_name = render_filename(
            policy=policy,
            fields=naming_fields,
            extension=ext,
            content_hash=item.content_hash,
        )
        category = resolve_category_destination(policy, naming_fields)

        proposed_path = str(Path(local_path.parent) / proposed_name)
        if category:
            proposed_path = f"{category}/{proposed_name}"

        plan = _build_mutation_plan(
            item=item,
            proposed_path=proposed_path,
            category=category,
            policy=policy,
            validated_fields=naming_fields,
        )
        record.mutation_plan = plan

        # PROCESSING → PLANNED
        self._transition(record, ProcessingStatus.PLANNED)
        store.save_run(record)

        if self._cfg.dry_run:
            return

        # Apply or quarantine
        if validation.status == ValidationStatus.ACCEPTED:
            applied = source.apply_mutation(plan, dry_run=False)
            if applied:
                record.applied_path = proposed_path
                self._transition(record, ProcessingStatus.APPLIED)
            else:
                self._quarantine(record, plan_reason_codes(plan), "mutation returned False")
        else:
            reason_codes = list(validation.reason_codes)
            self._quarantine(record, reason_codes, quarantine_reason(reason_codes))

    def _make_record(self, item: SourceItem) -> RunRecord:
        now = datetime.utcnow()
        return RunRecord(
            run_id=uuid.uuid4().hex,
            batch_id=self._cfg.batch_id,
            source_item=item,
            status=ProcessingStatus.PENDING,
            schema_version=self._cfg.policy.schema_version,
            policy_version=self._cfg.policy.policy_version,
            extractor_version=self._cfg.extractor_version,
            created_at=now,
            updated_at=now,
        )

    def _transition(self, record: RunRecord, new_status: ProcessingStatus, **data: Any) -> None:
        assert_transition(record.status, new_status)
        record.transition(new_status, **data)

    def _fail(self, record: RunRecord, message: str, reason_code: ReasonCode | None) -> None:
        try:
            assert_transition(record.status, ProcessingStatus.FAILED)
        except ValueError:
            return  # already terminal
        record.error_message = message
        record.error_reason_code = reason_code
        record.transition(ProcessingStatus.FAILED, error=message)
        self._cfg.audit_store.save_run(record)

    def _quarantine(
        self,
        record: RunRecord,
        reason_codes: list[ReasonCode],
        detail: str,
    ) -> None:
        try:
            assert_transition(record.status, ProcessingStatus.QUARANTINED)
        except ValueError:
            return
        record.transition(
            ProcessingStatus.QUARANTINED,
            reason_codes=[rc.value for rc in reason_codes],
            detail=detail,
        )


def _build_mutation_plan(
    item: SourceItem,
    proposed_path: str,
    category: str | None,
    policy: PolicyConfig,
    validated_fields: dict[str, Any],
) -> MutationPlan:
    return MutationPlan(
        item_id=item.item_id,
        source_path=item.original_path,
        proposed_path=proposed_path,
        proposed_category=category,
        operation=MutationOp.RENAME,
        reason="policy-driven rename",
        policy_version=policy.policy_version,
        schema_version=policy.schema_version,
        content_hash=item.content_hash,
        etag_at_plan_time=item.etag,
        validated_fields={k: str(v) for k, v in validated_fields.items()},
    )


def plan_reason_codes(plan: MutationPlan) -> list[ReasonCode]:
    return []
