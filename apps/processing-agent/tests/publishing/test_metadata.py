from __future__ import annotations

import hashlib
import asyncio
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from localizer_agent.publishing.base import (
    ApprovalBoundPublicationAdapter,
    StalePublicationApprovalError,
    require_exact_publication_approval,
)
from localizer_agent.publishing.metadata import MetadataValidationError, canonical_metadata_bytes, metadata_sha256
from localizer_agent.publishing.models import (
    Destination,
    FacebookMetadata,
    PublicationApprovalSnapshot,
    PublicationCommand,
    PublicationMetadata,
    YouTubeMetadata,
)


JOB_ID = "00000000-0000-4000-8000-000000000001"
PUBLICATION_ID = "00000000-0000-4000-8000-000000000002"
RENDER_ID = "00000000-0000-4000-8000-000000000003"
APPROVAL_ID = "00000000-0000-4000-8000-000000000004"
RENDER_SHA256 = "a" * 64


def valid_metadata() -> PublicationMetadata:
    return PublicationMetadata(
        (
            YouTubeMetadata(
                title="Tiên giới",
                description="Dòng một\n第二行",
                tags=("hoạt hình", "tu tiên"),
            ),
            FacebookMetadata(caption="Xin chào 世界", hashtags=("#HoatHinh", "#TuTien")),
        )
    )


def valid_command(**changes: object) -> PublicationCommand:
    metadata = valid_metadata()
    values: dict[str, object] = {
        "approval_id": APPROVAL_ID,
        "destination": Destination.YOUTUBE,
        "job_id": JOB_ID,
        "metadata": metadata,
        "metadata_sha256": metadata_sha256(metadata),
        "metadata_version": 3,
        "publication_id": PUBLICATION_ID,
        "render_id": RENDER_ID,
        "render_sha256": RENDER_SHA256,
        "render_version": 2,
    }
    values.update(changes)
    return PublicationCommand(**values)  # type: ignore[arg-type]


def test_metadata_hash_is_canonical_unicode_and_destination_order_independent() -> None:
    metadata = valid_metadata()
    reversed_metadata = PublicationMetadata(tuple(reversed(metadata.entries)))
    expected = (
        '[{"caption":"Xin chào 世界","destination":"FACEBOOK","hashtags":["#HoatHinh","#TuTien"]},'
        '{"description":"Dòng một\\n第二行","destination":"YOUTUBE","tags":["hoạt hình","tu tiên"],"title":"Tiên giới"}]'
    ).encode()

    assert canonical_metadata_bytes(metadata) == expected
    assert metadata_sha256(metadata) == hashlib.sha256(expected).hexdigest()
    assert metadata_sha256(reversed_metadata) == metadata_sha256(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        YouTubeMetadata(title="", description="Description", tags=()),
        YouTubeMetadata(title="x" * 101, description="Description", tags=()),
        YouTubeMetadata(title="Title", description="é" * 2501, tags=()),
        YouTubeMetadata(title="Title <draft>", description="Description", tags=()),
        YouTubeMetadata(title="Title", description="Description", tags=("x" * 501,)),
        YouTubeMetadata(title="Title", description="Description", tags=("",)),
        FacebookMetadata(caption="", hashtags=()),
        FacebookMetadata(caption="Caption\x00", hashtags=()),
        FacebookMetadata(caption="Caption", hashtags=("animation",)),
        FacebookMetadata(caption="Caption", hashtags=tuple(f"#tag{index}" for index in range(101))),
        FacebookMetadata(caption="x" * 63_207, hashtags=()),
    ],
)
def test_metadata_rejects_missing_oversized_or_provider_invalid_values(metadata: object) -> None:
    with pytest.raises(MetadataValidationError):
        PublicationMetadata((metadata,))  # type: ignore[arg-type]


def test_metadata_allows_multiline_printable_unicode_but_rejects_control_characters() -> None:
    assert PublicationMetadata((FacebookMetadata(caption="Dòng một\n第二行", hashtags=()),))

    with pytest.raises(MetadataValidationError):
        PublicationMetadata((FacebookMetadata(caption="unsafe\x1ftext", hashtags=()),))


def test_metadata_requires_unique_destination_identity() -> None:
    with pytest.raises(MetadataValidationError):
        PublicationMetadata(
            (
                FacebookMetadata(caption="First", hashtags=()),
                FacebookMetadata(caption="Second", hashtags=()),
            )
        )


def test_metadata_collections_are_immutable_after_construction() -> None:
    youtube = YouTubeMetadata(title="Title", description="Description", tags=["animation"])  # type: ignore[arg-type]
    facebook = FacebookMetadata(caption="Caption", hashtags=["#Animation"])  # type: ignore[arg-type]
    metadata = PublicationMetadata([youtube, facebook])  # type: ignore[arg-type]

    assert youtube.tags == ("animation",)
    assert facebook.hashtags == ("#Animation",)
    assert isinstance(metadata.entries, tuple)

    with pytest.raises(MetadataValidationError):
        YouTubeMetadata(title="Title", description="Description", tags="animation")  # type: ignore[arg-type]
    with pytest.raises(MetadataValidationError):
        FacebookMetadata(caption="Caption", hashtags="#Animation")  # type: ignore[arg-type]


