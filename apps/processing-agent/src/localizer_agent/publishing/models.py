"""Immutable, provider-neutral publication commands."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeAlias
from uuid import UUID


class Destination(str, Enum):
    YOUTUBE = "YOUTUBE"
    FACEBOOK = "FACEBOOK"


@dataclass(frozen=True)
class YouTubeMetadata:
    title: str
    description: str
    tags: tuple[str, ...]
    destination: Destination = field(default=Destination.YOUTUBE, init=False)

    def __post_init__(self) -> None:
        from localizer_agent.publishing.metadata import MetadataValidationError

        if isinstance(self.tags, (str, bytes)) or not isinstance(self.tags, (list, tuple)):
            raise MetadataValidationError("YouTube tags must be a list or tuple.")
        object.__setattr__(self, "tags", tuple(self.tags))

    def wire_value(self) -> dict[str, object]:
        return {
            "destination": self.destination.value,
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class FacebookMetadata:
    caption: str
    hashtags: tuple[str, ...]
    destination: Destination = field(default=Destination.FACEBOOK, init=False)

    def __post_init__(self) -> None:
        from localizer_agent.publishing.metadata import MetadataValidationError

        if isinstance(self.hashtags, (str, bytes)) or not isinstance(self.hashtags, (list, tuple)):
            raise MetadataValidationError("Facebook hashtags must be a list or tuple.")
        object.__setattr__(self, "hashtags", tuple(self.hashtags))

    def wire_value(self) -> dict[str, object]:
        return {
            "destination": self.destination.value,
            "caption": self.caption,
            "hashtags": list(self.hashtags),
        }


DestinationMetadata: TypeAlias = YouTubeMetadata | FacebookMetadata


@dataclass(frozen=True)
class PublicationMetadata:
    entries: tuple[DestinationMetadata, ...]

    def __post_init__(self) -> None:
        from localizer_agent.publishing.metadata import MetadataValidationError, validate_destination_metadata

        entries = tuple(self.entries)
        object.__setattr__(self, "entries", entries)
        if not 1 <= len(entries) <= 2:
            raise MetadataValidationError("Publication metadata requires one or two destinations.")
        destinations = [entry.destination for entry in entries]
        if len(set(destinations)) != len(destinations):
            raise MetadataValidationError("Publication metadata destinations must be unique.")
        for entry in entries:
            validate_destination_metadata(entry)

    def for_destination(self, destination: Destination) -> DestinationMetadata:
        for entry in self.entries:
            if entry.destination is destination:
                return entry
        raise ValueError("Publication metadata does not contain the requested destination.")

    @property
    def destinations(self) -> tuple[Destination, ...]:
        return tuple(sorted((entry.destination for entry in self.entries), key=lambda destination: destination.value))


def _require_uuid(value: str, field: str) -> None:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a canonical UUID.") from error
    if str(parsed) != value:
        raise ValueError(f"{field} must be a canonical UUID.")


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest.")


def _require_positive_int(value: int, field: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")


@dataclass(frozen=True)
class PublicationCommand:
    publication_id: str
    job_id: str
    destination: Destination
    render_id: str
    render_version: int
    render_sha256: str
    metadata_version: int
    metadata_sha256: str
    approval_id: str
    metadata: PublicationMetadata

    def __post_init__(self) -> None:
        from localizer_agent.publishing.metadata import metadata_sha256

        try:
            destination = Destination(self.destination)
        except (TypeError, ValueError) as error:
            raise ValueError("Publication destination is unsupported.") from error
        object.__setattr__(self, "destination", destination)
        for field, value in (
            ("publication_id", self.publication_id),
            ("job_id", self.job_id),
            ("render_id", self.render_id),
            ("approval_id", self.approval_id),
        ):
            _require_uuid(value, field)
        _require_sha256(self.render_sha256, "render_sha256")
        _require_sha256(self.metadata_sha256, "metadata_sha256")
        _require_positive_int(self.render_version, "render_version")
        _require_positive_int(self.metadata_version, "metadata_version")
        self.metadata.for_destination(destination)
        if metadata_sha256(self.metadata) != self.metadata_sha256:
            raise ValueError("Publication metadata does not match its immutable hash.")

    @property
    def provider_correlation(self) -> str:
        binding = json.dumps(
            {
                "destination": self.destination.value,
                "metadataSha256": self.metadata_sha256,
                "metadataVersion": self.metadata_version,
                "publicationId": self.publication_id,
                "renderId": self.render_id,
                "renderSha256": self.render_sha256,
                "renderVersion": self.render_version,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return f"localizer-{hashlib.sha256(binding).hexdigest()}"


@dataclass(frozen=True)
class PublicationApprovalSnapshot:
    approved: bool
    publication_id: str
    approval_id: str
    job_id: str
    render_id: str
    render_version: int
    render_sha256: str
    metadata_version: int
    metadata_sha256: str
    destinations: tuple[Destination, ...]

    def __post_init__(self) -> None:
        if type(self.approved) is not bool:
            raise ValueError("approved must be a boolean.")
        for field, value in (
            ("publication_id", self.publication_id),
            ("approval_id", self.approval_id),
            ("job_id", self.job_id),
            ("render_id", self.render_id),
        ):
            _require_uuid(value, field)
        _require_positive_int(self.render_version, "render_version")
        _require_positive_int(self.metadata_version, "metadata_version")
        _require_sha256(self.render_sha256, "render_sha256")
        _require_sha256(self.metadata_sha256, "metadata_sha256")
        if isinstance(self.destinations, (str, bytes)) or not isinstance(self.destinations, (list, tuple)):
            raise ValueError("destinations must be a list or tuple.")
        try:
            destinations = tuple(sorted((Destination(value) for value in self.destinations), key=lambda value: value.value))
        except (TypeError, ValueError) as error:
            raise ValueError("Approval contains an unsupported destination.") from error
        if not 1 <= len(destinations) <= 2 or len(set(destinations)) != len(destinations):
            raise ValueError("Approval destinations must contain one or two unique values.")
        object.__setattr__(self, "destinations", destinations)

    @classmethod
    def from_command(cls, command: PublicationCommand, *, approved: bool) -> PublicationApprovalSnapshot:
        return cls(
            approved=approved,
            publication_id=command.publication_id,
            approval_id=command.approval_id,
            job_id=command.job_id,
            render_id=command.render_id,
            render_version=command.render_version,
            render_sha256=command.render_sha256,
            metadata_version=command.metadata_version,
            metadata_sha256=command.metadata_sha256,
            destinations=command.metadata.destinations,
        )
