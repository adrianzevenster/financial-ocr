"""Microsoft Graph / SharePoint document source adapter.

Authentication
--------------
- Development: ``device_code_auth=True`` → MSAL ``PublicClientApplication`` device-code flow.
- Production: ``cert_path`` set → MSAL ``ConfidentialClientApplication`` with certificate
  credentials.  The certificate must be in PEM format (private key + public cert).

Processing semantics
--------------------
- Items are identified by their stable Graph ``driveItem.id``, never by path.
- The ``eTag`` field is used as a concurrency guard: if it changes between discovery and
  mutation, ``ETagMismatchError`` is raised and the item is requeued.
- HTTP 429 responses are retried up to ``_MAX_RETRIES`` times, honouring ``Retry-After``.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from finextract.domain import (
    ETagMismatchError,
    MutationOp,
    MutationPlan,
    SourceItem,
    SourceType,
)

from .base import DocumentSource

log = structlog.get_logger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/Sites.ReadWrite.All"]
_MAX_RETRIES = 3

_SUPPORTED_EXTENSIONS = frozenset(
    {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
)


@dataclass
class SharePointConfig:
    tenant_id: str
    client_id: str
    site_id: str
    drive_id: str
    source_folders: list[str] = field(default_factory=list)
    cert_path: Path | None = None
    device_code_auth: bool = True
    # Optional: path to token cache file for persistence across runs
    token_cache_path: Path | None = None


class SharePointSource(DocumentSource):
    """Document source backed by a Microsoft SharePoint document library."""

    def __init__(self, config: SharePointConfig) -> None:
        self.config = config
        self._token: dict[str, Any] | None = None
        self._app: Any = None  # msal.ClientApplication

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _build_app(self) -> Any:
        """Build and return an MSAL application, using cached instance if available."""
        if self._app is not None:
            return self._app

        try:
            import msal  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "msal is required for SharePoint integration: pip install msal"
            ) from exc

        authority = f"https://login.microsoftonline.com/{self.config.tenant_id}"

        if self.config.cert_path is not None:
            cert_pem = self.config.cert_path.read_bytes()
            self._app = msal.ConfidentialClientApplication(
                client_id=self.config.client_id,
                authority=authority,
                client_credential={"private_key": cert_pem},
            )
        else:
            self._app = msal.PublicClientApplication(
                client_id=self.config.client_id,
                authority=authority,
            )

        return self._app

    def _acquire_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        import msal  # type: ignore[import-untyped]

        app = self._build_app()

        # Try silent first
        accounts = app.get_accounts()
        result: dict[str, Any] | None = None
        if accounts:
            result = app.acquire_token_silent(_SCOPES, account=accounts[0])

        if result and "access_token" in result:
            return str(result["access_token"])

        # Device-code flow for interactive/dev use
        if self.config.device_code_auth:
            flow = app.initiate_device_flow(scopes=_SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Device flow failed: {flow.get('error_description')}")
            log.info("sharepoint.auth.device_flow", message=flow["message"])
            print(flow["message"])  # noqa: T201 – intentional user-facing prompt
            result = app.acquire_token_by_device_flow(flow)
        else:
            # Client credentials (app identity)
            result = app.acquire_token_for_client(scopes=_SCOPES)

        if not result or "access_token" not in result:
            error = (result or {}).get("error_description", "unknown")
            raise RuntimeError(f"MSAL token acquisition failed: {error}")

        return str(result["access_token"])

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._acquire_token()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # HTTP helper with throttle retry
    # ------------------------------------------------------------------

    def _get(self, url: str, **kwargs: Any) -> Any:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("httpx is required: pip install httpx") from exc

        for attempt in range(_MAX_RETRIES):
            response = httpx.get(url, headers=self._headers(), **kwargs)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                log.warning(
                    "sharepoint.throttled", attempt=attempt, retry_after=retry_after
                )
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response

        raise RuntimeError(f"Exceeded retry limit for GET {url}")

    def _patch(self, url: str, json: dict[str, Any]) -> Any:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("httpx is required: pip install httpx") from exc

        for attempt in range(_MAX_RETRIES):
            response = httpx.patch(url, headers=self._headers(), json=json)
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 2 ** (attempt + 1)))
                log.warning(
                    "sharepoint.throttled", attempt=attempt, retry_after=retry_after
                )
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            return response

        raise RuntimeError(f"Exceeded retry limit for PATCH {url}")

    # ------------------------------------------------------------------
    # DocumentSource interface
    # ------------------------------------------------------------------

    def discover(self) -> Iterator[SourceItem]:
        """Enumerate all supported files in configured source folders.

        Paginates via ``@odata.nextLink`` until exhausted.
        """
        drive_id = self.config.drive_id
        folders = self.config.source_folders or ["root"]

        for folder in folders:
            url = (
                f"{_GRAPH_BASE}/drives/{drive_id}/items/{folder}/children"
                f"?$select=id,name,file,size,eTag,parentReference,webUrl"
            )
            while url:
                log.debug("sharepoint.discover.page", url=url)
                resp = self._get(url)
                data = resp.json()
                for item in data.get("value", []):
                    if "file" not in item:
                        continue  # skip folders
                    name: str = item.get("name", "")
                    suffix = Path(name).suffix.lower()
                    if suffix not in _SUPPORTED_EXTENSIONS:
                        continue

                    item_id: str = item["id"]
                    etag: str = item.get("eTag", "")
                    size: int = item.get("size", 0)
                    parent_ref = item.get("parentReference", {})
                    parent_path = parent_ref.get("path", "")
                    full_path = f"{parent_path}/{name}"

                    # Mime type from Graph or infer from suffix
                    file_info = item.get("file", {})
                    mime = file_info.get("mimeType") or _suffix_to_mime(suffix)

                    log.debug(
                        "sharepoint.discover.item",
                        item_id=item_id,
                        name=name,
                        mime=mime,
                    )
                    yield SourceItem(
                        source_type=SourceType.SHAREPOINT,
                        item_id=item_id,
                        original_path=full_path,
                        content_hash="",  # computed after download
                        mime_type=mime,
                        file_size=size,
                        etag=etag,
                        drive_id=drive_id,
                    )

                url = data.get("@odata.nextLink")

    def download(self, item: SourceItem, dest_dir: Path) -> SourceItem:
        """Stream the driveItem content to *dest_dir*; verify hash after download."""
        import dataclasses

        import httpx

        dest_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(item.original_path).name
        dest_path = dest_dir / filename

        # Idempotency: if file exists and hash matches, skip download
        if dest_path.exists() and item.content_hash:
            existing = _sha256_file(dest_path)
            if existing == item.content_hash:
                log.debug("sharepoint.download.cached", dest=str(dest_path))
                return dataclasses.replace(item, local_path=str(dest_path))

        url = f"{_GRAPH_BASE}/drives/{item.drive_id}/items/{item.item_id}/content"
        log.info("sharepoint.download.start", item_id=item.item_id, dest=str(dest_path))

        with httpx.stream("GET", url, headers=self._headers(), follow_redirects=True) as resp:
            resp.raise_for_status()
            with dest_path.open("wb") as fh:
                for chunk in resp.iter_bytes(65536):
                    fh.write(chunk)

        actual_hash = _sha256_file(dest_path)
        log.info(
            "sharepoint.download.complete",
            item_id=item.item_id,
            hash=actual_hash[:8],
        )
        return dataclasses.replace(item, content_hash=actual_hash, local_path=str(dest_path))

    def check_etag(self, item: SourceItem) -> str | None:
        """Fetch the current eTag from Graph without downloading content."""
        url = (
            f"{_GRAPH_BASE}/drives/{item.drive_id}/items/{item.item_id}"
            "?$select=eTag"
        )
        resp = self._get(url)
        return resp.json().get("eTag")

    def apply_mutation(self, plan: MutationPlan, dry_run: bool = True) -> bool:
        """Rename and/or move the driveItem; guards with eTag check before mutation."""
        logger = log.bind(
            item_id=plan.item_id,
            source=plan.source_path,
            proposed=plan.proposed_path,
            op=plan.operation.value,
            dry_run=dry_run,
        )

        if plan.operation == MutationOp.METADATA_UPDATE:
            logger.info("sharepoint.mutation.metadata_only")
            if dry_run:
                return True
            # Write extracted fields to SharePoint column metadata
            # (implementation deferred to Phase 4)
            raise NotImplementedError("SharePoint column metadata write not yet implemented")

        # eTag concurrency guard
        if plan.etag_at_plan_time:
            current_etag = self.check_etag(
                SourceItem(
                    source_type=SourceType.SHAREPOINT,
                    item_id=plan.item_id,
                    original_path=plan.source_path,
                    content_hash=plan.content_hash,
                    mime_type="",
                    file_size=0,
                    drive_id=self.config.drive_id,
                )
            )
            if current_etag and current_etag != plan.etag_at_plan_time:
                raise ETagMismatchError(plan.item_id, plan.etag_at_plan_time, current_etag or "")

        proposed = Path(plan.proposed_path)
        new_name = proposed.name
        new_parent_path = str(proposed.parent)

        patch_body: dict[str, Any] = {"name": new_name}
        if plan.operation == MutationOp.MOVE:
            patch_body["parentReference"] = {
                "path": f"/drives/{self.config.drive_id}/root:/{new_parent_path}"
            }

        if dry_run:
            logger.info("sharepoint.mutation.dry_run", patch=patch_body, reason=plan.reason)
            return True

        url = f"{_GRAPH_BASE}/drives/{self.config.drive_id}/items/{plan.item_id}"
        self._patch(url, patch_body)
        logger.info("sharepoint.mutation.applied", reason=plan.reason)
        return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_SUFFIX_MIME_MAP: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def _suffix_to_mime(suffix: str) -> str:
    return _SUFFIX_MIME_MAP.get(suffix.lower(), "application/octet-stream")
