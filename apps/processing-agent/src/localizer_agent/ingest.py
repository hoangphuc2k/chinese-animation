"""Hardened, bounded ingest sources for creator HTTPS links and OCI inbox files."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import os
import re
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import ParseResult, urljoin, urlparse


class SourceIntegrityError(ValueError):
    """A source locator or downloaded content is unsafe for ingest."""


@dataclass(frozen=True)
class IngestedSource:
    path: Path
    sha256: str
    byte_count: int


class ReadableStream(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class HttpResponse(ReadableStream, Protocol):
    status: int

    def getheader(self, name: str, default: str | None = None) -> str | None: ...


class PinnedConnection(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> HttpResponse: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str, str, int], PinnedConnection]


_INBOX_FILE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified)


def _validated_https_addresses(url: str, resolver: Callable[[str], list[str]] | None = None) -> tuple[ParseResult, list[str]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SourceIntegrityError("Only credential-free HTTPS URLs are supported.")
    try:
        port = parsed.port
    except ValueError as error:
        raise SourceIntegrityError("HTTPS source port is invalid.") from error
    if port not in (None, 443):
        raise SourceIntegrityError("Only the default HTTPS port is supported.")
    resolve = resolver or _resolve_host
    try:
        addresses = resolve(parsed.hostname)
    except OSError as error:
        raise SourceIntegrityError("The source host could not be resolved.") from error
    if not addresses or any(not _public_address(address) for address in addresses):
        raise SourceIntegrityError("The source host resolved to a non-public address.")
    return parsed, addresses


def validate_https_url(url: str, resolver: Callable[[str], list[str]] | None = None) -> None:
    _validated_https_addresses(url, resolver)


def _resolve_host(hostname: str) -> list[str]:
    return sorted({entry[4][0] for entry in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)})


def _copy_stream(source: ReadableStream, destination: Path, max_bytes: int) -> IngestedSource:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.partial")
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with partial.open("xb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                byte_count += len(chunk)
                if byte_count > max_bytes:
                    raise SourceIntegrityError("Source exceeds the configured byte limit.")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return IngestedSource(path=destination, sha256=digest.hexdigest(), byte_count=byte_count)


def ingest_inbox_file(inbox_root: Path, inbox_file_id: str, destination: Path, max_bytes: int) -> IngestedSource:
    if not _INBOX_FILE_ID.fullmatch(inbox_file_id):
        raise SourceIntegrityError("Inbox file identifier is invalid.")
    root = inbox_root.resolve(strict=True)
    source = root / inbox_file_id
    if source.parent != root or source.is_symlink() or not source.is_file():
        raise SourceIntegrityError("Inbox source is not a regular file inside the configured inbox.")
    if source.stat().st_size > max_bytes:
        raise SourceIntegrityError("Source exceeds the configured byte limit.")
    with source.open("rb") as stream:
        return _copy_stream(stream, destination, max_bytes)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, timeout_seconds: int) -> None:
        super().__init__(hostname, port=443, timeout=timeout_seconds, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        socket_connection = socket.create_connection((self._address, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(socket_connection, server_hostname=self.host)


def ingest_https_url(
    url: str,
    destination: Path,
    max_bytes: int,
    timeout_seconds: int,
    resolver: Callable[[str], list[str]] | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> IngestedSource:
    """Download a creator HTTPS source, re-validating and re-pinning every redirect hop."""
    connect = connection_factory or _PinnedHTTPSConnection
    current_url = url
    for _ in range(_MAX_REDIRECTS + 1):
        parsed, addresses = _validated_https_addresses(current_url, resolver)
        outcome = _fetch_pinned(connect, parsed, addresses, destination, max_bytes, timeout_seconds)
        if isinstance(outcome, IngestedSource):
            return outcome
        current_url = urljoin(current_url, outcome)
    raise SourceIntegrityError(f"Source exceeded the {_MAX_REDIRECTS}-redirect limit.")


def _fetch_pinned(
    connect: ConnectionFactory,
    parsed: ParseResult,
    addresses: list[str],
    destination: Path,
    max_bytes: int,
    timeout_seconds: int,
) -> IngestedSource | str:
    """Fetch one validated URL; return the ingested source, or the raw Location of a redirect."""
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    last_error: OSError | None = None
    for address in addresses:
        connection = connect(parsed.hostname or "", address, timeout_seconds)
        try:
            connection.request("GET", request_target, headers={"Accept": "video/*,application/octet-stream"})
            response = connection.getresponse()
            if response.status in _REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location or not location.strip():
                    raise SourceIntegrityError("Source server returned a redirect without a location.")
                return location.strip()
            if not 200 <= response.status < 300:
                raise SourceIntegrityError("Source server returned a non-success response.")
            content_length = response.getheader("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError as error:
                    raise SourceIntegrityError("Source server returned an invalid content length.") from error
                if declared_length < 0 or declared_length > max_bytes:
                    raise SourceIntegrityError("Source exceeds the configured byte limit.")
            return _copy_stream(response, destination, max_bytes)
        except OSError as error:
            last_error = error
        finally:
            connection.close()
    raise SourceIntegrityError("Source server could not be reached.") from last_error
