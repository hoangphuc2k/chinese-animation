from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.pipeline.approval_gate import ApprovalSnapshot
from localizer_agent.pipeline.stages import VoiceHandler
from localizer_agent.speech.audio import TimingReviewRequired
from localizer_agent.workspace import JobWorkspaceManager


class RecordingClient:
    def __init__(self, approval: ApprovalSnapshot | None) -> None:
        self.approval = approval
        self.completions = []
        self.events: list[str] = []

    async def heartbeat(self, claim) -> None:
        return None

    async def get_script_approval(self, job_id, *, script_version, content_sha256):
        self.events.append("approval")
        return self.approval

    async def complete(self, claim, completion) -> None:
        self.completions.append(completion)


class FakeVoiceOperations:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error

    def synthesize(self, job_id, segments) -> bytes:
        self.events.append("synthesize")
        if self.error is not None:
            raise self.error
        assert [segment.text for segment in segments] == ["Xin chào"]
        return b"RIFFvoice"


def voice_fixture(tmp_path: Path, approval: bool = True):
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    script = json.dumps(
        {"segments": [{"endMs": 1200, "segmentId": "SEG-000001", "startMs": 0, "text": "Xin chào"}]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    script_path = workspace.root / "artifacts" / "script-01.json"
    script_path.parent.mkdir()
    script_path.write_bytes(script)
    script_hash = hashlib.sha256(script).hexdigest()
    claim = ClaimJobResponse.model_validate(
        {
            "jobId": "00000000-0000-4000-8000-000000000001",
            "jobType": "LOCALIZE",
            "jobVersion": 5,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "script": {
                    "artifactRef": script_path.name,
                    "byteCount": len(script),
                    "kind": "SCRIPT",
                    "sha256": script_hash,
                    "toolVersion": "gemini-2.5-flash",
                },
                "scriptSha256": script_hash,
                "scriptVersion": 2,
                "state": "GENERATING_VOICE",
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "GENERATING_VOICE",
        }
    )
    snapshot = (
        ApprovalSnapshot(approved=True, content_sha256=script_hash, job_id=claim.jobId, script_version=2)
        if approval
        else None
    )
    return manager, workspace, claim, snapshot


def test_voice_handler_rechecks_exact_approval_before_synthesis(tmp_path: Path) -> None:
    manager, workspace, claim, approval = voice_fixture(tmp_path)
    client = RecordingClient(approval)
    handler = VoiceHandler(
        client,
        manager,
        FakeVoiceOperations(client.events),
        config_version="azure-voice-v1",
        heartbeat_interval_seconds=1,
        tool_version="azure-neural-voice-v1",
    )

    asyncio.run(handler.handle(claim))

    assert client.events == ["approval", "synthesize"]
    completion = client.completions[0]
    assert (completion.expectedState, completion.state) == ("GENERATING_VOICE", "MIXING")
    assert completion.pipelineArtifacts[0].kind == "VOICE"
    assert (workspace.root / "artifacts" / completion.pipelineArtifacts[0].artifactRef).read_bytes() == b"RIFFvoice"


def test_voice_handler_returns_stale_approval_to_review_without_synthesis(tmp_path: Path) -> None:
    manager, _, claim, approval = voice_fixture(tmp_path, approval=False)
    client = RecordingClient(approval)
    handler = VoiceHandler(
        client,
        manager,
        FakeVoiceOperations(client.events),
        config_version="azure-voice-v1",
        heartbeat_interval_seconds=1,
        tool_version="azure-neural-voice-v1",
    )

    asyncio.run(handler.handle(claim))

    assert client.events == ["approval"]
    assert client.completions[0].state == "WAITING_SCRIPT_REVIEW"


def test_voice_handler_routes_unsafe_timing_to_human_review(tmp_path: Path) -> None:
    manager, _, claim, approval = voice_fixture(tmp_path)
    client = RecordingClient(approval)
    handler = VoiceHandler(
        client,
        manager,
        FakeVoiceOperations(client.events, TimingReviewRequired("too long")),
        config_version="azure-voice-v1",
        heartbeat_interval_seconds=1,
        tool_version="azure-neural-voice-v1",
    )

    asyncio.run(handler.handle(claim))

    assert client.events == ["approval", "synthesize"]
    assert client.completions[0].state == "TIMING_REVIEW_REQUIRED"
