from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from finextract.domain import CollisionError, NamingConfig, PolicyConfig


# Characters to transliterate before slug generation
_TRANSLITERATION = str.maketrans(
    "àáâãäåæçèéêëìíîïðñòóôõöùúûüýþß",
    "aaaaaaaceeeeiiiidnooooouuuuyts",
)

_MULTI_UNDERSCORE = re.compile(r"_+")
_LEADING_TRAILING = re.compile(r"^[_.\-]+|[_.\-]+$")


def _slugify(text: str, max_length: int, unsafe_pattern: re.Pattern[str]) -> str:
    """Convert free text to a filesystem-safe slug."""
    text = text.lower().translate(_TRANSLITERATION)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = unsafe_pattern.sub("_", text)
    text = re.sub(r"[\s,;]+", "_", text)
    text = _MULTI_UNDERSCORE.sub("_", text)
    text = _LEADING_TRAILING.sub("", text)
    return text[:max_length] if max_length else text


def _is_reserved(stem: str, reserved_names: list[str]) -> bool:
    upper = stem.upper()
    return any(upper == r.upper() or upper.startswith(r.upper() + ".") for r in reserved_names)


def _hash_suffix(content_hash: str, length: int = 8) -> str:
    return content_hash[:length]


def render_filename(
    policy: PolicyConfig,
    fields: dict[str, Any],
    extension: str,
    content_hash: str,
    existing_paths: set[str] | None = None,
) -> str:
    """Render a safe, policy-compliant filename from validated fields.

    Raises CollisionError if collision_strategy is 'fail' and a collision exists.
    """
    cfg: NamingConfig = policy.naming
    unsafe_re = re.compile(cfg.unsafe_chars)
    ml = cfg.component_max_length

    # Build template variables
    invoice_date = fields.get("invoice_date", "")
    if isinstance(invoice_date, datetime):
        invoice_date = invoice_date.date().isoformat()

    org_raw = fields.get("organization_name", "unknown")
    org_slug = _slugify(str(org_raw), ml, unsafe_re)
    if not org_slug:
        org_slug = "unknown"

    inv_num_raw = fields.get("invoice_number", "")
    inv_num_slug = _slugify(str(inv_num_raw), ml, unsafe_re)

    doc_type = str(fields.get("document_type", "invoice"))

    ext = extension.lstrip(".")

    variables: dict[str, str] = {
        "invoice_date": str(invoice_date),
        "organization_slug": org_slug,
        "document_type": doc_type,
        "invoice_number": inv_num_slug,
        "extension": ext,
    }

    try:
        name = cfg.template.format(**variables)
    except KeyError as exc:
        raise ValueError(f"Template references unknown variable {exc}: {cfg.template!r}")

    # Safety: strip unsafe chars from the whole name (not just components)
    name = unsafe_re.sub("_", name)
    name = _MULTI_UNDERSCORE.sub("_", name)

    # Enforce max length, preserving extension
    stem, *dot_ext = name.rsplit(".", 1)
    full_ext = f".{dot_ext[0]}" if dot_ext else ""
    max_stem = cfg.max_length - len(full_ext)
    if len(stem) > max_stem:
        stem = stem[:max_stem]
    name = stem + full_ext

    # Reserved name check
    stem_check = Path(name).stem
    if _is_reserved(stem_check, cfg.reserved_names):
        name = f"_{name}"

    # Collision handling
    if existing_paths and name in existing_paths:
        strategy = cfg.collision_strategy
        if strategy == "content_hash_suffix":
            suffix = _hash_suffix(content_hash)
            stem_no_ext = Path(name).stem
            name = f"{stem_no_ext}_{suffix}{full_ext}"
        elif strategy == "fail":
            raise CollisionError(name)
        # "skip" strategy: caller decides

    return name


def resolve_category_destination(policy: PolicyConfig, fields: dict[str, Any]) -> str | None:
    """Return the first matching category destination, with date variables expanded."""
    doc_type = str(fields.get("document_type", ""))

    for rule in policy.categories:
        # Simple equality expression: "document_type == 'invoice'"
        # Parse and evaluate safely
        match = re.match(
            r"^\s*(\w+)\s*==\s*['\"]([^'\"]+)['\"]\s*$",
            rule.when,
        )
        if match:
            field_name, expected_value = match.group(1), match.group(2)
            actual = str(fields.get(field_name, ""))
            if actual != expected_value:
                continue

        invoice_date = fields.get("invoice_date")
        year = month = ""
        if isinstance(invoice_date, datetime):
            year = invoice_date.strftime("%Y")
            month = invoice_date.strftime("%m")
        elif isinstance(invoice_date, str) and len(invoice_date) >= 7:
            year = invoice_date[:4]
            month = invoice_date[5:7]

        dest = rule.destination
        dest = dest.replace("{invoice_date_year}", year)
        dest = dest.replace("{invoice_date_month}", month)
        return dest

    return None
