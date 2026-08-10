from __future__ import annotations

from pathlib import Path

import pytest

from localizer_agent.workspace import DiskCapacityError, JobWorkspaceManager


def test_workspace_persists_an_atomic_sanitized_manifest_and_recovers_it(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    workspace = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")

    manifest = manager.update(workspace, stage="DOWNLOADED", source_byte_count=12, source_hash="a" * 64)

    assert manifest.stage == "DOWNLOADED"
    assert workspace.manifest_path.exists()
    assert not list(workspace.root.glob("*.tmp"))
    assert manager.recover() == [manifest]
    assert "creator-submission" not in workspace.manifest_path.read_text()


def test_workspace_can_be_reopened_by_a_sanitized_job_identifier(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    created = manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")

    reopened = manager.open("00000000-0000-4000-8000-000000000001")

    assert reopened == created
    assert manager.load(reopened).job_id == "00000000-0000-4000-8000-000000000001"


def test_workspace_rejects_unsafe_job_ids_and_enforces_disk_floor(tmp_path: Path) -> None:
    manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=10**30)

    with pytest.raises(DiskCapacityError):
        manager.create("00000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000002")

    safe_manager = JobWorkspaceManager(tmp_path / "workspace", minimum_free_bytes=1)
    with pytest.raises(ValueError):
        safe_manager.create("../../outside", "00000000-0000-4000-8000-000000000002")
