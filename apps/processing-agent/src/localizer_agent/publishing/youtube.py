"""Approval-gated, resumable, private YouTube uploads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from localizer_agent.publishing.base import ApprovalBoundPublicationAdapter, PublicationApprovalClient
from localizer_agent.publishing.models import Destination, PublicationCommand, YouTubeMetadata
from localizer_agent.publishing.youtube_auth import YouTubeAuthError, YouTubeCredentials


_CHUNK_GRANULARITY = 256 * 1024
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{6,64}")
_RANGE = re.compile(r"bytes=0-(\d+)")


class RetryableYouTubeError(RuntimeError):
    """A transient provider failure that may be retried within configured bounds."""


class SessionExpiredError(RuntimeError):
    """The provider no longer recognizes a resumable upload session."""


class YouTubeQuotaError(RuntimeError):
    """The provider rejected an operation because quota was exhausted."""


class YouTubeProcessingError(RuntimeError):
    """The provider returned an invalid or terminal upload state."""


class YouTubeIntegrityError(RuntimeError):
    """The opened media does not match the approved render digest."""


class YouTubeCheckpointError(RuntimeError):
    """A durable checkpoint is malformed or bound to different inputs."""


@dataclass(frozen=True)
class ChunkResult:
    next_offset: int
    video_id: str | None


@dataclass(frozen=True)
class YouTubeCheckpoint:
    provider_correlation: str
    render_sha256: str
    metadata_sha256: str
    byte_count: int
    session_reference: str | None
    next_offset: int
    video_id: str | None


@dataclass(frozen=True)
class YouTubePublishResult:
    video_id: str
    studio_url: str


class CheckpointStore(Protocol):
    def load(self, publication_id: str) -> YouTubeCheckpoint | None: ...

    def save(self, publication_id: str, checkpoint: YouTubeCheckpoint) -> None: ...


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self._values: dict[str, YouTubeCheckpoint] = {}

    def load(self, publication_id: str) -> YouTubeCheckpoint | None:
        return self._values.get(publication_id)

    def save(self, publication_id: str, checkpoint: YouTubeCheckpoint) -> None:
        self._values[publication_id] = checkpoint


class YouTubeGateway(Protocol):
    async def exchange_refresh_token(self, credentials: YouTubeCredentials) -> str: ...

    async def create_session(
        self,
        *,
        access_token: str,
        byte_count: int,
        correlation: str,
        metadata: dict[str, object],
    ) -> str: ...

    async def upload_chunk(
        self,
        *,
        access_token: str,
        session_reference: str,
        offset: int,
        total_bytes: int,
        data: bytes,
    ) -> ChunkResult: ...

    async def query_session(
        self,
        *,
        access_token: str,
        session_reference: str,
        total_bytes: int,
    ) -> ChunkResult: ...

    async def processing_status(self, *, access_token: str, video_id: str) -> str: ...


@dataclass(frozen=True)
class YouTubeHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class YouTubeHttpTransport(Protocol):
    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> YouTubeHttpResponse: ...


class UrllibYouTubeTransport:
    """Small async wrapper around urllib for the concrete YouTube gateway."""

    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> YouTubeHttpResponse:
        def request() -> YouTubeHttpResponse:
            try:
                with urlopen(  # noqa: S310 -- all URLs are fixed Google origins or validated resumable URLs
                    Request(url, data=body, headers=headers, method=method),
                    timeout=timeout_seconds,
                ) as response:
                    return YouTubeHttpResponse(response.status, dict(response.headers.items()), response.read())
            except HTTPError as error:
                return YouTubeHttpResponse(error.code, dict(error.headers.items()), error.read())

        try:
            return await asyncio.to_thread(request)
        except (OSError, URLError):
            raise RetryableYouTubeError("YouTube request failed before receiving a response.") from None


class YouTubeHttpGateway:
    """OAuth and YouTube Data API v3 implementation with sanitized failures."""

    def __init__(
        self,
        *,
        transport: YouTubeHttpTransport | None = None,
        request_timeout_seconds: int = 30,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("YouTube request timeout must be positive.")
        self._transport = transport or UrllibYouTubeTransport()
        self._request_timeout_seconds = request_timeout_seconds

    async def exchange_refresh_token(self, credentials: YouTubeCredentials) -> str:
        body = urlencode(
            {
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": credentials.refresh_token,
            }
        ).encode("ascii")
        response = await self._send(
            "POST",
            "https://oauth2.googleapis.com/token",
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            body,
        )
        if response.status_code != 200:
            raise YouTubeAuthError("YouTube OAuth token exchange was rejected.")
        payload = self._json_object(response, "OAuth token")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise YouTubeAuthError("YouTube OAuth token response was invalid.")
        return access_token

    async def create_session(
        self,
        *,
        access_token: str,
        byte_count: int,
        correlation: str,
        metadata: dict[str, object],
    ) -> str:
        if byte_count <= 0:
            raise ValueError("YouTube upload byte count must be positive.")
        if metadata.get("status") != {"privacyStatus": "private"}:
            raise ValueError("YouTube uploads must be created private.")
        response = await self._send(
            "POST",
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet%2Cstatus",
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Goog-Request-Reason": correlation,
                "X-Upload-Content-Length": str(byte_count),
                "X-Upload-Content-Type": "video/mp4",
            },
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
        )
        self._require_status(response, "session creation", {200})
        session_reference = self._header(response.headers, "location")
        self._validate_session_reference(session_reference)
        return session_reference

    async def upload_chunk(
        self,
        *,
        access_token: str,
        session_reference: str,
        offset: int,
        total_bytes: int,
        data: bytes,
    ) -> ChunkResult:
        self._validate_session_reference(session_reference)
        if offset < 0 or total_bytes <= 0 or not data or offset + len(data) > total_bytes:
            raise ValueError("YouTube chunk bounds are invalid.")
        response = await self._send(
            "PUT",
            session_reference,
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Length": str(len(data)),
                "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{total_bytes}",
                "Content-Type": "video/mp4",
            },
            data,
        )
        return self._resumable_result(response, total_bytes)

    async def query_session(
        self,
        *,
        access_token: str,
        session_reference: str,
        total_bytes: int,
    ) -> ChunkResult:
        self._validate_session_reference(session_reference)
        if total_bytes <= 0:
            raise ValueError("YouTube upload byte count must be positive.")
        response = await self._send(
            "PUT",
            session_reference,
            {
                "Authorization": f"Bearer {access_token}",
                "Content-Length": "0",
                "Content-Range": f"bytes */{total_bytes}",
            },
            b"",
        )
        return self._resumable_result(response, total_bytes)

    async def processing_status(self, *, access_token: str, video_id: str) -> str:
        if not _VIDEO_ID.fullmatch(video_id):
            raise YouTubeProcessingError("YouTube returned an invalid provider video ID.")
        query = urlencode({"id": video_id, "part": "status"})
        response = await self._send(
            "GET",
            f"https://www.googleapis.com/youtube/v3/videos?{query}",
            {"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
            None,
        )
        self._require_status(response, "processing status", {200})
        payload = self._json_object(response, "processing status")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise YouTubeProcessingError("YouTube processing status response was invalid.")
        status = items[0].get("status")
        upload_status = status.get("uploadStatus") if isinstance(status, dict) else None
        if upload_status == "processed":
            return "succeeded"
        if upload_status == "uploaded":
            return "processing"
        if upload_status in {"deleted", "failed", "rejected"}:
            return "failed"
        raise YouTubeProcessingError("YouTube processing status response was invalid.")

    async def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> YouTubeHttpResponse:
        try:
            return await self._transport.send(method, url, headers, body, self._request_timeout_seconds)
        except (YouTubeAuthError, YouTubeQuotaError, RetryableYouTubeError, SessionExpiredError, YouTubeProcessingError):
            raise
        except Exception:
            raise RetryableYouTubeError("YouTube request failed before receiving a response.") from None

    def _resumable_result(self, response: YouTubeHttpResponse, total_bytes: int) -> ChunkResult:
        self._require_status(response, "resumable upload", {200, 201, 308}, session_operation=True)
        if response.status_code == 308:
            range_value = self._header(response.headers, "range", required=False)
            if range_value is None:
                return ChunkResult(0, None)
            match = _RANGE.fullmatch(range_value)
            if match is None:
                raise YouTubeProcessingError("YouTube resumable range response was invalid.")
            next_offset = int(match.group(1)) + 1
            if next_offset > total_bytes:
                raise YouTubeProcessingError("YouTube resumable range response was invalid.")
            return ChunkResult(next_offset, None)
        payload = self._json_object(response, "resumable upload")
        video_id = payload.get("id")
        if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
            raise YouTubeProcessingError("YouTube resumable completion response was invalid.")
        return ChunkResult(total_bytes, video_id)

    @staticmethod
    def _header(headers: Mapping[str, str], name: str, *, required: bool = True) -> str | None:
        value = next((value for key, value in headers.items() if key.lower() == name.lower()), None)
        if required and (not isinstance(value, str) or not value):
            raise YouTubeProcessingError("YouTube response omitted required resumable state.")
        return value

    @staticmethod
    def _json_object(response: YouTubeHttpResponse, operation: str) -> dict[str, object]:
        try:
            payload = json.loads(response.body)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise YouTubeProcessingError(f"YouTube {operation} response was invalid.") from None
        if not isinstance(payload, dict):
            raise YouTubeProcessingError(f"YouTube {operation} response was invalid.")
        return payload

    @staticmethod
    def _validate_session_reference(session_reference: str | None) -> None:
        if not isinstance(session_reference, str):
            raise YouTubeProcessingError("YouTube returned an invalid resumable session reference.")
        parsed = urlparse(session_reference)
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not (hostname == "googleapis.com" or hostname.endswith(".googleapis.com"))
        ):
            raise YouTubeProcessingError("YouTube returned an invalid resumable session reference.")

    @staticmethod
    def _error_reasons(response: YouTubeHttpResponse) -> set[str]:
        try:
            payload = json.loads(response.body)
            error = payload.get("error") if isinstance(payload, dict) else None
            errors = error.get("errors") if isinstance(error, dict) else None
            if not isinstance(errors, list):
                return set()
            return {
                str(item["reason"])
                for item in errors
                if isinstance(item, dict) and isinstance(item.get("reason"), str)
            }
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return set()

    @classmethod
    def _require_status(
        cls,
        response: YouTubeHttpResponse,
        operation: str,
        allowed: set[int],
        *,
        session_operation: bool = False,
    ) -> None:
        if response.status_code in allowed:
            return
        reasons = cls._error_reasons(response)
        if session_operation and response.status_code in {404, 410}:
            raise SessionExpiredError("YouTube resumable session expired.")
        if response.status_code == 401 or reasons & {"authError", "invalidCredentials"}:
            raise YouTubeAuthError(f"YouTube {operation} authorization was rejected.")
        if reasons & {"dailyLimitExceeded", "quotaExceeded", "uploadLimitExceeded"}:
            raise YouTubeQuotaError(f"YouTube {operation} quota was exhausted.")
        if response.status_code in {408, 425, 429} or response.status_code >= 500 or reasons & {
            "rateLimitExceeded",
            "userRateLimitExceeded",
        }:
            raise RetryableYouTubeError(f"YouTube {operation} failed transiently.")
        raise YouTubeProcessingError(f"YouTube {operation} failed with HTTP {response.status_code}.")


class YouTubeAdapter(ApprovalBoundPublicationAdapter):
    destination = Destination.YOUTUBE

    def __init__(
        self,
        approval_client: PublicationApprovalClient,
        gateway: YouTubeGateway,
        checkpoints: CheckpointStore,
        credentials: Callable[[], YouTubeCredentials],
        *,
        chunk_size: int = 8 * 1024 * 1024,
        max_retries: int = 4,
        max_session_recreations: int = 2,
        max_processing_polls: int = 20,
        retry_delay: Callable[[float], Awaitable[None]] = asyncio.sleep,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 32.0,
    ) -> None:
        super().__init__(approval_client)
        if chunk_size <= 0 or chunk_size % _CHUNK_GRANULARITY != 0:
            raise ValueError("YouTube non-final chunk size must be a positive multiple of 256 KiB.")
        if max_retries < 0 or max_session_recreations < 0 or max_processing_polls <= 0:
            raise ValueError("YouTube upload bounds are invalid.")
        if initial_backoff_seconds <= 0 or max_backoff_seconds < initial_backoff_seconds:
            raise ValueError("YouTube retry delays are invalid.")
        self._gateway = gateway
        self._checkpoints = checkpoints
        self._credentials = credentials
        self._chunk_size = chunk_size
        self._max_retries = max_retries
        self._max_session_recreations = max_session_recreations
        self._max_processing_polls = max_processing_polls
        self._retry_delay = retry_delay
        self._initial_backoff_seconds = initial_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds

    async def publish_approved(self, command: PublicationCommand, media_path: Path) -> YouTubePublishResult:
        metadata = command.metadata.for_destination(self.destination)
        if not isinstance(metadata, YouTubeMetadata):
            raise ValueError("YouTube command requires YouTube metadata.")

        with media_path.open("rb") as media:
            actual_sha256, total_bytes = self._verify_media(media)
            if total_bytes <= 0 or actual_sha256 != command.render_sha256:
                raise YouTubeIntegrityError("Opened media bytes do not match the approved render SHA-256.")

            checkpoint = self._checkpoints.load(command.publication_id)
            if checkpoint is not None:
                self._validate_checkpoint(command, checkpoint, total_bytes)

            access_token = await self._gateway.exchange_refresh_token(self._credentials())
            if checkpoint is None:
                checkpoint = self._bound_checkpoint(command, total_bytes, None, 0, None)
            if checkpoint.video_id is None:
                checkpoint = await self._upload(command, metadata, media, total_bytes, access_token, checkpoint)

        return await self._wait_until_processed(access_token, checkpoint.video_id or "")

    async def _upload(
        self,
        command: PublicationCommand,
        metadata: YouTubeMetadata,
        media: BinaryIO,
        total_bytes: int,
        access_token: str,
        checkpoint: YouTubeCheckpoint,
    ) -> YouTubeCheckpoint:
        session = checkpoint.session_reference
        offset = checkpoint.next_offset
        retries = 0
        recreations = 0

        if session is None:
            session = await self._new_session(command, metadata, total_bytes, access_token)
            offset = 0
            self._save(command, total_bytes, session, offset, None)
        else:
            try:
                result, retries = await self._query_with_retry(access_token, session, total_bytes, retries)
                offset = self._validated_offset(result, total_bytes)
                reconciled = self._save(command, total_bytes, session, offset, result.video_id)
                if result.video_id is not None:
                    return reconciled
            except SessionExpiredError as error:
                session, recreations = await self._recreate_session(
                    command, metadata, total_bytes, access_token, recreations, error
                )
                offset = 0
                retries = 0

        while offset < total_bytes:
            media.seek(offset)
            data = media.read(min(self._chunk_size, total_bytes - offset))
            if not data:
                raise YouTubeIntegrityError("Verified media ended before the approved byte count.")
            if offset + len(data) < total_bytes and len(data) % _CHUNK_GRANULARITY != 0:
                raise YouTubeIntegrityError("A non-final YouTube chunk violated the 256 KiB boundary.")
            previous_offset = offset
            reconciled_after_error = False
            try:
                result = await self._gateway.upload_chunk(
                    access_token=access_token,
                    session_reference=session,
                    offset=offset,
                    total_bytes=total_bytes,
                    data=data,
                )
            except SessionExpiredError as error:
                session, recreations = await self._recreate_session(
                    command, metadata, total_bytes, access_token, recreations, error
                )
                offset = 0
                retries = 0
                continue
            except RetryableYouTubeError:
                retries = await self._consume_retry(retries)
                try:
                    result, retries = await self._query_with_retry(access_token, session, total_bytes, retries)
                    reconciled_after_error = True
                except SessionExpiredError as error:
                    session, recreations = await self._recreate_session(
                        command, metadata, total_bytes, access_token, recreations, error
                    )
                    offset = 0
                    retries = 0
                    continue
            offset = self._validated_offset(result, total_bytes)
            if result.video_id is None and not reconciled_after_error and offset <= previous_offset:
                raise YouTubeProcessingError("YouTube returned an invalid resumable offset.")
            saved = self._save(command, total_bytes, session, offset, result.video_id)
            if result.video_id is not None:
                return saved
            if offset > previous_offset:
                retries = 0

        raise YouTubeProcessingError("YouTube upload ended without a provider video ID.")

    async def _query_with_retry(
        self,
        access_token: str,
        session: str,
        total_bytes: int,
        retries: int,
    ) -> tuple[ChunkResult, int]:
        while True:
            try:
                return (
                    await self._gateway.query_session(
                        access_token=access_token,
                        session_reference=session,
                        total_bytes=total_bytes,
                    ),
                    retries,
                )
            except RetryableYouTubeError:
                retries = await self._consume_retry(retries)

    async def _consume_retry(self, retries: int) -> int:
        if retries >= self._max_retries:
            raise RetryableYouTubeError("YouTube retry bound was exhausted.")
        retries += 1
        await self._retry_delay(self._backoff(retries))
        return retries

    async def _recreate_session(
        self,
        command: PublicationCommand,
        metadata: YouTubeMetadata,
        total_bytes: int,
        access_token: str,
        recreations: int,
        error: SessionExpiredError,
    ) -> tuple[str, int]:
        if recreations >= self._max_session_recreations:
            raise error
        await self._retry_delay(self._backoff(recreations + 1))
        session = await self._new_session(command, metadata, total_bytes, access_token)
        self._save(command, total_bytes, session, 0, None)
        return session, recreations + 1

    async def _new_session(
        self,
        command: PublicationCommand,
        metadata: YouTubeMetadata,
        total_bytes: int,
        access_token: str,
    ) -> str:
        value = metadata.wire_value()
        value.pop("destination", None)
        return await self._gateway.create_session(
            access_token=access_token,
            byte_count=total_bytes,
            correlation=command.provider_correlation,
            metadata={"snippet": value, "status": {"privacyStatus": "private"}},
        )

    def _save(
        self,
        command: PublicationCommand,
        total_bytes: int,
        session_reference: str | None,
        next_offset: int,
        video_id: str | None,
    ) -> YouTubeCheckpoint:
        checkpoint = self._bound_checkpoint(
            command,
            total_bytes,
            session_reference,
            next_offset,
            video_id,
        )
        self._checkpoints.save(command.publication_id, checkpoint)
        return checkpoint

    @staticmethod
    def _bound_checkpoint(
        command: PublicationCommand,
        total_bytes: int,
        session_reference: str | None,
        next_offset: int,
        video_id: str | None,
    ) -> YouTubeCheckpoint:
        return YouTubeCheckpoint(
            provider_correlation=command.provider_correlation,
            render_sha256=command.render_sha256,
            metadata_sha256=command.metadata_sha256,
            byte_count=total_bytes,
            session_reference=session_reference,
            next_offset=next_offset,
            video_id=video_id,
        )

    @staticmethod
    def _validate_checkpoint(
        command: PublicationCommand,
        checkpoint: YouTubeCheckpoint,
        total_bytes: int,
    ) -> None:
        if (
            checkpoint.provider_correlation != command.provider_correlation
            or checkpoint.render_sha256 != command.render_sha256
            or checkpoint.metadata_sha256 != command.metadata_sha256
            or checkpoint.byte_count != total_bytes
        ):
            raise YouTubeCheckpointError("YouTube checkpoint bindings do not match the publication command.")
        if checkpoint.next_offset < 0 or checkpoint.next_offset > total_bytes:
            raise YouTubeCheckpointError("YouTube checkpoint offset is invalid.")
        if checkpoint.video_id is not None and checkpoint.next_offset != total_bytes:
            raise YouTubeCheckpointError("Completed YouTube checkpoint offset is invalid.")
        if checkpoint.video_id is None and checkpoint.session_reference is None and checkpoint.next_offset != 0:
            raise YouTubeCheckpointError("YouTube checkpoint resumable state is invalid.")

    @staticmethod
    def _verify_media(media: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_count = 0
        while chunk := media.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
        media.seek(0)
        return digest.hexdigest(), byte_count

    @staticmethod
    def _validated_offset(result: ChunkResult, total_bytes: int) -> int:
        if result.next_offset < 0 or result.next_offset > total_bytes:
            raise YouTubeProcessingError("YouTube returned an invalid resumable offset.")
        if result.video_id is not None and result.next_offset != total_bytes:
            raise YouTubeProcessingError("YouTube completed an upload at an invalid offset.")
        return result.next_offset

    def _backoff(self, attempt: int) -> float:
        return min(self._initial_backoff_seconds * (2 ** (attempt - 1)), self._max_backoff_seconds)

    async def _wait_until_processed(self, access_token: str, video_id: str) -> YouTubePublishResult:
        if not _VIDEO_ID.fullmatch(video_id):
            raise YouTubeProcessingError("YouTube returned an invalid provider video ID.")
        for poll in range(self._max_processing_polls):
            status = await self._gateway.processing_status(access_token=access_token, video_id=video_id)
            if status == "succeeded":
                return YouTubePublishResult(video_id, f"https://studio.youtube.com/video/{video_id}/edit")
            if status in {"failed", "rejected", "terminated"}:
                raise YouTubeProcessingError("YouTube could not process the private upload.")
            if status != "processing":
                raise YouTubeProcessingError("YouTube returned an unknown processing state.")
            if poll + 1 < self._max_processing_polls:
                await self._retry_delay(self._backoff(poll + 1))
        raise YouTubeProcessingError("YouTube processing did not finish within the polling bound.")


__all__ = ["YouTubeAuthError"]
