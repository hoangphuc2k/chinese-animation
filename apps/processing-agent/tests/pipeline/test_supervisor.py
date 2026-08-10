from __future__ import annotations

import asyncio

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.pipeline.supervisor import ProcessingSupervisor


def claim(state: str = "TRANSCRIBING") -> ClaimJobResponse:
    return ClaimJobResponse.model_validate(
        {
            "jobId": "00000000-0000-4000-8000-000000000001",
            "jobType": "IMPORT",
            "jobVersion": 3,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "normalizedMedia": {
                    "artifactRef": "normalized-media",
                    "byteCount": 10,
                    "kind": "NORMALIZED_MEDIA",
                    "sha256": "a" * 64,
                    "toolVersion": "ffmpeg-normalize-v1",
                }
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": state,
        }
    )


class RecordingClient:
    def __init__(self, result: ClaimJobResponse | None) -> None:
        self.result = result
        self.capabilities: tuple[list[str], list[str]] | None = None

    async def claim(self, job_types, states):
        self.capabilities = (list(job_types), list(states))
        return self.result


class RecordingHandler:
    def __init__(self) -> None:
        self.claims: list[ClaimJobResponse] = []

    async def handle(self, claimed: ClaimJobResponse) -> None:
        self.claims.append(claimed)


def test_supervisor_advertises_and_dispatches_only_registered_states() -> None:
    client = RecordingClient(claim())
    handler = RecordingHandler()
    supervisor = ProcessingSupervisor(client, {"TRANSCRIBING": handler}, supported_job_types=["IMPORT"])

    assert asyncio.run(supervisor.run_once()) is True
    assert client.capabilities == (["IMPORT"], ["TRANSCRIBING"])
    assert [item.state for item in handler.claims] == ["TRANSCRIBING"]


def test_supervisor_does_not_claim_when_no_state_handler_is_registered() -> None:
    client = RecordingClient(None)

    try:
        ProcessingSupervisor(client, {}, supported_job_types=["IMPORT"])
    except ValueError as error:
        assert "handler" in str(error).lower()
    else:
        raise AssertionError("expected an empty capability map to be rejected")
