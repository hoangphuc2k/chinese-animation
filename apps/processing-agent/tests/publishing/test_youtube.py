from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from localizer_agent.publishing.models import Destination, PublicationApprovalSnapshot, PublicationCommand
from localizer_agent.publishing.youtube import (
    ChunkResult,
    MemoryCheckpointStore,
    RetryableYouTubeError,
    SessionExpiredError,
    YouTubeAdapter,
    YouTubeAuthError,
    YouTubeCheckpoint,
    YouTubeCheckpointError,
    YouTubeHttpGateway,
    YouTubeHttpResponse,
    YouTubeIntegrityError,
    YouTubeProcessingError,
    YouTubeQuotaError,
)
from localizer_agent.publishing.youtube_auth import YouTubeCredentials, load_youtube_credentials
from tests.publishing.test_metadata import valid_command


CHUNK_SIZE = 256 * 1024
MEDIA_BYTES = b"a" * CHUNK_SIZE + b"tail"


class Vault:
    def __init__(self, value: str | Exception) -> None:
        self.value = value

    def read_secret(self, reference: str) -> str:
        assert reference == "vault://youtube"
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class Approvals:
    async def get_publication_approval(self, command: PublicationCommand) -> PublicationApprovalSnapshot:
        return PublicationApprovalSnapshot.from_command(command, approved=True)


class Gateway:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.upload_results: list[object] = []
        self.query_results: list[object] = []
        self.processing: list[str] = ["succeeded"]
        self.on_exchange: Callable[[], None] | None = None

    async def exchange_refresh_token(self, credentials: YouTubeCredentials) -> str:
        self.events.append("auth")
        if self.on_exchange is not None:
            self.on_exchange()
        return "access-token"

    async def create_session(self, *, access_token: str, byte_count: int, correlation: str, metadata: dict[str, object]) -> str:
        self.events.append(("create", metadata, byte_count, correlation))
        count = len([event for event in self.events if isinstance(event, tuple) and event[0] == "create"])
        return f"session-{count}"

    async def upload_chunk(
        self,
        *,
        access_token: str,
        session_reference: str,
        offset: int,
        total_bytes: int,
        data: bytes,
    ) -> ChunkResult:
        self.events.append(("chunk", offset, data, session_reference))
        result = (
            self.upload_results.pop(0)
            if self.upload_results
            else ChunkResult(
                offset + len(data),
                "Video_Id-1" if offset + len(data) == total_bytes else None,
            )
        )
        if isinstance(result, Exception):
            raise result
        return result

    async def query_session(self, *, access_token: str, session_reference: str, total_bytes: int) -> ChunkResult:
        self.events.append(("query", session_reference))
        result = self.query_results.pop(0) if self.query_results else ChunkResult(0, None)
        if isinstance(result, Exception):
            raise result
        return result

    async def processing_status(self, *, access_token: str, video_id: str) -> str:
        self.events.append(("processing", video_id))
        return self.processing.pop(0)


class Delays:
    def __init__(self) -> None:
        self.values: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)


class Transport:
    def __init__(self, *responses: YouTubeHttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, str], bytes | None, int]] = []

    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> YouTubeHttpResponse:
        self.requests.append((method, url, headers, body, timeout_seconds))
        return self.responses.pop(0)


def credentials_loader():
    return lambda: load_youtube_credentials(
        Vault(json.dumps({"clientId": "client", "clientSecret": "secret", "refreshToken": "refresh"})),
        "vault://youtube",
    )


