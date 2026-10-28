from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from localizer_agent.cleanup import CleanupHandler
from localizer_agent.generated.models import ClaimJobResponse
from localizer_agent.preview.registry import PreviewRegistry
from localizer_agent.workspace import JobWorkspaceManager

TARGET = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"


def cleanup_claim(target: str = TARGET) -> ClaimJobResponse:
    return ClaimJobResponse.model_validate(
        {
            "cleanupInput": {"targetJobId": target},
            "jobId": "00000000-0000-4000-8000-000000000010",
            "jobType": "DELETE_EXPIRED",
            "jobVersion": 2,
            "leaseExpiresAtUtc": "2026-10-26T00:05:00Z",
            "leaseToken": "t" * 43,
            "requestId": "00000000-0000-4000-8000-000000000099",
            "state": "QUEUED",
        }
    )


class Client:
    def __init__(self) -> None:
        self.completions = []

    async def complete(self, claim, completion) -> None:
        self.completions.append(completion)


def make(tmp_path: Path) -> tuple[CleanupHandler, Client, JobWorkspaceManager, PreviewRegistry]:
    manager = JobWorkspaceManager(tmp_path / "workspaces", minimum_free_bytes=0)
    registry = PreviewRegistry(tmp_path / "workspaces" / "preview-registry.json")
    client = Client()
    return CleanupHandler(client, manager, registry), client, manager, registry


def test_removes_the_target_workspace_and_its_preview_tokens_then_completes(tmp_path: Path) -> None:
    handler, client, manager, registry = make(tmp_path)
    target = manager.create(TARGET, OTHER)
    (target.root / "render.mp4").write_bytes(b"render")
    kept = manager.create(OTHER, OTHER)
    expires = datetime.now(UTC) + timedelta(minutes=5)
    token = registry.register(TARGET, f"{TARGET}/artifacts/render.mp4", expires)
    other_token = registry.register(OTHER, f"{OTHER}/artifacts/render.mp4", expires)

    asyncio.run(handler.handle(cleanup_claim()))

    assert not target.root.exists() and kept.root.exists()
    now = datetime.now(UTC)
    assert registry.resolve(token, TARGET, now) is None
    assert registry.resolve(other_token, OTHER, now) is not None
    assert PreviewRegistry(tmp_path / "workspaces" / "preview-registry.json").resolve(token, TARGET, now) is None
    [completion] = client.completions
    assert (completion.expectedState, completion.state, completion.step) == ("QUEUED", "COMPLETED", "workspace_removed")


def test_is_idempotent_when_the_workspace_is_already_gone(tmp_path: Path) -> None:
    handler, client, _, _ = make(tmp_path)

    asyncio.run(handler.handle(cleanup_claim()))

    assert [completion.step for completion in client.completions] == ["workspace_absent"]


def test_refuses_a_symlinked_workspace_and_leaves_its_target_untouched(tmp_path: Path) -> None:
    handler, client, _, _ = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / TARGET).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        asyncio.run(handler.handle(cleanup_claim()))

    assert (outside / "keep.txt").exists()
    assert client.completions == []
