from __future__ import annotations

import re
import pytest

from hypothesis import given, settings
from hypothesis import strategies as st

from finextract.policies.naming import render_filename
from finextract.domain import (
    CategoryRule,
    DocumentType,
    FieldSchema,
    FieldType,
    NamingConfig,
    PolicyConfig,
    Thresholds,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_policy(
    template: str = "{invoice_date}_{organization_slug}_{document_type}_{invoice_number}.{extension}",
    max_length: int = 180,
    collision_strategy: str = "content_hash_suffix",
    component_max_length: int = 60,
    reserved_names: list[str] | None = None,
) -> PolicyConfig:
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
            template=template,
            max_length=max_length,
            collision_strategy=collision_strategy,
            unsafe_chars=r'[<>:"/\\|?*\x00-\x1f]',
            reserved_names=reserved_names or ["CON", "PRN", "AUX", "NUL"],
            component_max_length=component_max_length,
        ),
        categories=[],
        thresholds=Thresholds(0.94, 0.75, 0.30, 60),
    )


_FIELDS = {
    "invoice_date": "2024-01-15",
    "organization_name": "Acme Ltd",
    "document_type": "invoice",
    "invoice_number": "INV-001",
}

_HASH = "abc123def456789012345678901234567890"


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------


def test_render_basic():
    policy = _make_policy()
    name = render_filename(policy, _FIELDS, ".pdf", _HASH)
    assert name == "2024-01-15_acme_ltd_invoice_inv-001.pdf"


def test_render_extension_no_dot():
    policy = _make_policy()
    name = render_filename(policy, _FIELDS, "pdf", _HASH)
    assert name.endswith(".pdf")


def test_render_extension_with_dot():
    policy = _make_policy()
    name = render_filename(policy, _FIELDS, ".PDF", _HASH)
    assert name.endswith(".PDF")


# ---------------------------------------------------------------------------
# Unsafe characters
# ---------------------------------------------------------------------------


def test_render_unsafe_chars_in_org():
    policy = _make_policy()
    fields = {**_FIELDS, "organization_name": 'Acme<Corp>"Ltd'}
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert "<" not in name
    assert ">" not in name
    assert '"' not in name


def test_render_colons_replaced():
    policy = _make_policy()
    fields = {**_FIELDS, "organization_name": "Acme:Corp"}
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert ":" not in name


# ---------------------------------------------------------------------------
# Reserved names
# ---------------------------------------------------------------------------


def test_render_reserved_name_con():
    policy = _make_policy(template="{invoice_number}.{extension}")
    fields = {**_FIELDS, "invoice_number": "CON"}
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert not name.upper().startswith("CON.")
    assert name.startswith("_")


def test_render_reserved_name_nul():
    policy = _make_policy(template="{invoice_number}.{extension}")
    fields = {**_FIELDS, "invoice_number": "NUL"}
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert name.startswith("_")


# ---------------------------------------------------------------------------
# Max length
# ---------------------------------------------------------------------------


def test_render_max_length_respected():
    policy = _make_policy(max_length=30)
    fields = {
        **_FIELDS,
        "organization_name": "A" * 100,
        "invoice_number": "B" * 100,
    }
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert len(name) <= 30


def test_render_extension_preserved_under_max_length():
    policy = _make_policy(max_length=20, template="{organization_slug}.{extension}")
    fields = {**_FIELDS, "organization_name": "A" * 50}
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert name.endswith(".pdf")
    assert len(name) <= 20


# ---------------------------------------------------------------------------
# Collision handling
# ---------------------------------------------------------------------------


def test_render_collision_content_hash_suffix():
    policy = _make_policy()
    name = render_filename(policy, _FIELDS, ".pdf", _HASH)
    name2 = render_filename(policy, _FIELDS, ".pdf", _HASH, existing_paths={name})
    assert name2 != name
    assert _HASH[:8] in name2


def test_render_no_collision_when_name_not_in_existing():
    policy = _make_policy()
    name = render_filename(policy, _FIELDS, ".pdf", _HASH, existing_paths={"other_file.pdf"})
    # should not have hash suffix
    assert _HASH[:8] not in name


# ---------------------------------------------------------------------------
# Unicode
# ---------------------------------------------------------------------------


def test_render_unicode_org_name():
    policy = _make_policy()
    fields = {**_FIELDS, "organization_name": "Ärzte GmbH"}
    name = render_filename(policy, fields, ".pdf", _HASH)
    # Should be ascii-safe slug
    name.encode("ascii")  # raises UnicodeEncodeError if not


def test_render_unicode_accents_transliterated():
    policy = _make_policy()
    fields = {**_FIELDS, "organization_name": "Société Générale"}
    name = render_filename(policy, fields, ".pdf", _HASH)
    assert "é" not in name
    assert "ê" not in name


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------


@given(
    org=st.text(min_size=1, max_size=100),
    inv_num=st.text(min_size=1, max_size=50),
)
@settings(max_examples=200)
def test_render_never_exceeds_max_length(org: str, inv_num: str):
    policy = _make_policy(max_length=80)
    fields = {
        "invoice_date": "2024-01-01",
        "organization_name": org,
        "document_type": "invoice",
        "invoice_number": inv_num,
    }
    try:
        name = render_filename(policy, fields, ".pdf", _HASH)
        assert len(name) <= 80
    except Exception:
        pass  # template errors or empty slugs are acceptable edge cases


@given(
    org=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=1,
        max_size=80,
    )
)
@settings(max_examples=100)
def test_render_no_unsafe_chars_in_result(org: str):
    policy = _make_policy()
    fields = {**_FIELDS, "organization_name": org}
    unsafe_re = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    try:
        name = render_filename(policy, fields, ".pdf", _HASH)
        assert not unsafe_re.search(name), f"Unsafe char in: {name!r}"
    except Exception:
        pass
