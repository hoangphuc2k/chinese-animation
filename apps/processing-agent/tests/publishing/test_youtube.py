from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from localizer_agent.publishing.models import Destination, PublicationApprovalSnapshot, PublicationCommand
from localizer_agent.publishing.youtube import (
    AmbiguousYouTubeCreationError,
    CheckpointStore,
    ChunkResult,
    ConcurrentYouTubePublishError,
    FileCheckpointStore,
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
SESSION_1 = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session-1"
EXISTING_SESSION = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=existing"


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
        self.create_results: list[object] = []
        self.upload_results: list[object] = []
        self.query_results: list[object] = []
        self.processing: list[object] = ["succeeded"]
        self.on_create: Callable[[], None] | None = None
        self.on_exchange: Callable[[], None] | None = None

    async def exchange_refresh_token(self, credentials: YouTubeCredentials) -> str:
        self.events.append("auth")
        if self.on_exchange is not None:
            self.on_exchange()
        return "access-token"

    async def create_session(self, *, access_token: str, byte_count: int, correlation: str, metadata: dict[str, object]) -> str:
        self.events.append(("create", metadata, byte_count, correlation))
        if self.on_create is not None:
            self.on_create()
        if self.create_results:
            result = self.create_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return str(result)
        count = len([event for event in self.events if isinstance(event, tuple) and event[0] == "create"])
        return f"https://www.googleapis.com/upload/youtube/v3/videos?upload_id=session-{count}"

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
        result = self.processing.pop(0)
        if isinstance(result, Exception):
            raise result
        return str(result)


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
        "creation_pending": False,
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
    store: CheckpointStore | None = None,
    *,
    payload: bytes = MEDIA_BYTES,
    delays: Delays | None = None,
    max_retries: int = 2,
    max_processing_retries: int = 2,
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
        max_processing_retries=max_processing_retries,
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
        session_reference=SESSION_1,
        next_offset=len(MEDIA_BYTES),
        video_id="Video_Id-1",
    )
    assert checkpoint is not None
    assert set(vars(checkpoint)) == {
        "byte_count",
        "creation_pending",
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
            session_reference=EXISTING_SESSION,
            next_offset=CHUNK_SIZE,
            video_id=None,
        ),
    )

    run_upload(tmp_path, gateway, store)

    assert not any(isinstance(event, tuple) and event[0] == "create" for event in gateway.events)
    query_index = gateway.events.index(("query", EXISTING_SESSION))
    chunk_index = gateway.events.index(("chunk", 0, MEDIA_BYTES[:CHUNK_SIZE], EXISTING_SESSION))
    assert query_index < chunk_index


def test_retry_queries_provider_offset_and_honors_exponential_delay(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [RetryableYouTubeError(), ChunkResult(CHUNK_SIZE, None)]
    gateway.query_results = [ChunkResult(0, None)]
    delays = Delays()

    run_upload(tmp_path, gateway, delays=delays)

    assert ("query", SESSION_1) in gateway.events
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


def test_processing_status_retry_recovers_with_its_own_exponential_budget(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.processing = [RetryableYouTubeError(), RetryableYouTubeError(), "succeeded"]
    delays = Delays()

    result, _ = run_upload(
        tmp_path,
        gateway,
        delays=delays,
        max_retries=0,
        max_processing_retries=2,
    )

    assert result.video_id == "Video_Id-1"
    assert delays.values == [1.0, 2.0]


def test_processing_status_retry_exhaustion_is_bounded_and_sanitized(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.processing = [
        RetryableYouTubeError("provider secret-value"),
        RetryableYouTubeError("provider secret-value"),
        RetryableYouTubeError("provider secret-value"),
    ]
    delays = Delays()

    with pytest.raises(RetryableYouTubeError, match="processing status retry bound") as raised:
        run_upload(tmp_path, gateway, delays=delays, max_processing_retries=2)

    assert "secret-value" not in str(raised.value)
    assert delays.values == [1.0, 2.0]
    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "processing"]) == 3


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
            session_reference=EXISTING_SESSION,
            next_offset=CHUNK_SIZE,
            video_id=None,
        ),
    )

    run_upload(tmp_path, gateway, store)

    assert gateway.events.index(("query", EXISTING_SESSION)) < gateway.events.index(
        ("chunk", 0, MEDIA_BYTES[:CHUNK_SIZE], SESSION_1)
    )


def test_successful_chunk_response_must_advance_or_complete(tmp_path: Path) -> None:
    gateway = Gateway()
    gateway.upload_results = [ChunkResult(0, None)]

    with pytest.raises(YouTubeProcessingError, match="invalid resumable offset"):
        run_upload(tmp_path, gateway)

    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "chunk"]) == 1


