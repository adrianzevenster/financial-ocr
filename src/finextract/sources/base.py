"""Abstract base class for document sources.

Idempotency contract
--------------------
- ``discover()`` must be safe to call multiple times; it must not modify source state.
- ``download()`` is idempotent: if the destination already contains a file with the
  same content_hash as the item, it must not overwrite it and must return an item
  pointing at the existing file.
- ``apply_mutation()`` with ``dry_run=True`` must never modify anything.
- ``apply_mutation()`` with ``dry_run=False`` must check the current eTag/hash before
  acting and raise ``ETagMismatchError`` if the source changed since the plan was made.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from finextract.domain import MutationPlan, SourceItem


class DocumentSource(ABC):
    """Adapter interface for any document repository (local FS, SharePoint, S3, …)."""

    @abstractmethod
    def discover(self) -> Iterator[SourceItem]:
        """Yield every processable document item in the source.

        Must not download file content; only enumerate identity and metadata.
        Safe to call multiple times without side effects.
        """

    @abstractmethod
    def download(self, item: SourceItem, dest_dir: Path) -> SourceItem:
        """Download *item* to *dest_dir* and return a copy with ``local_path`` set.

        Idempotent: if a file with the same ``content_hash`` already exists in
        ``dest_dir``, skip the download and return the existing path.

        Raises:
            SourceError: on network or I/O failure.
        """

    @abstractmethod
    def check_etag(self, item: SourceItem) -> str | None:
        """Return the current eTag/hash of *item* on the source, or ``None`` if unsupported.

        Used to detect concurrent modification between discovery and mutation.
        """

    @abstractmethod
    def apply_mutation(self, plan: MutationPlan, dry_run: bool = True) -> bool:
        """Execute or simulate the rename/move/metadata update described by *plan*.

        Args:
            plan: The mutation plan produced by the orchestrator.
            dry_run: When ``True``, log the proposed action but make no changes.

        Returns:
            ``True`` if the mutation was applied (or would be applied in dry-run),
            ``False`` if it was skipped (e.g. already in desired state).

        Raises:
            ETagMismatchError: if the source item changed since the plan was made.
            CollisionError: if the proposed path already exists and the policy is ``fail``.
            SourceError: on network or I/O failure.
        """
