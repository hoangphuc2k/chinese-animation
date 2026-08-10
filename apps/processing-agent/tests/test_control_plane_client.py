from __future__ import annotations

import asyncio
import json

from localizer_agent.control_plane import AgentControlPlaneClient, ControlPlaneError, HttpResponse
from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest


class RecordingTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
        self.responses = responses

    async def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout_seconds: int) -> HttpResponse:
        self.calls.append((method, url, headers, body))
        return self.responses.pop(0)


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


def transcription_claim() -> ClaimJobResponse:
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
            "state": "TRANSCRIBING",
        }
    )


def test_client_claims_only_import_work_and_completes_sanitized_result() -> None:
    async def scenario() -> tuple[ClaimJobResponse, RecordingTransport]:
        claim = import_claim()
        transport = RecordingTransport(
            [
                HttpResponse(200, json.dumps(claim.model_dump(mode="json")).encode()),
                HttpResponse(204, b""),
                HttpResponse(204, b""),
            ]
        )
        client = AgentControlPlaneClient("https://control-plane.example.test", "sensitive-token", transport)
        received = await client.claim_import()
        assert received == claim
        await client.heartbeat(received)
        await client.complete(
            received,
            CompleteStepRequest(
                expectedState="INGESTING",
                ingestResult={
                    "normalizedByteCount": 10,
                    "normalizedHash": "a" * 64,
                    "sourceByteCount": 12,
                    "sourceHash": "b" * 64,
                    "technicalMetadata": {"durationSeconds": 12.5, "formatName": "mov,mp4", "hasAudio": True, "hasVideo": True},
                },
                jobVersion=2,
                leaseToken=received.leaseToken,
                state="PROBING",
                step="ingested",
            ),
        )
        return claim, transport

    claim, transport = asyncio.run(scenario())

    method, url, headers, body = transport.calls[0]
    assert (method, url) == ("POST", "https://control-plane.example.test/api/v1/agent/jobs/claim")
    assert headers["Authorization"] == "Bearer sensitive-token"
    assert json.loads(body or b"{}") == {"supportedJobTypes": ["IMPORT"], "supportedStates": ["QUEUED", "INGESTING"]}
    assert transport.calls[1][1].endswith(f"/jobs/{claim.jobId}/heartbeat")
    assert transport.calls[2][1].endswith(f"/jobs/{claim.jobId}/complete")


def test_client_returns_no_claim_for_204_and_does_not_expose_response_bodies() -> None:
    async def scenario() -> None:
        client = AgentControlPlaneClient(
            "https://control-plane.example.test",
            "sensitive-token",
            RecordingTransport([HttpResponse(500, b"source url https://secret.example.test")]),
        )
        try:
            await client.claim_import()
        except ControlPlaneError as error:
            assert "secret.example.test" not in str(error)
            return
        raise AssertionError("expected control-plane failure")

    asyncio.run(scenario())


def test_client_accepts_a_state_aware_pipeline_claim_for_declared_job_types() -> None:
    async def scenario() -> tuple[ClaimJobResponse, RecordingTransport]:
        expected = transcription_claim()
        transport = RecordingTransport([HttpResponse(200, expected.model_dump_json().encode())])
        client = AgentControlPlaneClient("https://control-plane.example.test", "sensitive-token", transport)

        received = await client.claim(["IMPORT"], ["TRANSCRIBING"])

        assert received == expected
        return received, transport

    received, transport = asyncio.run(scenario())
    assert received.pipelineInput is not None
    assert received.pipelineInput.normalizedMedia.kind == "NORMALIZED_MEDIA"
    assert json.loads(transport.calls[0][3] or b"{}") == {"supportedJobTypes": ["IMPORT"], "supportedStates": ["TRANSCRIBING"]}


def test_client_rejects_a_completion_for_a_state_other_than_the_claimed_state() -> None:
    async def scenario() -> None:
        claimed = import_claim()
        client = AgentControlPlaneClient("https://control-plane.example.test", "sensitive-token", RecordingTransport([]))

        try:
            await client.complete(
                claimed,
                CompleteStepRequest(
                    expectedState="QUEUED",
                    jobVersion=claimed.jobVersion,
                    leaseToken=claimed.leaseToken,
                    state="INGESTING",
                    step="wrong_claim_state",
                ),
            )
        except ValueError as error:
            assert "state" in str(error).lower()
            return
        raise AssertionError("expected completion state mismatch")

    asyncio.run(scenario())
