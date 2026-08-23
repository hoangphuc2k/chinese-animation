"""Lease-bound production handlers for localization and media stages."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol, Sequence

from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest, PipelineArtifact
from localizer_agent.lease import LeaseGuard
from localizer_agent.localization.gemini_client import GeminiClient
from localizer_agent.localization.prompt import build_prompt
from localizer_agent.localization.validator import ScriptSegment, validate_script
from localizer_agent.pipeline.approval_gate import ApprovalSnapshot, StaleApprovalError, require_exact_approval
from localizer_agent.pipeline.lineage import ArtifactRecord, StageKey
from localizer_agent.pipeline.runner import StageResult, StageRunner
from localizer_agent.preview.registry import PreviewRegistry
from localizer_agent.render.ffmpeg import RenderInputs
from localizer_agent.speech.audio import TimingReviewRequired
from localizer_agent.subtitles.writer import write_srt
from localizer_agent.transcription.segments import TranscriptSegment
from localizer_agent.workspace import JobWorkspace, JobWorkspaceManager


class StageClient(Protocol):
    async def complete(self, claim: ClaimJobResponse, completion: CompleteStepRequest) -> None: ...

    async def heartbeat(self, claim: ClaimJobResponse) -> None: ...

    async def get_script_approval(
        self,
        job_id: str,
        *,
        script_version: int,
        content_sha256: str,
    ) -> ApprovalSnapshot | None: ...


class TranslationOperations(Protocol):
    def translate(self, segments: Sequence[TranscriptSegment]) -> list[ScriptSegment]: ...


class VoiceOperations(Protocol):
    def synthesize(self, job_id: str, segments: Sequence[ScriptSegment]) -> bytes: ...


class RenderOperations(Protocol):
    def render(self, inputs: RenderInputs) -> None: ...


class GeminiTranslationOperations:
    def __init__(self, client: GeminiClient, glossary: dict[str, str] | None = None) -> None:
        self._client = client
        self._glossary = dict(glossary or {})

    def translate(self, segments: Sequence[TranscriptSegment]) -> list[ScriptSegment]:
        translated = asyncio.run(self._client.generate_json(build_prompt(segments, self._glossary)))
        return validate_script(segments, translated)


def _artifact_path(workspace: JobWorkspace, artifact: PipelineArtifact) -> Path:
    if Path(artifact.artifactRef).name != artifact.artifactRef:
        raise RuntimeError("Pipeline artifact reference must be an opaque file name.")
    path = workspace.root / "artifacts" / artifact.artifactRef
    actual = ArtifactRecord.from_path(path, kind=artifact.kind, tool_version=artifact.toolVersion)
    if actual.sha256 != artifact.sha256 or actual.byte_count != artifact.byteCount:
        raise RuntimeError("Pipeline artifact does not match the control-plane lineage.")
    return path


def _transcript_segments(path: Path) -> list[TranscriptSegment]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(raw_segments, list):
        raise RuntimeError("Transcript artifact has no canonical segments.")
    segments: list[TranscriptSegment] = []
    previous_end = 0
    for raw in raw_segments:
        if not isinstance(raw, dict) or set(raw) != {"endMs", "segmentId", "startMs", "text"}:
            raise RuntimeError("Transcript artifact contains an invalid segment.")
        segment = TranscriptSegment(
            end_ms=int(raw["endMs"]),
            segment_id=str(raw["segmentId"]),
            start_ms=int(raw["startMs"]),
            text=str(raw["text"]),
        )
        if segment.start_ms < previous_end or segment.end_ms <= segment.start_ms or not segment.text.strip():
            raise RuntimeError("Transcript artifact segment timing is invalid.")
        segments.append(segment)
        previous_end = segment.end_ms
    if not segments:
        raise RuntimeError("Transcript artifact must contain at least one segment.")
    return segments


def _script_segments(path: Path) -> list[ScriptSegment]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(raw_segments, list):
        raise RuntimeError("Script artifact has no canonical segments.")
    segments: list[ScriptSegment] = []
    previous_end = 0
    for raw in raw_segments:
        if not isinstance(raw, dict) or set(raw) != {"endMs", "segmentId", "startMs", "text"}:
            raise RuntimeError("Script artifact contains an invalid segment.")
        segment = ScriptSegment(
            end_ms=int(raw["endMs"]),
            segment_id=str(raw["segmentId"]),
            start_ms=int(raw["startMs"]),
            text=str(raw["text"]),
        )
        if segment.start_ms < previous_end or segment.end_ms <= segment.start_ms or not segment.text.strip():
            raise RuntimeError("Script artifact segment timing is invalid.")
        segments.append(segment)
        previous_end = segment.end_ms
    if not segments:
        raise RuntimeError("Script artifact must contain at least one segment.")
    return segments


class TranslationHandler:
    def __init__(
        self,
        client: StageClient,
        workspace_manager: JobWorkspaceManager,
        operations: TranslationOperations,
        *,
        config_version: str,
        heartbeat_interval_seconds: float,
        tool_version: str,
    ) -> None:
        if not config_version or not tool_version:
            raise ValueError("Translation configuration and tool versions are required.")
        self._client = client
        self._config_version = config_version
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._operations = operations
        self._tool_version = tool_version
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        pipeline_input = claim.pipelineInput
        if claim.state != "TRANSLATING" or pipeline_input is None or pipeline_input.state != "TRANSLATING":
            raise RuntimeError("Translation handler received an invalid state-specific claim.")
        if not pipeline_input.googleProcessingAllowed:
            raise RuntimeError("Translation claim does not permit Google processing.")
        workspace = self._workspace_manager.open(claim.jobId)
        transcript_path = _artifact_path(workspace, pipeline_input.transcript)
        key = StageKey.for_input(claim.jobId, claim.jobVersion, claim.state, pipeline_input.transcript.sha256, self._config_version)

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            result = await asyncio.to_thread(self._run_stage, workspace, key, transcript_path)
            guard.assert_active()
            artifact = result.artifact
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="TRANSLATING",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    pipelineArtifacts=[
                        {
                            "artifactRef": artifact.path.name,
                            "byteCount": artifact.byte_count,
                            "kind": "SCRIPT",
                            "sha256": artifact.sha256,
                            "toolVersion": artifact.tool_version,
                        }
                    ],
                    state="WAITING_SCRIPT_REVIEW",
                    step="translated",
                ),
            )

    def _run_stage(self, workspace: JobWorkspace, key: StageKey, transcript_path: Path) -> StageResult:
        def execute(candidate: Path) -> None:
            script = self._operations.translate(_transcript_segments(transcript_path))
            candidate.write_text(
                json.dumps(
                    {"segments": [segment.json_value() for segment in script]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

        return StageRunner(workspace).run(
            key,
            execute=execute,
            kind="SCRIPT",
            suffix=".json",
            tool_version=self._tool_version,
        )


class VoiceHandler:
    def __init__(
        self,
        client: StageClient,
        workspace_manager: JobWorkspaceManager,
        operations: VoiceOperations,
        *,
        config_version: str,
        heartbeat_interval_seconds: float,
        tool_version: str,
    ) -> None:
        if not config_version or not tool_version:
            raise ValueError("Voice configuration and tool versions are required.")
        self._client = client
        self._config_version = config_version
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._operations = operations
        self._tool_version = tool_version
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        pipeline_input = claim.pipelineInput
        if claim.state != "GENERATING_VOICE" or pipeline_input is None or pipeline_input.state != "GENERATING_VOICE":
            raise RuntimeError("Voice handler received an invalid state-specific claim.")
        if pipeline_input.script.sha256 != pipeline_input.scriptSha256:
            raise RuntimeError("Voice claim script identity is inconsistent.")
        workspace = self._workspace_manager.open(claim.jobId)
        script_path = _artifact_path(workspace, pipeline_input.script)
        key = StageKey.for_input(claim.jobId, claim.jobVersion, claim.state, pipeline_input.scriptSha256, self._config_version)

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            approval = await self._client.get_script_approval(
                claim.jobId,
                script_version=pipeline_input.scriptVersion,
                content_sha256=pipeline_input.scriptSha256,
            )
            try:
                require_exact_approval(
                    claim.jobId,
                    pipeline_input.scriptVersion,
                    pipeline_input.scriptSha256,
                    approval,
                )
            except StaleApprovalError:
                guard.assert_active()
                await self._client.complete(
                    claim,
                    CompleteStepRequest(
                        expectedState="GENERATING_VOICE",
                        jobVersion=claim.jobVersion,
                        leaseToken=claim.leaseToken,
                        state="WAITING_SCRIPT_REVIEW",
                        step="script_approval_stale",
                    ),
                )
                return
            try:
                result = await asyncio.to_thread(self._run_stage, workspace, key, script_path, claim.jobId)
            except TimingReviewRequired:
                guard.assert_active()
                await self._client.complete(
                    claim,
                    CompleteStepRequest(
                        expectedState="GENERATING_VOICE",
                        jobVersion=claim.jobVersion,
                        leaseToken=claim.leaseToken,
                        state="TIMING_REVIEW_REQUIRED",
                        step="voice_timing_review_required",
                    ),
                )
                return
            guard.assert_active()
            artifact = result.artifact
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="GENERATING_VOICE",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    pipelineArtifacts=[
                        {
                            "artifactRef": artifact.path.name,
                            "byteCount": artifact.byte_count,
                            "kind": "VOICE",
                            "sha256": artifact.sha256,
                            "toolVersion": artifact.tool_version,
                        }
                    ],
                    state="MIXING",
                    step="voice_generated",
                ),
            )

    def _run_stage(self, workspace: JobWorkspace, key: StageKey, script_path: Path, job_id: str) -> StageResult:
        def execute(candidate: Path) -> None:
            candidate.write_bytes(self._operations.synthesize(job_id, _script_segments(script_path)))

        return StageRunner(workspace).run(
            key,
            execute=execute,
            kind="VOICE",
            suffix=".wav",
            tool_version=self._tool_version,
        )


class MixingHandler:
    def __init__(self, client: StageClient, workspace_manager: JobWorkspaceManager, *, heartbeat_interval_seconds: float) -> None:
        self._client = client
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        pipeline_input = claim.pipelineInput
        if claim.state != "MIXING" or pipeline_input is None or pipeline_input.state != "MIXING":
            raise RuntimeError("Mixing handler received an invalid state-specific claim.")
        workspace = self._workspace_manager.open(claim.jobId)
        script_path = _artifact_path(workspace, pipeline_input.script)
        voice_path = _artifact_path(workspace, pipeline_input.voice)
        audio_key = StageKey.for_input(claim.jobId, claim.jobVersion, "MIXED_AUDIO", pipeline_input.voice.sha256, "aligned-narration-v1")
        subtitle_key = StageKey.for_input(claim.jobId, claim.jobVersion, "SUBTITLES", pipeline_input.script.sha256, "srt-v1")

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            mixed, subtitles = await asyncio.gather(
                asyncio.to_thread(self._copy_audio, workspace, audio_key, voice_path),
                asyncio.to_thread(self._write_subtitles, workspace, subtitle_key, script_path),
            )
            guard.assert_active()
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="MIXING",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    pipelineArtifacts=[
                        self._pipeline_artifact(mixed.artifact, "MIXED_AUDIO"),
                        self._pipeline_artifact(subtitles.artifact, "SUBTITLES"),
                    ],
                    state="RENDERING",
                    step="audio_and_subtitles_mixed",
                ),
            )

    @staticmethod
    def _copy_audio(workspace: JobWorkspace, key: StageKey, voice_path: Path) -> StageResult:
        return StageRunner(workspace).run(
            key,
            execute=lambda candidate: shutil.copyfile(voice_path, candidate),
            kind="MIXED_AUDIO",
            suffix=".wav",
            tool_version="aligned-narration-v1",
        )

    @staticmethod
    def _write_subtitles(workspace: JobWorkspace, key: StageKey, script_path: Path) -> StageResult:
        return StageRunner(workspace).run(
            key,
            execute=lambda candidate: write_srt(_script_segments(script_path), candidate),
            kind="SUBTITLES",
            suffix=".srt",
            tool_version="srt-v1",
        )

    @staticmethod
    def _pipeline_artifact(artifact: ArtifactRecord, kind: str) -> dict[str, int | str]:
        return {
            "artifactRef": artifact.path.name,
            "byteCount": artifact.byte_count,
            "kind": kind,
            "sha256": artifact.sha256,
            "toolVersion": artifact.tool_version,
        }


class RenderHandler:
    def __init__(
        self,
        client: StageClient,
        workspace_manager: JobWorkspaceManager,
        operations: RenderOperations,
        watermark_path: Path,
        *,
        heartbeat_interval_seconds: float,
        tool_version: str,
    ) -> None:
        if not watermark_path.is_file() or not tool_version:
            raise ValueError("Rendering requires a watermark file and tool version.")
        self._client = client
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._operations = operations
        self._tool_version = tool_version
        self._watermark_path = watermark_path
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        pipeline_input = claim.pipelineInput
        if claim.state != "RENDERING" or pipeline_input is None or pipeline_input.state != "RENDERING":
            raise RuntimeError("Render handler received an invalid state-specific claim.")
        workspace = self._workspace_manager.open(claim.jobId)
        normalized = ArtifactRecord.from_path(workspace.normalized_path, kind="NORMALIZED_MEDIA", tool_version=pipeline_input.normalizedMedia.toolVersion)
        if normalized.sha256 != pipeline_input.normalizedMedia.sha256 or normalized.byte_count != pipeline_input.normalizedMedia.byteCount:
            raise RuntimeError("Normalized media does not match render claim lineage.")
        narration = _artifact_path(workspace, pipeline_input.mixedAudio)
        subtitles = _artifact_path(workspace, pipeline_input.subtitles)
        key = StageKey.for_input(claim.jobId, claim.jobVersion, claim.state, pipeline_input.mixedAudio.sha256, self._tool_version)

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            approval = await self._client.get_script_approval(
                claim.jobId,
                script_version=pipeline_input.scriptVersion,
                content_sha256=pipeline_input.scriptSha256,
            )
            try:
                require_exact_approval(claim.jobId, pipeline_input.scriptVersion, pipeline_input.scriptSha256, approval)
            except StaleApprovalError:
                guard.assert_active()
                await self._client.complete(
                    claim,
                    CompleteStepRequest(
                        expectedState="RENDERING",
                        jobVersion=claim.jobVersion,
                        leaseToken=claim.leaseToken,
                        state="WAITING_SCRIPT_REVIEW",
                        step="render_approval_stale",
                    ),
                )
                return
            result = await asyncio.to_thread(self._run_stage, workspace, key, narration, subtitles)
            guard.assert_active()
            artifact = result.artifact
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="RENDERING",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    pipelineArtifacts=[MixingHandler._pipeline_artifact(artifact, "RENDER")],
                    state="READY_FOR_PREVIEW",
                    step="render_validated",
                ),
            )

    def _run_stage(self, workspace: JobWorkspace, key: StageKey, narration: Path, subtitles: Path) -> StageResult:
        def execute(candidate: Path) -> None:
            self._operations.render(RenderInputs(candidate, narration, workspace.normalized_path, subtitles, self._watermark_path))

        return StageRunner(workspace).run(
            key,
            execute=execute,
            kind="RENDER",
            suffix=".mp4",
            tool_version=self._tool_version,
        )


class PreviewHandler:
    def __init__(
        self,
        client: StageClient,
        workspace_manager: JobWorkspaceManager,
        registry: PreviewRegistry,
        *,
        heartbeat_interval_seconds: float,
        now_utc: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._now_utc = now_utc or (lambda: datetime.now(UTC))
        self._registry = registry
        self._workspace_manager = workspace_manager

    async def handle(self, claim: ClaimJobResponse) -> None:
        pipeline_input = claim.pipelineInput
        if claim.state != "READY_FOR_PREVIEW" or pipeline_input is None or pipeline_input.state != "READY_FOR_PREVIEW":
            raise RuntimeError("Preview handler received an invalid state-specific claim.")
        workspace = self._workspace_manager.open(claim.jobId)
        render = _artifact_path(workspace, pipeline_input.render)
        _artifact_path(workspace, pipeline_input.subtitles)
        now = self._now_utc()
        if now.tzinfo is None:
            raise ValueError("Preview registration time must include a timezone.")
        expires_at = now.astimezone(UTC) + timedelta(minutes=5)

        async with LeaseGuard(self._client, claim, self._heartbeat_interval_seconds) as guard:
            token = self._registry.register(
                claim.jobId,
                f"{claim.jobId}/artifacts/{render.name}",
                expires_at,
            )
            guard.assert_active()
            await self._client.complete(
                claim,
                CompleteStepRequest(
                    expectedState="READY_FOR_PREVIEW",
                    jobVersion=claim.jobVersion,
                    leaseToken=claim.leaseToken,
                    preview={
                        "expiresAtUtc": expires_at.isoformat().replace("+00:00", "Z"),
                        "renderSha256": pipeline_input.render.sha256,
                        "token": token,
                    },
                    state="WAITING_PUBLISH_APPROVAL",
                    step="preview_registered",
                ),
            )
