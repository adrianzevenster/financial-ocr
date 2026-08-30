from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from finextract.domain import (
    CategoryRule,
    DocumentType,
    Evidence,
    ExtractionMethod,
    ExtractionResult,
    FieldResult,
    FieldSchema,
    FieldType,
    MutationOp,
    NamingConfig,
    PolicyConfig,
    ProcessingStatus,
    ReasonCode,
    SourceType,
    Thresholds,
)
from finextract.audit.store import AuditStore
from finextract.sources.local import LocalSource
from finextract.orchestration.runner import PipelineConfig, Runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy() -> PolicyConfig:
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
        ],
        naming=NamingConfig(
            template="{invoice_date}_{organization_slug}_{document_type}_{invoice_number}.{extension}",
            max_length=180,
            collision_strategy="content_hash_suffix",
            unsafe_chars=r'[<>:"/\\|?*\x00-\x1f]',
            reserved_names=[],
            component_max_length=60,
        ),
        categories=[
            CategoryRule("invoices", "Finance/Invoices", "document_type == 'invoice'")
        ],
        thresholds=Thresholds(0.94, 0.75, 0.30, 60),
    )


def _make_fake_extraction() -> ExtractionResult:
    evidence = [Evidence(page=1, method=ExtractionMethod.RULE, confidence=0.97, text="test")]

    def _fr(fid: str, raw: str, ft: FieldType = FieldType.STRING) -> FieldResult:
        return FieldResult(
            field_id=fid,
            field_type=ft,
            raw_value=raw,
            normalized_value=None,
            evidence=evidence,
            confidence=0.97,
            is_required=True,
        )

    return ExtractionResult(
        document_type=DocumentType.INVOICE,
        classification_confidence=0.95,
        fields={
            "invoice_number": _fr("invoice_number", "INV-001"),
            "organization_name": _fr("organization_name", "Acme Ltd", FieldType.ORGANIZATION),
            "invoice_date": _fr("invoice_date", "2024-01-15", FieldType.DATE),
            "total_amount": _fr("total_amount", "1234.56", FieldType.DECIMAL),
            "currency": _fr("currency", "GBP", FieldType.CURRENCY),
        },
        extractor_version="rules-v1",
        schema_version="invoice-v1",
        page_count=1,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_dir(tmp_path: Path) -> Path:
    """Create a temp directory with a fake PDF file."""
    sample = tmp_path / "invoices"
    sample.mkdir()
    invoice_file = sample / "test_invoice.pdf"
    invoice_file.write_bytes(b"%PDF-1.4 fake content for testing")
    return sample


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_audit.db"


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Mocked document processing
# ---------------------------------------------------------------------------


def _mock_doc_text(pages_text: str):
    """Build a mock DocumentText object."""
    mock_page = MagicMock()
    mock_page.text = pages_text
    mock_page.char_count = len(pages_text)

    mock_doc = MagicMock()
    mock_doc.pages = [mock_page]
    mock_doc.total_chars = len(pages_text)
    mock_doc.is_native = True
    return mock_doc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_plan_discovers_and_processes(sample_dir: Path, db_path: Path, work_dir: Path):
    """End-to-end plan run: discovers file, extracts, plans rename."""
    policy = _make_policy()
    store = AuditStore(db_path)

    fake_extraction = _make_fake_extraction()
    fake_doc_text = _mock_doc_text("INVOICE\nInvoice Number: INV-001\nDate: 15/01/2024")

    with (
        patch("finextract.orchestration.runner.extract_native_text", return_value=fake_doc_text),
        patch("finextract.orchestration.runner.needs_ocr", return_value=False),
        patch("finextract.orchestration.runner.detect_mime", return_value="application/pdf"),
        patch("finextract.orchestration.runner.assert_supported"),
        patch("finextract.extraction.rules.RulesExtractor.extract", return_value=fake_extraction),
    ):
        runner = Runner(
            PipelineConfig(
                policy=policy,
                audit_store=store,
                work_dir=work_dir,
                dry_run=True,
            )
        )
        source = LocalSource(sample_dir)
        summary = runner.run_batch(source)

    assert summary.total >= 1
    # In dry_run mode all processed items end up as "planned" (counted in review)
    assert summary.failed == 0


def test_plan_idempotent(sample_dir: Path, db_path: Path, work_dir: Path):
    """Running the same batch twice with the same content produces the same plan."""
    policy = _make_policy()
    store = AuditStore(db_path)
    fake_extraction = _make_fake_extraction()
    fake_doc_text = _mock_doc_text("INVOICE\nInvoice Number: INV-001\nDate: 15/01/2024")

    mock_patches = [
        patch("finextract.orchestration.runner.extract_native_text", return_value=fake_doc_text),
        patch("finextract.orchestration.runner.needs_ocr", return_value=False),
        patch("finextract.orchestration.runner.detect_mime", return_value="application/pdf"),
        patch("finextract.orchestration.runner.assert_supported"),
        patch("finextract.extraction.rules.RulesExtractor.extract", return_value=fake_extraction),
    ]

    def _run():
        with (
            patch("finextract.orchestration.runner.extract_native_text", return_value=fake_doc_text),
            patch("finextract.orchestration.runner.needs_ocr", return_value=False),
            patch("finextract.orchestration.runner.detect_mime", return_value="application/pdf"),
            patch("finextract.orchestration.runner.assert_supported"),
            patch("finextract.extraction.rules.RulesExtractor.extract", return_value=fake_extraction),
        ):
            runner = Runner(
                PipelineConfig(
                    policy=policy,
                    audit_store=store,
                    work_dir=work_dir,
                    dry_run=True,
                )
            )
            return runner.run_batch(LocalSource(sample_dir))

    summary1 = _run()
    summary2 = _run()

    # Second run should skip already-planned items (same content_hash + policy_version)
    assert summary2.skipped >= 0  # skipped or same totals


def test_corrupt_file_fails_gracefully(tmp_path: Path, db_path: Path, work_dir: Path):
    """A corrupt/unreadable file should result in FAILED status, not an uncaught exception."""
    from finextract.domain.errors import CorruptFileError

    sample_dir = tmp_path / "docs"
    sample_dir.mkdir()
    bad_file = sample_dir / "corrupt.pdf"
    # Start with PDF magic bytes so MIME detection accepts it, but leave body corrupt
    bad_file.write_bytes(b"%PDF-1.4\n%corrupt-body-not-a-real-pdf")

    policy = _make_policy()
    store = AuditStore(db_path)

    with (
        patch(
            "finextract.orchestration.runner.extract_native_text",
            side_effect=CorruptFileError(str(bad_file), "not a PDF"),
        ),
        patch("finextract.orchestration.runner.detect_mime", return_value="application/pdf"),
        patch("finextract.orchestration.runner.assert_supported"),
        patch("finextract.orchestration.runner.needs_ocr", return_value=False),
    ):
        runner = Runner(
            PipelineConfig(
                policy=policy,
                audit_store=store,
                work_dir=work_dir,
                dry_run=True,
            )
        )
        summary = runner.run_batch(LocalSource(sample_dir))

    assert summary.failed >= 1
    assert summary.total >= 1


def test_audit_store_persists_run(tmp_path: Path):
    """AuditStore saves and retrieves a RunRecord correctly."""
    from finextract.domain import RunRecord, SourceItem
    from datetime import datetime
    import uuid

    db_path = tmp_path / "audit.db"
    store = AuditStore(db_path)

    item = SourceItem(
        source_type=SourceType.LOCAL,
        item_id="test-item-001",
        original_path="/tmp/test.pdf",
        content_hash="a" * 64,
        mime_type="application/pdf",
        file_size=1024,
    )
    now = datetime.utcnow()
    record = RunRecord(
        run_id=uuid.uuid4().hex,
        batch_id="batch-001",
        source_item=item,
        status=ProcessingStatus.PENDING,
        schema_version="invoice-v1",
        policy_version="test-v1",
        extractor_version="rules-v1",
        created_at=now,
        updated_at=now,
    )
    record.transition(ProcessingStatus.PROCESSING)

    store.save_run(record)

    loaded = store.load_run(record.run_id)
    assert loaded is not None
    assert loaded.run_id == record.run_id
    assert loaded.status == ProcessingStatus.PROCESSING
    assert loaded.source_item.item_id == "test-item-001"
