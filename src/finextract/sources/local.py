"""Local filesystem document source adapter."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from pathlib import Path

import structlog

from finextract.domain import (
    CollisionError,
    ETagMismatchError,
    MutationOp,
    MutationPlan,
    SourceItem,
    SourceType,
)

from .base import DocumentSource

log = structlog.get_logger(__name__)

SUPPORTED_MIMES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "image/webp",
    }
)

_SUFFIX_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _detect_mime(path: Path) -> str:
    try:
        import magic  # type: ignore[import-untyped]

        mime = magic.from_file(str(path), mime=True)
        if mime:
            return mime
    except Exception:
        pass
    return _SUFFIX_MIME.get(path.suffix.lower(), "application/octet-stream")


class LocalSource(DocumentSource):
    """Discovers and mutates documents on the local filesystem."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def discover(self) -> Iterator[SourceItem]:
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            mime = _detect_mime(path)
            if mime not in SUPPORTED_MIMES:
                continue
            try:
                content_hash = _sha256_file(path)
                size = path.stat().st_size
            except OSError as exc:
                log.warning("local.discover.skip", path=str(path), reason=str(exc))
                continue

            item_id = _sha256_str(str(path))
            log.debug("local.discover.found", path=str(path), mime=mime, hash=content_hash[:8])
            yield SourceItem(
                source_type=SourceType.LOCAL,
                item_id=item_id,
                original_path=str(path),
                content_hash=content_hash,
                mime_type=mime,
                file_size=size,
                etag=content_hash,  # content hash serves as etag for local files
                local_path=str(path),
            )

    def download(self, item: SourceItem, dest_dir: Path) -> SourceItem:
        """Copy file to dest_dir; idempotent if same hash already present."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        source_path = Path(item.original_path)
        dest_path = dest_dir / source_path.name

        if dest_path.exists():
            existing_hash = _sha256_file(dest_path)
            if existing_hash == item.content_hash:
                log.debug(
                    "local.download.cached",
                    dest=str(dest_path),
                    hash=item.content_hash[:8],
                )
                import dataclasses

                return dataclasses.replace(item, local_path=str(dest_path))
            # Different content — use a unique name to avoid overwrite
            dest_path = dest_dir / f"{item.content_hash[:8]}_{source_path.name}"

        shutil.copy2(source_path, dest_path)
        log.info("local.download.copied", src=str(source_path), dest=str(dest_path))
        import dataclasses

        return dataclasses.replace(item, local_path=str(dest_path))

    def check_etag(self, item: SourceItem) -> str | None:
        """Return current content hash of the file; None if file no longer exists."""
        path = Path(item.original_path)
        if not path.exists():
            return None
        return _sha256_file(path)

    def apply_mutation(self, plan: MutationPlan, dry_run: bool = True) -> bool:
        source = Path(plan.source_path)
        proposed = Path(plan.proposed_path)

        logger = log.bind(
            item_id=plan.item_id,
            source=str(source),
            proposed=str(proposed),
            op=plan.operation.value,
            dry_run=dry_run,
        )

        if not source.exists():
            logger.warning("local.mutation.source_missing")
            return False

        # eTag guard
        if plan.etag_at_plan_time is not None:
            current_hash = _sha256_file(source)
            if current_hash != plan.etag_at_plan_time:
                raise ETagMismatchError(plan.item_id, plan.etag_at_plan_time, current_hash)

        if plan.operation == MutationOp.METADATA_UPDATE:
            logger.info("local.mutation.metadata_only_noop")
            return True

        if proposed.exists():
            raise CollisionError(str(proposed))

        if dry_run:
            logger.info("local.mutation.dry_run", reason=plan.reason)
            return True

        proposed.parent.mkdir(parents=True, exist_ok=True)

        if plan.operation in (MutationOp.RENAME, MutationOp.MOVE):
            source.rename(proposed)
            logger.info("local.mutation.applied", reason=plan.reason)
            return True

        logger.warning("local.mutation.unknown_op", op=plan.operation.value)
        return False
