from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from localizer_agent.publishing.base import StalePublicationApprovalError
from localizer_agent.publishing.facebook import (
    AmbiguousFacebookCreationError,
    AmbiguousFacebookCompletionError,
    ConcurrentFacebookPublishError,
    FacebookCheckpointError,
    FacebookAdapter,
    FacebookCheckpoint,
    FacebookConfig,
    FacebookHttpGateway,
    FacebookHttpResponse,
    FacebookIntegrityError,
    FacebookProcessingError,
    FacebookPublishResult,
    FacebookRateLimitError,
    FacebookRejectedError,
    FacebookTransferResult,
    FacebookUploadSession,
    FileFacebookCheckpointStore,
    MemoryFacebookCheckpointStore,
    RetryableFacebookError,
)
from localizer_agent.publishing.facebook_auth import (
    FacebookAuthError,
    FacebookPageCredentials,
    load_facebook_page_credentials,
)
from localizer_agent.publishing.models import Destination, PublicationApprovalSnapshot, PublicationCommand
from tests.publishing.test_metadata import valid_command


MEDIA_BYTES = b"approved facebook render"


class Approvals:
    def __init__(self, snapshot: PublicationApprovalSnapshot | None = None) -> None:
        self.snapshot = snapshot

    async def get_publication_approval(self, command: PublicationCommand) -> PublicationApprovalSnapshot | None:
        return self.snapshot or PublicationApprovalSnapshot.from_command(command, approved=True)


class Gateway:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.start_results: list[object] = []
        self.query_results: list[object] = []
        self.transfer_results: list[object] = []
        self.finish_results: list[object] = []
        self.processing: list[object] = ["succeeded"]

    async def start_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        byte_count: int,
        correlation: str,
    ) -> FacebookUploadSession:
        self.events.append(("start", config.page_id, config.graph_api_version, byte_count, correlation))
        if self.start_results:
            result = self.start_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        return FacebookUploadSession("upload-session-1", "123456789012345", 0, min(8, byte_count))

    async def query_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        video_id: str,
    ) -> FacebookTransferResult:
        self.events.append(("query", upload_session_id, video_id))
        if self.query_results:
            result = self.query_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        return FacebookTransferResult(0, 8)

    async def transfer_chunk(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        start_offset: int,
        data: bytes,
    ) -> FacebookTransferResult:
        self.events.append(("transfer", start_offset, data, upload_session_id))
        if self.transfer_results:
            result = self.transfer_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result  # type: ignore[return-value]
        next_offset = start_offset + len(data)
        return FacebookTransferResult(next_offset, next_offset + 8)

    async def finish_upload(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        upload_session_id: str,
        metadata: dict[str, object],
    ) -> None:
        self.events.append(("finish", upload_session_id, metadata))
        if self.finish_results:
            result = self.finish_results.pop(0)
            if isinstance(result, Exception):
                raise result

    async def processing_status(
        self,
        *,
        credentials: FacebookPageCredentials,
        config: FacebookConfig,
        video_id: str,
    ) -> str:
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
    def __init__(self, *responses: FacebookHttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict[str, str], bytes | None, int]] = []

    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> FacebookHttpResponse:
        self.requests.append((method, url, headers, body, timeout_seconds))
        return self.responses.pop(0)


def credentials_loader(events: list[object] | None = None):
    def load() -> FacebookPageCredentials:
        if events is not None:
            events.append("credentials")
        return load_facebook_page_credentials(
            Vault(json.dumps({"pageAccessToken": "page-secret"})),
            "vault://facebook/page-token",
        )

    return load


def write_media(tmp_path: Path, payload: bytes = MEDIA_BYTES) -> tuple[Path, PublicationCommand]:
    media = tmp_path / "render.mp4"
    media.write_bytes(payload)
    command = valid_command(
        destination=Destination.FACEBOOK,
        render_sha256=hashlib.sha256(payload).hexdigest(),
    )
    return media, command


