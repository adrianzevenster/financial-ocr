# Evaluation Manifests

Evaluation manifests are JSONL files where each line describes one document and its expected extracted values.

## Format

```jsonl
{"path": "path/to/file.pdf", "expected": {"invoice_number": "INV-001", "invoice_date": "2024-01-15", "organization_name": "Acme Ltd", "total_amount": "1234.56", "currency": "GBP"}}
```

### Fields

| Field | Description |
|---|---|
| `path` | Path to the document file, relative to the manifest or absolute. |
| `expected` | Dict of field_id → normalized expected value (strings). Dates in `YYYY-MM-DD`. Amounts as plain decimal strings without currency symbols. Currency as ISO 4217 code. |

## Data requirements

**Manifests must never contain real customer invoice data.** Use only:
- Synthetic invoices generated for testing purposes.
- Publicly available sample invoices with no personally identifiable information.
- Anonymized invoices with supplier names, amounts, and dates replaced with fictional values.

## Running evaluation

```bash
finextract evaluate \
  --manifest evals/manifests/invoice-v1.jsonl \
  --config configs/policies/default-v1.yaml
```

The command exits with code 1 if any required field achieves less than 90% exact-match accuracy on the manifest.

## Held-out set

Keep a separate held-out manifest that developers do not tune against. Use supplier-based or time-based splits to avoid leakage. The held-out set is the primary quality gate before production rollout.
