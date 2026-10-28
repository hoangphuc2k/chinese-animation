"""Capability-scoped dispatch for one lease-bound processing stage at a time."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from localizer_agent.generated.models import ClaimJobResponse, CompleteStepRequest, FailureCode, JobType
from localizer_agent.ingest import SourceIntegrityError, SourceUnavailableError
from localizer_agent.render.validator import RenderValidationError
from localizer_agent.subtitles.writer import SubtitleError
from localizer_agent.transcription.segments import SegmentError


class ClaimClient(Protocol):
    async def claim(self, supported_job_types: Sequence[JobType], supported_states: Sequence[str]) -> ClaimJobResponse | None: ...

    async def complete(self, claim: ClaimJobResponse, completion: CompleteStepRequest) -> None: ...


class StageHandler(Protocol):
    async def handle(self, claim: ClaimJobResponse) -> None: ...


# Errors that will recur identically on every retry. Reporting them ends the step instead of
# letting the lease expire and the same input crash again. Anything else is treated as transient
# and is bounded by the control plane's per-step attempt cap.
_DETERMINISTIC_FAILURES: tuple[tuple[type[Exception], str, FailureCode, str], ...] = (
    (SubtitleError, "MANUAL_REVIEW_REQUIRED", "MANUAL_REVIEW_REQUIRED", "subtitle_rules_failed"),
    (SourceIntegrityError, "FAILED", "MEDIA_INVALID", "source_rejected"),
    (SegmentError, "FAILED", "MEDIA_INVALID", "transcript_invalid"),
    (RenderValidationError, "FAILED", "MEDIA_INVALID", "render_invalid"),
)


def _deterministic_failure(error: Exception) -> tuple[str, FailureCode, str] | None:
    if isinstance(error, SourceUnavailableError):
        return None
    for error_type, state, code, step in _DETERMINISTIC_FAILURES:
        if isinstance(error, error_type):
            return state, code, step
    return None


class JobTypeRouter:
    """Dispatches claims for one state to the handler registered for the claimed job type."""

    def __init__(self, handlers: Mapping[JobType, StageHandler]) -> None:
        if not handlers:
            raise ValueError("At least one job-type handler is required.")
        self._handlers = dict(handlers)

    @property
    def job_types(self) -> tuple[JobType, ...]:
        return tuple(self._handlers)

    async def handle(self, claim: ClaimJobResponse) -> None:
        handler = self._handlers.get(claim.jobType)
        if handler is None:
            raise RuntimeError("Control plane returned a job type outside the declared agent capability set.")
        await handler.handle(claim)


class ProcessingSupervisor:
    """Claims only work for which this process has a registered production handler."""

    def __init__(
        self,
        client: ClaimClient,
        handlers: Mapping[str, StageHandler],
        *,
        supported_job_types: Sequence[JobType] | None = None,
        job_types_by_state: Mapping[str, Sequence[JobType]] | None = None,
    ) -> None:
        if not handlers:
            raise ValueError("At least one processing-state handler is required.")
        if job_types_by_state is not None:
            if supported_job_types is not None or set(job_types_by_state) != set(handlers) or any(not types for types in job_types_by_state.values()):
                raise ValueError("Every handler requires one explicit non-empty job-type capability.")
            capabilities = {state: tuple(job_types_by_state[state]) for state in handlers}
        else:
            if not supported_job_types:
                raise ValueError("At least one supported job type is required.")
            capabilities = {state: tuple(supported_job_types) for state in handlers}
        self._client = client
        self._handlers = dict(handlers)
        self._job_types_by_state = capabilities

    async def run_once(self) -> bool:
        for state, handler in self._handlers.items():
            claim = await self._client.claim(self._job_types_by_state[state], [state])
            if claim is None:
                continue
            if claim.state != state:
                raise RuntimeError("Control plane returned a state outside the declared agent capability set.")
            try:
                await handler.handle(claim)
            except Exception as error:
                failure = _deterministic_failure(error)
                if failure is None:
                    raise
                state, code, step = failure
                await self._client.complete(
                    claim,
                    CompleteStepRequest(
                        expectedState=claim.state,
                        failureCode=code,
                        jobVersion=claim.jobVersion,
                        leaseToken=claim.leaseToken,
                        state=state,
                        step=step,
                    ),
                )
            return True
        return False
