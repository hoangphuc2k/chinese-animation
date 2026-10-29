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


class CompletingClient(RecordingClient):
    def __init__(self, result: ClaimJobResponse | None) -> None:
        super().__init__(result)
        self.completions: list[tuple[ClaimJobResponse, object]] = []

    async def complete(self, claimed, completion) -> None:
        self.completions.append((claimed, completion))


class RaisingHandler:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def handle(self, claimed: ClaimJobResponse) -> None:
        raise self.error


def test_deterministic_media_error_completes_the_job_as_failed_instead_of_crash_looping() -> None:
    from localizer_agent.ingest import SourceIntegrityError

    client = CompletingClient(claim())
    supervisor = ProcessingSupervisor(client, {"TRANSCRIBING": RaisingHandler(SourceIntegrityError("bad source"))}, supported_job_types=["IMPORT"])

    assert asyncio.run(supervisor.run_once()) is True
    [(_, completion)] = client.completions
    assert (completion.expectedState, completion.state, completion.failureCode) == ("TRANSCRIBING", "FAILED", "MEDIA_INVALID")
    assert completion.leaseToken == "t" * 43 and completion.jobVersion == 3


def test_subtitle_rule_violation_stops_for_manual_review() -> None:
    from localizer_agent.subtitles.writer import SubtitleError

    client = CompletingClient(claim())
    supervisor = ProcessingSupervisor(client, {"TRANSCRIBING": RaisingHandler(SubtitleError("cue too long"))}, supported_job_types=["IMPORT"])

    assert asyncio.run(supervisor.run_once()) is True
    [(_, completion)] = client.completions
    assert (completion.state, completion.failureCode, completion.step) == (
        "MANUAL_REVIEW_REQUIRED",
        "MANUAL_REVIEW_REQUIRED",
        "subtitle_rules_failed",
    )


def test_transient_errors_are_left_to_lease_expiry_and_the_attempt_cap() -> None:
    client = CompletingClient(claim())
    supervisor = ProcessingSupervisor(client, {"TRANSCRIBING": RaisingHandler(TimeoutError("provider timeout"))}, supported_job_types=["IMPORT"])

    try:
        asyncio.run(supervisor.run_once())
    except TimeoutError:
        pass
    else:
        raise AssertionError("transient errors must propagate")
    assert client.completions == []


def test_job_type_router_dispatches_queued_claims_by_job_type() -> None:
    from localizer_agent.pipeline.supervisor import JobTypeRouter

    imports, cleanups = RecordingHandler(), RecordingHandler()
    router = JobTypeRouter({"IMPORT": imports, "DELETE_EXPIRED": cleanups})
    cleanup_claim = ClaimJobResponse.model_validate(
        {
            "cleanupInput": {"targetJobId": "00000000-0000-4000-8000-000000000001"},
            "jobId": "00000000-0000-4000-8000-000000000010",
            "jobType": "DELETE_EXPIRED",
            "jobVersion": 2,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "QUEUED",
        }
    )

    asyncio.run(router.handle(cleanup_claim))

    assert router.job_types == ("IMPORT", "DELETE_EXPIRED")
    assert cleanups.claims == [cleanup_claim] and imports.claims == []


def test_transient_source_outages_are_not_reported_as_media_failures() -> None:
    from localizer_agent.ingest import SourceUnavailableError

    client = CompletingClient(claim())
    supervisor = ProcessingSupervisor(client, {"TRANSCRIBING": RaisingHandler(SourceUnavailableError("503"))}, supported_job_types=["IMPORT"])

    try:
        asyncio.run(supervisor.run_once())
    except SourceUnavailableError:
        pass
    else:
        raise AssertionError("transient source errors must propagate")
    assert client.completions == []