def checkpoint_for(command: PublicationCommand, expected_byte_count: int, **changes: object) -> FacebookCheckpoint:
    values: dict[str, object] = {
        "provider_correlation": command.provider_correlation,
        "render_sha256": command.render_sha256,
        "metadata_sha256": command.metadata_sha256,
        "byte_count": expected_byte_count,
        "page_id": "123456789",
        "graph_api_version": "v24.0",
        "creation_pending": False,
        "upload_session_id": "upload-session-existing",
        "video_id": "123456789012345",
        "next_offset": 8,
        "completion_pending": False,
        "completed": False,
    }
    values.update(changes)
    return FacebookCheckpoint(**values)  # type: ignore[arg-type]


class Vault:
    def __init__(self, value: str | Exception) -> None:
        self.value = value

    def read_secret(self, reference: str) -> str:
        assert reference == "vault://facebook/page-token"
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def test_vault_page_token_is_required_and_redacted() -> None:
    credentials = load_facebook_page_credentials(
        Vault(json.dumps({"pageAccessToken": "page-secret"})),
        "vault://facebook/page-token",
    )

    assert credentials == FacebookPageCredentials(page_access_token="page-secret")
    assert "page-secret" not in repr(credentials)

    for payload in ({}, {"pageAccessToken": ""}, {"pageAccessToken": "token", "pageId": "123"}):
        with pytest.raises(FacebookAuthError, match="unavailable or invalid"):
            load_facebook_page_credentials(Vault(json.dumps(payload)), "vault://facebook/page-token")

    with pytest.raises(FacebookAuthError, match="unavailable or invalid") as raised:
        load_facebook_page_credentials(
            Vault(RuntimeError("vault leaked page-secret")),
            "vault://facebook/page-token",
        )
    assert "page-secret" not in str(raised.value)


