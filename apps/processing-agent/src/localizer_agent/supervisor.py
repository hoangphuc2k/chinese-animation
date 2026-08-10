"""Single-purpose IMPORT supervisor with resumable state transitions."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol

from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest, HttpsLocator, OciLocator
from localizer_agent.ingest import IngestedSource, ingest_https_url, ingest_inbox_file
from localizer_agent.lease import LeaseGuard
from localizer_agent.media import TechnicalMetadata
from localizer_agent.workspace import JobWorkspace, JobWorkspaceManager


class ImportClient(Protocol):
    async def claim_import(self) -> ClaimJobResponse | None: ...
    async def complete(self, claim: ClaimJobResponse, completion: CompleteStepRequest) -> None: ...
    async def heartbeat(self, claim: ClaimJobResponse) -> None: ...


class MediaOperations(Protocol):
    def normalize(self, source: Path, destination: Path) -> None: ...
    def probe(self, source: Path) -> TechnicalMetadata: ...


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    return digest.hexdigest(), byte_count


class ImportSupervisor:
    def __init__(
        self,
        client: ImportClient,
        workspace_manager: JobWorkspaceManager | None,
        media_processor: MediaOperations | None,
        inbox_root: Path | None,
        max_source_bytes: int | None,
        heartbeat_interval_seconds: float,
        request_timeout_seconds: int = 30,
    ) -> None:
        self._client = client
        self._workspace_manager = workspace_manager
        self._media_processor = media_processor
        self._inbox_root = inbox_root
        self._max_source_bytes = max_source_bytes
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._request_timeout_seconds = request_timeout_seconds

    async def run_once(self) -> bool:
        claim = await self._client.claim_import()
        if claim is None:
            return False
        await self.handle(claim)
        return True

    async def handle(self, claim: ClaimJobResponse) -> None:
        if claim.state == "QUEUED":
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="QUEUED",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    state="INGESTING",
                    step="ingest_started",
                ),
            )
            return
        if claim.state != "INGESTING" or claim.importInput is None:
            raise RuntimeError("Import supervisor received a state outside its declared capability set.")
        if self._workspace_manager is None or self._media_processor is None or self._inbox_root is None or self._max_source_bytes is None:
            raise RuntimeError("Import supervisor is missing ingest dependencies.")

        workspace = self._workspace_manager.create(claim.jobId, claim.importInput.sourceAssetId)
        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            manifest = self._workspace_manager.load(workspace)
            source = await asyncio.to_thread(self._recovered_source, workspace, manifest.source_hash, manifest.source_byte_count)
            if source is None:
                source = await asyncio.to_thread(self._ingest, claim, workspace.source_path)
                self._workspace_manager.update(workspace, stage="DOWNLOADED", source_byte_count=source.byte_count, source_hash=source.sha256)
            normalized = await asyncio.to_thread(self._recovered_source, workspace, manifest.normalized_hash, manifest.normalized_byte_count, True)
            if normalized is None:
                await asyncio.to_thread(self._media_processor.normalize, workspace.source_path, workspace.normalized_path)
                normalized_hash, normalized_byte_count = await asyncio.to_thread(_file_digest, workspace.normalized_path)
                self._workspace_manager.update(
                    workspace,
                    stage="NORMALIZED",
                    normalized_byte_count=normalized_byte_count,
                    normalized_hash=normalized_hash,
                )
            else:
                normalized_hash, normalized_byte_count = normalized.sha256, normalized.byte_count
            metadata = await asyncio.to_thread(self._media_processor.probe, workspace.normalized_path)
            guard.assert_active()
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="INGESTING",
                    ingestResult={
                        "normalizedByteCount": normalized_byte_count,
                        "normalizedHash": normalized_hash,
                        "sourceByteCount": source.byte_count,
                        "sourceHash": source.sha256,
                        "technicalMetadata": metadata.control_plane_value(),
                    },
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    state="PROBING",
                    step="ingested",
                ),
            )

    def _ingest(self, claim: ClaimJobResponse, destination: Path) -> IngestedSource:
        locator = claim.importInput.locator if claim.importInput is not None else None
        if isinstance(locator, OciLocator):
            return ingest_inbox_file(self._inbox_root or Path(), locator.inboxFileId, destination, self._max_source_bytes or 0)
        if isinstance(locator, HttpsLocator):
            return ingest_https_url(locator.url, destination, self._max_source_bytes or 0, self._request_timeout_seconds)
        raise RuntimeError("Import claim did not contain a supported source locator.")

    @staticmethod
    def _recovered_source(
        workspace: JobWorkspace,
        expected_hash: str | None,
        expected_byte_count: int | None,
        normalized: bool = False,
    ) -> IngestedSource | None:
        path = workspace.normalized_path if normalized else workspace.source_path
        if expected_hash is None or expected_byte_count is None or not path.is_file():
            return None
        actual_hash, actual_byte_count = _file_digest(path)
        if actual_hash != expected_hash or actual_byte_count != expected_byte_count:
            return None
        return IngestedSource(path=path, sha256=actual_hash, byte_count=actual_byte_count)
