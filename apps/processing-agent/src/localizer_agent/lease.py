"""Lease keepalive guard that prevents completing after a lost heartbeat."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Protocol

from localizer_agent.generated.models import ClaimJobResponse


class HeartbeatClient(Protocol):
    async def heartbeat(self, claim: ClaimJobResponse) -> None: ...


class LeaseLostError(RuntimeError):
    pass


class LeaseGuard:
    def __init__(self, client: HeartbeatClient, claim: ClaimJobResponse, interval_seconds: float) -> None:
        self._client = client
        self._claim = claim
        self._interval_seconds = interval_seconds
        self._failure: BaseException | None = None
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "LeaseGuard":
        self._task = asyncio.create_task(self._heartbeat_loop())
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    def assert_active(self) -> None:
        if self._failure is not None:
            raise LeaseLostError("The job lease was lost while the agent was working.") from self._failure

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                await self._client.heartbeat(self._claim)
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # preserve a transport cancellation/failure for the caller
            self._failure = error
