from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from localizer_agent.ingest import SourceIntegrityError, ingest_inbox_file, validate_https_url


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
