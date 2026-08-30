from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .enums import (
    DocumentType,
    ExtractionMethod,
    FieldType,
    MutationOp,
    ProcessingStatus,
    ReasonCode,
    SourceType,
    ValidationStatus,
)


@dataclass
class Evidence:
    """Provenance for a single extracted value."""

    page: int
    method: ExtractionMethod
    confidence: float  # 0.0 – 1.0
    text: str  # verbatim source text
    span: tuple[int, int] | None = None  # char offsets within page text
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 in pt


@dataclass
class FieldResult:
    """Extraction outcome for one schema field."""

    field_id: str
    field_type: FieldType
    raw_value: str | None
    normalized_value: str | datetime | Decimal | None  # always typed, never float
    evidence: list[Evidence]
    confidence: float  # 0.0 – 1.0
    is_required: bool = False


@dataclass
class ExtractionResult:
    """Full extraction output from one extractor pass."""

    document_type: DocumentType
    classification_confidence: float
    fields: dict[str, FieldResult]
    extractor_version: str
    schema_version: str
    page_count: int = 0
    ocr_used: bool = False
    raw_text_length: int = 0


@dataclass
class FieldValidation:
    field_id: str
    status: str  # "ok" | "missing" | "invalid" | "low_confidence"
    reason_codes: list[ReasonCode] = field(default_factory=list)


@dataclass
class ValidationResult:
    status: ValidationStatus
    overall_confidence: float
    field_validations: dict[str, FieldValidation]
    reason_codes: list[ReasonCode] = field(default_factory=list)

    @property
    def accepted_fields(self) -> dict[str, FieldResult | None]:
        return {}  # populated by validator using extraction result


@dataclass
class SourceItem:
    """Identity and metadata for one discoverable document."""

    source_type: SourceType
    item_id: str
    original_path: str
    content_hash: str  # SHA-256 hex
    mime_type: str
    file_size: int
    etag: str | None = None
    drive_id: str | None = None
    item_version: str | None = None
    local_path: str | None = None  # path to local copy for processing


@dataclass
class MutationPlan:
    """Proposed rename/move for a single SharePoint or local item."""

    item_id: str
    source_path: str
    proposed_path: str
    proposed_category: str | None
    operation: MutationOp
    reason: str
    policy_version: str
    schema_version: str
    content_hash: str
    etag_at_plan_time: str | None
    validated_fields: dict[str, Any]
    dry_run: bool = True


@dataclass
class RunEvent:
    """Immutable event in a run's history."""

    event_type: str
    timestamp: datetime
    data: dict[str, Any]


@dataclass
class RunRecord:
    """Full state of processing one document in one pipeline run."""

    run_id: str
    batch_id: str
    source_item: SourceItem
    status: ProcessingStatus
    schema_version: str
    policy_version: str
    extractor_version: str
    created_at: datetime
    updated_at: datetime
    document_type: DocumentType | None = None
    extraction: ExtractionResult | None = None
    validation: ValidationResult | None = None
    mutation_plan: MutationPlan | None = None
    applied_path: str | None = None
    error_message: str | None = None
    error_reason_code: ReasonCode | None = None
    events: list[RunEvent] = field(default_factory=list)

    def add_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.events.append(
            RunEvent(
                event_type=event_type,
                timestamp=datetime.utcnow(),
                data=data or {},
            )
        )

    def transition(self, new_status: ProcessingStatus, **data: Any) -> None:
        old = self.status
        self.status = new_status
        self.updated_at = datetime.utcnow()
        self.add_event(
            "state_transition",
            {"from": old.value, "to": new_status.value, **data},
        )


@dataclass
class FieldSchema:
    """One field definition from the schema YAML."""

    id: str
    type: FieldType
    required: bool
    description: str = ""


@dataclass
class NamingConfig:
    template: str
    max_length: int
    collision_strategy: str
    unsafe_chars: str
    reserved_names: list[str]
    component_max_length: int


@dataclass
class CategoryRule:
    id: str
    destination: str
    when: str  # simple equality expression, e.g. "document_type == 'invoice'"


@dataclass
class Thresholds:
    auto_apply: float
    manual_review: float
    ocr_min_coverage: float
    ocr_min_confidence: float


@dataclass
class PolicyConfig:
    """Fully validated, in-memory representation of the combined schema + policy."""

    policy_version: str
    schema_version: str
    document_type: DocumentType
    fields: list[FieldSchema]
    naming: NamingConfig
    categories: list[CategoryRule]
    thresholds: Thresholds

    @property
    def required_fields(self) -> list[FieldSchema]:
        return [f for f in self.fields if f.required]

    @property
    def field_map(self) -> dict[str, FieldSchema]:
        return {f.id: f for f in self.fields}


@dataclass
class RunSummary:
    """Aggregated statistics for a completed batch."""

    batch_id: str
    total: int
    accepted: int
    review: int
    quarantined: int
    failed: int
    skipped: int
    dry_run: bool
    started_at: datetime
    finished_at: datetime

    @property
    def auto_apply_coverage(self) -> float:
        if self.total == 0:
            return 0.0
        return self.accepted / self.total
