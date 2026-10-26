"""Approval-gated Facebook Page video publishing."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from localizer_agent.publishing.base import ApprovalBoundPublicationAdapter, PublicationApprovalClient
from localizer_agent.publishing.facebook_auth import FacebookAuthError, FacebookPageCredentials
from localizer_agent.publishing.models import Destination, FacebookMetadata, PublicationCommand


_GRAPH_VERSION = re.compile(r"v[1-9][0-9]*\.[0-9]+")
_PAGE_ID = re.compile(r"[1-9][0-9]{0,31}")
_PROVIDER_CORRELATION = re.compile(r"localizer-[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,256}")
_VIDEO_ID = re.compile(r"[1-9][0-9]{0,31}")


class FacebookIntegrityError(RuntimeError):
    """The opened media does not match the approved render digest."""


class ConcurrentFacebookPublishError(RuntimeError):
    """Another process or task owns this publication's Facebook state machine."""


class FacebookProcessingError(RuntimeError):
    """Facebook returned an invalid or terminal publishing state."""


class RetryableFacebookError(RuntimeError):
    """A transient Facebook failure that may be retried within configured bounds."""


class FacebookRateLimitError(RetryableFacebookError):
    """Facebook rejected a request because a rate limit was reached."""


class FacebookRejectedError(RuntimeError):
    """Facebook definitively rejected a request (non-rate-limit, non-auth 4xx); nothing was applied."""


class FacebookCheckpointError(RuntimeError):
    """A Facebook checkpoint is malformed or bound to different inputs."""


class AmbiguousFacebookCreationError(RuntimeError):
    """A start request may have succeeded without returning its resumable state."""


class AmbiguousFacebookCompletionError(RuntimeError):
    """A finish request may have succeeded without returning confirmation."""


@dataclass(frozen=True)
class FacebookConfig:
    page_id: str
    graph_api_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.page_id, str) or _PAGE_ID.fullmatch(self.page_id) is None:
            raise ValueError("Facebook Page ID is invalid.")
        if not isinstance(self.graph_api_version, str) or _GRAPH_VERSION.fullmatch(self.graph_api_version) is None:
            raise ValueError("Facebook Graph API version is invalid.")


@dataclass(frozen=True)
class FacebookUploadSession:
    upload_session_id: str
    video_id: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class FacebookTransferResult:
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class FacebookCheckpoint:
    provider_correlation: str
    render_sha256: str
    metadata_sha256: str
    byte_count: int
    page_id: str
    graph_api_version: str
    creation_pending: bool
    upload_session_id: str | None
    video_id: str | None
    next_offset: int
    completion_pending: bool
    completed: bool


def _validate_checkpoint_shape(checkpoint: FacebookCheckpoint) -> None:
    if not isinstance(checkpoint.provider_correlation, str) or _PROVIDER_CORRELATION.fullmatch(
        checkpoint.provider_correlation
    ) is None:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if not isinstance(checkpoint.render_sha256, str) or _SHA256.fullmatch(checkpoint.render_sha256) is None:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if not isinstance(checkpoint.metadata_sha256, str) or _SHA256.fullmatch(checkpoint.metadata_sha256) is None:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if type(checkpoint.byte_count) is not int or checkpoint.byte_count <= 0:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if not isinstance(checkpoint.page_id, str) or _PAGE_ID.fullmatch(checkpoint.page_id) is None:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if not isinstance(checkpoint.graph_api_version, str) or _GRAPH_VERSION.fullmatch(checkpoint.graph_api_version) is None:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if any(type(value) is not bool for value in (checkpoint.creation_pending, checkpoint.completion_pending, checkpoint.completed)):
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if type(checkpoint.next_offset) is not int or not 0 <= checkpoint.next_offset <= checkpoint.byte_count:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if checkpoint.creation_pending:
        if (
            checkpoint.upload_session_id is not None
            or checkpoint.video_id is not None
            or checkpoint.next_offset != 0
            or checkpoint.completion_pending
            or checkpoint.completed
        ):
            raise FacebookCheckpointError("Facebook checkpoint is malformed.")
        return
    if checkpoint.upload_session_id is None and checkpoint.video_id is None:
        if checkpoint.next_offset != 0 or checkpoint.completion_pending or checkpoint.completed:
            raise FacebookCheckpointError("Facebook checkpoint is malformed.")
        return
    if (
        not isinstance(checkpoint.upload_session_id, str)
        or _SESSION_ID.fullmatch(checkpoint.upload_session_id) is None
        or not isinstance(checkpoint.video_id, str)
        or _VIDEO_ID.fullmatch(checkpoint.video_id) is None
    ):
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if checkpoint.completion_pending and checkpoint.completed:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")
    if (checkpoint.completion_pending or checkpoint.completed) and checkpoint.next_offset != checkpoint.byte_count:
        raise FacebookCheckpointError("Facebook checkpoint is malformed.")


