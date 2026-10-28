"""Typed, authenticated control-plane client for lease-bound agent work."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from collections.abc import Sequence
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from localizer_agent.generated.models import ClaimJobRequest, ClaimJobResponse, CompleteStepRequest, HeartbeatRequest, JobType
from localizer_agent.pipeline.approval_gate import ApprovalSnapshot


@dataclass(frozen=True)
class AccessServiceToken:
    """Cloudflare Access service-token credentials, sent as CF-Access-Client-Id/Secret headers."""

    client_id: str
    client_secret: str = field(repr=False)


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
    def __init__(self, base_url: str, service_token: AccessServiceToken, transport: HttpTransport | None = None, request_timeout_seconds: int = 30) -> None:
        self._base_url = base_url.rstrip("/")
        self._service_token = service_token
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

    async def get_script_approval(self, job_id: str, *, script_version: int, content_sha256: str) -> ApprovalSnapshot | None:
        if script_version <= 0 or len(content_sha256) != 64 or any(character not in "0123456789abcdef" for character in content_sha256):
            raise ValueError("Approval lookup requires a positive script version and lowercase SHA-256.")
        query = urlencode({"contentHash": content_sha256, "scriptVersion": script_version})
        response = await self._request("GET", f"/api/v1/agent/jobs/{job_id}/script-approval?{query}", None)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ControlPlaneError(response.status_code, "script approval lookup")
        try:
            payload = json.loads(response.body)
            if not isinstance(payload, dict) or set(payload) != {"approved", "contentHash", "jobId", "scriptVersion"}:
                raise ValueError("invalid approval response")
            approval = ApprovalSnapshot(
                approved=payload["approved"] is True,
                content_sha256=str(payload["contentHash"]),
                job_id=str(payload["jobId"]),
                script_version=int(payload["scriptVersion"]),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ControlPlaneError(502, "script approval response validation") from error
        if approval.job_id != job_id or approval.script_version != script_version or approval.content_sha256 != content_sha256:
            raise ControlPlaneError(502, "script approval response validation")
        return approval

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

    async def _request(self, method: str, path: str, payload: dict[str, object] | None) -> HttpResponse:
        return await self._transport.send(
            method,
            f"{self._base_url}{path}",
            {
                "CF-Access-Client-Id": self._service_token.client_id,
                "CF-Access-Client-Secret": self._service_token.client_secret,
                "Content-Type": "application/json",
            },
            None if payload is None else json.dumps(payload, separators=(",", ":")).encode(),
            self._request_timeout_seconds,
        )

    @staticmethod
    def _valid_claim_protocol(claim: ClaimJobResponse) -> bool:
        needs_import_input = claim.jobType == "IMPORT" and claim.state in {"QUEUED", "INGESTING"}
        if needs_import_input != (claim.importInput is not None):
            return False
        pipeline_input = claim.pipelineInput
        if claim.state == "PROBING":
            return claim.ingestResult is not None and pipeline_input is None
        if claim.state == "EXTRACTING_AUDIO":
            return pipeline_input is not None and pipeline_input.state == claim.state and pipeline_input.normalizedMedia.kind == "NORMALIZED_MEDIA"
        if claim.state == "TRANSCRIBING":
            return pipeline_input is not None and pipeline_input.state == claim.state and pipeline_input.extractedAudio.kind == "EXTRACTED_AUDIO"
        if claim.state == "TRANSLATING":
            return pipeline_input is not None and pipeline_input.state == claim.state and pipeline_input.googleProcessingAllowed and pipeline_input.transcript.kind == "TRANSCRIPT"
        if claim.state == "GENERATING_VOICE":
            return pipeline_input is not None and pipeline_input.state == claim.state and pipeline_input.script.kind == "SCRIPT" and pipeline_input.script.sha256 == pipeline_input.scriptSha256
        if claim.state == "MIXING":
            return pipeline_input is not None and pipeline_input.state == claim.state and pipeline_input.script.kind == "SCRIPT" and pipeline_input.voice.kind == "VOICE"
        if claim.state == "RENDERING":
            return (
                pipeline_input is not None
                and pipeline_input.state == claim.state
                and pipeline_input.mixedAudio.kind == "MIXED_AUDIO"
                and pipeline_input.normalizedMedia.kind == "NORMALIZED_MEDIA"
                and pipeline_input.subtitles.kind == "SUBTITLES"
            )
        if claim.state == "READY_FOR_PREVIEW":
            return pipeline_input is not None and pipeline_input.state == claim.state and pipeline_input.render.kind == "RENDER" and pipeline_input.subtitles.kind == "SUBTITLES"
        return pipeline_input is None
