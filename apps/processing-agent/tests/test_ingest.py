from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from localizer_agent.ingest import SourceIntegrityError, ingest_https_url, ingest_inbox_file, validate_https_url


def test_inbox_ingest_copies_a_stable_file_to_a_controlled_destination(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    payload = b"creator-approved-media"
    (inbox / "creator-submission-42.mp4").write_bytes(payload)
    destination = tmp_path / "workspace" / "source.bin"

    result = ingest_inbox_file(inbox, "creator-submission-42.mp4", destination, max_bytes=1024)

    assert destination.read_bytes() == payload
    assert result.byte_count == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert not destination.with_suffix(".bin.partial").exists()


@pytest.mark.parametrize(
    ("url", "addresses"),
    [
        ("http://creator.example.test/video.mp4", ["93.184.216.34"]),
        ("https://user:pass@creator.example.test/video.mp4", ["93.184.216.34"]),
        ("https://creator.example.test/video.mp4", ["127.0.0.1"]),
        ("https://creator.example.test/video.mp4", ["10.0.0.7"]),
    ],
)
def test_https_ingest_rejects_unsafe_urls_and_resolved_addresses(url: str, addresses: list[str]) -> None:
    with pytest.raises(SourceIntegrityError):
        validate_https_url(url, lambda _: addresses)


def test_inbox_ingest_rejects_path_escape_and_oversized_sources(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "large.mp4").write_bytes(b"0123456789")

    with pytest.raises(SourceIntegrityError):
        ingest_inbox_file(inbox, "../large.mp4", tmp_path / "destination", max_bytes=100)
    with pytest.raises(SourceIntegrityError):
        ingest_inbox_file(inbox, "large.mp4", tmp_path / "destination", max_bytes=9)


PUBLIC_ADDRESSES = {
    "creator.example.test": ["93.184.216.34"],
    "cdn.example.test": ["93.184.216.35"],
    "internal.example.test": ["10.0.0.7"],
}


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None, body: bytes = b"") -> None:
        self.status = status
        self._headers = headers or {}
        self._body = io.BytesIO(body)

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class FakeServer:
    """Serves canned responses keyed by (hostname, request target) and records every pinned connection."""

    def __init__(self, routes: dict[tuple[str, str], FakeResponse]) -> None:
        self.routes = routes
        self.connections: list[tuple[str, str, int]] = []
        self.requests: list[tuple[str, str, str]] = []

    def connect(self, hostname: str, address: str, timeout_seconds: int) -> "FakeConnection":
        self.connections.append((hostname, address, timeout_seconds))
        return FakeConnection(self, hostname, address)


class FakeConnection:
    def __init__(self, server: FakeServer, hostname: str, address: str) -> None:
        self._server = server
        self._hostname = hostname
        self._address = address
        self._target = ""

    def request(self, method: str, target: str, headers: dict[str, str]) -> None:
        self._server.requests.append((method, self._hostname, target))
        self._target = target

    def getresponse(self) -> FakeResponse:
        return self._server.routes[(self._hostname, self._target)]

    def close(self) -> None:
        pass


def resolve_fixture(hostname: str) -> list[str]:
    return PUBLIC_ADDRESSES[hostname]


def test_https_ingest_follows_a_validated_redirect_and_repins_the_connection(tmp_path: Path) -> None:
    payload = b"redirected-creator-media"
    server = FakeServer(
        {
            ("creator.example.test", "/video.mp4"): FakeResponse(302, {"Location": "https://cdn.example.test/files/v.mp4?sig=1"}),
            ("cdn.example.test", "/files/v.mp4?sig=1"): FakeResponse(200, {"Content-Length": str(len(payload))}, payload),
        }
    )
    destination = tmp_path / "workspace" / "source.bin"

    result = ingest_https_url(
        "https://creator.example.test/video.mp4",
        destination,
        max_bytes=1024,
        timeout_seconds=7,
        resolver=resolve_fixture,
        connection_factory=server.connect,
    )

    assert destination.read_bytes() == payload
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert server.connections == [
        ("creator.example.test", "93.184.216.34", 7),
        ("cdn.example.test", "93.184.216.35", 7),
    ]


def test_https_ingest_resolves_relative_redirect_locations(tmp_path: Path) -> None:
    server = FakeServer(
        {
            ("creator.example.test", "/a/video.mp4"): FakeResponse(301, {"Location": "../b/video.mp4"}),
            ("creator.example.test", "/b/video.mp4"): FakeResponse(200, {}, b"media"),
        }
    )

    ingest_https_url(
        "https://creator.example.test/a/video.mp4",
        tmp_path / "source.bin",
        max_bytes=1024,
        timeout_seconds=7,
        resolver=resolve_fixture,
        connection_factory=server.connect,
    )

    assert [target for _, _, target in server.requests] == ["/a/video.mp4", "/b/video.mp4"]


