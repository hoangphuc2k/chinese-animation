"""Lease-bound normalized-media to immutable transcript production stage."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol

from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest
from localizer_agent.lease import LeaseGuard
from localizer_agent.pipeline.lineage import ArtifactRecord, StageKey
from localizer_agent.pipeline.runner import StageResult, StageRunner
from localizer_agent.transcription.segments import TranscriptSegment
from localizer_agent.transcription.whisper_cpp import TranscriptArtifact
from localizer_agent.workspace import JobWorkspace, JobWorkspaceManager


class TranscriptionClient(Protocol):
    async def complete(self, claim: ClaimJobResponse, completion: CompleteStepRequest) -> None: ...
    async def heartbeat(self, claim: ClaimJobResponse) -> None: ...


class WhisperOperations(Protocol):
    def extract_pcm(self, source: Path, destination: Path) -> None: ...
    def transcribe(self, audio_path: Path, output_prefix: Path) -> TranscriptArtifact: ...


class ProbeHandler:
    def __init__(self, client: TranscriptionClient, *, heartbeat_interval_seconds: float) -> None:
        self._client = client
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    async def handle(self, claim: ClaimJobResponse) -> None:
        if claim.state != "PROBING" or claim.ingestResult is None:
            raise RuntimeError("Probe handler received an invalid state-specific claim.")
        has_audio = claim.ingestResult.technicalMetadata.hasAudio
        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            guard.assert_active()
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="PROBING",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    state="EXTRACTING_AUDIO" if has_audio else "FAILED",
                    step="probe_validated" if has_audio else "audio_track_missing",
                ),
            )


class AudioExtractionHandler:
    def __init__(
        self,
        client: TranscriptionClient,
        workspace_manager: JobWorkspaceManager,
        whisper: WhisperOperations,
        *,
        config_version: str,
        heartbeat_interval_seconds: float,
        tool_version: str,
    ) -> None:
        if not config_version or not tool_version:
            raise ValueError("Audio extraction configuration and tool versions are required.")
        self._client = client
        self._config_version = config_version
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._tool_version = tool_version
        self._whisper = whisper
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        pipeline_input = claim.pipelineInput
        if claim.state != "EXTRACTING_AUDIO" or pipeline_input is None or pipeline_input.state != "EXTRACTING_AUDIO":
            raise RuntimeError("Audio extraction handler received an invalid state-specific claim.")
        workspace = self._workspace_manager.open(claim.jobId)
        normalized = ArtifactRecord.from_path(
            workspace.normalized_path,
            kind="NORMALIZED_MEDIA",
            tool_version=pipeline_input.normalizedMedia.toolVersion,
        )
        if normalized.sha256 != pipeline_input.normalizedMedia.sha256 or normalized.byte_count != pipeline_input.normalizedMedia.byteCount:
            raise RuntimeError("Normalized media does not match the audio-extraction lineage.")
        key = StageKey.for_input(claim.jobId, claim.jobVersion, claim.state, normalized.sha256, self._config_version)

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            result = await asyncio.to_thread(self._run_stage, workspace, key)
            guard.assert_active()
            artifact = result.artifact
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="EXTRACTING_AUDIO",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    pipelineArtifacts=[
                        {
                            "artifactRef": artifact.path.name,
                            "byteCount": artifact.byte_count,
                            "kind": "EXTRACTED_AUDIO",
                            "sha256": artifact.sha256,
                            "toolVersion": artifact.tool_version,
                        }
                    ],
                    state="TRANSCRIBING",
                    step="audio_extracted",
                ),
            )

    def _run_stage(self, workspace: JobWorkspace, key: StageKey) -> StageResult:
        return StageRunner(workspace).run(
            key,
            execute=lambda candidate: self._whisper.extract_pcm(workspace.normalized_path, candidate),
            kind="EXTRACTED_AUDIO",
            suffix=".wav",
            tool_version=self._tool_version,
        )


class TranscriptionHandler:
    def __init__(
        self,
        client: TranscriptionClient,
        workspace_manager: JobWorkspaceManager,
        whisper: WhisperOperations,
        *,
        config_version: str,
        heartbeat_interval_seconds: float,
        tool_version: str,
    ) -> None:
        if not config_version or not tool_version:
            raise ValueError("Transcription configuration and tool versions are required.")
        self._client = client
        self._config_version = config_version
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._tool_version = tool_version
        self._whisper = whisper
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        if claim.state != "TRANSCRIBING" or claim.pipelineInput is None:
            raise RuntimeError("Transcription handler received an invalid state-specific claim.")
        expected = claim.pipelineInput.extractedAudio
        workspace = self._workspace_manager.open(claim.jobId)
        audio_path = workspace.root / "artifacts" / expected.artifactRef
        extracted = await asyncio.to_thread(
            ArtifactRecord.from_path,
            audio_path,
            kind="EXTRACTED_AUDIO",
            tool_version=expected.toolVersion,
        )
        if extracted.sha256 != expected.sha256 or extracted.byte_count != expected.byteCount:
            raise RuntimeError("Extracted audio does not match the control-plane lineage.")
        key = StageKey.for_input(claim.jobId, claim.jobVersion, claim.state, extracted.sha256, self._config_version)

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            result, transcript = await asyncio.to_thread(self._run_stage, workspace, key, audio_path)
            guard.assert_active()
            artifact = result.artifact
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="TRANSCRIBING",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    pipelineArtifacts=[
                        {
                            "artifactRef": artifact.path.name,
                            "byteCount": artifact.byte_count,
                            "kind": "TRANSCRIPT",
                            "sha256": artifact.sha256,
                            "toolVersion": artifact.tool_version,
                        }
                    ],
                    segmentIds=[segment.segment_id for segment in transcript.segments],
                    state="TRANSLATING",
                    step="transcribed",
                ),
            )

    def _run_stage(self, workspace: JobWorkspace, key: StageKey, audio_path: Path) -> tuple[StageResult, TranscriptArtifact]:
        output_prefix = workspace.root / f".{key.value}.whisper"
        transcript: TranscriptArtifact | None = None

        def execute(candidate: Path) -> None:
            nonlocal transcript
            transcript = self._whisper.transcribe(audio_path, output_prefix)
            payload = {
                "binaryVersion": transcript.binary_version,
                "modelSha256": transcript.model_sha256,
                "segments": [segment.json_value() for segment in transcript.segments],
            }
            candidate.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")

        try:
            result = StageRunner(workspace).run(
                key,
                execute=execute,
                kind="TRANSCRIPT",
                suffix=".json",
                tool_version=self._tool_version,
            )
            if transcript is None:
                persisted = json.loads(result.artifact.path.read_text(encoding="utf-8"))
                raw_segments = persisted["segments"]
                if not isinstance(raw_segments, list):
                    raise RuntimeError("Replayed transcript has invalid canonical segments.")
                transcript = TranscriptArtifact(
                    binary_version=str(persisted["binaryVersion"]),
                    model_sha256=str(persisted["modelSha256"]),
                    segments=[
                        TranscriptSegment(
                            end_ms=int(segment["endMs"]),
                            segment_id=str(segment["segmentId"]),
                            start_ms=int(segment["startMs"]),
                            text=str(segment["text"]),
                        )
                        for segment in raw_segments
                    ],
                )
            return result, transcript
        finally:
            output_prefix.with_suffix(".json").unlink(missing_ok=True)
