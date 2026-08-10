from __future__ import annotations

import asyncio
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.media import TechnicalMetadata
from localizer_agent.supervisor import ImportSupervisor
from localizer_agent.workspace import JobWorkspaceManager


def import_claim() -> ClaimJobResponse:
    return ClaimJobResponse.model_validate(
        {
            "importInput": {
                "locator": {"inboxFileId": "creator-submission-42", "kind": "OCI_INBOX_FILE"},
                "rightsEvidenceHash": "a" * 64,
                "sourceAssetId": "00000000-0000-4000-8000-000000000002",
            },
            "jobId": "00000000-0000-4000-8000-000000000001",
            "jobType": "IMPORT",
            "jobVersion": 2,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "INGESTING",
        }
    )


class QueueClient:
    def __init__(self) -> None:
        self.completed = []

    async def claim_import(self):
        return import_claim().model_copy(update={"state": "QUEUED"})

    async def complete(self, claim, completion) -> None:
        self.completed.append(completion)

    async def heartbeat(self, claim) -> None:
        return None


def test_supervisor_moves_a_queued_import_to_ingesting_before_heavy_work() -> None:
    client = QueueClient()
    supervisor = ImportSupervisor(client, None, None, None, None, heartbeat_interval_seconds=1)

    assert asyncio.run(supervisor.run_once()) is True
    assert len(client.completed) == 1
    assert client.completed[0].expectedState == "QUEUED"
    assert client.completed[0].state == "INGESTING"


def test_supervisor_handles_a_claim_supplied_by_the_outer_dispatcher() -> None:
    client = QueueClient()
    supervisor = ImportSupervisor(client, None, None, None, None, heartbeat_interval_seconds=1)

    asyncio.run(supervisor.handle(import_claim().model_copy(update={"state": "QUEUED"})))

    assert len(client.completed) == 1
    assert client.completed[0].state == "INGESTING"


class IngestClient(QueueClient):
    async def claim_import(self):
        return import_claim()


class FakeMedia:
    def normalize(self, source: Path, destination: Path) -> None:
        destination.write_bytes(b"normalized-media")

    def probe(self, source: Path) -> TechnicalMetadata:
        return TechnicalMetadata(12.5, "mov,mp4", True, True)


def test_supervisor_ingests_and_reports_only_sanitized_metadata(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "creator-submission-42").write_bytes(b"source-media")
    client = IngestClient()
    supervisor = ImportSupervisor(
        client,
        JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1),
        FakeMedia(),
        inbox,
        1024,
        heartbeat_interval_seconds=1,
    )

    assert asyncio.run(supervisor.run_once()) is True
    completion = client.completed[0]
    assert completion.expectedState == "INGESTING"
    assert completion.state == "PROBING"
    assert completion.ingestResult is not None
    assert completion.ingestResult.sourceByteCount == len(b"source-media")
    assert "creator-submission" not in completion.model_dump_json()
