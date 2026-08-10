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
        supported_job_types: Sequence[JobType],
    ) -> None:
        if not handlers:
            raise ValueError("At least one processing-state handler is required.")
        if not supported_job_types:
            raise ValueError("At least one supported job type is required.")
        self._client = client
        self._handlers = dict(handlers)
        self._supported_job_types = tuple(supported_job_types)

    async def run_once(self) -> bool:
        claim = await self._client.claim(self._supported_job_types, tuple(self._handlers))
        if claim is None:
            return False
        handler = self._handlers.get(claim.state)
        if handler is None:
            raise RuntimeError("Control plane returned a state outside the declared agent capability set.")
        await handler.handle(claim)
        return True
