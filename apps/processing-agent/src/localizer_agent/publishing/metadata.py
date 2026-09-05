"""Canonical publication metadata validation and hashing."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from localizer_agent.publishing.models import DestinationMetadata, PublicationMetadata


class MetadataValidationError(ValueError):
    """Raised when metadata cannot be submitted safely to its destination."""


def _require_text(value: str, field: str, *, multiline: bool = False) -> None:
    if not isinstance(value, str) or not value.strip():
        raise MetadataValidationError(f"{field} is required.")
    allowed_controls = {"\n", "\r", "\t"} if multiline else set()
    if any(unicodedata.category(character) == "Cc" and character not in allowed_controls for character in value):
        raise MetadataValidationError(f"{field} contains a disallowed control character.")


def _youtube_tag_length(tags: tuple[str, ...]) -> int:
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(0, len(tags) - 1)


def validate_destination_metadata(metadata: DestinationMetadata) -> None:
    from localizer_agent.publishing.models import FacebookMetadata, YouTubeMetadata

    if isinstance(metadata, YouTubeMetadata):
        _require_text(metadata.title, "YouTube title")
        _require_text(metadata.description, "YouTube description", multiline=True)
        if len(metadata.title) > 100:
            raise MetadataValidationError("YouTube title exceeds 100 characters.")
        if len(metadata.description.encode("utf-8")) > 5000:
            raise MetadataValidationError("YouTube description exceeds 5000 UTF-8 bytes.")
        if "<" in metadata.title or ">" in metadata.title or "<" in metadata.description or ">" in metadata.description:
            raise MetadataValidationError("YouTube title and description cannot contain angle brackets.")
        for tag in metadata.tags:
            _require_text(tag, "YouTube tag")
        if _youtube_tag_length(metadata.tags) > 500:
            raise MetadataValidationError("YouTube tags exceed the provider's 500-character representation limit.")
        return

    if isinstance(metadata, FacebookMetadata):
        _require_text(metadata.caption, "Facebook caption", multiline=True)
        if len(metadata.caption) > 63_206:
            raise MetadataValidationError("Facebook caption exceeds the defensive 63,206-character Page post limit.")
        if len(metadata.hashtags) > 100:
            raise MetadataValidationError("Facebook metadata exceeds 100 hashtags.")
        for hashtag in metadata.hashtags:
            if not hashtag.startswith("#") or len(hashtag) == 1 or not hashtag[1:].replace("_", "").isalnum() or not hashtag.isascii():
                raise MetadataValidationError("Facebook hashtags must use ASCII letters, numbers, or underscores after #.")
        return

    raise MetadataValidationError("Publication metadata has an unsupported destination.")


def canonical_metadata_bytes(metadata: PublicationMetadata) -> bytes:
    values = sorted((entry.wire_value() for entry in metadata.entries), key=lambda value: str(value["destination"]))
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def metadata_sha256(metadata: PublicationMetadata) -> str:
    return hashlib.sha256(canonical_metadata_bytes(metadata)).hexdigest()