def test_fail_fallback_makes_zero_provider_calls_before_exact_approval(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    stale = replace(
        PublicationApprovalSnapshot.from_command(command, approved=True),
        metadata_version=command.metadata_version + 1,
    )
    gateway = Gateway()
    credential_events: list[object] = []
    adapter = FacebookAdapter(
        Approvals(stale),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(credential_events),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    with pytest.raises(StalePublicationApprovalError):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []
    assert credential_events == []


def test_render_hash_is_verified_before_credentials_or_first_provider_byte(tmp_path: Path) -> None:
    media, _ = write_media(tmp_path)
    command = valid_command(destination=Destination.FACEBOOK, render_sha256="b" * 64)
    gateway = Gateway()
    credential_events: list[object] = []
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(credential_events),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    with pytest.raises(FacebookIntegrityError, match="approved render"):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []
    assert credential_events == []


def test_approved_upload_is_resumable_checkpointed_and_returns_video_link(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.processing = ["processing", "succeeded"]
    store = MemoryFacebookCheckpointStore()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        retry_delay=lambda _: asyncio.sleep(0),
    )

    result = asyncio.run(adapter.publish(command, media))

    assert result == FacebookPublishResult(
        video_id="123456789012345",
        video_url="https://www.facebook.com/123456789012345",
    )
    transfers = [event for event in gateway.events if isinstance(event, tuple) and event[0] == "transfer"]
    assert [(event[1], event[2]) for event in transfers] == [
        (0, MEDIA_BYTES[0:8]),
        (8, MEDIA_BYTES[8:16]),
        (16, MEDIA_BYTES[16:24]),
    ]
    finish = next(event for event in gateway.events if isinstance(event, tuple) and event[0] == "finish")
    assert finish[2] == {
        "description": "Xin chào 世界\n#HoatHinh #TuTien",
        "published": True,
    }
    assert store.load(command.publication_id) == FacebookCheckpoint(
        provider_correlation=command.provider_correlation,
        render_sha256=command.render_sha256,
        metadata_sha256=command.metadata_sha256,
        byte_count=len(MEDIA_BYTES),
        page_id="123456789",
        graph_api_version="v24.0",
        creation_pending=False,
        upload_session_id="upload-session-1",
        video_id="123456789012345",
        next_offset=len(MEDIA_BYTES),
        completion_pending=False,
        completed=True,
    )


def test_transfer_uses_provider_directed_resumable_ranges(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.start_results = [FacebookUploadSession("upload-session-1", "123456789012345", 0, 5)]
    gateway.transfer_results = [
        FacebookTransferResult(5, 10),
        FacebookTransferResult(10, 15),
        FacebookTransferResult(15, 20),
        FacebookTransferResult(20, 24),
        FacebookTransferResult(24, 24),
    ]
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        retry_delay=lambda _: asyncio.sleep(0),
    )

    asyncio.run(adapter.publish(command, media))

    transfers = [event for event in gateway.events if isinstance(event, tuple) and event[0] == "transfer"]
    assert [(event[1], len(event[2])) for event in transfers] == [(0, 5), (5, 5), (10, 5), (15, 5), (20, 4)]


def test_restart_reconciles_provider_offset_before_resuming_transfer(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.query_results = [FacebookTransferResult(16, 24)]
    store = MemoryFacebookCheckpointStore()
    store.save(command.publication_id, checkpoint_for(command, len(MEDIA_BYTES)))
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        retry_delay=lambda _: asyncio.sleep(0),
    )

    asyncio.run(adapter.publish(command, media))

    assert not any(isinstance(event, tuple) and event[0] == "start" for event in gateway.events)
    assert gateway.events.index(("query", "upload-session-existing", "123456789012345")) < gateway.events.index(
        ("transfer", 16, MEDIA_BYTES[16:24], "upload-session-existing")
    )


def test_completed_checkpoint_never_transfers_or_finishes_twice(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    store = MemoryFacebookCheckpointStore()
    store.save(
        command.publication_id,
        checkpoint_for(command, len(MEDIA_BYTES), next_offset=len(MEDIA_BYTES), completed=True),
    )
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        retry_delay=lambda _: asyncio.sleep(0),
    )

    result = asyncio.run(adapter.publish(command, media))

    assert result.video_id == "123456789012345"
    assert not any(
        isinstance(event, tuple) and event[0] in {"start", "query", "transfer", "finish"}
        for event in gateway.events
    )


def test_ambiguous_completion_reconciles_status_before_repeating_finish(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    store = MemoryFacebookCheckpointStore()
    store.save(
        command.publication_id,
        checkpoint_for(
            command,
            len(MEDIA_BYTES),
            next_offset=len(MEDIA_BYTES),
            completion_pending=True,
        ),
    )
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        retry_delay=lambda _: asyncio.sleep(0),
    )

    result = asyncio.run(adapter.publish(command, media))

    assert result.video_id == "123456789012345"
    assert gateway.events == [("processing", "123456789012345")]
    assert store.load(command.publication_id).completed is True  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_correlation", "localizer-" + "0" * 64),
        ("render_sha256", "b" * 64),
        ("metadata_sha256", "c" * 64),
        ("byte_count", len(MEDIA_BYTES) + 1),
        ("page_id", "987654321"),
        ("graph_api_version", "v23.0"),
    ],
)
def test_stale_checkpoint_bindings_fail_before_credentials_or_provider_calls(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    store = MemoryFacebookCheckpointStore()
    store.save(command.publication_id, checkpoint_for(command, len(MEDIA_BYTES), **{field: value}))
    credential_events: list[object] = []
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(credential_events),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    with pytest.raises(FacebookCheckpointError, match="bindings"):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []
    assert credential_events == []


def test_file_checkpoint_survives_restart_without_credentials(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    checkpoint_root = tmp_path / "facebook-checkpoints"
    first_gateway = Gateway()
    first_gateway.transfer_results = [FacebookTransferResult(8, 16), FacebookAuthError("expired")]
    first_adapter = FacebookAdapter(
        Approvals(),
        first_gateway,
        FileFacebookCheckpointStore(checkpoint_root),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        retry_delay=lambda _: asyncio.sleep(0),
    )

    with pytest.raises(FacebookAuthError):
        asyncio.run(first_adapter.publish(command, media))

    checkpoint_path = checkpoint_root / f"{command.publication_id}.json"
    persisted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert persisted["next_offset"] == 8
    assert persisted["page_id"] == "123456789"
    assert persisted["graph_api_version"] == "v24.0"
    assert "page-secret" not in checkpoint_path.read_text(encoding="utf-8")

    second_gateway = Gateway()
    second_gateway.query_results = [FacebookTransferResult(8, 16)]
    second_adapter = FacebookAdapter(
        Approvals(),
        second_gateway,
        FileFacebookCheckpointStore(checkpoint_root),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        retry_delay=lambda _: asyncio.sleep(0),
    )

    result = asyncio.run(second_adapter.publish(command, media))

    assert result.video_id == "123456789012345"
    assert second_gateway.events.index(("query", "upload-session-1", "123456789012345")) < second_gateway.events.index(
        ("transfer", 8, MEDIA_BYTES[8:16], "upload-session-1")
    )


def test_creation_intent_is_durable_and_blocks_ambiguous_restart(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    gateway = Gateway()
    gateway.start_results = [RuntimeError("provider leaked page-secret")]
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    with pytest.raises(AmbiguousFacebookCreationError, match="manual recovery") as raised:
        asyncio.run(adapter.publish(command, media))

    assert "page-secret" not in str(raised.value)
    pending = store.load(command.publication_id)
    assert pending is not None and pending.creation_pending is True

    restarted_gateway = Gateway()
    credential_events: list[object] = []
    restarted = FacebookAdapter(
        Approvals(),
        restarted_gateway,
        store,
        credentials_loader(credential_events),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )
    with pytest.raises(AmbiguousFacebookCreationError, match="manual recovery"):
        asyncio.run(restarted.publish(command, media))
    assert restarted_gateway.events == []
    assert credential_events == []


def test_explicit_start_auth_rejection_allows_safe_retry_with_fresh_token(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    rejected_gateway = Gateway()
    rejected_gateway.start_results = [FacebookAuthError("expired page-secret")]
    rejected = FacebookAdapter(
        Approvals(),
        rejected_gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    with pytest.raises(FacebookAuthError, match="start authorization") as raised:
        asyncio.run(rejected.publish(command, media))
    assert "page-secret" not in str(raised.value)

    result = asyncio.run(
        FacebookAdapter(
            Approvals(),
            Gateway(),
            store,
            credentials_loader(),
            FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
            max_chunk_size=8,
            retry_delay=lambda _: asyncio.sleep(0),
        ).publish(command, media)
    )
    assert result.video_id == "123456789012345"


def test_explicit_start_rate_limit_retries_within_bound(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.start_results = [
        FacebookRateLimitError("rate page-secret"),
        FacebookUploadSession("upload-session-1", "123456789012345", 0, 8),
    ]
    delays = Delays()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        max_retries=1,
        retry_delay=delays,
    )

    result = asyncio.run(adapter.publish(command, media))

    assert result.video_id == "123456789012345"
    assert delays.values == [1.0]
    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "start"]) == 2


def test_rate_limited_transfer_reconciles_offset_with_bounded_backoff(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [FacebookRateLimitError(), FacebookTransferResult(8, 16)]
    gateway.query_results = [FacebookTransferResult(0, 8)]
    delays = Delays()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        max_retries=2,
        retry_delay=delays,
    )

    asyncio.run(adapter.publish(command, media))

    assert delays.values == [1.0]
    assert ("query", "upload-session-1", "123456789012345") in gateway.events
    first_chunk_calls = [
        event
        for event in gateway.events
        if isinstance(event, tuple) and event[:2] == ("transfer", 0)
    ]
    assert len(first_chunk_calls) == 2


def test_rate_limited_offset_query_uses_the_same_bounded_retry_budget(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [FacebookRateLimitError(), FacebookTransferResult(8, 16)]
    gateway.query_results = [FacebookRateLimitError(), FacebookTransferResult(0, 8)]
    delays = Delays()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        max_retries=2,
        retry_delay=delays,
    )

    asyncio.run(adapter.publish(command, media))

    assert delays.values == [1.0, 2.0]
    assert len([event for event in gateway.events if isinstance(event, tuple) and event[0] == "query"]) == 2


def test_expired_page_token_is_not_retried_or_exposed(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [FacebookAuthError("expired page-secret")]
    delays = Delays()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
        retry_delay=delays,
    )

    with pytest.raises(FacebookAuthError):
        asyncio.run(adapter.publish(command, media))

    assert delays.values == []
    assert not any(isinstance(event, tuple) and event[0] == "query" for event in gateway.events)


def test_processing_rate_limit_recovers_with_an_independent_retry_budget(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.processing = [FacebookRateLimitError(), "succeeded"]
    delays = Delays()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=len(MEDIA_BYTES),
        max_retries=0,
        max_processing_retries=1,
        retry_delay=delays,
    )

    result = asyncio.run(adapter.publish(command, media))

    assert result.video_id == "123456789012345"
    assert delays.values == [1.0]


def test_processing_partial_failure_keeps_completion_and_never_republishes(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    first_gateway = Gateway()
    first_gateway.processing = ["failed"]
    adapter = FacebookAdapter(
        Approvals(),
        first_gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=len(MEDIA_BYTES),
    )

    with pytest.raises(FacebookProcessingError, match="could not process"):
        asyncio.run(adapter.publish(command, media))

    assert store.load(command.publication_id).completed is True  # type: ignore[union-attr]
    second_gateway = Gateway()
    second_gateway.processing = ["failed"]
    restarted = FacebookAdapter(
        Approvals(),
        second_gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )
    with pytest.raises(FacebookProcessingError, match="could not process"):
        asyncio.run(restarted.publish(command, media))
    assert not any(
        isinstance(event, tuple) and event[0] in {"start", "query", "transfer", "finish"}
        for event in second_gateway.events
    )


def test_finish_intent_is_durable_and_ambiguous_failure_is_sanitized(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.finish_results = [RuntimeError("provider leaked page-secret")]
    store = MemoryFacebookCheckpointStore()
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=len(MEDIA_BYTES),
    )

    with pytest.raises(AmbiguousFacebookCompletionError, match="reconcile") as raised:
        asyncio.run(adapter.publish(command, media))

    assert "page-secret" not in str(raised.value)
    pending = store.load(command.publication_id)
    assert pending is not None and pending.completion_pending is True and pending.completed is False

    restarted_gateway = Gateway()
    restarted = FacebookAdapter(
        Approvals(),
        restarted_gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )
    result = asyncio.run(restarted.publish(command, media))
    assert result.video_id == "123456789012345"
    assert restarted_gateway.events == [("processing", "123456789012345")]


def test_http_gateway_uses_configured_graph_version_and_resumable_protocol() -> None:
    transport = Transport(
        FacebookHttpResponse(
            200,
            {},
            b'{"upload_session_id":"session-1","video_id":"123456789012345","start_offset":"0","end_offset":"8"}',
        ),
        FacebookHttpResponse(200, {}, b'{"start_offset":"8","end_offset":"16"}'),
        FacebookHttpResponse(200, {}, b'{"start_offset":"8","end_offset":"16"}'),
        FacebookHttpResponse(200, {}, b'{"success":true}'),
        FacebookHttpResponse(200, {}, b'{"status":{"video_status":"ready"}}'),
    )
    gateway = FacebookHttpGateway(transport=transport, request_timeout_seconds=19)
    credentials = FacebookPageCredentials("page-secret")
    config = FacebookConfig(page_id="123456789", graph_api_version="v24.0")

    async def exercise() -> tuple[FacebookUploadSession, FacebookTransferResult, FacebookTransferResult, str]:
        session = await gateway.start_upload(
            credentials=credentials,
            config=config,
            byte_count=24,
            correlation="localizer-" + "a" * 64,
        )
        transferred = await gateway.transfer_chunk(
            credentials=credentials,
            config=config,
            upload_session_id=session.upload_session_id,
            start_offset=0,
            data=b"abcdefgh",
        )
        queried = await gateway.query_upload(
            credentials=credentials,
            config=config,
            upload_session_id=session.upload_session_id,
            video_id=session.video_id,
        )
        await gateway.finish_upload(
            credentials=credentials,
            config=config,
            upload_session_id=session.upload_session_id,
            metadata={"description": "Caption\n#Tag", "published": True},
        )
        status = await gateway.processing_status(credentials=credentials, config=config, video_id=session.video_id)
        return session, transferred, queried, status

    session, transferred, queried, status = asyncio.run(exercise())

    assert session == FacebookUploadSession("session-1", "123456789012345", 0, 8)
    assert transferred == FacebookTransferResult(8, 16)
    assert queried == FacebookTransferResult(8, 16)
    assert status == "succeeded"
    assert all(request[4] == 19 for request in transport.requests)
    assert all("v24.0" in request[1] for request in transport.requests)
    assert all("page-secret" not in request[1] and b"page-secret" not in (request[3] or b"") for request in transport.requests)
    assert all(request[2]["Authorization"] == "Bearer page-secret" for request in transport.requests)
    start_fields = parse_qs((transport.requests[0][3] or b"").decode())
    assert start_fields == {"file_size": ["24"], "upload_phase": ["start"]}
    assert b'name="video_file_chunk"' in (transport.requests[1][3] or b"")
    assert b"abcdefgh" in (transport.requests[1][3] or b"")
    query = parse_qs(urlparse(transport.requests[2][1]).query)
    assert query == {"upload_phase": ["transfer"], "upload_session_id": ["session-1"]}
    finish_fields = parse_qs((transport.requests[3][3] or b"").decode())
    assert finish_fields == {
        "description": ["Caption\n#Tag"],
        "published": ["true"],
        "upload_phase": ["finish"],
        "upload_session_id": ["session-1"],
    }


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            FacebookHttpResponse(401, {}, b'{"error":{"code":190,"message":"expired page-secret"}}'),
            FacebookAuthError,
        ),
        (
            FacebookHttpResponse(400, {}, b'{"error":{"code":4,"message":"rate page-secret"}}'),
            FacebookRateLimitError,
        ),
        (FacebookHttpResponse(503, {}, b"provider page-secret"), RetryableFacebookError),
        (
            FacebookHttpResponse(400, {}, b'{"error":{"code":100,"message":"bad page-secret"}}'),
            FacebookRejectedError,
        ),
        (FacebookHttpResponse(404, {}, b"missing page-secret"), FacebookRejectedError),
        (FacebookHttpResponse(408, {}, b"timeout page-secret"), RetryableFacebookError),
    ],
)
def test_http_gateway_maps_provider_errors_without_leaking_tokens_or_bodies(
    response: FacebookHttpResponse,
    error_type: type[Exception],
) -> None:
    gateway = FacebookHttpGateway(transport=Transport(response))

    with pytest.raises(error_type) as raised:
        asyncio.run(
            gateway.transfer_chunk(
                credentials=FacebookPageCredentials("page-secret"),
                config=FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
                upload_session_id="session-secret",
                start_offset=0,
                data=b"data",
            )
        )

    message = str(raised.value)
    assert "page-secret" not in message
    assert "session-secret" not in message


def test_malformed_durable_checkpoint_fails_closed_before_credentials_or_provider(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    checkpoint_root = tmp_path / "facebook-checkpoints"
    store = FileFacebookCheckpointStore(checkpoint_root)
    (checkpoint_root / f"{command.publication_id}.json").write_text("{not-json", encoding="utf-8")
    gateway = Gateway()
    credential_events: list[object] = []
    adapter = FacebookAdapter(
        Approvals(),
        gateway,
        store,
        credentials_loader(credential_events),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    with pytest.raises(FacebookCheckpointError, match="malformed"):
        asyncio.run(adapter.publish(command, media))

    assert gateway.events == []
    assert credential_events == []


def test_concurrent_publishers_cannot_create_two_facebook_sessions(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()

    class BlockingGateway(Gateway):
        def __init__(self) -> None:
            super().__init__()
            self.creation_started = asyncio.Event()
            self.release_creation = asyncio.Event()

        async def start_upload(
            self,
            *,
            credentials: FacebookPageCredentials,
            config: FacebookConfig,
            byte_count: int,
            correlation: str,
        ) -> FacebookUploadSession:
            self.events.append(("start", config.page_id, config.graph_api_version, byte_count, correlation))
            self.creation_started.set()
            await self.release_creation.wait()
            return FacebookUploadSession("upload-session-1", "123456789012345", 0, min(8, byte_count))

    first_gateway = BlockingGateway()
    second_gateway = Gateway()
    first = FacebookAdapter(
        Approvals(),
        first_gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        max_chunk_size=8,
    )
    second = FacebookAdapter(
        Approvals(),
        second_gateway,
        store,
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
    )

    async def exercise() -> None:
        first_task = asyncio.create_task(first.publish(command, media))
        await first_gateway.creation_started.wait()
        with pytest.raises(ConcurrentFacebookPublishError, match="already in progress"):
            await second.publish(command, media)
        first_gateway.release_creation.set()
        await first_task

    asyncio.run(exercise())
    assert len([event for event in first_gateway.events if isinstance(event, tuple) and event[0] == "start"]) == 1
    assert second_gateway.events == []


def test_file_checkpoint_reservation_is_exclusive_across_store_instances(tmp_path: Path) -> None:
    _, command = write_media(tmp_path)
    checkpoint_root = tmp_path / "facebook-checkpoints"
    first = FileFacebookCheckpointStore(checkpoint_root)
    second = FileFacebookCheckpointStore(checkpoint_root)

    with first.reserve(command.publication_id):
        with pytest.raises(ConcurrentFacebookPublishError, match="already in progress"):
            with second.reserve(command.publication_id):
                pytest.fail("concurrent durable reservation was admitted")

    with second.reserve(command.publication_id):
        pass


def facebook_adapter(gateway: Gateway, store: MemoryFacebookCheckpointStore | None = None, **options: object) -> FacebookAdapter:
    return FacebookAdapter(
        Approvals(),
        gateway,
        store or MemoryFacebookCheckpointStore(),
        credentials_loader(),
        FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
        **options,  # type: ignore[arg-type]
    )


def gateway_calls(gateway: Gateway, name: str) -> list[tuple[object, ...]]:
    return [event for event in gateway.events if isinstance(event, tuple) and event[0] == name]


def test_exhausted_start_rate_limit_is_not_ambiguous_and_allows_a_later_retry(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    gateway = Gateway()
    gateway.start_results = [FacebookRateLimitError("rate page-secret"), FacebookRateLimitError("rate page-secret")]
    delays = Delays()

    with pytest.raises(FacebookRateLimitError, match="retry bound") as raised:
        asyncio.run(facebook_adapter(gateway, store, max_retries=1, retry_delay=delays).publish(command, media))

    assert "page-secret" not in str(raised.value)
    assert delays.values == [1.0]
    assert len(gateway_calls(gateway, "start")) == 2
    pending = store.load(command.publication_id)
    assert pending is not None and pending.creation_pending is False and pending.upload_session_id is None

    restarted = Gateway()
    result = asyncio.run(facebook_adapter(restarted, store, max_chunk_size=8, retry_delay=Delays()).publish(command, media))
    assert result.video_id == "123456789012345"
    assert len(gateway_calls(restarted, "start")) == 1


def test_transient_start_failure_without_a_response_remains_ambiguous(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    gateway = Gateway()
    gateway.start_results = [RetryableFacebookError("Facebook upload start failed transiently.")]
    delays = Delays()

    with pytest.raises(AmbiguousFacebookCreationError, match="manual recovery"):
        asyncio.run(facebook_adapter(gateway, store, retry_delay=delays).publish(command, media))

    assert delays.values == []
    assert store.load(command.publication_id).creation_pending is True  # type: ignore[union-attr]


def test_definitive_start_rejection_is_not_ambiguous(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    gateway = Gateway()
    gateway.start_results = [FacebookRejectedError("Facebook upload start failed with HTTP 400.")]

    with pytest.raises(FacebookRejectedError):
        asyncio.run(facebook_adapter(gateway, store).publish(command, media))

    assert store.load(command.publication_id).creation_pending is False  # type: ignore[union-attr]


def test_transient_transfer_and_query_failures_reconcile_within_the_retry_bound(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [RetryableFacebookError("Facebook upload transfer failed transiently.")]
    gateway.query_results = [RetryableFacebookError("Facebook upload offset query failed transiently."), FacebookTransferResult(0, 8)]
    delays = Delays()

    result = asyncio.run(facebook_adapter(gateway, max_chunk_size=8, max_retries=2, retry_delay=delays).publish(command, media))

    assert result.video_id == "123456789012345"
    assert delays.values == [1.0, 2.0]
    assert len(gateway_calls(gateway, "query")) == 2
    assert len([event for event in gateway_calls(gateway, "transfer") if event[1] == 0]) == 2


def test_transient_transfer_failures_exhaust_the_shared_retry_bound(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [RetryableFacebookError("transient"), RetryableFacebookError("transient")]
    gateway.query_results = [FacebookTransferResult(0, 8)]
    delays = Delays()

    with pytest.raises(FacebookRateLimitError, match="retry bound"):
        asyncio.run(facebook_adapter(gateway, max_chunk_size=8, max_retries=1, retry_delay=delays).publish(command, media))

    assert delays.values == [1.0]
    assert gateway_calls(gateway, "finish") == []


def test_stalled_provider_offset_consumes_retries_and_cannot_loop_forever(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [FacebookTransferResult(0, 8)] * 10
    delays = Delays()

    with pytest.raises(FacebookProcessingError, match="did not advance"):
        asyncio.run(facebook_adapter(gateway, max_chunk_size=8, max_retries=2, retry_delay=delays).publish(command, media))

    assert delays.values == [1.0, 2.0]
    assert len(gateway_calls(gateway, "transfer")) == 3
    assert gateway_calls(gateway, "finish") == []


def test_single_stalled_provider_offset_recovers_after_a_bounded_retry(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    gateway = Gateway()
    gateway.transfer_results = [FacebookTransferResult(0, 8)]
    delays = Delays()

    result = asyncio.run(facebook_adapter(gateway, max_chunk_size=8, max_retries=1, retry_delay=delays).publish(command, media))

    assert result.video_id == "123456789012345"
    assert delays.values == [1.0]


def test_definitive_finish_rejection_is_not_mislabelled_ambiguous(tmp_path: Path) -> None:
    media, command = write_media(tmp_path)
    store = MemoryFacebookCheckpointStore()
    gateway = Gateway()
    gateway.finish_results = [FacebookRejectedError("Facebook upload finish failed with HTTP 400.")]

    with pytest.raises(FacebookRejectedError, match="HTTP 400"):
        asyncio.run(facebook_adapter(gateway, store, max_chunk_size=len(MEDIA_BYTES)).publish(command, media))

    saved = store.load(command.publication_id)
    assert saved is not None and saved.completion_pending is False and saved.completed is False
    assert saved.next_offset == len(MEDIA_BYTES)
    assert gateway_calls(gateway, "processing") == []


def test_http_gateway_maps_expired_video_status_to_failed() -> None:
    gateway = FacebookHttpGateway(transport=Transport(FacebookHttpResponse(200, {}, b'{"status":{"video_status":"expired"}}')))

    status = asyncio.run(
        gateway.processing_status(
            credentials=FacebookPageCredentials("page-secret"),
            config=FacebookConfig(page_id="123456789", graph_api_version="v24.0"),
            video_id="123456789012345",
        )
    )

    assert status == "failed"
