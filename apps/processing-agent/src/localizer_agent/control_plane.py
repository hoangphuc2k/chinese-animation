"""Typed, authenticated control-plane client for lease-bound agent work."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Protocol
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from localizer_agent.generated.models import ClaimJobRequest, ClaimJobResponse, CompleteStepRequest, HeartbeatRequest, JobType


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes


class HttpTransport(Protocol):
    async def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout_seconds: int) -> HttpResponse: ...


class ControlPlaneError(RuntimeError):
    def __init__(self, status_code: int, operation: str) -> None:
        super().__init__(f"Control-plane {operation} failed with HTTP {status_code}.")
        self.status_code = status_code


class UrllibTransport:
    async def send(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout_seconds: int) -> HttpResponse:
        def request() -> HttpResponse:
            try:
                with urlopen(Request(url, data=body, headers=headers, method=method), timeout=timeout_seconds) as response:  # noqa: S310 -- origin is verified configuration
                    return HttpResponse(response.status, response.read())
            except HTTPError as error:
                return HttpResponse(error.code, error.read())

        return await asyncio.to_thread(request)


class AgentControlPlaneClient:
    def __init__(self, base_url: str, access_token: str, transport: HttpTransport | None = None, request_timeout_seconds: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._transport = transport or UrllibTransport()
        self._request_timeout_seconds = request_timeout_seconds

    async def claim(self, supported_job_types: Sequence[JobType], supported_states: Sequence[str]) -> ClaimJobResponse | None:
        declared_types = list(supported_job_types)
        declared_states = list(supported_states)
        request = ClaimJobRequest(supportedJobTypes=declared_types, supportedStates=declared_states)
        response = await self._request("POST", "/api/v1/agent/jobs/claim", request.model_dump(mode="json"))
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise ControlPlaneError(response.status_code, "claim")
        claim = ClaimJobResponse.model_validate_json(response.body)
        if claim.jobType not in declared_types or claim.state not in declared_states or not self._valid_claim_protocol(claim):
            raise ControlPlaneError(502, "claim protocol validation")
        return claim

    async def claim_import(self) -> ClaimJobResponse | None:
        """Compatibility entrypoint for the ingest-only supervisor."""
        return await self.claim(["IMPORT"], ["QUEUED", "INGESTING"])

    async def heartbeat(self, claim: ClaimJobResponse) -> None:
        response = await self._request(
            "POST",
            f"/api/v1/agent/jobs/{claim.jobId}/heartbeat",
            HeartbeatRequest(jobVersion=claim.jobVersion, leaseToken=claim.leaseToken).model_dump(mode="json"),
        )
        if response.status_code != 204:
            raise ControlPlaneError(response.status_code, "heartbeat")

    async def complete(self, claim: ClaimJobResponse, completion: CompleteStepRequest) -> None:
        if completion.jobVersion != claim.jobVersion or completion.leaseToken != claim.leaseToken:
            raise ValueError("Completion must use the claim's job version and lease token.")
        if completion.expectedState != claim.state:
            raise ValueError("Completion expected state must match the claimed state.")
        if completion.ingestResult is not None and (completion.expectedState != "INGESTING" or completion.state != "PROBING"):
            raise ValueError("Ingest metadata requires an INGESTING to PROBING transition.")
        response = await self._request("POST", f"/api/v1/agent/jobs/{claim.jobId}/complete", completion.model_dump(mode="json", exclude_none=True))
        if response.status_code != 204:
            raise ControlPlaneError(response.status_code, "completion")

    async def _request(self, method: str, path: str, payload: dict[str, object]) -> HttpResponse:
        return await self._transport.send(
            method,
            f"{self._base_url}{path}",
            {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"},
            json.dumps(payload, separators=(",", ":")).encode(),
            self._request_timeout_seconds,
        )

    @staticmethod
    def _valid_claim_protocol(claim: ClaimJobResponse) -> bool:
        needs_import_input = claim.jobType == "IMPORT" and claim.state in {"QUEUED", "INGESTING"}
        if needs_import_input != (claim.importInput is not None):
            return False
        if claim.state == "TRANSCRIBING":
            return claim.pipelineInput is not None and claim.pipelineInput.normalizedMedia.kind == "NORMALIZED_MEDIA"
        return claim.pipelineInput is None
