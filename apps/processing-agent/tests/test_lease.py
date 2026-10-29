from __future__ import annotations

import asyncio

from datetime import UTC, datetime, timedelta

from localizer_agent.control_plane import ControlPlaneError
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
    def __init__(self, fail: bool = False, errors: list[BaseException] | None = None) -> None:
        self.calls = 0
        self.fail = fail
        self.errors = list(errors or [])

    async def heartbeat(self, claim: object) -> None:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        if self.fail:
            raise ControlPlaneError(409, "heartbeat")


def claim_expiring_in(seconds: float) -> ClaimJobResponse:
    expires = datetime.now(UTC) + timedelta(seconds=seconds)
    return import_claim().model_copy(update={"leaseExpiresAtUtc": expires.strftime("%Y-%m-%dT%H:%M:%S.%fZ")})


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


def test_lease_guard_keeps_the_lease_through_transient_heartbeat_failures() -> None:
    # One network blip or timeout must not abandon a long transcription or render.
    async def scenario() -> HeartbeatClient:
        client = HeartbeatClient(errors=[TimeoutError("slow network"), ControlPlaneError(503, "heartbeat")])
        async with LeaseGuard(client, claim_expiring_in(60), interval_seconds=0.01) as guard:
            await asyncio.sleep(0.08)
            guard.assert_active()
        return client

    assert asyncio.run(scenario()).calls >= 3


def test_lease_guard_gives_up_once_transient_failures_outlast_the_lease_window() -> None:
    async def scenario() -> None:
        client = HeartbeatClient(errors=[TimeoutError("down")] * 50)
        async with LeaseGuard(client, claim_expiring_in(0.03), interval_seconds=0.01) as guard:
            await asyncio.sleep(0.1)
            try:
                guard.assert_active()
            except LeaseLostError:
                return
            raise AssertionError("expected a lost lease")

    asyncio.run(scenario())