@dataclass(frozen=True)
class FacebookPublishResult:
    video_id: str
    video_url: str


@dataclass(frozen=True)
class FacebookHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class FacebookHttpTransport(Protocol):
    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> FacebookHttpResponse: ...


class UrllibFacebookTransport:
    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> FacebookHttpResponse:
        def request() -> FacebookHttpResponse:
            try:
                with urlopen(  # noqa: S310 -- origins and path components are validated and fixed below
                    Request(url, data=body, headers=headers, method=method),
                    timeout=timeout_seconds,
                ) as response:
                    return FacebookHttpResponse(response.status, dict(response.headers.items()), response.read())
            except HTTPError as error:
                return FacebookHttpResponse(error.code, dict(error.headers.items()), error.read())

        try:
            return await asyncio.to_thread(request)
        except (OSError, URLError):
            raise RetryableFacebookError("Facebook request failed before receiving a response.") from None


class FacebookGateway(Protocol):
    async def start_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        byte_count: int,
        correlation: str,
    ) -> FacebookUploadSession: ...

    async def query_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        video_id: str,
    ) -> FacebookTransferResult: ...

    async def transfer_chunk(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        start_offset: int,
        data: bytes,
    ) -> FacebookTransferResult: ...

    async def finish_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        metadata: dict[str, object],
    ) -> None: ...

    async def processing_status(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        video_id: str,
    ) -> str: ...


