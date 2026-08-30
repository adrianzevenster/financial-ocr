from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import structlog
from rich.console import Console
from rich.table import Table

console = Console()


def _configure_logging(verbose: bool) -> None:
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if verbose else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING)


@click.group()
@click.option("--verbose/--no-verbose", default=False, help="Enable verbose structured logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """finextract — financial document extraction and reclassification pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _configure_logging(verbose)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--source", type=click.Choice(["local"]), default="local", show_default=True)
@click.option("--input", "input_path", required=True, type=click.Path(exists=True), help="Input directory.")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True), help="Policy YAML path.")
@click.option("--output", "output_path", default=None, type=click.Path(), help="Write plan JSON to this path.")
@click.option("--db", "db_path", default="audit.db", show_default=True, help="Audit database path.")
@click.pass_context
def plan(
    ctx: click.Context,
    source: str,
    input_path: str,
    config_path: str,
    output_path: str | None,
    db_path: str,
) -> None:
    """Discover documents, run extraction, and produce a rename plan without applying it."""
    from finextract.policies.loader import load_policy
    from finextract.sources.local import LocalSource
    from finextract.audit.store import AuditStore
    from finextract.orchestration.runner import PipelineConfig, Runner

    policy = load_policy(Path(config_path))
    store = AuditStore(Path(db_path))
    src = LocalSource(Path(input_path))

    with tempfile.TemporaryDirectory() as tmp:
        runner = Runner(
            PipelineConfig(
                policy=policy,
                audit_store=store,
                work_dir=Path(tmp),
                dry_run=True,
            )
        )
        summary = runner.run_batch(src)

    _print_summary(summary)

    if output_path:
        records = store.list_quarantined()  # placeholder — extend to list all
        _write_plan_json(Path(output_path), store, summary.batch_id)
        console.print(f"[green]Plan written to {output_path}[/green]")


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--plan", "plan_path", required=True, type=click.Path(exists=True), help="Plan JSON produced by 'plan'.")
@click.option("--db", "db_path", default="audit.db", show_default=True)
@click.pass_context
def apply(ctx: click.Context, plan_path: str, db_path: str) -> None:
    """Apply a previously produced rename plan."""
    from finextract.audit.store import AuditStore
    from finextract.sources.local import LocalSource
    from finextract.domain import ProcessingStatus

    store = AuditStore(Path(db_path))

    with open(plan_path) as fh:
        plan_records = json.load(fh)

    applied = 0
    for entry in plan_records:
        run_id = entry.get("run_id")
        if not run_id:
            continue
        record = store.load_run(run_id)
        if record is None or record.status != ProcessingStatus.PLANNED:
            continue
        if record.mutation_plan is None:
            continue

        source_root = Path(record.source_item.original_path).parent
        src = LocalSource(source_root)
        success = src.apply_mutation(record.mutation_plan, dry_run=False)
        if success:
            record.applied_path = record.mutation_plan.proposed_path
            record.transition(ProcessingStatus.APPLIED)
            store.save_run(record)
            applied += 1
            console.print(f"[green]Applied:[/green] {record.source_item.original_path} → {record.mutation_plan.proposed_path}")
        else:
            console.print(f"[yellow]Skipped:[/yellow] {record.source_item.original_path}")

    console.print(f"\n{applied} mutation(s) applied.")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.option("--dry-run/--apply", default=True, show_default=True)