@pytest.mark.parametrize(
    "location",
    [
        "https://internal.example.test/video.mp4",
        "https://127.0.0.1/video.mp4",
        "http://cdn.example.test/video.mp4",
        "https://user:pass@cdn.example.test/video.mp4",
        "https://cdn.example.test:8443/video.mp4",
        None,
    ],
)
def test_https_ingest_rejects_unsafe_or_missing_redirect_targets_before_connecting(tmp_path: Path, location: str | None) -> None:
    headers = {} if location is None else {"Location": location}
    server = FakeServer({("creator.example.test", "/video.mp4"): FakeResponse(307, headers)})

    def resolver(hostname: str) -> list[str]:
        return ["127.0.0.1"] if hostname == "127.0.0.1" else resolve_fixture(hostname)

    with pytest.raises(SourceIntegrityError):
        ingest_https_url(
            "https://creator.example.test/video.mp4",
            tmp_path / "source.bin",
            max_bytes=1024,
            timeout_seconds=7,
            resolver=resolver,
            connection_factory=server.connect,
        )

    assert [hostname for hostname, _, _ in server.connections] == ["creator.example.test"]
    assert not (tmp_path / "source.bin").exists()


def redirect_chain(hops: int) -> dict[tuple[str, str], FakeResponse]:
    routes = {("creator.example.test", f"/hop{index}"): FakeResponse(308, {"Location": f"/hop{index + 1}"}) for index in range(hops)}
    routes[("creator.example.test", f"/hop{hops}")] = FakeResponse(200, {}, b"media")
    return routes


def test_https_ingest_follows_at_most_five_redirects(tmp_path: Path) -> None:
    allowed = FakeServer(redirect_chain(5))
    ingest_https_url(
        "https://creator.example.test/hop0",
        tmp_path / "allowed.bin",
        max_bytes=1024,
        timeout_seconds=7,
        resolver=resolve_fixture,
        connection_factory=allowed.connect,
    )
    assert len(allowed.requests) == 6

    exceeded = FakeServer(redirect_chain(6))
    with pytest.raises(SourceIntegrityError, match="redirect"):
        ingest_https_url(
            "https://creator.example.test/hop0",
            tmp_path / "exceeded.bin",
            max_bytes=1024,
            timeout_seconds=7,
            resolver=resolve_fixture,
            connection_factory=exceeded.connect,
        )
    assert len(exceeded.requests) == 6
    assert not (tmp_path / "exceeded.bin").exists()


def test_https_ingest_still_rejects_non_redirect_error_responses(tmp_path: Path) -> None:
    server = FakeServer({("creator.example.test", "/video.mp4"): FakeResponse(404)})

    with pytest.raises(SourceIntegrityError, match="non-success"):
        ingest_https_url(
            "https://creator.example.test/video.mp4",
            tmp_path / "source.bin",
            max_bytes=1024,
            timeout_seconds=7,
            resolver=resolve_fixture,
            connection_factory=server.connect,
        )


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_https_ingest_reports_server_outages_as_transient(tmp_path: Path, status: int) -> None:
    from localizer_agent.ingest import SourceUnavailableError

    server = FakeServer({("creator.example.test", "/video.mp4"): FakeResponse(status)})

    with pytest.raises(SourceUnavailableError):
        ingest_https_url(
            "https://creator.example.test/video.mp4",
            tmp_path / "source.bin",
            max_bytes=1024,
            timeout_seconds=7,
            resolver=resolve_fixture,
            connection_factory=server.connect,
        )


def test_https_ingest_reports_dns_failure_as_transient(tmp_path: Path) -> None:
    from localizer_agent.ingest import SourceUnavailableError

    def failing_resolver(host: str) -> list[str]:
        raise OSError("temporary DNS failure")

    with pytest.raises(SourceUnavailableError):
        ingest_https_url("https://creator.example.test/video.mp4", tmp_path / "source.bin", 1024, 7, resolver=failing_resolver)


def test_https_ingest_keeps_definitive_rejections_deterministic(tmp_path: Path) -> None:
    from localizer_agent.ingest import SourceUnavailableError

    server = FakeServer({("creator.example.test", "/video.mp4"): FakeResponse(404)})

    with pytest.raises(SourceIntegrityError) as raised:
        ingest_https_url(
            "https://creator.example.test/video.mp4",
            tmp_path / "source.bin",
            max_bytes=1024,
            timeout_seconds=7,
            resolver=resolve_fixture,
            connection_factory=server.connect,
        )
    assert not isinstance(raised.value, SourceUnavailableError)