class FacebookHttpGateway:
    """Meta Graph resumable video API implementation with sanitized failures."""

    def __init__(
        self,
        *,
        transport: FacebookHttpTransport | None = None,
        request_timeout_seconds: int = 30,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("Facebook request timeout must be positive.")
        self._transport = transport or UrllibFacebookTransport()
        self._request_timeout_seconds = request_timeout_seconds

    async def start_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        byte_count: int,
        correlation: str,
    ) -> FacebookUploadSession:
        if byte_count <= 0 or _PROVIDER_CORRELATION.fullmatch(correlation) is None:
            raise ValueError("Facebook upload start bindings are invalid.")
        response = await self._send(
            "POST",
            self._video_collection_url(config),
            self._form_headers(credentials),
            urlencode({"file_size": str(byte_count), "upload_phase": "start"}).encode("ascii"),
        )
        self._require_status(response, "upload start", {200})
        payload = self._json_object(response, "upload start")
        upload_session_id = payload.get("upload_session_id")
        video_id = payload.get("video_id")
        if (
            not isinstance(upload_session_id, str)
            or _SESSION_ID.fullmatch(upload_session_id) is None
            or not isinstance(video_id, str)
            or _VIDEO_ID.fullmatch(video_id) is None
        ):
            raise FacebookProcessingError("Facebook upload start response was invalid.")
        result = self._offsets(payload, "upload start")
        return FacebookUploadSession(upload_session_id, video_id, result.start_offset, result.end_offset)

    async def transfer_chunk(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        start_offset: int,
        data: bytes,
    ) -> FacebookTransferResult:
        self._validate_session(upload_session_id)
        if type(start_offset) is not int or start_offset < 0 or not data:
            raise ValueError("Facebook upload chunk bounds are invalid.")
        boundary = f"----localizer{secrets.token_hex(16)}"
        body = self._multipart_body(
            boundary,
            {
                "start_offset": str(start_offset),
                "upload_phase": "transfer",
                "upload_session_id": upload_session_id,
            },
            data,
        )
        response = await self._send(
            "POST",
            self._video_collection_url(config),
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {credentials.page_access_token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            body,
        )
        self._require_status(response, "upload transfer", {200})
        return self._offsets(self._json_object(response, "upload transfer"), "upload transfer")

    async def query_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        video_id: str,
    ) -> FacebookTransferResult:
        self._validate_session(upload_session_id)
        self._validate_video(video_id)
        query = urlencode({"upload_phase": "transfer", "upload_session_id": upload_session_id})
        response = await self._send(
            "GET",
            f"{self._video_collection_url(config)}?{query}",
            self._auth_headers(credentials),
            None,
        )
        self._require_status(response, "upload offset query", {200})
        return self._offsets(self._json_object(response, "upload offset query"), "upload offset query")

    async def finish_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        metadata: dict[str, object],
    ) -> None:
        self._validate_session(upload_session_id)
        if set(metadata) != {"description", "published"} or not isinstance(metadata["description"], str):
            raise ValueError("Facebook finish metadata is invalid.")
        if metadata["published"] is not True:
            raise ValueError("Facebook approved uploads must publish at completion.")
        body = urlencode(
            {
                "description": metadata["description"],
                "published": "true",
                "upload_phase": "finish",
                "upload_session_id": upload_session_id,
            }
        ).encode("utf-8")
        response = await self._send("POST", self._video_collection_url(config), self._form_headers(credentials), body)
        self._require_status(response, "upload finish", {200})
        payload = self._json_object(response, "upload finish")
        if payload.get("success") is not True:
            raise FacebookProcessingError("Facebook upload finish response was invalid.")

    async def processing_status(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        video_id: str,
    ) -> str:
        self._validate_video(video_id)
        response = await self._send(
            "GET",
            f"https://graph.facebook.com/{config.graph_api_version}/{video_id}?fields=status",
            self._auth_headers(credentials),
            None,
        )
        self._require_status(response, "processing status", {200})
        payload = self._json_object(response, "processing status")
        status = payload.get("status")
        video_status = status.get("video_status") if isinstance(status, dict) else None
        if video_status == "ready":
            return "succeeded"
        if video_status in {"processing", "pending"}:
            return "processing"
        if video_status == "uploading":
            return "uploading"
        if video_status in {"error", "failed", "expired"}:
            return "failed"
        raise FacebookProcessingError("Facebook processing status response was invalid.")

    async def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> FacebookHttpResponse:
        try:
            return await self._transport.send(method, url, headers, body, self._request_timeout_seconds)
        except (FacebookAuthError, FacebookRejectedError, RetryableFacebookError, FacebookProcessingError):
            raise
        except Exception:
            raise RetryableFacebookError("Facebook request failed before receiving a response.") from None

    @staticmethod
    def _video_collection_url(config: FacebookConfig) -> str:
        return f"https://graph-video.facebook.com/{config.graph_api_version}/{config.page_id}/videos"

    @staticmethod
    def _auth_headers(credentials: FacebookPageCredentials) -> dict[str, str]:
        return {"Accept": "application/json", "Authorization": f"Bearer {credentials.page_access_token}"}

    @classmethod
    def _form_headers(cls, credentials: FacebookPageCredentials) -> dict[str, str]:
        return cls._auth_headers(credentials) | {"Content-Type": "application/x-www-form-urlencoded"}

    @staticmethod
    def _json_object(response: FacebookHttpResponse, operation: str) -> dict[str, object]:
        try:
            payload = json.loads(response.body)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise FacebookProcessingError(f"Facebook {operation} response was invalid.") from None
        if not isinstance(payload, dict):
            raise FacebookProcessingError(f"Facebook {operation} response was invalid.")
        return payload

    @classmethod
    def _offsets(cls, payload: dict[str, object], operation: str) -> FacebookTransferResult:
        start = cls._decimal_offset(payload.get("start_offset"))
        end = cls._decimal_offset(payload.get("end_offset"))
        if start is None or end is None or end < start:
            raise FacebookProcessingError(f"Facebook {operation} response contained invalid offsets.")
        return FacebookTransferResult(start, end)

    @staticmethod
    def _decimal_offset(value: object) -> int | None:
        if type(value) is int and value >= 0:
            return value
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return int(value)
        return None

    @staticmethod
    def _validate_session(upload_session_id: str) -> None:
        if not isinstance(upload_session_id, str) or _SESSION_ID.fullmatch(upload_session_id) is None:
            raise ValueError("Facebook upload session ID is invalid.")

    @staticmethod
    def _validate_video(video_id: str) -> None:
        if not isinstance(video_id, str) or _VIDEO_ID.fullmatch(video_id) is None:
            raise FacebookProcessingError("Facebook returned an invalid provider video ID.")

    @staticmethod
    def _multipart_body(boundary: str, fields: dict[str, str], data: bytes) -> bytes:
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.extend(
                [
                    f"--{boundary}\r\n".encode("ascii"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                    value.encode("ascii"),
                    b"\r\n",
                ]
            )
        parts.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                b'Content-Disposition: form-data; name="video_file_chunk"; filename="chunk.mp4"\r\n',
                b"Content-Type: application/octet-stream\r\n\r\n",
                data,
                b"\r\n",
                f"--{boundary}--\r\n".encode("ascii"),
            ]
        )
        return b"".join(parts)

    @staticmethod
    def _error_code(response: FacebookHttpResponse) -> int | None:
        try:
            payload = json.loads(response.body)
            error = payload.get("error") if isinstance(payload, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            return code if type(code) is int else None
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    @classmethod
    def _require_status(cls, response: FacebookHttpResponse, operation: str, allowed: set[int]) -> None:
        if response.status_code in allowed:
            return
        code = cls._error_code(response)
        if response.status_code == 401 or code in {102, 190}:
            raise FacebookAuthError(f"Facebook {operation} authorization was rejected.")
        if response.status_code == 429 or code in {4, 17, 32, 613}:
            raise FacebookRateLimitError(f"Facebook {operation} was rate limited.")
        if response.status_code in {408, 425} or response.status_code >= 500:
            raise RetryableFacebookError(f"Facebook {operation} failed transiently.")
        if 400 <= response.status_code < 500:
            raise FacebookRejectedError(f"Facebook {operation} failed with HTTP {response.status_code}.")
        raise FacebookProcessingError(f"Facebook {operation} failed with HTTP {response.status_code}.")


class FacebookCheckpointStore(Protocol):
    def reserve(self, publication_id: str) -> AbstractContextManager[None]: ...

    def load(self, publication_id: str) -> FacebookCheckpoint | None: ...

    def save(self, publication_id: str, checkpoint: FacebookCheckpoint) -> None: ...


class MemoryFacebookCheckpointStore:
    def __init__(self) -> None:
        self._values: dict[str, FacebookCheckpoint] = {}
        self._reservation_guard = threading.Lock()
        self._reservations: set[str] = set()

    @contextmanager
    def reserve(self, publication_id: str) -> Iterator[None]:
        with self._reservation_guard:
            if publication_id in self._reservations:
                raise ConcurrentFacebookPublishError(
                    "A Facebook publish for this publication is already in progress."
                )
            self._reservations.add(publication_id)
        try:
            yield
        finally:
            with self._reservation_guard:
                self._reservations.remove(publication_id)

    def load(self, publication_id: str) -> FacebookCheckpoint | None:
        return self._values.get(publication_id)

    def save(self, publication_id: str, checkpoint: FacebookCheckpoint) -> None:
        self._values[publication_id] = checkpoint


def _checkpoint_path(root: Path, publication_id: str) -> Path:
    try:
        parsed = UUID(publication_id)
    except (AttributeError, TypeError, ValueError):
        raise FacebookCheckpointError("Facebook checkpoint publication ID is invalid.") from None
    if str(parsed) != publication_id:
        raise FacebookCheckpointError("Facebook checkpoint publication ID is invalid.")
    return root / f"{publication_id}.json"


class FileFacebookCheckpointStore:
    """Atomic, process-durable JSON checkpoints containing only non-secret Facebook state."""

    _FIELDS = {
        "byte_count",
        "completed",
        "completion_pending",
        "creation_pending",
        "graph_api_version",
        "metadata_sha256",
        "next_offset",
        "page_id",
        "provider_correlation",
        "render_sha256",
        "upload_session_id",
        "video_id",
    }

    def __init__(self, root: Path) -> None:
        self._root = root
        try:
            self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            raise FacebookCheckpointError("Facebook checkpoint directory is unavailable.") from None
        if not self._root.is_dir() or self._root.is_symlink():
            raise FacebookCheckpointError("Facebook checkpoint directory is unavailable.")

    @contextmanager
    def reserve(self, publication_id: str) -> Iterator[None]:
        _checkpoint_path(self._root, publication_id)
        lock_path = self._root / f".{publication_id}.lock"
        descriptor: int | None = None
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ConcurrentFacebookPublishError(
                    "A Facebook publish for this publication is already in progress."
                ) from None
            yield
        except ConcurrentFacebookPublishError:
            raise
        except OSError:
            raise FacebookCheckpointError("Facebook checkpoint reservation is unavailable.") from None
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def load(self, publication_id: str) -> FacebookCheckpoint | None:
        path = _checkpoint_path(self._root, publication_id)
        if not path.exists():
            if path.is_symlink():
                raise FacebookCheckpointError("Facebook durable checkpoint is malformed.")
            return None
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024:
                raise ValueError("unsafe checkpoint")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != self._FIELDS:
                raise ValueError("invalid fields")
            checkpoint = FacebookCheckpoint(**payload)
            _validate_checkpoint_shape(checkpoint)
            return checkpoint
        except FacebookCheckpointError:
            raise
        except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise FacebookCheckpointError("Facebook durable checkpoint is malformed.") from None

    def save(self, publication_id: str, checkpoint: FacebookCheckpoint) -> None:
        path = _checkpoint_path(self._root, publication_id)
        _validate_checkpoint_shape(checkpoint)
        serialized = json.dumps(vars(checkpoint), separators=(",", ":"), sort_keys=True)
        temporary_path: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._root,
                prefix=f".{publication_id}.",
                suffix=".tmp",
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(serialized)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
            directory_descriptor = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            raise FacebookCheckpointError("Facebook durable checkpoint could not be saved.") from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class FacebookAdapter(ApprovalBoundPublicationAdapter):
    destination = Destination.FACEBOOK

    def __init__(
        self,
        approval_client: PublicationApprovalClient,
        gateway: FacebookGateway,
        checkpoints: FacebookCheckpointStore,
        credentials: Callable[[], FacebookPageCredentials],
        config: FacebookConfig,
        *,
        max_chunk_size: int = 4 * 1024 * 1024,
        max_retries: int = 4,
        max_processing_retries: int = 4,
        max_processing_polls: int = 20,
        retry_delay: Callable[[float], Awaitable[None]] = asyncio.sleep,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 32.0,
    ) -> None:
        super().__init__(approval_client)
        if max_chunk_size <= 0 or max_retries < 0 or max_processing_retries < 0 or max_processing_polls <= 0:
            raise ValueError("Facebook publishing bounds are invalid.")
        if initial_backoff_seconds <= 0 or max_backoff_seconds < initial_backoff_seconds:
            raise ValueError("Facebook retry delays are invalid.")
        self._gateway = gateway
        self._checkpoints = checkpoints
        self._credentials = credentials
        self._config = config
        self._max_chunk_size = max_chunk_size
        self._max_retries = max_retries
        self._max_processing_retries = max_processing_retries
        self._max_processing_polls = max_processing_polls
        self._retry_delay = retry_delay
        self._initial_backoff_seconds = initial_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds

    async def publish_approved(self, command: PublicationCommand, media_path: Path) -> FacebookPublishResult:
        with self._checkpoints.reserve(command.publication_id):
            return await self._publish_exclusive(command, media_path)

    async def _publish_exclusive(self, command: PublicationCommand, media_path: Path) -> FacebookPublishResult:
        metadata = command.metadata.for_destination(self.destination)
        if not isinstance(metadata, FacebookMetadata):
            raise ValueError("Facebook command requires Facebook metadata.")
        with media_path.open("rb") as media:
            actual_sha256, total_bytes = self._verify_media(media)
            if total_bytes <= 0 or actual_sha256 != command.render_sha256:
                raise FacebookIntegrityError("Opened media bytes do not match the approved render SHA-256.")

            checkpoint = self._checkpoints.load(command.publication_id)
            if checkpoint is not None:
                self._validate_checkpoint(command, checkpoint, total_bytes)
                if checkpoint.creation_pending:
                    raise AmbiguousFacebookCreationError(
                        "Facebook upload creation is ambiguous; manual recovery is required before retrying."
                    )
            credentials = self._credentials()
            provider_end_offset = total_bytes
            if checkpoint is None or checkpoint.upload_session_id is None:
                session = await self._start_with_retry(command, total_bytes, credentials)
                checkpoint = self._save(
                    command,
                    total_bytes,
                    False,
                    session.upload_session_id,
                    session.video_id,
                    session.start_offset,
                    False,
                    False,
                )
                provider_end_offset = session.end_offset
            elif checkpoint.completion_pending:
                completion_status = await self._gateway.processing_status(
                    credentials=credentials,
                    config=self._config,
                    video_id=checkpoint.video_id or "",
                )
                if completion_status == "succeeded":
                    checkpoint = self._save(
                        command,
                        total_bytes,
                        False,
                        checkpoint.upload_session_id,
                        checkpoint.video_id,
                        checkpoint.next_offset,
                        False,
                        True,
                    )
                    return FacebookPublishResult(
                        checkpoint.video_id or "",
                        f"https://www.facebook.com/{checkpoint.video_id}",
                    )
                if completion_status == "processing":
                    checkpoint = self._save(
                        command,
                        total_bytes,
                        False,
                        checkpoint.upload_session_id,
                        checkpoint.video_id,
                        checkpoint.next_offset,
                        False,
                        True,
                    )
                elif completion_status == "uploading":
                    checkpoint = self._save(
                        command,
                        total_bytes,
                        False,
                        checkpoint.upload_session_id,
                        checkpoint.video_id,
                        checkpoint.next_offset,
                        False,
                        False,
                    )
                elif completion_status == "failed":
                    raise FacebookProcessingError("Facebook could not process the published video.")
                else:
                    raise FacebookProcessingError("Facebook returned an unknown processing state.")
            elif not checkpoint.completed:
                reconciled, _ = await self._query_with_retry(
                    credentials,
                    checkpoint.upload_session_id or "",
                    checkpoint.video_id or "",
                    0,
                )
                if reconciled.start_offset < 0 or reconciled.start_offset > total_bytes:
                    raise FacebookProcessingError("Facebook returned an invalid resumable offset.")
                provider_end_offset = reconciled.end_offset
                checkpoint = self._save(
                    command,
                    total_bytes,
                    False,
                    checkpoint.upload_session_id,
                    checkpoint.video_id,
                    reconciled.start_offset,
                    False,
                    False,
                )

            retries = 0
            while checkpoint.next_offset < total_bytes:
                media.seek(checkpoint.next_offset)
                provider_chunk_size = provider_end_offset - checkpoint.next_offset
                if provider_chunk_size <= 0:
                    raise FacebookProcessingError("Facebook returned an invalid resumable range.")
                requested = min(self._max_chunk_size, total_bytes - checkpoint.next_offset, provider_chunk_size)
                data = media.read(requested)
                if not data:
                    raise FacebookIntegrityError("Verified media ended before the approved byte count.")
                try:
                    result = await self._gateway.transfer_chunk(
                        credentials=credentials,
                        config=self._config,
                        upload_session_id=checkpoint.upload_session_id or "",
                        start_offset=checkpoint.next_offset,
                        data=data,
                    )
                except RetryableFacebookError:
                    # Rate limits, timeouts and 5xx leave the accepted offset unknown; the retry is consumed here.
                    retries = await self._consume_retry(retries)
                    result, retries = await self._query_with_retry(
                        credentials,
                        checkpoint.upload_session_id or "",
                        checkpoint.video_id or "",
                        retries,
                    )
                    reconciled = True
                else:
                    reconciled = False
                if result.start_offset <= checkpoint.next_offset or result.start_offset > total_bytes:
                    if result.start_offset == checkpoint.next_offset:
                        if not reconciled:
                            if retries >= self._max_retries:
                                raise FacebookProcessingError(
                                    "Facebook did not advance the resumable offset within the retry bound."
                                )
                            retries = await self._consume_retry(retries)
                        continue
                    raise FacebookProcessingError("Facebook returned an invalid resumable offset.")
                if result.end_offset < result.start_offset:
                    raise FacebookProcessingError("Facebook returned an invalid resumable range.")
                provider_end_offset = result.end_offset
                retries = 0
                checkpoint = self._save(
                    command,
                    total_bytes,
                    False,
                    checkpoint.upload_session_id,
                    checkpoint.video_id,
                    result.start_offset,
                    False,
                    False,
                )

            if not checkpoint.completed:
                checkpoint = self._save(
                    command,
                    total_bytes,
                    False,
                    checkpoint.upload_session_id,
                    checkpoint.video_id,
                    checkpoint.next_offset,
                    True,
                    False,
                )
                finish_retries = 0
                while True:
                    try:
                        await self._gateway.finish_upload(
                            credentials=credentials,
                            config=self._config,
                            upload_session_id=checkpoint.upload_session_id or "",
                            metadata=self._metadata_value(metadata),
                        )
                        break
                    except FacebookRateLimitError:
                        finish_retries = await self._consume_retry(finish_retries)
                    except FacebookAuthError:
                        raise FacebookAuthError("Facebook finish authorization was rejected.") from None
                    except FacebookRejectedError:
                        # A definitive 4xx means the finish was not applied; clear the ambiguity marker.
                        self._save(
                            command,
                            total_bytes,
                            False,
                            checkpoint.upload_session_id,
                            checkpoint.video_id,
                            checkpoint.next_offset,
                            False,
                            False,
                        )
                        raise
                    except Exception:
                        raise AmbiguousFacebookCompletionError(
                            "Facebook upload completion is ambiguous; reconcile provider status before retrying."
                        ) from None
                checkpoint = self._save(
                    command,
                    total_bytes,
                    False,
                    checkpoint.upload_session_id,
                    checkpoint.video_id,
                    checkpoint.next_offset,
                    False,
                    True,
                )

        return await self._wait_until_processed(credentials, checkpoint.video_id or "")

    def _save(
        self,
        command: PublicationCommand,
        total_bytes: int,
        creation_pending: bool,
        upload_session_id: str | None,
        video_id: str | None,
        next_offset: int,
        completion_pending: bool,
        completed: bool,
    ) -> FacebookCheckpoint:
        checkpoint = FacebookCheckpoint(
            provider_correlation=command.provider_correlation,
            render_sha256=command.render_sha256,
            metadata_sha256=command.metadata_sha256,
            byte_count=total_bytes,
            page_id=self._config.page_id,
            graph_api_version=self._config.graph_api_version,
            creation_pending=creation_pending,
            upload_session_id=upload_session_id,
            video_id=video_id,
            next_offset=next_offset,
            completion_pending=completion_pending,
            completed=completed,
        )
        self._checkpoints.save(command.publication_id, checkpoint)
        return checkpoint

    def _validate_checkpoint(
        self,
        command: PublicationCommand,
        checkpoint: FacebookCheckpoint,
        total_bytes: int,
    ) -> None:
        if (
            checkpoint.provider_correlation != command.provider_correlation
            or checkpoint.render_sha256 != command.render_sha256
            or checkpoint.metadata_sha256 != command.metadata_sha256
            or checkpoint.byte_count != total_bytes
            or checkpoint.page_id != self._config.page_id
            or checkpoint.graph_api_version != self._config.graph_api_version
        ):
            raise FacebookCheckpointError("Facebook checkpoint bindings do not match the publication command.")
        _validate_checkpoint_shape(checkpoint)

    @staticmethod
    def _metadata_value(metadata: FacebookMetadata) -> dict[str, object]:
        description = metadata.caption
        if metadata.hashtags:
            description = f"{description}\n{' '.join(metadata.hashtags)}"
        return {"description": description, "published": True}

    @staticmethod
    def _verify_media(media: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_count = 0
        while chunk := media.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
        media.seek(0)
        return digest.hexdigest(), byte_count

    def _backoff(self, attempt: int) -> float:
        return min(self._initial_backoff_seconds * (2 ** (attempt - 1)), self._max_backoff_seconds)

    async def _consume_retry(self, retries: int) -> int:
        if retries >= self._max_retries:
            raise FacebookRateLimitError("Facebook retry bound was exhausted.")
        retries += 1
        await self._retry_delay(self._backoff(retries))
        return retries

    async def _query_with_retry(
        self,
        credentials: FacebookPageCredentials,
        upload_session_id: str,
        video_id: str,
        retries: int,
    ) -> tuple[FacebookTransferResult, int]:
        while True:
            try:
                return (
                    await self._gateway.query_upload(
                        credentials=credentials,
                        config=self._config,
                        upload_session_id=upload_session_id,
                        video_id=video_id,
                    ),
                    retries,
                )
            except RetryableFacebookError:
                retries = await self._consume_retry(retries)

    async def _start_with_retry(
        self,
        command: PublicationCommand,
        total_bytes: int,
        credentials: FacebookPageCredentials,
    ) -> FacebookUploadSession:
        retries = 0
        while True:
            self._save(command, total_bytes, True, None, None, 0, False, False)
            try:
                return await self._gateway.start_upload(
                    credentials=credentials,
                    config=self._config,
                    byte_count=total_bytes,
                    correlation=command.provider_correlation,
                )
            except FacebookAuthError:
                self._save(command, total_bytes, False, None, None, 0, False, False)
                raise FacebookAuthError("Facebook upload start authorization was rejected.") from None
            except FacebookRateLimitError:
                # A rate-limit rejection is definitive: no upload session was created.
                self._save(command, total_bytes, False, None, None, 0, False, False)
                retries = await self._consume_retry(retries)
            except FacebookRejectedError:
                self._save(command, total_bytes, False, None, None, 0, False, False)
                raise
            except Exception:
                raise AmbiguousFacebookCreationError(
                    "Facebook upload creation is ambiguous; manual recovery is required before retrying."
                ) from None

    async def _wait_until_processed(
        self,
        credentials: FacebookPageCredentials,
        video_id: str,
    ) -> FacebookPublishResult:
        poll = 0
        processing_retries = 0
        while poll < self._max_processing_polls:
            try:
                status = await self._gateway.processing_status(
                    credentials=credentials,
                    config=self._config,
                    video_id=video_id,
                )
            except FacebookRateLimitError:
                if processing_retries >= self._max_processing_retries:
                    raise FacebookRateLimitError(
                        "Facebook processing status retry bound was exhausted."
                    ) from None
                processing_retries += 1
                await self._retry_delay(self._backoff(processing_retries))
                continue
            poll += 1
            if status == "succeeded":
                return FacebookPublishResult(video_id, f"https://www.facebook.com/{video_id}")
            if status == "failed":
                raise FacebookProcessingError("Facebook could not process the published video.")
            if status != "processing":
                raise FacebookProcessingError("Facebook returned an unknown processing state.")
            if poll < self._max_processing_polls:
                await self._retry_delay(self._backoff(poll))
        raise FacebookProcessingError("Facebook processing did not finish within the polling bound.")


__all__ = [
    "AmbiguousFacebookCompletionError",
    "AmbiguousFacebookCreationError",
    "ConcurrentFacebookPublishError",
    "FacebookAdapter",
    "FacebookCheckpoint",
    "FacebookCheckpointError",
    "FacebookConfig",
    "FacebookGateway",
    "FacebookHttpGateway",
    "FacebookHttpResponse",
    "FacebookHttpTransport",
    "FacebookIntegrityError",
    "FacebookProcessingError",
    "FacebookPublishResult",
    "FacebookRateLimitError",
    "FacebookRejectedError",
    "RetryableFacebookError",
    "FacebookTransferResult",
    "FacebookUploadSession",
    "FileFacebookCheckpointStore",
    "MemoryFacebookCheckpointStore",
]