def write_media(tmp_path: Path, payload: bytes = MEDIA_BYTES) -> tuple[Path, PublicationCommand]:
    media = tmp_path / "render.mp4"
    media.write_bytes(payload)
    command = valid_command(
        destination=Destination.YOUTUBE,
        render_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return media, command


def checkpoint_for(
    command: PublicationCommand,
    expected_byte_count: int,
    *,
    session_reference: str | None,
    next_offset: int,
    video_id: str | None,
    **changes: object,
) -> YouTubeCheckpoint:
    values: dict[str, object] = {
        "byte_count": expected_byte_count,
        "metadata_sha256": command.metadata_sha256,
        "next_offset": next_offset,
        "provider_correlation": command.provider_correlation,
        "render_sha256": command.render_sha256,
        "session_reference": session_reference,
        "video_id": video_id,
    }
    values.update(changes)
    return YouTubeCheckpoint(**values)  # type: ignore[arg-type]


def run_upload(
    tmp_path: Path,
    gateway: Gateway,
    store: MemoryCheckpointStore | None = None,
    *,
    payload: bytes = MEDIA_BYTES,
    delays: Delays | None = None,
    max_retries: int = 2,
    max_session_recreations: int = 2,
    max_processing_polls: int = 3,
):
    media, command = write_media(tmp_path, payload)
    delay = delays or Delays()
    adapter = YouTubeAdapter(
        Approvals(),
        gateway,
        store or MemoryCheckpointStore(),
        credentials_loader(),
        chunk_size=CHUNK_SIZE,
        max_retries=max_retries,
        max_session_recreations=max_session_recreations,
        max_processing_polls=max_processing_polls,
        retry_delay=delay,
    )
    return asyncio.run(adapter.publish(command, media)), command


def test_vault_credentials_are_required_and_redacted() -> None:
    credentials = credentials_loader()()
    assert (credentials.client_id, credentials.refresh_token) == ("client", "refresh")
    assert "secret" not in repr(credentials) and "refresh" not in repr(credentials)

    for payload in ({}, {"clientId": "client", "clientSecret": "secret", "refreshToken": ""}):
        with pytest.raises(YouTubeAuthError, match="unavailable or invalid"):
            load_youtube_credentials(Vault(json.dumps(payload)), "vault://youtube")

    with pytest.raises(YouTubeAuthError, match="unavailable or invalid") as raised:
        load_youtube_credentials(Vault(RuntimeError("vault leaked secret-value")), "vault://youtube")
    assert "secret-value" not in str(raised.value)


def test_render_hash_is_verified_before_any_provider_call(tmp_path: Path) -> None:
    media, _ = write_media(tmp_path)
    command = valid_command(destination=Destination.YOUTUBE, render_sha256="b" * 64)
    gateway = Gateway()
    adapter = YouTubeAdapter(Approvals(), gateway, MemoryCheckpointStore(), credentials_loader())

    with pytest.raises(YouTubeIntegrityError, match="approved render"):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []


def test_upload_reads_from_the_same_handle_that_was_verified(tmp_path: Path) -> None:
    media, command = write_media(tmp_path, b"approved")
    gateway = Gateway()

    def replace_path() -> None:
        replacement = tmp_path / "replacement.mp4"
        replacement.write_bytes(b"tampered")
        os.replace(replacement, media)

    gateway.on_exchange = replace_path
    adapter = YouTubeAdapter(
        Approvals(),
        gateway,
        MemoryCheckpointStore(),
        credentials_loader(),
        retry_delay=Delays(),
    )

    result = asyncio.run(adapter.publish(command, media))

    chunks = [event[2] for event in gateway.events if isinstance(event, tuple) and event[0] == "chunk"]
    assert chunks == [b"approved"]
    assert result.video_id == "Video_Id-1"


def test_upload_is_chunked_checkpointed_private_and_returns_studio_link(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.processing = ["processing", "succeeded"]
    store = MemoryCheckpointStore()
    delays = Delays()

    result, command = run_upload(tmp_path, gateway, store, delays=delays)

    creation = next(event for event in gateway.events if isinstance(event, tuple) and event[0] == "create")
    assert creation[1]["status"] == {"privacyStatus": "private"}
    chunks = [event for event in gateway.events if isinstance(event, tuple) and event[0] == "chunk"]
    assert [(event[1], len(event[2])) for event in chunks] == [(0, CHUNK_SIZE), (CHUNK_SIZE, 4)]
    checkpoint = store.load(command.publication_id)
    assert checkpoint == checkpoint_for(
        command,
        len(MEDIA_BYTES),
        session_reference="session-1",
        next_offset=len(MEDIA_BYTES),
        video_id="Video_Id-1",
    )
    assert checkpoint is not None
    assert set(vars(checkpoint)) == {
        "byte_count",
        "metadata_sha256",
        "next_offset",
        "provider_correlation",
        "render_sha256",
        "session_reference",
        "video_id",
    }
    assert result.studio_url == "https://studio.youtube.com/video/Video_Id-1/edit"
    assert delays.values == [1.0]


@pytest.mark.parametrize("chunk_size", [0, 1, CHUNK_SIZE - 1, CHUNK_SIZE + 1])
def test_non_final_chunk_size_must_be_a_positive_256_kib_multiple(chunk_size: int) -> None:
    with pytest.raises(ValueError, match="256 KiB"):
        YouTubeAdapter(
            Approvals(),
            Gateway(),
            MemoryCheckpointStore(),
            credentials_loader(),
            chunk_size=chunk_size,
        )


def test_restart_reconciles_provider_offset_before_sending_a_chunk(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.query_results = [ChunkResult(0, None)]
    store = MemoryCheckpointStore()
    _, command = write_media(tmp_path)
    store.save(
        command.publication_id,
        checkpoint_for(
            command,
            len(MEDIA_BYTES),
            session_reference="existing",
            next_offset=CHUNK_SIZE,
            video_id=None,
        ),
    )

    run_upload(tmp_path, gateway, store)

    assert not any(isinstance(event, tuple) and event[0] == "create" for event in gateway.events)
    query_index = gateway.events.index(("query", "existing"))
    chunk_index = gateway.events.index(("chunk", 0, MEDIA_BYTES[:CHUNK_SIZE], "existing"))
    assert query_index < chunk_index


def test_retry_queries_provider_offset_and_honors_exponential_delay(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [RetryableYouTubeError(), ChunkResult(CHUNK_SIZE, None)]
    gateway.query_results = [ChunkResult(0, None)]
    delays = Delays()

    run_upload(tmp_path, gateway, delays=delays)

    assert ("query", "session-1") in gateway.events
    assert delays.values == [1.0]
    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "chunk" and event[1] == 0]) == 2


def test_chunk_retry_is_bounded(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [RetryableYouTubeError(), RetryableYouTubeError(), RetryableYouTubeError()]
    gateway.query_results = [ChunkResult(0, None), ChunkResult(0, None)]
    delays = Delays()

    with pytest.raises(RetryableYouTubeError):
        run_upload(tmp_path, gateway, delays=delays, max_retries=2)

    assert delays.values == [1.0, 2.0]
    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "chunk"]) == 3


def test_expired_session_starts_a_new_private_session(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [SessionExpiredError()]
    delays = Delays()

    run_upload(tmp_path, gateway, delays=delays)

    creations = [event for event in gateway.events if isinstance(event, tuple) and event[0] == "create"]
    assert len(creations) == 2
    assert all(event[1]["status"] == {"privacyStatus": "private"} for event in creations)
    assert delays.values == [1.0]


def test_expired_reconciled_session_is_recreated_before_upload(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.query_results = [SessionExpiredError()]
    store = MemoryCheckpointStore()
    _, command = write_media(tmp_path)
    store.save(
        command.publication_id,
        checkpoint_for(
            command,
            len(MEDIA_BYTES),
            session_reference="existing",
            next_offset=CHUNK_SIZE,
            video_id=None,
        ),
    )

    run_upload(tmp_path, gateway, store)

    assert gateway.events.index(("query", "existing")) < gateway.events.index(
        ("chunk", 0, MEDIA_BYTES[:CHUNK_SIZE], "session-1")
    )


def test_successful_chunk_response_must_advance_or_complete(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [ChunkResult(0, None)]

    with pytest.raises(YouTubeProcessingError, match="invalid resumable offset"):
        run_upload(tmp_path, gateway)

    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "chunk"]) == 1


def test_expired_session_recreation_is_bounded(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [SessionExpiredError(), SessionExpiredError()]

    with pytest.raises(SessionExpiredError):
        run_upload(tmp_path, gateway, max_session_recreations=1)

    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "create"]) == 2


def test_completed_checkpoint_never_uploads_twice(tmp_path: Path) -> None:
    gateway = Gateway()
    store = MemoryCheckpointStore()
    _, command = write_media(tmp_path)
    store.save(
        command.publication_id,
        checkpoint_for(
            command,
            len(MEDIA_BYTES),
            session_reference=None,
            next_offset=len(MEDIA_BYTES),
            video_id="Video_Id-1",
        ),
    )

    run_upload(tmp_path, gateway, store)

    assert not any(isinstance(event, tuple) and event[0] in {"create", "chunk", "query"} for event in gateway.events)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_correlation", "localizer-stale"),
        ("render_sha256", "b" * 64),
        ("metadata_sha256", "c" * 64),
        ("byte_count", len(MEDIA_BYTES) + 1),
    ],
)
def test_stale_checkpoint_bindings_are_rejected_before_provider_calls(tmp_path: Path, field: str, value: object) -> None:
    gateway = Gateway()
    store = MemoryCheckpointStore()
    media, command = write_media(tmp_path)
    store.save(
        command.publication_id,
        checkpoint_for(
            command,
            len(MEDIA_BYTES),
            session_reference="existing",
            next_offset=0,
            video_id=None,
            **{field: value},
        ),
    )
    adapter = YouTubeAdapter(Approvals(), gateway, store, credentials_loader())

    with pytest.raises(YouTubeCheckpointError, match="bindings"):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []


