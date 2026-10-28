"""Lease keepalive guard that prevents completing after a lost heartbeat."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol

from localizer_agent.control_plane import ControlPlaneError
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
        self._lease_window_seconds = 0.0
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "LeaseGuard":
        # Lease time still granted by the claim; each successful heartbeat renews a window of this length.
        self._lease_window_seconds = self._remaining_lease_seconds()
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

    def _remaining_lease_seconds(self) -> float:
        expires_at = datetime.fromisoformat(self._claim.leaseExpiresAtUtc.replace("Z", "+00:00"))
        return (expires_at - datetime.now(UTC)).total_seconds()

    async def _heartbeat_loop(self) -> None:
        last_renewed = time.monotonic()
        try:
            while True:
                await asyncio.sleep(self._interval_seconds)
                try:
                    await self._client.heartbeat(self._claim)
                    last_renewed = time.monotonic()
                except ControlPlaneError as error:
                    # 4xx: the control plane rejected this lease (lost, stale or unauthorized). Final.
                    if 400 <= error.status_code < 500:
                        raise
                    if time.monotonic() - last_renewed >= self._lease_window_seconds:
                        raise
                except (OSError, TimeoutError):
                    # Network failures and timeouts are retried until the lease could have expired.
                    if time.monotonic() - last_renewed >= self._lease_window_seconds:
                        raise
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # preserve a transport cancellation/failure for the caller
            self._failure = error
