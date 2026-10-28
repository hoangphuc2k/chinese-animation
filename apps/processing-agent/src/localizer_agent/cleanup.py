"""Delete an expired or failed job's OCI artifacts once the control plane schedules cleanup."""

from __future__ import annotations

import asyncio
from typing import Protocol

from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest
from localizer_agent.preview.registry import PreviewRegistry
from localizer_agent.workspace import JobWorkspaceManager


class CompletionClient(Protocol):
    async def complete(self, claim: ClaimJobResponse, completion: CompleteStepRequest) -> None: ...


class CleanupHandler:
    def __init__(self, client: CompletionClient, workspace_manager: JobWorkspaceManager, preview_registry: PreviewRegistry) -> None:
        self._client = client
        self._workspace_manager = workspace_manager
        self._preview_registry = preview_registry

    async def handle(self, claim: ClaimJobResponse) -> None:
        if claim.jobType != "DELETE_EXPIRED" or claim.state != "QUEUED" or claim.cleanupInput is None:
            raise RuntimeError("Cleanup handler received a claim outside its declared capability set.")
        target_job_id = claim.cleanupInput.targetJobId
        # Revoke first so no preview can be served for files that are about to disappear.
        self._preview_registry.revoke_job(target_job_id)
        removed = await asyncio.to_thread(self._workspace_manager.remove, target_job_id)
        await self._client.complete(
            claim,
            CompleteStepRequest(
                expectedState="QUEUED",
                jobVersion=claim.jobVersion,
                leaseToken=claim.leaseToken,
                state="COMPLETED",
                step="workspace_removed" if removed else "workspace_absent",
            ),
        )
