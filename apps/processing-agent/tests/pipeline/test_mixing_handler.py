from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.pipeline.stages import MixingHandler
from localizer_agent.workspace import JobWorkspaceManager


class RecordingClient:
    def __init__(self) -> None:
        self.completions = []

    async def heartbeat(self, claim) -> None:
        return None

    async def complete(self, claim, completion) -> None:
        self.completions.append(completion)


def test_mixing_handler_publishes_aligned_audio_and_deterministic_subtitles(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    artifacts = workspace.root / "artifacts"
    artifacts.mkdir()
    script = json.dumps(
        {"segments": [{"endMs": 1200, "segmentId": "SEG-000001", "startMs": 0, "text": "Xin chào"}]},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    voice = b"RIFFaligned-voice"
    (artifacts / "script.json").write_bytes(script)
    (artifacts / "voice.wav").write_bytes(voice)
    claim = ClaimJobResponse.model_validate(
        {
            "jobId": "00000000-0000-4000-8000-000000000001",
            "jobType": "LOCALIZE",
            "jobVersion": 6,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "script": {"artifactRef": "script.json", "byteCount": len(script), "kind": "SCRIPT", "sha256": hashlib.sha256(script).hexdigest(), "toolVersion": "gemini"},
                "state": "MIXING",
                "voice": {"artifactRef": "voice.wav", "byteCount": len(voice), "kind": "VOICE", "sha256": hashlib.sha256(voice).hexdigest(), "toolVersion": "azure"},
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "MIXING",
        }
    )
    client = RecordingClient()

    asyncio.run(MixingHandler(client, manager, heartbeat_interval_seconds=1).handle(claim))

    completion = client.completions[0]
    assert (completion.expectedState, completion.state) == ("MIXING", "RENDERING")
    assert {artifact.kind for artifact in completion.pipelineArtifacts} == {"MIXED_AUDIO", "SUBTITLES"}
    subtitle = next(artifact for artifact in completion.pipelineArtifacts if artifact.kind == "SUBTITLES")
    assert "Xin chào" in (artifacts / subtitle.artifactRef).read_text(encoding="utf-8")
