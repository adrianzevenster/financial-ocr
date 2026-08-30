"""FastAPI server exposing the finextract pipeline as an HTTP API."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from finextract.documents.detection import assert_supported, detect_mime
from finextract.documents.extraction import extract_images_for_ocr, extract_native_text, needs_ocr
from finextract.documents.ocr import merge_ocr_into_document_text, ocr_document
from finextract.domain import ExtractionResult, FieldResult, FinextractError, UnsupportedMimeError
from finextract.extraction.filename_parser import parse_filename
from finextract.extraction.rules import RulesExtractor
from finextract.policies.loader import load_policy
from finextract.policies.naming import render_filename, resolve_category_destination
from finextract.validation.validator import Validator

app = FastAPI(title="finextract API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load default policy once at startup
_DEFAULT_POLICY_PATH = Path(__file__).parent.parent.parent / "configs" / "policies" / "default-v1.yaml"


def _get_policy(policy_path: Path = _DEFAULT_POLICY_PATH):
    return load_policy(policy_path)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class FieldOut(BaseModel):
    raw: str | None
    normalized: str | None
    confidence: float
    required: bool


class ExtractionResponse(BaseModel):
    document_type: str
    classification_confidence: float
    fields: dict[str, FieldOut]
    proposed_filename: str | None
    proposed_category: str | None
    validation_status: str
    overall_confidence: float
    ocr_used: bool
    page_count: int
    reason_codes: list[str]


class ErrorResponse(BaseModel):
    error: str
    reason_code: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _merge_extractions(
    filename_result: ExtractionResult | None,
    content_result: ExtractionResult,
) -> ExtractionResult:
    """Merge filename-parsed fields (high confidence) with content-extracted fields.

    Filename fields win whenever present; content fields fill any gaps.
    Document type from filename wins unless it's UNKNOWN.
    """
    if filename_result is None:
        return content_result

    merged_fields: dict[str, FieldResult] = {}

    # Start with content fields as the base
    merged_fields.update(content_result.fields)

    # Overwrite with filename fields (higher confidence)
    merged_fields.update(filename_result.fields)

    doc_type = filename_result.document_type
    class_conf = filename_result.classification_confidence
    if doc_type.value == "unknown":
        doc_type = content_result.document_type
        class_conf = content_result.classification_confidence

    from finextract.domain import DocumentType as _DT  # avoid circular at top-level
    return ExtractionResult(
        document_type=doc_type,
        classification_confidence=class_conf,
        fields=merged_fields,
        extractor_version=f"{filename_result.extractor_version}+{content_result.extractor_version}",
        schema_version=content_result.schema_version,
        page_count=content_result.page_count,
        raw_text_length=content_result.raw_text_length,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}


@app.post("/extract", response_model=ExtractionResponse)
async def extract(file: UploadFile = File(...)) -> ExtractionResponse:
    """Upload a PDF or image and receive extraction results."""
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    original_filename = file.filename or "upload.pdf"
    suffix = Path(original_filename).suffix or ".pdf"

    with tempfile.TemporaryDirectory() as tmp:
        local_path = Path(tmp) / f"upload{suffix}"
        local_path.write_bytes(content)

        try:
            mime = detect_mime(local_path)
            assert_supported(local_path, mime)
        except UnsupportedMimeError as exc:
            raise HTTPException(status_code=415, detail=str(exc))
        except FinextractError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            doc_text = extract_native_text(local_path)
            ocr_used = False
            policy = _get_policy()

            if needs_ocr(doc_text, policy.thresholds.ocr_min_coverage):
                image_pages = extract_images_for_ocr(local_path)
                ocr_result = ocr_document(image_pages)
                doc_text = merge_ocr_into_document_text(doc_text, ocr_result)
                ocr_used = True
        except FinextractError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Text extraction failed: {exc}")

        try:
            full_text = "\n\n".join(p.text for p in doc_text.pages)
            page_texts = [p.text for p in doc_text.pages]

            # Parse filename first — it's authoritative when it follows the SA convention
            filename_extraction = parse_filename(original_filename)

            extractor = RulesExtractor(schema_version=policy.schema_version)
            content_extraction = extractor.extract(full_text, page_texts, policy)

            # Merge: filename fields override content fields; content fills gaps
            extraction = _merge_extractions(filename_extraction, content_extraction)

            validator = Validator(policy)
            validation, normalized_fields = validator.validate(extraction)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}")

    # Build naming fields
    naming_fields: dict[str, Any] = {"document_type": extraction.document_type.value}
    for fid, fr in normalized_fields.items():
        val = fr.normalized_value if fr.normalized_value is not None else fr.raw_value
        if val is not None:
            naming_fields[fid] = val

    # Propose filename
    proposed_filename: str | None = None
    proposed_category: str | None = None
    try:
        proposed_filename = render_filename(
            policy=policy,
            fields=naming_fields,
            extension=suffix,
            content_hash="0" * 64,
        )
        proposed_category = resolve_category_destination(policy, naming_fields)
    except Exception:
        pass

    # Build field output
    field_out: dict[str, FieldOut] = {}
    for field_schema in policy.fields:
        fid = field_schema.id
        norm_fr = normalized_fields.get(fid)
        raw_fr = extraction.fields.get(fid)

        raw_val = raw_fr.raw_value if raw_fr else None
        norm_val = str(norm_fr.normalized_value) if (norm_fr and norm_fr.normalized_value is not None) else raw_val
        conf = norm_fr.confidence if norm_fr else (raw_fr.confidence if raw_fr else 0.0)

        field_out[fid] = FieldOut(
            raw=raw_val,
            normalized=norm_val,
            confidence=round(conf, 3),
            required=field_schema.required,
        )

    return ExtractionResponse(
        document_type=extraction.document_type.value,
        classification_confidence=round(extraction.classification_confidence, 3),
        fields=field_out,
        proposed_filename=proposed_filename,
        proposed_category=proposed_category,
        validation_status=validation.status.value,
        overall_confidence=round(validation.overall_confidence, 3),
        ocr_used=ocr_used,
        page_count=doc_text.total_chars and len(doc_text.pages),
        reason_codes=[rc.value for rc in validation.reason_codes],
    )
