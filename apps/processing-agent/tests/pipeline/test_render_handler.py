from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.pipeline.approval_gate import ApprovalSnapshot
from localizer_agent.pipeline.stages import RenderHandler
from localizer_agent.workspace import JobWorkspaceManager


class RecordingClient:
    def __init__(self, approval: ApprovalSnapshot) -> None:
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


class FakeRenderOperations:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def render(self, inputs) -> None:
        self.events.append("render")
        assert inputs.normalized_media.read_bytes() == b"normalized"
        assert inputs.narration.read_bytes() == b"voice"
        assert "Xin chào" in inputs.subtitles.read_text(encoding="utf-8")
        inputs.destination.write_bytes(b"validated-render")


def test_render_handler_rechecks_approval_and_publishes_validated_render(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    workspace.normalized_path.write_bytes(b"normalized")
    artifacts = workspace.root / "artifacts"
    artifacts.mkdir()
    (artifacts / "mixed.wav").write_bytes(b"voice")
    (artifacts / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,200\nXin chào\n", encoding="utf-8")
    watermark = tmp_path / "watermark.png"
    watermark.write_bytes(b"png")
    script_hash = "a" * 64
    claim = ClaimJobResponse.model_validate(
        {
            "jobId": "00000000-0000-4000-8000-000000000001", "jobType": "RENDER", "jobVersion": 7,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z", "leaseToken": "t" * 43,
            "pipelineInput": {
                "mixedAudio": {"artifactRef": "mixed.wav", "byteCount": 5, "kind": "MIXED_AUDIO", "sha256": hashlib.sha256(b"voice").hexdigest(), "toolVersion": "mix"},
                "normalizedMedia": {"artifactRef": "normalized-media", "byteCount": 10, "kind": "NORMALIZED_MEDIA", "sha256": hashlib.sha256(b"normalized").hexdigest(), "toolVersion": "normalize"},
                "scriptSha256": script_hash, "scriptVersion": 2, "state": "RENDERING",
                "subtitles": {"artifactRef": "captions.srt", "byteCount": (artifacts / "captions.srt").stat().st_size, "kind": "SUBTITLES", "sha256": hashlib.sha256((artifacts / "captions.srt").read_bytes()).hexdigest(), "toolVersion": "srt"},
            },
            "requestId": "00000000-0000-4000-8000-000000000099", "state": "RENDERING",
        }
    )
    client = RecordingClient(ApprovalSnapshot(True, script_hash, claim.jobId, 2))
    handler = RenderHandler(client, manager, FakeRenderOperations(client.events), watermark, heartbeat_interval_seconds=1, tool_version="ffmpeg-v1")

    asyncio.run(handler.handle(claim))

    assert client.events == ["approval", "render"]
    completion = client.completions[0]
    assert (completion.expectedState, completion.state) == ("RENDERING", "READY_FOR_PREVIEW")
    assert completion.pipelineArtifacts[0].kind == "RENDER"
