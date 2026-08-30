from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from finextract.domain import (
    CategoryRule,
    ConfigError,
    DocumentType,
    FieldSchema,
    FieldType,
    NamingConfig,
    PolicyConfig,
    Thresholds,
)


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open() as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"Expected a YAML mapping in {path}")
    return data


def _field_type(raw: str) -> FieldType:
    try:
        return FieldType(raw)
    except ValueError:
        raise ConfigError(f"Unknown field type: {raw!r}. Valid types: {[t.value for t in FieldType]}")


def load_policy(policy_path: Path, schema_path: Path | None = None) -> PolicyConfig:
    """Load and merge a policy YAML with its referenced schema YAML."""
    policy_data = _load_yaml(policy_path)

    # Resolve schema path
    schema_version = policy_data.get("schema_version")
    if not schema_version:
        raise ConfigError(f"policy_version missing 'schema_version' in {policy_path}")

    if schema_path is None:
        schema_path = policy_path.parent.parent / "schemas" / f"{schema_version}.yaml"

    schema_data = _load_yaml(schema_path)

    # --- Parse schema fields ---
    raw_fields = schema_data.get("fields", [])
    if not raw_fields:
        raise ConfigError(f"Schema {schema_path} has no fields defined")

    fields: list[FieldSchema] = []
    for raw in raw_fields:
        try:
            fields.append(
                FieldSchema(
                    id=raw["id"],
                    type=_field_type(raw["type"]),
                    required=bool(raw.get("required", False)),
                    description=raw.get("description", ""),
                )
            )
        except KeyError as exc:
            raise ConfigError(f"Field definition missing key {exc} in {schema_path}")

    # --- Parse document type ---
    doc_type_raw = schema_data.get("document_type", "unknown")
    try:
        doc_type = DocumentType(doc_type_raw)
    except ValueError:
        raise ConfigError(f"Unknown document_type {doc_type_raw!r} in {schema_path}")

    # --- Parse naming ---
    naming_raw = policy_data.get("naming", {})
    naming = NamingConfig(
        template=naming_raw.get(
            "template",
            "{invoice_date}_{organization_slug}_{document_type}_{invoice_number}.{extension}",
        ),
        max_length=int(naming_raw.get("max_length", 180)),
        collision_strategy=naming_raw.get("collision_strategy", "content_hash_suffix"),
        unsafe_chars=naming_raw.get("unsafe_chars", r'[<>:"/\\|?*\x00-\x1f]'),
        reserved_names=naming_raw.get("reserved_names", []),
        component_max_length=int(naming_raw.get("component_max_length", 60)),
    )

    # --- Parse categories ---
    categories: list[CategoryRule] = []
    for cat in policy_data.get("categories", []):
        try:
            categories.append(
                CategoryRule(
                    id=cat["id"],
                    destination=cat["destination"],
                    when=cat["when"],
                )
            )
        except KeyError as exc:
            raise ConfigError(f"Category rule missing key {exc} in {policy_path}")

    # --- Parse thresholds ---
    thresh_raw = policy_data.get("thresholds", {})
    thresholds = Thresholds(
        auto_apply=float(thresh_raw.get("auto_apply", 0.94)),
        manual_review=float(thresh_raw.get("manual_review", 0.75)),
        ocr_min_coverage=float(thresh_raw.get("ocr_min_coverage", 0.30)),
        ocr_min_confidence=float(thresh_raw.get("ocr_min_confidence", 60)),
    )

    policy_version = policy_data.get("policy_version")
    if not policy_version:
        raise ConfigError(f"'policy_version' missing in {policy_path}")

    return PolicyConfig(
        policy_version=policy_version,
        schema_version=schema_data.get("schema_version", schema_version),
        document_type=doc_type,
        fields=fields,
        naming=naming,
        categories=categories,
        thresholds=thresholds,
    )


def default_policy_path() -> Path:
    return Path(__file__).parent.parent.parent.parent / "configs" / "policies" / "default-v1.yaml"
