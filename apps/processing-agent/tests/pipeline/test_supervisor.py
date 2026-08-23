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
                "extractedAudio": {
                    "artifactRef": "audio.wav",
                    "byteCount": 10,
                    "kind": "EXTRACTED_AUDIO",
                    "sha256": "a" * 64,
                    "toolVersion": "ffmpeg-pcm16-v1",
                },
                "state": "TRANSCRIBING",
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": state,
        }
    )


class RecordingClient:
    def __init__(self, result: ClaimJobResponse | None) -> None:
        self.result = result
        self.capabilities: list[tuple[list[str], list[str]]] = []

    async def claim(self, job_types, states):
        self.capabilities.append((list(job_types), list(states)))
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
    assert client.capabilities == [(["IMPORT"], ["TRANSCRIBING"])]
    assert [item.state for item in handler.claims] == ["TRANSCRIBING"]


def test_supervisor_does_not_claim_when_no_state_handler_is_registered() -> None:
    client = RecordingClient(None)

    try:
        ProcessingSupervisor(client, {}, supported_job_types=["IMPORT"])
    except ValueError as error:
        assert "handler" in str(error).lower()
    else:
        raise AssertionError("expected an empty capability map to be rejected")


def test_supervisor_does_not_advertise_a_cross_product_of_job_types_and_states() -> None:
    class SequencedClient(RecordingClient):
        async def claim(self, job_types, states):
            self.capabilities.append((list(job_types), list(states)))
            return None

    client = SequencedClient(None)
    import_handler = RecordingHandler()
    translation_handler = RecordingHandler()
    supervisor = ProcessingSupervisor(
        client,
        {"QUEUED": import_handler, "TRANSLATING": translation_handler},
        job_types_by_state={"QUEUED": ["IMPORT"], "TRANSLATING": ["IMPORT", "LOCALIZE"]},
    )

    assert asyncio.run(supervisor.run_once()) is False
    assert client.capabilities == [(["IMPORT"], ["QUEUED"]), (["IMPORT", "LOCALIZE"], ["TRANSLATING"])]