@pytest.mark.parametrize("error", [YouTubeQuotaError(), YouTubeAuthError()])
def test_quota_and_auth_errors_are_not_retried(tmp_path: Path, error: Exception) -> None:
    gateway = Gateway()
    gateway.upload_results = [error]
    delays = Delays()

    with pytest.raises(type(error)):
        run_upload(tmp_path, gateway, delays=delays)

    assert not any(isinstance(event, tuple) and event[0] == "query" for event in gateway.events)
    assert delays.values == []


def test_processing_failure_is_terminal_after_bounded_backoff(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.processing = ["processing", "processing", "failed"]
    delays = Delays()

    with pytest.raises(YouTubeProcessingError, match="could not process"):
        run_upload(tmp_path, gateway, delays=delays)

    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "processing"]) == 3
    assert delays.values == [1.0, 2.0]


def test_processing_poll_count_and_delays_are_bounded(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.processing = ["processing", "processing", "processing"]
    delays = Delays()

    with pytest.raises(YouTubeProcessingError, match="polling bound"):
        run_upload(tmp_path, gateway, delays=delays, max_processing_polls=3)

    assert delays.values == [1.0, 2.0]


def test_http_gateway_exchanges_oauth_and_parses_resumable_protocol() -> None:
    session_url = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=opaque"
    transport = Transport(
        YouTubeHttpResponse(200, {}, b'{"access_token":"fresh-token"}'),
        YouTubeHttpResponse(200, {"Location": session_url}, b""),
        YouTubeHttpResponse(308, {"Range": f"bytes=0-{CHUNK_SIZE - 1}"}, b""),
        YouTubeHttpResponse(200, {}, b'{"id":"Video_Id-1"}'),
        YouTubeHttpResponse(200, {}, b'{"items":[{"status":{"uploadStatus":"processed"}}]}'),
    )
    gateway = YouTubeHttpGateway(transport=transport, request_timeout_seconds=17)
    credentials = YouTubeCredentials("client", "secret", "refresh")

    async def exercise() -> tuple[str, str, ChunkResult, ChunkResult, str]:
        token = await gateway.exchange_refresh_token(credentials)
        session = await gateway.create_session(
            access_token=token,
            byte_count=CHUNK_SIZE + 4,
            correlation="localizer-correlation",
            metadata={"snippet": {"title": "Title"}, "status": {"privacyStatus": "private"}},
        )
        first = await gateway.upload_chunk(
            access_token=token,
            session_reference=session,
            offset=0,
            total_bytes=CHUNK_SIZE + 4,
            data=b"a" * CHUNK_SIZE,
        )
        final = await gateway.query_session(
            access_token=token,
            session_reference=session,
            total_bytes=CHUNK_SIZE + 4,
        )
        status = await gateway.processing_status(access_token=token, video_id="Video_Id-1")
        return token, session, first, final, status

    token, session, first, final, status = asyncio.run(exercise())

    oauth_payload = parse_qs((transport.requests[0][3] or b"").decode())
    assert oauth_payload == {
        "client_id": ["client"],
        "client_secret": ["secret"],
        "grant_type": ["refresh_token"],
        "refresh_token": ["refresh"],
    }
    assert (token, session) == ("fresh-token", session_url)
    assert first == ChunkResult(CHUNK_SIZE, None)
    assert final == ChunkResult(CHUNK_SIZE + 4, "Video_Id-1")
    assert status == "succeeded"
    assert all(request[4] == 17 for request in transport.requests)


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (YouTubeHttpResponse(401, {}, b'{"error":{"message":"token secret-value"}}'), YouTubeAuthError),
        (YouTubeHttpResponse(403, {}, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}'), YouTubeQuotaError),
        (YouTubeHttpResponse(503, {}, b"provider secret-value"), RetryableYouTubeError),
        (YouTubeHttpResponse(400, {}, b"provider secret-value"), YouTubeProcessingError),
    ],
)
def test_http_gateway_maps_errors_without_leaking_provider_or_credential_data(
    response: YouTubeHttpResponse,
    error_type: type[Exception],
) -> None:
    gateway = YouTubeHttpGateway(transport=Transport(response))

    with pytest.raises(error_type) as raised:
        asyncio.run(
            gateway.upload_chunk(
                access_token="access-secret",
                session_reference="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session-secret",
                offset=0,
                total_bytes=4,
                data=b"data",
            )
        )

    message = str(raised.value)
    assert "secret-value" not in message
    assert "access-secret" not in message
    assert "session-secret" not in message