def test_file_checkpoint_store_survives_a_genuine_new_instance_restart(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    first_store = FileCheckpointStore(checkpoint_root)
    first_gateway = Gateway()
    first_gateway.upload_results = [ChunkResult(CHUNK_SIZE, None), YouTubeQuotaError()]

    with pytest.raises(YouTubeQuotaError):
        run_upload(tmp_path, first_gateway, first_store)

    _, command = write_media(tmp_path)
    checkpoint_path = checkpoint_root / f"{command.publication_id}.json"
    persisted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert persisted == {
        "byte_count": len(MEDIA_BYTES),
        "creation_pending": False,
        "metadata_sha256": command.metadata_sha256,
        "next_offset": CHUNK_SIZE,
        "provider_correlation": command.provider_correlation,
        "render_sha256": command.render_sha256,
        "session_reference": SESSION_1,
        "video_id": None,
    }
    assert not list(checkpoint_root.glob("*.tmp"))
    assert "access-token" not in checkpoint_path.read_text(encoding="utf-8")

    second_store = FileCheckpointStore(checkpoint_root)
    second_gateway = Gateway()
    second_gateway.query_results = [ChunkResult(CHUNK_SIZE, None)]

    result, _ = run_upload(tmp_path, second_gateway, second_store)

    assert second_gateway.events.index(("query", SESSION_1)) < second_gateway.events.index(
        ("chunk", CHUNK_SIZE, b"tail", SESSION_1)
    )
    assert result.video_id == "Video_Id-1"
    assert FileCheckpointStore(checkpoint_root).load(command.publication_id).video_id == "Video_Id-1"


def test_malformed_durable_checkpoint_fails_closed_before_provider_calls(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    store = FileCheckpointStore(checkpoint_root)
    media, command = write_media(tmp_path)
    (checkpoint_root / f"{command.publication_id}.json").write_text("{not-json", encoding="utf-8")
    gateway = Gateway()
    adapter = YouTubeAdapter(Approvals(), gateway, store, credentials_loader())

    with pytest.raises(YouTubeCheckpointError, match="malformed"):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []


def test_broken_symlink_checkpoint_fails_closed(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    store = FileCheckpointStore(checkpoint_root)
    _, command = write_media(tmp_path)
    (checkpoint_root / f"{command.publication_id}.json").symlink_to(tmp_path / "missing-checkpoint")

    with pytest.raises(YouTubeCheckpointError, match="malformed"):
        store.load(command.publication_id)


def test_durable_checkpoint_deserialization_requires_exact_typed_fields(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    store = FileCheckpointStore(checkpoint_root)
    _, command = write_media(tmp_path)
    checkpoint = checkpoint_for(
        command,
        len(MEDIA_BYTES),
        session_reference=SESSION_1,
        next_offset=CHUNK_SIZE,
        video_id=None,
    )
    store.save(command.publication_id, checkpoint)
    checkpoint_path = checkpoint_root / f"{command.publication_id}.json"
    valid = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    malformed_values = [
        valid | {"next_offset": True},
        valid | {"byte_count": "262148"},
        valid | {"creation_pending": 0},
        valid | {"session_reference": "https://attacker.invalid/upload"},
        valid | {"video_id": "bad id", "next_offset": len(MEDIA_BYTES)},
        valid | {"unexpected": "field"},
        {key: value for key, value in valid.items() if key != "render_sha256"},
    ]

    for malformed in malformed_values:
        checkpoint_path.write_text(json.dumps(malformed), encoding="utf-8")
        with pytest.raises(YouTubeCheckpointError, match="malformed"):
            FileCheckpointStore(checkpoint_root).load(command.publication_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"next_offset": True},
        {"creation_pending": "false"},
        {"session_reference": "opaque-session"},
        {"session_reference": "https://attacker.invalid/upload"},
        {"session_reference": "https://www.googleapis.com/oauth2/token"},
        {"session_reference": f"{SESSION_1}&access_token=secret-value"},
        {"session_reference": f"{SESSION_1}&upload_id="},
        {"session_reference": f"{SESSION_1}&unexpected=value"},
        {"next_offset": len(MEDIA_BYTES), "video_id": "bad id"},
    ],
)
def test_checkpoint_values_from_any_store_are_validated_before_provider_calls(
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    store = MemoryCheckpointStore()
    media, command = write_media(tmp_path)
    store.save(
        command.publication_id,
        replace(
            checkpoint_for(
                command,
                len(MEDIA_BYTES),
                session_reference=SESSION_1,
                next_offset=0,
                video_id=None,
            ),
            **changes,
        ),
    )
    gateway = Gateway()
    adapter = YouTubeAdapter(Approvals(), gateway, store, credentials_loader())

    with pytest.raises(YouTubeCheckpointError):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []


def test_creation_intent_is_durable_before_request_and_blocks_ambiguous_retry(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    store = FileCheckpointStore(checkpoint_root)
    _, command = write_media(tmp_path)
    observed: list[YouTubeCheckpoint | None] = []
    gateway = Gateway()
    gateway.create_results = [RetryableYouTubeError("provider secret-value")]
    gateway.on_create = lambda: observed.append(FileCheckpointStore(checkpoint_root).load(command.publication_id))

    with pytest.raises(AmbiguousYouTubeCreationError, match="manual recovery") as raised:
        run_upload(tmp_path, gateway, store)

    assert "secret-value" not in str(raised.value)
    assert observed and observed[0] is not None and observed[0].creation_pending is True
    persisted = FileCheckpointStore(checkpoint_root).load(command.publication_id)
    assert persisted is not None and persisted.creation_pending is True
    assert persisted.session_reference is None

    restarted_gateway = Gateway()
    with pytest.raises(AmbiguousYouTubeCreationError, match="manual recovery"):
        run_upload(tmp_path, restarted_gateway, FileCheckpointStore(checkpoint_root))
    assert restarted_gateway.events == []


def test_invalid_session_creation_response_remains_ambiguous(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    store = FileCheckpointStore(checkpoint_root)
    gateway = Gateway()
    gateway.create_results = [YouTubeProcessingError("missing session reference")]

    with pytest.raises(AmbiguousYouTubeCreationError, match="manual recovery"):
        run_upload(tmp_path, gateway, store)

    _, command = write_media(tmp_path)
    checkpoint = FileCheckpointStore(checkpoint_root).load(command.publication_id)
    assert checkpoint is not None and checkpoint.creation_pending is True


def test_explicit_session_creation_rejection_allows_a_later_safe_attempt(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    store = FileCheckpointStore(checkpoint_root)
    gateway = Gateway()
    gateway.create_results = [YouTubeAuthError("rejected")]

    with pytest.raises(YouTubeAuthError):
        run_upload(tmp_path, gateway, store)

    _, command = write_media(tmp_path)
    rejected = FileCheckpointStore(checkpoint_root).load(command.publication_id)
    assert rejected is not None and rejected.creation_pending is False
    assert rejected.session_reference is None

    result, _ = run_upload(tmp_path, Gateway(), FileCheckpointStore(checkpoint_root))
    assert result.video_id == "Video_Id-1"


def test_concurrent_adapters_cannot_both_create_a_session(tmp_path: Path) -> None:
    media, command = write_media(tmp_path, b"approved")
    store = MemoryCheckpointStore()

    class BlockingGateway(Gateway):
        def __init__(self) -> None:
            super().__init__()
            self.creation_started = asyncio.Event()
            self.release_creation = asyncio.Event()

        async def create_session(
            self,
            *,
            access_token: str,
            byte_count: int,
            correlation: str,
            metadata: dict[str, object],
        ) -> str:
            self.events.append(("create", metadata, byte_count, correlation))
            self.creation_started.set()
            await self.release_creation.wait()
            return SESSION_1

    first_gateway = BlockingGateway()
    second_gateway = Gateway()
    first_adapter = YouTubeAdapter(
        Approvals(), first_gateway, store, credentials_loader(), retry_delay=Delays()
    )
    second_adapter = YouTubeAdapter(
        Approvals(), second_gateway, store, credentials_loader(), retry_delay=Delays()
    )

    async def exercise() -> None:
        first = asyncio.create_task(first_adapter.publish(command, media))
        await first_gateway.creation_started.wait()
        with pytest.raises(ConcurrentYouTubePublishError, match="already in progress"):
            await second_adapter.publish(command, media)
        assert second_gateway.events == []
        first_gateway.release_creation.set()
        await first

    asyncio.run(exercise())
    assert len([event for event in first_gateway.events if isinstance(event, tuple) and event[0] == "create"]) == 1


def test_durable_store_reservation_is_exclusive_across_instances_and_releases(tmp_path: Path) -> None:
    checkpoint_root = tmp_path / "youtube-checkpoints"
    _, command = write_media(tmp_path)
    first_store = FileCheckpointStore(checkpoint_root)
    second_store = FileCheckpointStore(checkpoint_root)

    with first_store.reserve(command.publication_id):
        with pytest.raises(ConcurrentYouTubePublishError, match="already in progress"):
            with second_store.reserve(command.publication_id):
                pytest.fail("concurrent durable reservation was admitted")

    with second_store.reserve(command.publication_id):
        pass


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
            session_reference=EXISTING_SESSION,
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
    session_url = SESSION_1
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
