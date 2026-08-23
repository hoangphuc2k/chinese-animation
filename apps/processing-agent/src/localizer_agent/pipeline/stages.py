"""Lease-bound production handlers for localization and media stages."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol, Sequence

from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest, PipelineArtifact
from localizer_agent.lease import LeaseGuard
from localizer_agent.localization.gemini_client import GeminiClient
from localizer_agent.localization.prompt import build_prompt
from localizer_agent.localization.validator import ScriptSegment, validate_script
from localizer_agent.pipeline.approval_gate import ApprovalSnapshot, StaleApprovalError, require_exact_approval
from localizer_agent.pipeline.lineage import ArtifactRecord, StageKey
from localizer_agent.pipeline.runner import StageResult, StageRunner
from localizer_agent.speech.audio import TimingReviewRequired
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
