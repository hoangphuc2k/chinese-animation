from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.pipeline.transcription_stage import AudioExtractionHandler, ProbeHandler, TranscriptionHandler
from localizer_agent.transcription.segments import TranscriptSegment
from localizer_agent.transcription.whisper_cpp import TranscriptArtifact
from localizer_agent.workspace import JobWorkspaceManager


class RecordingClient:
    def __init__(self) -> None:
        self.completions = []

    async def heartbeat(self, claim) -> None:
        return None

    async def complete(self, claim, completion) -> None:
        self.completions.append(completion)


class FakeWhisper:
    def extract_pcm(self, source: Path, destination: Path) -> None:
        assert source.read_bytes() == b"normalized-media"
        destination.write_bytes(b"pcm")

    def transcribe(self, audio_path: Path, output_prefix: Path) -> TranscriptArtifact:
        assert audio_path.read_bytes() == b"pcm"
        return TranscriptArtifact(
            binary_version="whisper.cpp-1.7.4",
            model_sha256="b" * 64,
            segments=[TranscriptSegment(end_ms=1200, segment_id="SEG-000001", start_ms=0, text="你好")],
        )


def test_transcription_handler_verifies_input_and_completes_with_immutable_lineage(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    artifacts = workspace.root / "artifacts"
    artifacts.mkdir()
    extracted = artifacts / "audio.wav"
    extracted.write_bytes(b"pcm")
    extracted_hash = hashlib.sha256(b"pcm").hexdigest()
    claim = ClaimJobResponse.model_validate(
        {
            "jobId": "00000000-0000-4000-8000-000000000001",
            "jobType": "IMPORT",
            "jobVersion": 3,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "extractedAudio": {
                    "artifactRef": extracted.name,
                    "byteCount": extracted.stat().st_size,
                    "kind": "EXTRACTED_AUDIO",
                    "sha256": extracted_hash,
                    "toolVersion": "ffmpeg-pcm16-v1",
                },
                "state": "TRANSCRIBING",
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "TRANSCRIBING",
        }
    )
    client = RecordingClient()
    handler = TranscriptionHandler(
        client,
        manager,
        FakeWhisper(),
        config_version="whisper-small-v1",
        heartbeat_interval_seconds=1,
        tool_version="whisper.cpp-1.7.4",
    )

    asyncio.run(handler.handle(claim))

    completion = client.completions[0]
    assert (completion.expectedState, completion.state) == ("TRANSCRIBING", "TRANSLATING")
    assert completion.pipelineArtifacts[0].kind == "TRANSCRIPT"
    assert completion.segmentIds == ["SEG-000001"]


def test_probe_and_audio_extraction_bridge_ingest_to_transcription(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")
    workspace.normalized_path.write_bytes(b"normalized-media")
    normalized_hash = hashlib.sha256(b"normalized-media").hexdigest()
    client = RecordingClient()
    probe_claim = ClaimJobResponse.model_validate(
        {
            "ingestResult": {
                "normalizedByteCount": len(b"normalized-media"),
                "normalizedHash": normalized_hash,
                "sourceByteCount": 20,
                "sourceHash": "a" * 64,
                "technicalMetadata": {"durationSeconds": 12.5, "formatName": "mp4", "hasAudio": True, "hasVideo": True},
            },
            "jobId": workspace.root.name,
            "jobType": "IMPORT",
            "jobVersion": 3,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "PROBING",
        }
    )

    asyncio.run(ProbeHandler(client, heartbeat_interval_seconds=1).handle(probe_claim))

    assert (client.completions[0].expectedState, client.completions[0].state) == ("PROBING", "EXTRACTING_AUDIO")
    extraction_claim = ClaimJobResponse.model_validate(
        {
            "jobId": workspace.root.name,
            "jobType": "IMPORT",
            "jobVersion": 4,
            "leaseExpiresAtUtc": "2026-08-04T00:05:00Z",
            "leaseToken": "t" * 43,
            "pipelineInput": {
                "normalizedMedia": {
                    "artifactRef": "normalized-media",
                    "byteCount": len(b"normalized-media"),
                    "kind": "NORMALIZED_MEDIA",
                    "sha256": normalized_hash,
                    "toolVersion": "ffmpeg-normalize-v1",
                },
                "state": "EXTRACTING_AUDIO",
            },
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "EXTRACTING_AUDIO",
        }
    )
    extractor = AudioExtractionHandler(
        client,
        manager,
        FakeWhisper(),
        config_version="ffmpeg-pcm16-v1",
        heartbeat_interval_seconds=1,
        tool_version="ffmpeg-pcm16-v1",
    )

    asyncio.run(extractor.handle(extraction_claim))

    completion = client.completions[1]
    assert (completion.expectedState, completion.state) == ("EXTRACTING_AUDIO", "TRANSCRIBING")
    assert completion.pipelineArtifacts[0].kind == "EXTRACTED_AUDIO"
