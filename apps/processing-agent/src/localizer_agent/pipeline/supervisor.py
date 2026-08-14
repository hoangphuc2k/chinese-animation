"""Capability-scoped dispatch for one lease-bound processing stage at a time."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from localizer_agent.generated.models import ClaimJobResponse, JobType


class ClaimClient(Protocol):
    async def claim(self, supported_job_types: Sequence[JobType], supported_states: Sequence[str]) -> ClaimJobResponse | None: ...


class StageHandler(Protocol):
    async def handle(self, claim: ClaimJobResponse) -> None: ...


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
            await handler.handle(claim)
            return True
        return False
