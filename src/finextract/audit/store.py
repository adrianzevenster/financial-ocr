from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from finextract.domain import (
    AuditError,
    DocumentType,
    MutationOp,
    MutationPlan,
    ProcessingStatus,
    ReasonCode,
    RunEvent,
    RunRecord,
    RunSummary,
    SourceItem,
    SourceType,
)

log = structlog.get_logger()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    document_type TEXT,
    overall_confidence REAL,
    proposed_path TEXT,
    applied_path TEXT,
    error_message TEXT,
    error_reason_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    data TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_runs_item ON runs(item_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id);
"""


class AuditStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._saved_event_count: dict[str, int] = {}
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_run(self, record: RunRecord) -> None:
        row = self._serialize_run(record)
        new_events = self._new_events(record)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, batch_id, item_id, source_path, content_hash,
                        status, schema_version, policy_version, extractor_version,
                        document_type, overall_confidence, proposed_path,
                        applied_path, error_message, error_reason_code,
                        created_at, updated_at
                    ) VALUES (
                        :run_id, :batch_id, :item_id, :source_path, :content_hash,
                        :status, :schema_version, :policy_version, :extractor_version,
                        :document_type, :overall_confidence, :proposed_path,
                        :applied_path, :error_message, :error_reason_code,
                        :created_at, :updated_at
                    )
                    ON CONFLICT(run_id) DO UPDATE SET
                        status = excluded.status,
                        document_type = excluded.document_type,
                        overall_confidence = excluded.overall_confidence,
                        proposed_path = excluded.proposed_path,
                        applied_path = excluded.applied_path,
                        error_message = excluded.error_message,
                        error_reason_code = excluded.error_reason_code,
                        updated_at = excluded.updated_at
                    """,
                    row,
                )
                conn.executemany(
                    """
                    INSERT INTO run_events (run_id, event_type, occurred_at, data)
                    VALUES (:run_id, :event_type, :occurred_at, :data)
                    """,
                    new_events,
                )
        except sqlite3.Error as exc:
            raise AuditError(f"Failed to save run {record.run_id}: {exc}") from exc

        self._saved_event_count[record.run_id] = len(record.events)
        log.debug("audit.saved_run", run_id=record.run_id, status=record.status.value)

    def load_run(self, run_id: str) -> RunRecord | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    return None
                events = conn.execute(
                    "SELECT * FROM run_events WHERE run_id = ? ORDER BY id",
                    (run_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AuditError(f"Failed to load run {run_id}: {exc}") from exc

        record = self._deserialize_run(dict(row), [dict(e) for e in events])
        self._saved_event_count[run_id] = len(record.events)
        return record

    def find_existing(
        self, item_id: str, content_hash: str, policy_version: str
    ) -> RunRecord | None:
        terminal = ("planned", "applied", "skipped")
        placeholders = ",".join("?" * len(terminal))
        try:
            with self._connect() as conn:
                row = conn.execute(
                    f"""
                    SELECT run_id FROM runs
                    WHERE item_id = ?
                      AND content_hash = ?
                      AND policy_version = ?
                      AND status IN ({placeholders})
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (item_id, content_hash, policy_version, *terminal),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AuditError(f"Failed to query existing run: {exc}") from exc

        if row is None:
            return None
        return self.load_run(row["run_id"])

    def get_batch_summary(self, batch_id: str) -> RunSummary:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM runs WHERE batch_id = ? GROUP BY status",
                    (batch_id,),
                ).fetchall()
                meta = conn.execute(
                    "SELECT MIN(created_at) as started, MAX(updated_at) as finished FROM runs WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AuditError(f"Failed to get batch summary: {exc}") from exc

        counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}
        total = sum(counts.values())

        started_at = _parse_dt(meta["started"]) if meta and meta["started"] else datetime.utcnow()
        finished_at = _parse_dt(meta["finished"]) if meta and meta["finished"] else datetime.utcnow()

        return RunSummary(
            batch_id=batch_id,
            total=total,
            accepted=counts.get("applied", 0),
            review=counts.get("planned", 0),
            quarantined=counts.get("quarantined", 0),
            failed=counts.get("failed", 0),
            skipped=counts.get("skipped", 0),
            dry_run=False,
            started_at=started_at,
            finished_at=finished_at,
        )

    def list_quarantined(self, batch_id: str | None = None) -> list[RunRecord]:
        try:
            with self._connect() as conn:
                if batch_id:
                    rows = conn.execute(
                        "SELECT run_id FROM runs WHERE status = 'quarantined' AND batch_id = ?",
                        (batch_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT run_id FROM runs WHERE status = 'quarantined'"
                    ).fetchall()
        except sqlite3.Error as exc:
            raise AuditError(f"Failed to list quarantined runs: {exc}") from exc

        return [self.load_run(r["run_id"]) for r in rows if self.load_run(r["run_id"]) is not None]  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise AuditError(f"Failed to initialize audit database at {self._db_path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _new_events(self, record: RunRecord) -> list[dict[str, Any]]:
        already_saved = self._saved_event_count.get(record.run_id, 0)
        new = record.events[already_saved:]
        return [
            {
                "run_id": record.run_id,
                "event_type": ev.event_type,
                "occurred_at": ev.timestamp.isoformat(),
                "data": json.dumps(ev.data),
            }
            for ev in new
        ]

    def _serialize_run(self, record: RunRecord) -> dict[str, Any]:
        overall_confidence: float | None = None
        if record.validation is not None:
            overall_confidence = record.validation.overall_confidence

        proposed_path: str | None = None
        if record.mutation_plan is not None:
            proposed_path = record.mutation_plan.proposed_path

        return {
            "run_id": record.run_id,
            "batch_id": record.batch_id,
            "item_id": record.source_item.item_id,
            "source_path": record.source_item.original_path,
            "content_hash": record.source_item.content_hash,
            "status": record.status.value,
            "schema_version": record.schema_version,
            "policy_version": record.policy_version,
            "extractor_version": record.extractor_version,
            "document_type": record.document_type.value if record.document_type else None,
            "overall_confidence": overall_confidence,
            "proposed_path": proposed_path,
            "applied_path": record.applied_path,
            "error_message": record.error_message,
            "error_reason_code": record.error_reason_code.value if record.error_reason_code else None,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        }

    def _deserialize_run(self, row: dict[str, Any], events: list[dict[str, Any]]) -> RunRecord:
        source_item = SourceItem(
            source_type=SourceType.LOCAL,
            item_id=row["item_id"],
            original_path=row["source_path"],
            content_hash=row["content_hash"],
            mime_type="",
            file_size=0,
        )

        doc_type: DocumentType | None = None
        if row.get("document_type"):
            try:
                doc_type = DocumentType(row["document_type"])
            except ValueError:
                pass

        error_rc: ReasonCode | None = None
        if row.get("error_reason_code"):
            try:
                error_rc = ReasonCode(row["error_reason_code"])
            except ValueError:
                pass

        run_events = [
            RunEvent(
                event_type=ev["event_type"],
                timestamp=_parse_dt(ev["occurred_at"]),
                data=json.loads(ev["data"]),
            )
            for ev in events
        ]

        # Reconstruct a minimal MutationPlan if proposed_path present
        mutation_plan: MutationPlan | None = None
        if row.get("proposed_path"):
            mutation_plan = MutationPlan(
                item_id=row["item_id"],
                source_path=row["source_path"],
                proposed_path=row["proposed_path"],
                proposed_category=None,
                operation=MutationOp.RENAME,
                reason="restored from audit",
                policy_version=row["policy_version"],
                schema_version=row["schema_version"],
                content_hash=row["content_hash"],
                etag_at_plan_time=None,
                validated_fields={},
            )

        return RunRecord(
            run_id=row["run_id"],
            batch_id=row["batch_id"],
            source_item=source_item,
            status=ProcessingStatus(row["status"]),
            schema_version=row["schema_version"],
            policy_version=row["policy_version"],
            extractor_version=row["extractor_version"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            document_type=doc_type,
            mutation_plan=mutation_plan,
            applied_path=row.get("applied_path"),
            error_message=row.get("error_message"),
            error_reason_code=error_rc,
            events=run_events,
        )


def _parse_dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return datetime.utcnow()
