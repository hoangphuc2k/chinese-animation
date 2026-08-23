from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.pipeline.stages import PreviewHandler
from localizer_agent.preview.registry import PreviewRegistry
from localizer_agent.workspace import JobWorkspaceManager


class RecordingClient:
    def __init__(self) -> None:
        self.completions = []

    async def heartbeat(self, claim) -> None:
        return None

    async def complete(self, claim, completion) -> None:
        self.completions.append(completion)


def test_preview_handler_registers_job_bound_five_minute_descriptor(tmp_path: Path) -> None:
    now = datetime(2026, 8, 23, 4, 5, 6, tzinfo=UTC)
    job_id = "00000000-0000-4000-8000-000000000001"
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create(job_id, "00000000-0000-4000-8000-000000000002")
    artifacts = workspace.root / "artifacts"
    artifacts.mkdir()
    render = artifacts / "render.mp4"
    subtitles = artifacts / "captions.srt"
    render.write_bytes(b"validated-render")
    subtitles.write_text("preview captions", encoding="utf-8")
    render_hash = hashlib.sha256(render.read_bytes()).hexdigest()
    claim = ClaimJobResponse.model_validate(
        {
            "jobId": job_id,
            "jobType": "RENDER",
            "jobVersion": 8,
            "leaseExpiresAtUtc": "2026-08-23T04:10:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "render": {
                    "artifactRef": render.name,
                    "byteCount": render.stat().st_size,
                    "kind": "RENDER",
                    "sha256": render_hash,
                    "toolVersion": "ffmpeg-v1",
                },
                "state": "READY_FOR_PREVIEW",
                "subtitles": {
                    "artifactRef": subtitles.name,
                    "byteCount": subtitles.stat().st_size,
                    "kind": "SUBTITLES",
                    "sha256": hashlib.sha256(subtitles.read_bytes()).hexdigest(),
                    "toolVersion": "srt-v1",
                },
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "READY_FOR_PREVIEW",
        }
    )
    client = RecordingClient()
    registry_path = tmp_path / "preview-registry.json"
    registry = PreviewRegistry(registry_path)
    handler = PreviewHandler(
        client,
        manager,
        registry,
        heartbeat_interval_seconds=1,
        now_utc=lambda: now,
    )

    asyncio.run(handler.handle(claim))

    completion = client.completions[0]
    assert (completion.expectedState, completion.state, completion.step) == (
        "READY_FOR_PREVIEW",
        "WAITING_PUBLISH_APPROVAL",
        "preview_registered",
    )
    assert completion.preview.expiresAtUtc == (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert completion.preview.renderSha256 == render_hash
    assert PreviewRegistry(registry_path).resolve(completion.preview.token, job_id, now) == f"{job_id}/artifacts/{render.name}"
    assert render.name not in completion.model_dump_json()
