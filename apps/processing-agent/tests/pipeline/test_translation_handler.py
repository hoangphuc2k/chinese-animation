from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.localization.validator import ScriptSegment
from localizer_agent.pipeline.stages import TranslationHandler
from localizer_agent.transcription.segments import TranscriptSegment
from localizer_agent.workspace import JobWorkspaceManager


class RecordingClient:
    def __init__(self) -> None:
        self.completions = []

    async def heartbeat(self, claim) -> None:
        return None

    async def complete(self, claim, completion) -> None:
        self.completions.append(completion)


class FakeTranslator:
    def translate(self, segments: list[TranscriptSegment]) -> list[ScriptSegment]:
        assert [segment.text for segment in segments] == ["你好"]
        return [ScriptSegment(end_ms=1200, segment_id="SEG-000001", start_ms=0, text="Xin chào")]


def test_translation_handler_requires_rights_and_publishes_reviewable_script(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    transcript = json.dumps(
        {
            "binaryVersion": "whisper.cpp-1.7.4",
            "modelSha256": "b" * 64,
            "segments": [{"endMs": 1200, "segmentId": "SEG-000001", "startMs": 0, "text": "你好"}],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    artifact_path = workspace.root / "artifacts" / "transcript-01.json"
    artifact_path.parent.mkdir()
    artifact_path.write_bytes(transcript)
    transcript_hash = hashlib.sha256(transcript).hexdigest()
    claim = ClaimJobResponse.model_validate(
        {
            "jobId": "00000000-0000-4000-8000-000000000001",
            "jobType": "LOCALIZE",
            "jobVersion": 4,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "googleProcessingAllowed": True,
                "state": "TRANSLATING",
                "transcript": {
                    "artifactRef": artifact_path.name,
                    "byteCount": len(transcript),
                    "kind": "TRANSCRIPT",
                    "sha256": transcript_hash,
                    "toolVersion": "whisper.cpp-1.7.4",
                },
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "TRANSLATING",
        }
    )
    client = RecordingClient()
    handler = TranslationHandler(
        client,
        manager,
        FakeTranslator(),
        config_version="gemini-prompt-v1",
        heartbeat_interval_seconds=1,
        tool_version="gemini-2.5-flash",
    )

    asyncio.run(handler.handle(claim))

    completion = client.completions[0]
    assert (completion.expectedState, completion.state) == ("TRANSLATING", "WAITING_SCRIPT_REVIEW")
    assert completion.pipelineArtifacts[0].kind == "SCRIPT"
    script_path = workspace.root / "artifacts" / completion.pipelineArtifacts[0].artifactRef
    assert json.loads(script_path.read_text(encoding="utf-8"))["segments"][0]["text"] == "Xin chào"
