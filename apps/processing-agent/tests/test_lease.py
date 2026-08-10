from __future__ import annotations

import asyncio

from localizer_agent.lease import LeaseGuard, LeaseLostError
from localizer_agent.generated.models import ClaimJobResponse


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


class HeartbeatClient:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def heartbeat(self, claim: object) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("lease rejected")


def test_lease_guard_heartbeats_while_work_is_running() -> None:
    async def scenario() -> int:
        client = HeartbeatClient()
        async with LeaseGuard(client, import_claim(), interval_seconds=0.01):
            await asyncio.sleep(0.03)
        return client.calls

    assert asyncio.run(scenario()) >= 1


def test_lease_guard_surfaces_heartbeat_failure_before_completion() -> None:
    async def scenario() -> None:
        client = HeartbeatClient(fail=True)
        async with LeaseGuard(client, import_claim(), interval_seconds=0.01) as guard:
            await asyncio.sleep(0.02)
            try:
                guard.assert_active()
            except LeaseLostError:
                return
            raise AssertionError("expected a lost lease")

    asyncio.run(scenario())