@click.option("--db", "db_path", default="audit.db", show_default=True)
@click.pass_context
def sync(ctx: click.Context, config_path: str, dry_run: bool, db_path: str) -> None:
    """Sync and reclassify documents from SharePoint."""
    required_env = [
        "SHAREPOINT_TENANT_ID",
        "SHAREPOINT_CLIENT_ID",
        "SHAREPOINT_SITE_ID",
        "SHAREPOINT_DRIVE_ID",
    ]
    missing = [v for v in required_env if not os.getenv(v)]
    if missing:
        console.print(
            "[red]SharePoint sync not yet configured.[/red]\n"
            "Set the following environment variables:\n"
            + "\n".join(f"  {v}" for v in missing)
        )
        sys.exit(1)

    from finextract.policies.loader import load_policy
    from finextract.sources.sharepoint import SharePointConfig, SharePointSource
    from finextract.audit.store import AuditStore
    from finextract.orchestration.runner import PipelineConfig, Runner
    from pathlib import Path as _Path

    policy = load_policy(_Path(config_path))
    store = AuditStore(_Path(db_path))

    sp_config = SharePointConfig(
        tenant_id=os.environ["SHAREPOINT_TENANT_ID"],
        client_id=os.environ["SHAREPOINT_CLIENT_ID"],
        site_id=os.environ["SHAREPOINT_SITE_ID"],
        drive_id=os.environ["SHAREPOINT_DRIVE_ID"],
        source_folders=os.getenv("SHAREPOINT_SOURCE_FOLDERS", "").split(","),
        cert_path=_Path(os.environ["SHAREPOINT_CERT_PATH"]) if os.getenv("SHAREPOINT_CERT_PATH") else None,
        device_code_auth=not bool(os.getenv("SHAREPOINT_CERT_PATH")),
    )
    src = SharePointSource(sp_config)

    with tempfile.TemporaryDirectory() as tmp:
        runner = Runner(
            PipelineConfig(
                policy=policy,
                audit_store=store,
                work_dir=_Path(tmp),
                dry_run=dry_run,
            )
        )
        summary = runner.run_batch(src)

    _print_summary(summary)


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--manifest", required=True, type=click.Path(exists=True), help="JSONL evaluation manifest.")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True))
@click.pass_context
def evaluate(ctx: click.Context, manifest: str, config_path: str) -> None:
    """Run extraction against a golden-set manifest and report per-field accuracy."""
    from finextract.policies.loader import load_policy
    from finextract.documents.extraction import extract_native_text, needs_ocr, extract_images_for_ocr
    from finextract.documents.ocr import ocr_document, merge_ocr_into_document_text
    from finextract.extraction.rules import RulesExtractor
    from finextract.validation.validator import Validator
    from finextract.validation.normalizers import normalize_field

    policy = load_policy(Path(config_path))
    extractor = RulesExtractor(schema_version=policy.schema_version)
    validator = Validator(policy)

    field_ids = [f.id for f in policy.required_fields]
    correct: dict[str, int] = {fid: 0 for fid in field_ids}
    total_docs = 0
    all_correct_docs = 0

    with open(manifest) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            path = Path(entry["path"])
            expected: dict[str, str] = entry.get("expected", {})

            if not path.exists():
                console.print(f"[yellow]Skip (not found):[/yellow] {path}")
                continue

            try:
                doc_text = extract_native_text(path)
                if needs_ocr(doc_text, policy.thresholds.ocr_min_coverage):
                    image_pages = extract_images_for_ocr(path)
                    ocr_result = ocr_document(image_pages)
                    doc_text = merge_ocr_into_document_text(doc_text, ocr_result)

                full_text = "\n\n".join(p.text for p in doc_text.pages)
                page_texts = [p.text for p in doc_text.pages]
                extraction = extractor.extract(full_text, page_texts, policy)
                _, normalized = validator.validate(extraction)
            except Exception as exc:
                console.print(f"[red]Error:[/red] {path}: {exc}")
                total_docs += 1
                continue

            total_docs += 1
            doc_all_correct = True
            for fid in field_ids:
                exp = expected.get(fid)
                if exp is None:
                    continue
                got_fr = normalized.get(fid)
                got = str(got_fr.normalized_value) if (got_fr and got_fr.normalized_value is not None) else ""
                match = got.strip() == exp.strip()
                if match:
                    correct[fid] += 1
                else:
                    doc_all_correct = False
            if doc_all_correct:
                all_correct_docs += 1

    # Report
    table = Table(title="Evaluation Results")
    table.add_column("Field", style="bold")
    table.add_column("Correct")
    table.add_column("Total")
    table.add_column("Accuracy", style="green")

    failed = False
    for fid in field_ids:
        acc = correct[fid] / total_docs if total_docs else 0.0
        color = "green" if acc >= 0.90 else "red"
        table.add_row(fid, str(correct[fid]), str(total_docs), f"[{color}]{acc:.1%}[/{color}]")
        if acc < 0.90:
            failed = True

    console.print(table)
    doc_acc = all_correct_docs / total_docs if total_docs else 0.0
    console.print(f"\nAll-fields-correct: {all_correct_docs}/{total_docs} ({doc_acc:.1%})")

    if failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print component versions."""
    from finextract import __version__
    from finextract.extraction.rules import RulesExtractor

    extractor = RulesExtractor()
    console.print(f"finextract {__version__}")
    console.print(f"  extractor: {extractor.version}")
    console.print(f"  schema:    {extractor.schema_version}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_summary(summary: Any) -> None:
    table = Table(title=f"Batch {summary.batch_id}")
    table.add_column("Status", style="bold")
    table.add_column("Count")
    table.add_row("Total", str(summary.total))
    table.add_row("[green]Applied[/green]", str(summary.accepted))
    table.add_row("[yellow]Planned/Review[/yellow]", str(summary.review))
    table.add_row("[red]Quarantined[/red]", str(summary.quarantined))
    table.add_row("[red]Failed[/red]", str(summary.failed))
    table.add_row("Skipped", str(summary.skipped))
    console.print(table)


def _write_plan_json(output_path: Path, store: Any, batch_id: str) -> None:
    from finextract.domain import ProcessingStatus
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Load all runs for this batch via quarantined + direct DB query
    # We serialize key fields only — no document text
    try:
        import sqlite3
        conn = sqlite3.connect(store._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM runs WHERE batch_id = ?", (batch_id,)
        ).fetchall()
        conn.close()
    except Exception:
        rows = []

    records = []
    for row in rows:
        records.append(dict(row))

    with open(output_path, "w") as fh:
        json.dump(records, fh, indent=2, default=str)