def test_command_requires_all_exact_bindings_and_is_immutable() -> None:
    command = valid_command()

    assert command.metadata.for_destination(command.destination).destination is Destination.YOUTUBE
    with pytest.raises(FrozenInstanceError):
        command.render_version = 4  # type: ignore[misc]

    for changes in (
        {"publication_id": ""},
        {"render_sha256": "ABC"},
        {"metadata_version": 0},
        {"metadata_version": True},
        {"render_version": 1.5},
        {"metadata_sha256": "b" * 64},
        {"destination": "TIKTOK"},
    ):
        with pytest.raises(ValueError):
            valid_command(**changes)


def test_provider_correlation_is_stable_specific_and_contains_no_metadata_text() -> None:
    command = valid_command()

    correlation = command.provider_correlation

    assert correlation == valid_command().provider_correlation
    assert correlation != replace(command, publication_id="00000000-0000-4000-8000-000000000099").provider_correlation
    assert correlation.startswith("localizer-")
    assert "Tiên giới" not in correlation
    assert "Xin chào" not in correlation


def test_exact_publication_approval_accepts_every_bound_value() -> None:
    command = valid_command()
    approval = PublicationApprovalSnapshot.from_command(command, approved=True)

    assert require_exact_publication_approval(command, approval) is approval


@pytest.mark.parametrize(
    "change",
    [
        {"approved": False},
        {"publication_id": "00000000-0000-4000-8000-000000000099"},
        {"approval_id": "00000000-0000-4000-8000-000000000099"},
        {"job_id": "00000000-0000-4000-8000-000000000099"},
        {"render_id": "00000000-0000-4000-8000-000000000099"},
        {"render_version": 3},
        {"render_sha256": "b" * 64},
        {"metadata_version": 4},
        {"metadata_sha256": "b" * 64},
        {"destinations": (Destination.FACEBOOK,)},
    ],
)
def test_exact_publication_approval_rejects_missing_revoked_or_stale_binding(change: dict[str, object]) -> None:
    command = valid_command()
    approval = replace(PublicationApprovalSnapshot.from_command(command, approved=True), **change)

    with pytest.raises(StalePublicationApprovalError):
        require_exact_publication_approval(command, approval)

    with pytest.raises(StalePublicationApprovalError):
        require_exact_publication_approval(command, None)


@pytest.mark.parametrize(
    "change",
    [
        {"approved": 1},
        {"publication_id": "not-a-uuid"},
        {"approval_id": "not-a-uuid"},
        {"render_version": 2.0},
        {"metadata_version": True},
        {"render_sha256": "A" * 64},
        {"metadata_sha256": "B" * 64},
        {"destinations": "YOUTUBE"},
        {"destinations": (Destination.YOUTUBE, Destination.YOUTUBE)},
    ],
)
def test_publication_approval_snapshot_rejects_malformed_control_plane_values(change: dict[str, object]) -> None:
    values = PublicationApprovalSnapshot.from_command(valid_command(), approved=True).__dict__ | change

    with pytest.raises(ValueError):
        PublicationApprovalSnapshot(**values)  # type: ignore[arg-type]


def test_approval_bound_adapter_rechecks_before_media_access(tmp_path: Path) -> None:
    command = valid_command()
    events: list[str] = []

    class ApprovalClient:
        async def get_publication_approval(self, requested: PublicationCommand) -> PublicationApprovalSnapshot:
            events.append("approval")
            return PublicationApprovalSnapshot.from_command(requested, approved=True)

    class Adapter(ApprovalBoundPublicationAdapter):
        destination = Destination.YOUTUBE

        async def publish_approved(self, requested: PublicationCommand, media_path: Path) -> None:
            events.append(f"media:{media_path.read_text()}")

    media_path = tmp_path / "render.mp4"
    media_path.write_text("render")

    asyncio.run(Adapter(ApprovalClient()).publish(command, media_path))

    assert events == ["approval", "media:render"]


def test_approval_bound_adapter_never_accesses_media_for_stale_approval(tmp_path: Path) -> None:
    command = valid_command()
    events: list[str] = []

    class ApprovalClient:
        async def get_publication_approval(self, requested: PublicationCommand) -> PublicationApprovalSnapshot:
            events.append("approval")
            return replace(PublicationApprovalSnapshot.from_command(requested, approved=True), render_sha256="b" * 64)

    class Adapter(ApprovalBoundPublicationAdapter):
        destination = Destination.YOUTUBE

        async def publish_approved(self, requested: PublicationCommand, media_path: Path) -> None:
            events.append("media")

    with pytest.raises(StalePublicationApprovalError):
        asyncio.run(Adapter(ApprovalClient()).publish(command, tmp_path / "missing.mp4"))

    assert events == ["approval"]
